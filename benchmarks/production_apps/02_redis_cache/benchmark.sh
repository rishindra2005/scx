#!/usr/bin/env bash
# ==============================================================================
# High-Throughput Redis Pipelining & Latency Benchmark Driver
# Compares unpipelined vs pipelined throughput and evaluates tail latencies
# ==============================================================================
set -eo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-6379}"
REQUESTS="${3:-200000}"
CLIENTS="${4:-100}"

echo "======================================================================"
echo " Redis Production Cache Benchmark Suite (sched_ext evaluation)        "
echo " Target: ${HOST}:${PORT} | Requests: ${REQUESTS} | Clients: ${CLIENTS}"
echo "======================================================================"

# Wait for Redis server readiness
until redis-cli -h "${HOST}" -p "${PORT}" ping > /dev/null 2>&1; do
    echo "[Wait] Waiting for Redis server at ${HOST}:${PORT} to respond..."
    sleep 0.5
done
echo "[OK] Connected to Redis server."
echo ""

# ------------------------------------------------------------------------------
# Test 1: Baseline Unpipelined (P=1) - Measures Event Loop & Wakeup Latency
# ------------------------------------------------------------------------------
echo ">>> [1/3] Running Unpipelined Baseline (P=1, 50 Clients, 100,000 Reqs)..."
echo "    Assesses kernel scheduling wakeup latency on socket events."
redis-benchmark -h "${HOST}" -p "${PORT}" \
    -t get,set \
    -n 100000 \
    -c 50 \
    --latency-history -i 1 -q || true

echo ""
# ------------------------------------------------------------------------------
# Test 2: Moderate Pipelining (P=16) - Tests I/O Thread Batching
# ------------------------------------------------------------------------------
echo ">>> [2/3] Running Moderate Pipelining (P=16, 100 Clients, ${REQUESTS} Reqs)..."
echo "    Assesses multi-threaded I/O (io-threads 4) batch processing efficiency."
redis-benchmark -h "${HOST}" -p "${PORT}" \
    -t get,set \
    -n "${REQUESTS}" \
    -c "${CLIENTS}" \
    -P 16 \
    -q

echo ""
# ------------------------------------------------------------------------------
# Test 3: Ultra Pipelining (P=64) - Maximum Throughput & Syscall Amortization
# ------------------------------------------------------------------------------
echo ">>> [3/3] Running High-Throughput Pipelining (P=64, 150 Clients, 500,000 Reqs)..."
echo "    Saturates memory bus, socket buffers, and evaluates context switch overhead."
redis-benchmark -h "${HOST}" -p "${PORT}" \
    -t get,set \
    -n 500000 \
    -c 150 \
    -P 64 \
    -q

echo ""
echo "======================================================================"
echo " Current Redis Cache Memory & Operation Stats:                       "
echo "======================================================================"
redis-cli -h "${HOST}" -p "${PORT}" info stats | grep -E "total_commands_processed|instantaneous_ops_per_sec|rejected_connections"
redis-cli -h "${HOST}" -p "${PORT}" info memory | grep -E "used_memory_human|used_memory_peak_human|mem_fragmentation_ratio"
echo "======================================================================"
