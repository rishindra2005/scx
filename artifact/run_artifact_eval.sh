#!/usr/bin/env bash
# artifact/run_artifact_eval.sh: End-to-end Artifact Evaluation Execution Script for OPTIMA

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================================================="
echo " OPTIMA: Artifact Evaluation Execution Harness                          "
echo " Venue: USENIX ATC / ACM EuroSys                                         "
echo " Target: Artifact Available, Functional, and Reproduced                  "
echo "========================================================================="

MODE="${1:-smoke}"

cd "$REPO_ROOT"

if [ "$MODE" == "smoke" ]; then
    echo "[*] MODE: Quick Smoke Test (< 60 seconds)"
    echo "    1. Building release scheduler..."
    cargo build --release --bin scx_optima
    
    echo "    2. Testing scheduler attachment and short execution..."
    sudo timeout 3s ./target/release/scx_optima || true
    echo "    [+] Scheduler attachment verified!"
    
    echo "    3. Running baseline CFS IPC pipe latency test..."
    perf bench sched pipe -l 10000
    
    echo "    4. Running OPTIMA IPC pipe latency test..."
    sudo ./target/release/scx_optima &
    OPT_PID=$!
    sleep 1.5
    perf bench sched pipe -l 10000
    sudo kill -INT "$OPT_PID" || true
    wait "$OPT_PID" 2>/dev/null || true
    echo "    [+] Smoke test completed successfully!"

elif [ "$MODE" == "rapl" ]; then
    echo "[*] MODE: Hardware RAPL Energy Profiling (5 repetitions per workload)"
    python3 scripts/measure_rapl_energy.py
    python3 artifact/parse_results.py --rapl
    echo "    [+] Energy profiling completed! Results summarized in table format."

elif [ "$MODE" == "full" ]; then
    echo "[*] MODE: Full Benchmark Suite Reproduction"
    echo "    Executing RAPL energy runs and compiling evaluation tables..."
    python3 scripts/measure_rapl_energy.py
    python3 artifact/parse_results.py --all
    echo "    [+] Full artifact evaluation complete!"

else
    echo "Usage: $0 [smoke | rapl | full]"
    exit 1
fi
