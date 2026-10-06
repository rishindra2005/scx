#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# ==============================================================================
# Master Host-Side Production Benchmark Suite Runner
# Validates scx_optima across 5 Production Architectures:
#   1. API Gateway & Microservices (NGINX + uvloop fan-out)
#   2. In-Memory Cache (Production Redis Pipelined)
#   3. Pro-Audio DSP Loop (48kHz / 64-sample 1.33ms Buffer Deadlines)
#   4. HFT Order Matching Engine (LMAX Disruptor Lock-Free LOB)
#   5. 120 FPS Interactive Game Server (Spatial Physics & State Sync)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PROD_DIR="$BASE_DIR/benchmarks/production_apps"
RESULTS_DIR="$SCRIPT_DIR/results"
TARGET_DIR="$BASE_DIR/target/release"
REPORT_FILE="$PROD_DIR/PRODUCTION_BENCHMARK_REPORT.md"
CSV_FILE="$RESULTS_DIR/production_results.csv"

mkdir -p "$RESULTS_DIR"

# Configurable options
QUICK_MODE=false
SCHEDULERS=("default_cfs_eevdf" "scx_optima" "scx_rlfifo" "scx_rdtai" "scx_rusty" "scx_rustland")

print_usage() {
    echo "Usage: $0 [options]"
    echo "  --quick               Run shortened iteration for rapid verification"
    echo "  --schedulers 's1 s2'  Specify list of schedulers (default: '${SCHEDULERS[*]}')"
    echo "  --help                Show this help"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)
            QUICK_MODE=true
            shift
            ;;
        --schedulers)
            read -r -a SCHEDULERS <<< "$2"
            shift 2
            ;;
        --help|-h)
            print_usage
            ;;
        *)
            echo "Unknown argument: $1"
            print_usage
            ;;
    esac
done

if [ "$QUICK_MODE" = true ]; then
    AUDIO_FRAMES=5000
    HFT_ORDERS=50000
    GAME_DURATION=10
    GATEWAY_REQS=1000
    REDIS_REQS=50000
else
    AUDIO_FRAMES=30000
    HFT_ORDERS=250000
    GAME_DURATION=25
    GATEWAY_REQS=2500
    REDIS_REQS=100000
fi

echo "================================================================================"
echo "    Master Host-Side Production Benchmark Harness (sched_ext Evaluation)       "
echo "================================================================================"
echo " Workload Suite: 5 Production Applications"
echo " Topology:       AMD Ryzen AI 9 HX 370 (12 Logical Isolated Cores: CPUs 2-7, 14-19)"
echo " Memory Slice:   4.0 GB RAM Ceiling (benchmark.slice)"
echo " L3 Cache Mask:  AMD CAT Exclusive ff00 (/sys/fs/resctrl/benchmark)"
echo " Evaluated:      ${SCHEDULERS[*]}"
echo " Quick Mode:     $QUICK_MODE"
echo " Results Dir:    $RESULTS_DIR"
echo "================================================================================"
echo ""

# 1. Enable Hardware, Cache & Kernel Isolation
echo "[*] Activating hardware, cache, and kernel isolation..."
sudo "$BASE_DIR/scripts/isolate_benchmark_env.sh" enable

# 2. Build Any Missing Production App Docker Images
echo "[*] Verifying container images..."
build_if_missing() {
    local tag="$1"
    local dir="$2"
    if ! docker image inspect "$tag" >/dev/null 2>&1; then
        echo "  [Build] Building $tag from $dir..."
        docker build -t "$tag" "$dir"
    fi
}

build_if_missing "scx-api-gateway:latest"          "$PROD_DIR/01_api_gateway"
build_if_missing "scx-redis-cache:latest"          "$PROD_DIR/02_redis_cache"
build_if_missing "scx-bench-realtime-audio:latest" "$PROD_DIR/03_realtime_audio"
build_if_missing "scx-bench-hft-matching:latest"  "$PROD_DIR/04_hft_matching"
build_if_missing "scx-bench-game-server:latest"   "$PROD_DIR/05_game_tick_server"

# Helper function to attach docker container PID to AMD CAT L3 cache isolation
attach_to_resctrl() {
    local cid="$1"
    local cpid
    cpid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null || echo 0)
    if [ -n "$cpid" ] && [ "$cpid" -gt 0 ]; then
        sudo bash -c "echo $cpid > /sys/fs/resctrl/benchmark/tasks 2>/dev/null" || true
    fi
}

# 3. Main Benchmark Execution Loop
for SCHED in "${SCHEDULERS[@]}"; do
    echo ""
    echo "################################################################################"
    echo ">>> EVALUATING SCHEDULER: $SCHED <<<"
    echo "################################################################################"

    # Start or verify scheduler state
    if [ "$SCHED" != "default_cfs_eevdf" ]; then
        echo "  [Host] Launching scheduler: $SCHED..."
        sudo "$TARGET_DIR/$SCHED" > "$RESULTS_DIR/${SCHED}_host.log" 2>&1 &
        SCHED_PID=$!
        for i in {1..8}; do
            CURRENT_OPS=$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo "None")
            if [ "$CURRENT_OPS" != "None" ]; then
                break
            fi
            sleep 1
        done
        echo "  [Host] Active sched_ext ops: $CURRENT_OPS"
        if [ "$CURRENT_OPS" = "None" ]; then
            echo "  [ERROR] $SCHED failed to load! Log:"
            cat "$RESULTS_DIR/${SCHED}_host.log" | tail -n 10
            continue
        fi
    else
        echo "  [Host] Running on baseline Linux kernel CFS/EEVDF (sched_ext disabled)"
        if [ "$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'None')" != "None" ]; then
            sudo pkill -SIGINT -f "$TARGET_DIR" 2>/dev/null || true
            sleep 2
        fi
    fi

    # --------------------------------------------------------------------------
    # App 1: Cloud-Native API Gateway & Microservices
    # --------------------------------------------------------------------------
    echo ""
    echo "--- [1/5] Running App 1: Cloud-Native API Gateway (NGINX + uvloop) ---"
    docker rm -f "bench_gw_${SCHED}" >/dev/null 2>&1 || true
    GW_CID=$(docker create \
        --cgroup-parent benchmark.slice \
        -m 4g --memory-swap 4g \
        -p 18080:80 \
        --name "bench_gw_${SCHED}" \
        scx-api-gateway:latest)
    docker start "$GW_CID" >/dev/null
    attach_to_resctrl "$GW_CID"

    echo "    Waiting for API Gateway readiness..."
    for i in {1..20}; do
        if curl -s -f http://127.0.0.1:18080/healthz >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done

    echo "    Executing API Gateway load generator inside isolated container..."
    GW_LOG="$RESULTS_DIR/${SCHED}_gateway.log"
    docker exec "$GW_CID" python3 /app/benchmark_client.py \
        --url "http://127.0.0.1/api/v1/dashboard" \
        --concurrency 50 \
        --requests "$GATEWAY_REQS" | tee "$GW_LOG"

    # Parse JSON metrics from log
    python3 -c "
import re, json
log = open('$GW_LOG').read()
tput = float(re.search(r'Throughput:\s+([\d\.]+)\s+req/s', log).group(1)) if re.search(r'Throughput:\s+([\d\.]+)\s+req/s', log) else 0.0
p50 = float(re.search(r'P50\s+\(Median\):\s+([\d\.]+)\s+ms', log).group(1)) if re.search(r'P50\s+\(Median\):\s+([\d\.]+)\s+ms', log) else 0.0
p95 = float(re.search(r'P95:\s+([\d\.]+)\s+ms', log).group(1)) if re.search(r'P95:\s+([\d\.]+)\s+ms', log) else 0.0
p99 = float(re.search(r'P99:\s+([\d\.]+)\s+ms', log).group(1)) if re.search(r'P99:\s+([\d\.]+)\s+ms', log) else 0.0
errors = int(re.search(r'Failed Reqs:\s+(\d+)', log).group(1)) if re.search(r'Failed Reqs:\s+(\d+)', log) else 0
data = {'throughput_rps': tput, 'p50_ms': p50, 'p95_ms': p95, 'p99_ms': p99, 'errors': errors}
json.dump(data, open('$RESULTS_DIR/${SCHED}_gateway.json', 'w'), indent=2)
" || true

    docker stop "$GW_CID" >/dev/null 2>&1 || true
    docker rm -f "$GW_CID" >/dev/null 2>&1 || true

    # --------------------------------------------------------------------------
    # App 2: In-Memory Redis Cache Tier
    # --------------------------------------------------------------------------
    echo ""
    echo "--- [2/5] Running App 2: In-Memory Redis Cache Tier (Pipelined) ---"
    docker rm -f "bench_rd_${SCHED}" >/dev/null 2>&1 || true
    RD_CID=$(docker create \
        --cgroup-parent benchmark.slice \
        -m 4g --memory-swap 4g \
        -p 16379:6379 \
        --name "bench_rd_${SCHED}" \
        scx-redis-cache:latest)
    docker start "$RD_CID" >/dev/null
    attach_to_resctrl "$RD_CID"

    echo "    Waiting for Redis server readiness..."
    for i in {1..20}; do
        if redis-cli -p 16379 ping >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done

    echo "    Running redis-benchmark latency & throughput test..."
    RD_CSV="$RESULTS_DIR/${SCHED}_redis_raw.csv"
    redis-benchmark -h 127.0.0.1 -p 16379 -t get,set -n "$REDIS_REQS" -c 50 --csv > "$RD_CSV" 2>/dev/null || true

    python3 -c "
import csv, json
data = {'get_rps': 0, 'get_p50_us': 0, 'get_p95_us': 0, 'get_p99_us': 0, 'set_p99_us': 0}
try:
    with open('$RD_CSV') as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row: continue
            cmd = row[0].replace('\"', '').strip().upper()
            rps = float(row[1].replace('\"', ''))
            p50 = float(row[4].replace('\"', '')) * 1000.0
            p95 = float(row[5].replace('\"', '')) * 1000.0
            p99 = float(row[6].replace('\"', '')) * 1000.0
            if cmd == 'GET':
                data['get_rps'] = rps
                data['get_p50_us'] = p50
                data['get_p95_us'] = p95
                data['get_p99_us'] = p99
            elif cmd == 'SET':
                data['set_p99_us'] = p99
except Exception as e:
    pass
json.dump(data, open('$RESULTS_DIR/${SCHED}_redis.json', 'w'), indent=2)
" || true

    docker stop "$RD_CID" >/dev/null 2>&1 || true
    docker rm -f "$RD_CID" >/dev/null 2>&1 || true

    # --------------------------------------------------------------------------
    # App 3: Real-Time Pro-Audio DSP Loop (48kHz / 64-sample 1.33ms)
    # --------------------------------------------------------------------------
    echo ""
    echo "--- [3/5] Running App 3: Real-Time Pro-Audio DSP Engine (48kHz/64-smp) ---"
    AUD_CID=$(docker create \
        --cgroup-parent benchmark.slice \
        --cap-add=sys_nice \
        -m 4g --memory-swap 4g \
        -v "$RESULTS_DIR:/results" \
        scx-bench-realtime-audio:latest \
        -n "$AUDIO_FRAMES" -j "/results/${SCHED}_audio_dsp.json")
    docker start "$AUD_CID" >/dev/null
    attach_to_resctrl "$AUD_CID"
    docker wait "$AUD_CID" >/dev/null 2>&1 || true
    docker logs "$AUD_CID" || true
    docker rm -f "$AUD_CID" >/dev/null 2>&1 || true

    # --------------------------------------------------------------------------
    # App 4: High-Frequency Trading (HFT) Matching Engine
    # --------------------------------------------------------------------------
    echo ""
    echo "--- [4/5] Running App 4: Ultra-Low Latency HFT Matching Engine ---"
    HFT_CID=$(docker create \
        --cgroup-parent benchmark.slice \
        -m 4g --memory-swap 4g \
        -v "$RESULTS_DIR:/results" \
        scx-bench-hft-matching:latest \
        -n "$HFT_ORDERS" -j "/results/${SCHED}_hft.json")
    docker start "$HFT_CID" >/dev/null
    attach_to_resctrl "$HFT_CID"
    docker wait "$HFT_CID" >/dev/null 2>&1 || true
    docker logs "$HFT_CID" || true
    docker rm -f "$HFT_CID" >/dev/null 2>&1 || true

    # --------------------------------------------------------------------------
    # App 5: 120 FPS Interactive Game Server (500 Connected Clients)
    # --------------------------------------------------------------------------
    echo ""
    echo "--- [5/5] Running App 5: 120 FPS Interactive Game Server ---"
    GM_CID=$(docker create \
        --cgroup-parent benchmark.slice \
        -m 4g --memory-swap 4g \
        -v "$RESULTS_DIR:/results" \
        scx-bench-game-server:latest \
        -c 500 -d "$GAME_DURATION" -j "/results/${SCHED}_game.json")
    docker start "$GM_CID" >/dev/null
    attach_to_resctrl "$GM_CID"
    docker wait "$GM_CID" >/dev/null 2>&1 || true
    docker logs "$GM_CID" || true
    docker rm -f "$GM_CID" >/dev/null 2>&1 || true

    # Teardown current scheduler
    if [ "$SCHED" != "default_cfs_eevdf" ]; then
        echo "  [Host] Stopping scheduler: $SCHED..."
        sudo pkill -SIGINT -f "$TARGET_DIR/$SCHED" 2>/dev/null || true
        for i in {1..6}; do
            if [ "$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'None')" = "None" ]; then
                break
            fi
            sleep 1
        done
        if [ "$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'None')" != "None" ]; then
            sudo pkill -9 -f "$TARGET_DIR/$SCHED" 2>/dev/null || true
            sleep 2
        fi
        echo "  [Host] Sched_ext state: $(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'None')"
    fi
    sleep 2
done

# Ensure sched_ext is reset
if [ "$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo 'None')" != "None" ]; then
    sudo pkill -9 -f "$TARGET_DIR" 2>/dev/null || true
fi

# 4. Generate Master Production Benchmark Report & CSV
echo ""
echo "================================================================================"
echo " Generating Master Production Benchmark Report and CSV..."
echo "================================================================================"
python3 "$SCRIPT_DIR/parse_production_results.py" "$RESULTS_DIR" "$REPORT_FILE" "$CSV_FILE"

echo ""
echo "[+] All production benchmarks completed successfully!"
echo "    Master Report: $REPORT_FILE"
echo "    Master CSV:    $CSV_FILE"
echo "================================================================================"
