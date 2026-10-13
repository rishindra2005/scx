#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Master Orchestrator for 3x 16-Workload Isolated QEMU/KVM Benchmark Suite
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_KEY="$SCRIPT_DIR/qemu_key"
RESULTS_DIR="$BASE_DIR/sch_tests/qemu_results"
mkdir -p "$RESULTS_DIR"

SSH_CMD="ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $SSH_KEY -p 2222 root@localhost"

echo "================================================================================"
echo "  LAUNCHING ISOLATED QEMU/KVM 16-WORKLOAD 3X BENCHMARK ORCHESTRATOR             "
echo "================================================================================"

# 1. Start VM with strict 50/50 hardware core isolation & L3 cache mask
echo "[1/5] Starting QEMU VM pinned to isolated cores (2-7, 14-19)..."
"$SCRIPT_DIR/start_vm.sh"

# 2. Wait for VM SSH availability
echo "[2/5] Waiting for VM SSH to become responsive..."
for i in {1..60}; do
    if $SSH_CMD "true" >/dev/null 2>&1; then
        echo "      Guest VM SSH active and responsive on port 2222."
        break
    fi
    sleep 2
    if [ "$i" -eq 60 ]; then
        echo "ERROR: Timeout waiting for VM SSH on port 2222!"
        exit 1
    fi
done

# 3. Synchronize latest repository files and binaries
echo "[3/5] Synchronizing latest code and binaries to Guest VM (/root/scx)..."
$SSH_CMD "mkdir -p /root/scx/target/release /root/scx/sch_tests/qemu /root/scx/sch_tests/qemu_results"
rsync -az -e "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $SSH_KEY -p 2222" \
    --exclude="target/debug" \
    --exclude="sch_tests/qemu/*.img" \
    --exclude="sch_tests/qemu/*.qcow2" \
    --exclude="sch_tests/qemu/*.iso" \
    --exclude="sch_tests/qemu/initrd*" \
    --exclude=".git" \
    "$BASE_DIR/" "root@localhost:/root/scx/"
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$SSH_KEY" -P 2222 \
    "$SCRIPT_DIR/guest_suite.py" "root@localhost:/root/scx/sch_tests/qemu/guest_suite.py"
echo "      Code synchronization complete."

# 4. Execute 3x 16-workload benchmark suite
echo "[4/5] Executing 16-Workload 3x Benchmark Suite inside Guest VM..."
echo "      Streaming output to console and $RESULTS_DIR/suite_execution.log..."
$SSH_CMD "python3 -u /root/scx/sch_tests/qemu/guest_suite.py" 2>&1 | tee "$RESULTS_DIR/suite_execution.log"

# 5. Retrieve all raw logs, CSVs, and reports
echo "[5/5] Retrieving benchmark results and statistical reports..."
rsync -az -e "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $SSH_KEY -p 2222" \
    "root@localhost:/root/scx/sch_tests/qemu_results/" "$RESULTS_DIR/"

# 6. Graceful VM shutdown
echo "[*] Powering off isolated Guest VM..."
$SSH_CMD "poweroff" >/dev/null 2>&1 || true
sleep 3

echo "================================================================================"
echo "  BENCHMARKS COMPLETED SUCCESSFULLY!                                            "
echo "  Results: $RESULTS_DIR/QEMU_16_WORKLOADS_3X_REPORT.md                          "
echo "  CSV:     $RESULTS_DIR/qemu_3x_results.csv                                     "
echo "================================================================================"
