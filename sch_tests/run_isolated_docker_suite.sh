#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Complete Unified Isolated Docker Benchmark Suite
# Includes ALL tests from run_benchmarks.sh and run_benchmark_lat.sh:
#   1. Sysbench CPU Events/s
#   2. Schbench Wakeup Latency (P50, P90, P99, P99.9)
#   3. Schbench Request Latency (P50, P90, P99, P99.9)
#   4. Hackbench Context Switching Time
#   5. Redis Key-Value Latency (GET & SET P50, P99)
#   6. Cyclictest Real-Time HFT Jitter (Avg, P50, P99, Max)
#   7. iperf3 Local Network Throughput (Gbps)
#   8. Linux Kernel Compilation Time (make -j12 kernel/)

set -euo pipefail

BASE_DIR="/home/rishi/Desktop/OS/project/scx"
RESULTS_DIR="$BASE_DIR/benchmarks/docker"
TARGET_DIR="$BASE_DIR/target/release"
LINUX_DIR="$BASE_DIR/sch_tests/linux"
REPORT_FILE="$RESULTS_DIR/COMPLETE_BENCHMARK_REPORT.md"
CSV_FILE="$RESULTS_DIR/results_complete.csv"

mkdir -p "$RESULTS_DIR"

# Schedulers to evaluate
if [ $# -gt 0 ]; then
    SCHEDULERS=("$@")
    if [ -f "$CSV_FILE" ]; then
        for s in "${SCHEDULERS[@]}"; do
            sed -i "/^$s,/d" "$CSV_FILE"
        done
    else
        echo "scheduler,sysbench_eps,hackbench_time,compile_time,iperf_gbps,w_p50,w_p90,w_p99,w_p999,r_p50,r_p90,r_p99,r_p999,redis_g_p50,redis_g_p99,redis_s_p50,redis_s_p99,cyc_avg,cyc_p50,cyc_p99,cyc_max" > "$CSV_FILE"
    fi
else
    SCHEDULERS=("default_cfs_eevdf" "scx_optima" "scx_rdtai" "scx_rusty" "scx_rustland" "scx_rlfifo" "scx_lavd" "scx_bpfland" "scx_flash" "scx_beerland")
    # CSV Header
    echo "scheduler,sysbench_eps,hackbench_time,compile_time,iperf_gbps,w_p50,w_p90,w_p99,w_p999,r_p50,r_p90,r_p99,r_p999,redis_g_p50,redis_g_p99,redis_s_p50,redis_s_p99,cyc_avg,cyc_p50,cyc_p99,cyc_max" > "$CSV_FILE"
fi

# Ensure hardware and cache isolation are enabled
sudo "$BASE_DIR/scripts/isolate_benchmark_env.sh" enable

# Clean up any leftover sched_ext scheduler before beginning
if [ "$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'disabled')" = "enabled" ]; then
    sudo pkill -9 -f "$TARGET_DIR" 2>/dev/null || true
    sleep 2
fi

for SCHED in "${SCHEDULERS[@]}"; do
    echo ""
    echo "=========================================================="
    echo ">>> EVALUATING SCHEDULER: $SCHED <<<"
    echo "=========================================================="

    if [ "$SCHED" != "default_cfs_eevdf" ]; then
        echo "  [Host] Launching scheduler: $SCHED..."
        sudo rm -f "/tmp/${SCHED}_host.log"
        sudo bash -c "$TARGET_DIR/$SCHED > /tmp/${SCHED}_host.log 2>&1" &
        SCHED_PID=$!
        for i in {1..8}; do
            CURRENT_OPS=$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo "disabled")
            if [ "$CURRENT_OPS" = "enabled" ] || ([ "$CURRENT_OPS" != "disabled" ] && [ "$CURRENT_OPS" != "None" ]); then
                break
            fi
            sleep 1
        done
        echo "  [Host] Active sched_ext ops: $CURRENT_OPS"
        if [ "$CURRENT_OPS" = "disabled" ] || [ "$CURRENT_OPS" = "None" ]; then
            echo "  [ERROR] $SCHED failed to load! Log:"
            cat "/tmp/${SCHED}_host.log" | tail -n 10
            continue
        fi
    else
        echo "  [Host] Running on baseline Linux kernel CFS/EEVDF (sched_ext disabled)"
    fi

    echo "  [Docker] Spawning isolated 4GB container with full benchmark suite..."
    CID=$(docker create \
        --cgroup-parent benchmark.slice \
        --cap-add=sys_nice \
        -m 4g --memory-swap 4g \
        -v "$RESULTS_DIR:/results" \
        -v "$LINUX_DIR:/linux" \
        -v "$BASE_DIR/sch_tests/docker_bench_inner.sh:/usr/local/bin/run_bench:ro" \
        scx-bench-runner:latest "$SCHED")

    docker start "$CID" >/dev/null
    CPID=$(docker inspect -f '{{.State.Pid}}' "$CID")
    if [ -n "$CPID" ] && [ "$CPID" -gt 0 ]; then
        sudo bash -c "echo $CPID > /sys/fs/resctrl/benchmark/tasks" 2>/dev/null || true
    fi

    # Stream container logs in real time
    docker attach "$CID" || true
    docker wait "$CID" >/dev/null 2>&1 || true
    docker rm -f "$CID" >/dev/null 2>&1 || true

    if [ "$SCHED" != "default_cfs_eevdf" ]; then
        echo "  [Host] Stopping scheduler: $SCHED..."
        sudo pkill -SIGINT -f "$TARGET_DIR/$SCHED" 2>/dev/null || true
        for i in {1..6}; do
            CURRENT_STATE=$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo "disabled")
            if [ "$CURRENT_STATE" = "disabled" ] || [ "$CURRENT_STATE" = "None" ]; then
                break
            fi
            sleep 1
        done
        if [ "$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'disabled')" = "enabled" ]; then
            sudo pkill -9 -f "$TARGET_DIR/$SCHED" 2>/dev/null || true
            sleep 2
        fi
        echo "  [Host] Scheduler stopped. Sched_ext state: $(cat /sys/kernel/sched_ext/state 2>/dev/null || echo 'disabled')"
    fi
    sleep 2
done

# Ensure sched_ext is completely reset to disabled
if [ "$(cat /sys/kernel/sched_ext/state 2>/dev/null || cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'disabled')" = "enabled" ]; then
    sudo pkill -9 -f "$TARGET_DIR" 2>/dev/null || true
    sleep 2
fi
echo "Active sched_ext state at suite completion: $(cat /sys/kernel/sched_ext/state 2>/dev/null || echo 'disabled')"

echo ""
echo "=========================================================="
echo " Generating Master Benchmark Report..."
echo "=========================================================="

cat << 'EOF' > "$REPORT_FILE"
# Full Unified Scheduler Benchmark Report (Isolated Environment)
**Hardware:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 Cores / 24 Threads)
- **Isolation Scope:** 2 Zen 5 P-cores (Cores 2, 3) + 4 Zen 5c E-cores (Cores 4-7) = 12 Logical CPUs (`2-7, 14-19`)
- **CPU Clock Frequencies:** Completely Uncapped (P-cores 5.16 GHz, E-cores 3.29 GHz)
- **Memory Ceiling:** Strict 4.0 GB RAM constraint
- **L3 Cache Partition:** AMD CAT `resctrl` allocated upper 8 ways (`ff00`)
- **Scheduler Partition:** Linux `cgroup v2` isolated domain (kernel `sched_domain` rebuilt; 0 host interference)
- **Container Environment:** `debian:bookworm-slim` minimal Linux container

---

## 1. Throughput & Heavy Stress Workloads
*Higher is better for Sysbench & iperf3. Lower is better for Hackbench & Kernel Compile.*

| Scheduler | Sysbench CPU (Events/s) | Hackbench (s) | Kernel Compile (s) | Network iperf3 (Gbps) |
| :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s eps htime ktime gbps wp50 wp90 wp99 wp999 rp50 rp90 rp99 rp999 rg50 rg99 rs50 rs99 cavg cp50 cp99 cmax; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | $eps | ${htime}s | ${ktime}s | ${gbps} Gbps |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

cat << 'EOF' >> "$REPORT_FILE"

---

## 2. Wakeup Latencies (Schbench)
*Time from thread wake to execution (Microseconds - Lower is better)*

| Scheduler | 50.0th (Median) | 90.0th | 99.0th | 99.9th (Tail) |
| :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s eps htime ktime gbps wp50 wp90 wp99 wp999 rp50 rp90 rp99 rp999 rg50 rg99 rs50 rs99 cavg cp50 cp99 cmax; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | ${wp50} us | ${wp90} us | ${wp99} us | ${wp999} us |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

cat << 'EOF' >> "$REPORT_FILE"

---

## 3. Request Latencies (Schbench)
*End-to-end request processing time (Microseconds - Lower is better)*

| Scheduler | 50.0th (Median) | 90.0th | 99.0th | 99.9th (Tail) |
| :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s eps htime ktime gbps wp50 wp90 wp99 wp999 rp50 rp90 rp99 rp999 rg50 rg99 rs50 rs99 cavg cp50 cp99 cmax; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | ${rp50} us | ${rp90} us | ${rp99} us | ${rp999} us |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

cat << 'EOF' >> "$REPORT_FILE"

---

## 4. Key-Value Store Latency (Redis)
*Database operation latency (Milliseconds - Lower is better)*

| Scheduler | GET P50 (ms) | GET P99 (ms) | SET P50 (ms) | SET P99 (ms) |
| :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s eps htime ktime gbps wp50 wp90 wp99 wp999 rp50 rp90 rp99 rp999 rg50 rg99 rs50 rs99 cavg cp50 cp99 cmax; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | ${rg50} ms | ${rg99} ms | ${rs50} ms | ${rs99} ms |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

cat << 'EOF' >> "$REPORT_FILE"

---

## 5. Real-Time HFT Jitter (Cyclictest)
*Real-time wakeup jitter under high scheduling stress (Microseconds - Lower is better)*

| Scheduler | Avg (us) | P50 (us) | P99 (us) | Max (us) |
| :--- | :--- | :--- | :--- | :--- |
EOF

while IFS=',' read -r s eps htime ktime gbps wp50 wp90 wp99 wp999 rp50 rp90 rp99 rp999 rg50 rg99 rs50 rs99 cavg cp50 cp99 cmax; do
    if [ "$s" != "scheduler" ]; then
        echo "| **$s** | ${cavg} us | ${cp50} us | ${cp99} us | ${cmax} us |" >> "$REPORT_FILE"
    fi
done < "$CSV_FILE"

echo "Report generated at: $REPORT_FILE"
cat "$REPORT_FILE"
