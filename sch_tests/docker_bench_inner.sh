#!/bin/bash
# Comprehensive Consolidated Benchmark Runner
# Executes all benchmarks from both run_benchmarks.sh and run_benchmark_lat.sh
set -euo pipefail

SCHED_NAME="${1:-unknown}"
RESULTS_DIR="/results"

echo "=========================================================="
echo " Running Full Consolidated Benchmark in Isolated Container"
echo " Target Scheduler: $SCHED_NAME"
echo " Date:             $(date)"
echo " CPUs Allowed:     $(grep Cpus_allowed_list /proc/self/status | awk '{print $2}')"
echo " Cgroup RAM Limit: $(cat /sys/fs/cgroup/memory.max 2>/dev/null | awk '{printf "%.1f GB\n", $1/1024/1024/1024}' || echo 'Unrestricted')"
echo "=========================================================="

mkdir -p "$RESULTS_DIR"

# -------------------------------------------------------------
# 1. Sysbench (CPU Events/s Throughput)
# -------------------------------------------------------------
echo "--> [1/7] Running Sysbench CPU Throughput..."
SYS_LOG="$RESULTS_DIR/${SCHED_NAME}_sysbench.log"
sysbench cpu --cpu-max-prime=30000 --threads=12 --time=15 run > "$SYS_LOG" 2>&1
EPS=$(grep "events per second:" "$SYS_LOG" | awk '{print $4}' || echo "N/A")
echo "    Result: $EPS events/s"

# -------------------------------------------------------------
# 2. Schbench (Wakeup & Request Latencies)
# -------------------------------------------------------------
echo "--> [2/7] Running Schbench Latencies..."
SCH_LOG="$RESULTS_DIR/${SCHED_NAME}_schbench.log"
schbench -m 4 -t 3 -r 15 > "$SCH_LOG" 2>&1 || true

W_P50=$(grep -A 6 "Wakeup Latencies" "$SCH_LOG" 2>/dev/null | grep "50.0th:" | tail -1 | sed 's/.*50.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
W_P90=$(grep -A 6 "Wakeup Latencies" "$SCH_LOG" 2>/dev/null | grep "90.0th:" | tail -1 | sed 's/.*90.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
W_P99=$(grep -A 6 "Wakeup Latencies" "$SCH_LOG" 2>/dev/null | grep "99.0th:" | tail -1 | sed 's/.*99.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
W_P999=$(grep -A 6 "Wakeup Latencies" "$SCH_LOG" 2>/dev/null | grep "99.9th:" | tail -1 | sed 's/.*99.9th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")

R_P50=$(grep -A 6 "Request Latencies" "$SCH_LOG" 2>/dev/null | grep "50.0th:" | tail -1 | sed 's/.*50.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
R_P90=$(grep -A 6 "Request Latencies" "$SCH_LOG" 2>/dev/null | grep "90.0th:" | tail -1 | sed 's/.*90.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
R_P99=$(grep -A 6 "Request Latencies" "$SCH_LOG" 2>/dev/null | grep "99.0th:" | tail -1 | sed 's/.*99.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
R_P999=$(grep -A 6 "Request Latencies" "$SCH_LOG" 2>/dev/null | grep "99.9th:" | tail -1 | sed 's/.*99.9th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
echo "    Wakeup Latency:  p50=${W_P50}us, p99=${W_P99}us, p99.9=${W_P999}us"
echo "    Request Latency: p50=${R_P50}us, p99=${R_P99}us, p99.9=${R_P999}us"

# -------------------------------------------------------------
# 3. Hackbench (IPC & Context Switch Stress)
# -------------------------------------------------------------
echo "--> [3/7] Running Hackbench Context-Switch Stress..."
HACK_LOG="$RESULTS_DIR/${SCHED_NAME}_hackbench.log"
hackbench -g 20 -l 1000 > "$HACK_LOG" 2>&1 || true
H_TIME=$(grep "Time:" "$HACK_LOG" | awk '{print $2}' || echo "N/A")
echo "    Result: ${H_TIME}s"

# -------------------------------------------------------------
# 4. Redis Key-Value Store Latency
# -------------------------------------------------------------
echo "--> [4/7] Running Redis Latency Benchmark..."
pkill -9 -x redis-server 2>/dev/null || true
redis-server --daemonize yes --protected-mode no --save "" --appendonly no >/dev/null 2>&1
sleep 1

# Warmup
redis-benchmark -t set,get -n 25000 -q > /dev/null 2>&1 || true
# Record
REDIS_OUT=$(redis-benchmark -t set,get -n 50000 --csv 2>&1 || true)
pkill -9 -x redis-server 2>/dev/null || true

G_P50=$(echo "$REDIS_OUT" | grep "GET" | awk -F',' '{print $5}' | tr -d '"' || echo "N/A")
G_P95=$(echo "$REDIS_OUT" | grep "GET" | awk -F',' '{print $6}' | tr -d '"' || echo "N/A")
G_P99=$(echo "$REDIS_OUT" | grep "GET" | awk -F',' '{print $7}' | tr -d '"' || echo "N/A")

S_P50=$(echo "$REDIS_OUT" | grep "SET" | awk -F',' '{print $5}' | tr -d '"' || echo "N/A")
S_P95=$(echo "$REDIS_OUT" | grep "SET" | awk -F',' '{print $6}' | tr -d '"' || echo "N/A")
S_P99=$(echo "$REDIS_OUT" | grep "SET" | awk -F',' '{print $7}' | tr -d '"' || echo "N/A")
echo "    Redis GET Latency: p50=${G_P50}ms, p99=${G_P99}ms"
echo "    Redis SET Latency: p50=${S_P50}ms, p99=${S_P99}ms"

# -------------------------------------------------------------
# 5. Real-Time HFT Jitter (Cyclictest)
# -------------------------------------------------------------
echo "--> [5/7] Running Cyclictest Real-Time Jitter..."
HIST_FILE=$(mktemp)
# 10s cyclictest run
cyclictest --smp -p 95 -l 50000 -q --duration=10s --histogram=5000 > "$HIST_FILE" 2>&1 || true

MAX_VAL=$(grep "Max Latencies:" "$HIST_FILE" 2>/dev/null | sed 's/.*Max Latencies://' | tr ' ' '\n' | grep -v '^$' | sort -rn | head -1 | awk '{print $1+0}' | tr -d '\r\n' || echo "N/A")
if [ -z "$MAX_VAL" ]; then MAX_VAL="N/A"; fi

AVG_VAL=$(grep "Avg Latencies:" "$HIST_FILE" 2>/dev/null | sed 's/.*Avg Latencies://' | tr ' ' '\n' | grep -v '^$' | awk '{sum+=$1; count++} END {if (count>0) printf "%.1f", sum/count; else printf "N/A"}' | tr -d '\r\n' || echo "N/A")
if [ -z "$AVG_VAL" ]; then AVG_VAL="N/A"; fi

CLEAN_HIST=$(mktemp)
grep "^[0-9]" "$HIST_FILE" | awk '{sum=0; for(i=2;i<=NF;i++) sum+=$i; print $1, sum}' > "$CLEAN_HIST" 2>/dev/null || true

get_cyclictest_percentile() {
    local p=$1
    local file=$2
    local total=$(awk '{sum+=$2} END {print sum}' "$file" 2>/dev/null || echo 0)
    if [ "$total" -eq 0 ] 2>/dev/null; then echo "N/A"; return; fi
    local target=$(echo "$p * $total / 100" | bc 2>/dev/null || echo 0)
    awk -v target="$target" '{count+=$2; if (count >= target) {print $1 + 0; exit}}' "$file"
}

CYC_P50=$(get_cyclictest_percentile 50 "$CLEAN_HIST" | tr -d '\r\n')
if [ -z "$CYC_P50" ]; then CYC_P50="N/A"; fi

CYC_P90=$(get_cyclictest_percentile 90 "$CLEAN_HIST" | tr -d '\r\n')
if [ -z "$CYC_P90" ]; then CYC_P90="N/A"; fi

CYC_P99=$(get_cyclictest_percentile 99 "$CLEAN_HIST" | tr -d '\r\n')
if [ -z "$CYC_P99" ]; then CYC_P99="N/A"; fi

rm -f "$HIST_FILE" "$CLEAN_HIST"

echo "    Cyclictest Jitter: avg=${AVG_VAL}us, p50=${CYC_P50}us, p99=${CYC_P99}us, max=${MAX_VAL}us"

# -------------------------------------------------------------
# 6. Local Network Loopback Throughput (iperf3)
# -------------------------------------------------------------
echo "--> [6/7] Running Local Network Throughput (iperf3)..."
pkill -9 -x iperf3 2>/dev/null || true
iperf3 -s -D > /dev/null 2>&1 || true
sleep 1
NET_OUT=$(iperf3 -c 127.0.0.1 -t 5 --json 2>&1 || true)
GBPS=$(echo "$NET_OUT" | jq '.end.sum_received.bits_per_second / 1000000000' 2>/dev/null | awk '{printf "%.2f", $1}' || echo "N/A")
pkill -9 -x iperf3 2>/dev/null || true
echo "    Network Throughput: ${GBPS} Gbps"

# -------------------------------------------------------------
# 7. Linux Kernel Compilation
# -------------------------------------------------------------
echo "--> [7/7] Running Linux Kernel Compilation Stress..."
COMPILE_TIME="N/A"
if [ -d "/linux" ] && [ -f "/linux/.config" ]; then
    cd /linux
    make clean > /dev/null 2>&1 || true
    START_TIME=$(date +%s.%N)
    if make -j12 kernel/ > "$RESULTS_DIR/${SCHED_NAME}_kbuild.log" 2>&1; then
        END_TIME=$(date +%s.%N)
        COMPILE_TIME=$(echo "$END_TIME - $START_TIME" | bc | awk '{printf "%.2f", $0}')
        echo "    Compile Time: ${COMPILE_TIME}s"
    else
        COMPILE_TIME="FAILED"
        echo "    Compile: FAILED (check ${SCHED_NAME}_kbuild.log)"
    fi
else
    echo "    Compile: Skipped (no /linux tree mounted)"
fi

# -------------------------------------------------------------
# Record to CSV and JSONL
# -------------------------------------------------------------
# CSV Format:
# scheduler,sysbench_eps,hackbench_time,compile_time,iperf_gbps,w_p50,w_p90,w_p99,w_p999,r_p50,r_p90,r_p99,r_p999,redis_g_p50,redis_g_p99,redis_s_p50,redis_s_p99,cyc_avg,cyc_p50,cyc_p99,cyc_max
echo "$SCHED_NAME,$EPS,$H_TIME,$COMPILE_TIME,$GBPS,$W_P50,$W_P90,$W_P99,$W_P999,$R_P50,$R_P90,$R_P99,$R_P999,$G_P50,$G_P99,$S_P50,$S_P99,$AVG_VAL,$CYC_P50,$CYC_P99,$MAX_VAL" >> "$RESULTS_DIR/results_complete.csv"

echo "=== Finished Complete Benchmark Pass for $SCHED_NAME ==="
