#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Rigorous Bare-Metal Benchmark Runner for Linux Schedulers
# Evaluates default_cfs_eevdf, scx_optima, and scx_rlfifo across genuine workloads:
# 1. perf bench sched messaging
# 2. perf bench sched pipe (latency & throughput)
# 3. sysbench cpu (throughput EPS & thread fairness stddev)
# 4. sysbench memory (throughput ops/s & transfer MiB/s)
# 5. hackbench (process & thread)
# 6. schbench (wakeup & request latencies)
# 7. Linux kernel subsystem compilation (make -j24 kernel/)
# 8. PMU hardware counters (instructions, cycles, cache-misses)

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (sudo)"
   exit 1
fi

BASE_DIR="/home/rishi/Desktop/OS/project/scx"
RESULTS_DIR="$BASE_DIR/sch_tests/baremetal_results"
TARGET_DIR="$BASE_DIR/target/release"
LINUX_DIR="$BASE_DIR/sch_tests/linux"
REPORT_FILE="$RESULTS_DIR/BAREMETAL_BENCHMARK_REPORT.md"
CSV_FILE="$RESULTS_DIR/baremetal_results.csv"
mkdir -p "$RESULTS_DIR"

SCHEDULERS=("default_cfs_eevdf" "scx_optima" "scx_rdtai" "scx_rusty" "scx_rustland" "scx_rlfifo" "scx_lavd" "scx_bpfland" "scx_flash" "scx_beerland")
if [ $# -gt 0 ]; then
    SCHEDULERS=("$@")
fi

if [ $# -eq 0 ] || [ ! -f "$CSV_FILE" ]; then
    echo "scheduler,sysbench_cpu_eps,sysbench_fairness_stddev,sysbench_mem_ops,sysbench_mem_bw,perf_msg_time,perf_pipe_lat_us,perf_pipe_ops,hackbench_proc_s,hackbench_thread_s,schbench_w_p50,schbench_w_p99,schbench_r_p50,schbench_r_p99,compile_time_s,cache_miss_pct,ipc" > "$CSV_FILE"
fi

# Disable hardware isolation so baremetal benchmarks access ALL 24 CPU cores
"$BASE_DIR/scripts/isolate_benchmark_env.sh" disable
echo "Active Bare-Metal Logical CPUs: $(cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || echo '0-23')"

# Clean up any leftover sched_ext scheduler
if [ "$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'disabled')" = "enabled" ]; then
    pkill -9 -f "$TARGET_DIR" 2>/dev/null || true
    sleep 2
fi

for SCHED in "${SCHEDULERS[@]}"; do
    echo ""
    echo "=========================================================="
    echo ">>> BARE-METAL EVALUATION: $SCHED <<<"
    echo "=========================================================="

    SCHED_PID=""
    if [ "$SCHED" != "default_cfs_eevdf" ]; then
        echo "  [Start] Launching scheduler: $SCHED..."
        "$TARGET_DIR/$SCHED" > "$RESULTS_DIR/${SCHED}_host.log" 2>&1 &
        SCHED_PID=$!
        for i in {1..8}; do
            CURRENT_STATE=$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo "disabled")
            if [ "$CURRENT_STATE" = "enabled" ] || ([ "$CURRENT_STATE" != "disabled" ] && [ "$CURRENT_STATE" != "None" ]); then
                break
            fi
            sleep 1
        done
        echo "  [Start] Active sched_ext state: $CURRENT_STATE"
        if [ "$CURRENT_STATE" = "disabled" ] || [ "$CURRENT_STATE" = "None" ]; then
            echo "  [ERROR] $SCHED failed to load! Log:"
            cat "$RESULTS_DIR/${SCHED}_host.log" | tail -n 10
            continue
        fi
    else
        echo "  [Start] Running baseline Linux CFS/EEVDF (sched_ext disabled)"
    fi

    # 1. Sysbench CPU Throughput & Fairness
    echo "  [1/8] Running Sysbench CPU (24 threads, 10s)..."
    SYS_CPU_LOG="$RESULTS_DIR/${SCHED}_sysbench_cpu.log"
    sysbench cpu --cpu-max-prime=30000 --threads=24 --time=10 run > "$SYS_CPU_LOG" 2>&1
    CPU_EPS=$(grep "events per second:" "$SYS_CPU_LOG" | awk '{print $4}' || echo "N/A")
    CPU_STDDEV=$(grep "events (avg/stddev):" "$SYS_CPU_LOG" | awk -F'/' '{print $3}' || echo "N/A")
    echo "        EPS: $CPU_EPS, Fairness stddev: $CPU_STDDEV"

    # 2. Sysbench Memory Throughput
    echo "  [2/8] Running Sysbench Memory (24 threads, 10s)..."
    SYS_MEM_LOG="$RESULTS_DIR/${SCHED}_sysbench_mem.log"
    sysbench memory --threads=24 --time=10 run > "$SYS_MEM_LOG" 2>&1
    MEM_OPS=$(grep "Total operations:" "$SYS_MEM_LOG" | sed 's/.*(\([0-9.]*\) per second).*/\1/' || echo "N/A")
    MEM_BW=$(grep "transferred (" "$SYS_MEM_LOG" | sed 's/.*(\([0-9.]*\) MiB\/sec).*/\1/' || echo "N/A")
    echo "        Ops/s: $MEM_OPS, Bandwidth: ${MEM_BW} MiB/s"

    # 3. Perf bench sched messaging
    echo "  [3/8] Running perf bench sched messaging (400 threads)..."
    PERF_MSG_LOG="$RESULTS_DIR/${SCHED}_perf_msg.log"
    perf bench sched messaging -p -g 10 -t 20 > "$PERF_MSG_LOG" 2>&1
    MSG_TIME=$(grep "Total time:" "$PERF_MSG_LOG" | awk '{print $3}' || echo "N/A")
    echo "        Message Passing Time: ${MSG_TIME}s"

    # 4. Perf bench sched pipe
    echo "  [4/8] Running perf bench sched pipe (50,000 ops)..."
    PERF_PIPE_LOG="$RESULTS_DIR/${SCHED}_perf_pipe.log"
    perf bench sched pipe -l 50000 > "$PERF_PIPE_LOG" 2>&1
    PIPE_LAT=$(grep "usecs/op" "$PERF_PIPE_LOG" | awk '{print $1}' || echo "N/A")
    PIPE_OPS=$(grep "ops/sec" "$PERF_PIPE_LOG" | awk '{print $1}' || echo "N/A")
    echo "        Pipe Latency: ${PIPE_LAT} us/op, Throughput: ${PIPE_OPS} ops/s"

    # 5. Hackbench (Process & Thread)
    echo "  [5/8] Running Hackbench (Process & Thread)..."
    HACK_P_LOG="$RESULTS_DIR/${SCHED}_hackbench_p.log"
    hackbench -p -g 10 -l 1000 > "$HACK_P_LOG" 2>&1
    H_P_TIME=$(grep "Time:" "$HACK_P_LOG" | awk '{print $2}' || echo "N/A")

    HACK_T_LOG="$RESULTS_DIR/${SCHED}_hackbench_t.log"
    hackbench -T -g 10 -l 1000 > "$HACK_T_LOG" 2>&1
    H_T_TIME=$(grep "Time:" "$HACK_T_LOG" | awk '{print $2}' || echo "N/A")
    echo "        Hackbench Process: ${H_P_TIME}s, Thread: ${H_T_TIME}s"

    # 6. Schbench Wakeup & Request Latencies
    echo "  [6/8] Running Schbench (-m 8 -t 4 -r 10)..."
    SCH_LOG="$RESULTS_DIR/${SCHED}_schbench.log"
    "$BASE_DIR/sch_tests/schbench/schbench" -m 8 -t 4 -r 10 > "$SCH_LOG" 2>&1 || true
    W_P50=$(grep -A 6 "Wakeup Latencies" "$SCH_LOG" 2>/dev/null | grep "50.0th:" | tail -1 | sed 's/.*50.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
    W_P99=$(grep -A 6 "Wakeup Latencies" "$SCH_LOG" 2>/dev/null | grep "99.0th:" | tail -1 | sed 's/.*99.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
    R_P50=$(grep -A 6 "Request Latencies" "$SCH_LOG" 2>/dev/null | grep "50.0th:" | tail -1 | sed 's/.*50.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
    R_P99=$(grep -A 6 "Request Latencies" "$SCH_LOG" 2>/dev/null | grep "99.0th:" | tail -1 | sed 's/.*99.0th:[[:space:]]*//' | awk '{print $1}' || echo "N/A")
    echo "        Wakeup: P50=${W_P50}us P99=${W_P99}us | Request: P50=${R_P50}us P99=${R_P99}us"

    # 7. Linux Subsystem Compile (kernel/)
    echo "  [7/8] Running Linux Subsystem Compilation (make -j24 kernel/)..."
    cd "$LINUX_DIR"
    make clean > /dev/null 2>&1 || true
    START_TIME=$(date +%s.%N)
    if make -j24 kernel/ > "$RESULTS_DIR/${SCHED}_kbuild.log" 2>&1; then
        END_TIME=$(date +%s.%N)
        COMPILE_TIME=$(echo "$END_TIME - $START_TIME" | bc | awk '{printf "%.2f", $0}')
        echo "        Compile Time: ${COMPILE_TIME}s"
    else
        COMPILE_TIME="FAILED"
        echo "        Compile: FAILED"
    fi
    cd "$BASE_DIR"

    # 8. PMU Hardware Performance Counters
    echo "  [8/8] Measuring PMU hardware metrics (perf stat)..."
    PERF_STAT_LOG="$RESULTS_DIR/${SCHED}_perf_stat.log"
    perf stat -e instructions,cycles,cache-misses,cache-references -- \
        sysbench cpu --cpu-max-prime=25000 --threads=24 --time=5 run > "$PERF_STAT_LOG" 2>&1
    INSTR=$(grep "instructions" "$PERF_STAT_LOG" | awk '{print $1}' | tr -d ',' || echo "0")
    CYCLES=$(grep "cycles" "$PERF_STAT_LOG" | awk '{print $1}' | tr -d ',' || echo "0")
    MISSES=$(grep "cache-misses" "$PERF_STAT_LOG" | awk '{print $1}' | tr -d ',' || echo "0")
    REFS=$(grep "cache-references" "$PERF_STAT_LOG" | awk '{print $1}' | tr -d ',' || echo "0")
    IPC=$(awk -v i="$INSTR" -v c="$CYCLES" 'BEGIN {if(c>0) printf "%.2f", i/c; else print "N/A"}')
    CACHE_PCT=$(awk -v m="$MISSES" -v r="$REFS" 'BEGIN {if(r>0) printf "%.2f", (m*100)/r; else print "N/A"}')
    echo "        IPC: $IPC, Cache Misses: ${CACHE_PCT}%"

    # Stop scheduler
    if [ "$SCHED" != "default_cfs_eevdf" ]; then
        echo "  [Stop] Stopping scheduler $SCHED..."
        kill -SIGINT "$SCHED_PID" 2>/dev/null || true
        for i in {1..6}; do
            CURRENT_STATE=$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo "disabled")
            if [ "$CURRENT_STATE" = "disabled" ] || [ "$CURRENT_STATE" = "None" ]; then
                break
            fi
            sleep 1
        done
        if [ "$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'disabled')" = "enabled" ]; then
            pkill -9 -f "$TARGET_DIR/$SCHED" 2>/dev/null || true
            sleep 2
        fi
        echo "  [Stop] Sched_ext state: $(cat /sys/kernel/sched_ext/state 2>/dev/null || echo 'disabled')"
    fi

    # Append to CSV
    sed -i "/^$SCHED,/d" "$CSV_FILE"
    echo "$SCHED,$CPU_EPS,$CPU_STDDEV,$MEM_OPS,$MEM_BW,$MSG_TIME,$PIPE_LAT,$PIPE_OPS,$H_P_TIME,$H_T_TIME,$W_P50,$W_P99,$R_P50,$R_P99,$COMPILE_TIME,$CACHE_PCT,$IPC" >> "$CSV_FILE"
    sleep 2
done

# Reset to disabled
if [ "$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'disabled')" = "enabled" ]; then
    pkill -9 -f "$TARGET_DIR" 2>/dev/null || true
    sleep 2
fi

# Generate Markdown Report
cat << 'EOF' > "$REPORT_FILE"
# Genuine Bare-Metal Linux Scheduler Benchmark Report
**System:** AMD Ryzen AI 9 HX 370 (12 Physical Cores / 24 Logical Threads: 4 Zen 5 P-cores @ 5.16 GHz + 8 Zen 5c E-cores @ 3.29 GHz)  
**Kernel:** Linux 7.0.0-38-generic with sched_ext support  
**Execution Environment:** Unrestricted bare-metal host (full 24 threads, all memory, direct hardware PMU access)  
**Measurement Integrity:** 100% genuine data recorded directly from workload execution stdout/stderr logs. Zero synthetic scaling or extrapolation.

---

## 1. IPC, Context-Switching & Real-Time Responsiveness
*Lower is better for latencies and completion times; higher is better for pipe throughput.*

| Scheduler | Hackbench Process (s) | Hackbench Thread (s) | Perf Msg Time (s) | Perf Pipe Latency (us) | Perf Pipe Throughput (ops/s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s c_eps c_std m_ops m_bw p_msg p_lat p_ops h_p h_t w_50 w_99 r_50 r_99 k_time c_miss ipc_v; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | ${h_p}s | ${h_t}s | ${p_msg}s | ${p_lat} us | ${p_ops} ops/s |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

cat << 'EOF' >> "$REPORT_FILE"

---

## 2. Scheduling Latencies (Schbench)
*Wakeup and Request latencies in microseconds (Lower is better).*

| Scheduler | Wakeup P50 (us) | Wakeup P99 (us) | Request P50 (us) | Request P99 (us) |
| :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s c_eps c_std m_ops m_bw p_msg p_lat p_ops h_p h_t w_50 w_99 r_50 r_99 k_time c_miss ipc_v; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | ${w_50} us | ${w_99} us | ${r_50} us | ${r_99} us |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

cat << 'EOF' >> "$REPORT_FILE"

---

## 3. Computational Throughput, Memory Bus & Fairness
*Higher EPS, Memory ops/s, and IPC is better. Lower fairness variance and cache misses is better.*

| Scheduler | Sysbench CPU (Events/s) | Fairness Stddev (Variance) | Sysbench Memory (Ops/s) | Memory Bandwidth (MiB/s) | Kernel Compile (make -j24 kernel/) (s) | Cache Misses (%) | IPC |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s c_eps c_std m_ops m_bw p_msg p_lat p_ops h_p h_t w_50 w_99 r_50 r_99 k_time c_miss ipc_v; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | $c_eps | $c_std | $m_ops | ${m_bw} MiB/s | ${k_time}s | ${c_miss}% | $ipc_v |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

echo ""
echo "Master Bare-Metal Benchmark Report generated at: $REPORT_FILE"
cat "$REPORT_FILE"
