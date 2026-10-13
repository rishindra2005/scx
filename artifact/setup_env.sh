#!/usr/bin/env bash
# artifact/setup_env.sh: Environment setup and prerequisite validation for OPTIMA Artifact Evaluation

set -euo pipefail

echo "================================================================="
echo " OPTIMA Artifact Evaluation: Environment Setup & Sanity Check  "
echo "================================================================="

# 1. Verify Operating System and Kernel
echo "[*] Checking Linux kernel and sched_ext capabilities..."
KERNEL_VER=$(uname -r)
echo "    Kernel release: $KERNEL_VER"

if [ ! -f /sys/kernel/sched_ext/state ] && [ ! -d /sys/kernel/sched_ext ]; then
    echo "[-] WARNING: /sys/kernel/sched_ext not found. Ensure kernel is booted with CONFIG_SCHED_CLASS_EXT=y."
else
    echo "[+] Linux sched_ext interface detected!"
fi

# 2. Check Hardware RAPL powercap interface
echo "[*] Checking Intel/AMD RAPL sysfs counters..."
if [ -f /sys/class/powercap/intel-rapl:0/energy_uj ]; then
    echo "[+] Hardware RAPL Package energy counter available (/sys/class/powercap/intel-rapl:0/energy_uj)"
else
    echo "[-] WARNING: RAPL sysfs not found. Energy profiling requires intel-rapl or amd_energy modules."
fi

# 3. Check Compilers and Build Tools
echo "[*] Verifying compilers and tools..."
for tool in cargo clang llvm-strip bpftool perf sysbench hackbench iperf3; do
    if command -v "$tool" &> /dev/null; then
        echo "    [+] Found: $tool ($(command -v "$tool"))"
    else
        echo "    [-] Missing: $tool (Please install via: sudo apt-get install -y clang llvm libbpf-dev sysbench rt-tests iperf3 linux-tools-generic)"
    fi
done

# 4. Build OPTIMA and peer schedulers
echo "[*] Compiling OPTIMA release binary..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
cargo build --release --bin scx_optima

if [ -f "target/release/scx_optima" ]; then
    echo "[+] scx_optima built successfully: $(ls -lh target/release/scx_optima | awk '{print $5, $9}')"
else
    echo "[-] Build failed: target/release/scx_optima not found!"
    exit 1
fi

echo "================================================================="
echo "[+] Environment setup and verification complete!"
echo "================================================================="
