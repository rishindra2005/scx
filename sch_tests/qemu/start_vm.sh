#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VM_PID_FILE="$SCRIPT_DIR/qemu.pid"
CONSOLE_LOG="$SCRIPT_DIR/vm_console.log"

if [ -f "$VM_PID_FILE" ]; then
    PID=$(cat "$VM_PID_FILE" || true)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "VM is already running with PID $PID"
        exit 0
    fi
fi

echo "[*] Activating hardware isolation and L3 cache partitioning..."
sudo "$SCRIPT_DIR/../../scripts/isolate_benchmark_env.sh" enable

echo "[*] Attaching to isolated cgroup partition and L3 cache ways..."
VM_CGROUP="/sys/fs/cgroup/benchmark.slice/vm"
sudo mkdir -p "$VM_CGROUP"
echo $$ | sudo tee "$VM_CGROUP/cgroup.procs" >/dev/null 2>&1 || true
if [ -d "/sys/fs/resctrl/benchmark" ]; then
    echo $$ | sudo tee /sys/fs/resctrl/benchmark/tasks >/dev/null 2>&1 || true
fi

echo "[*] Launching QEMU VM pinned to isolated cores (2-7,14-19: 2 P-cores + 4 E-cores)..."
rm -f "$CONSOLE_LOG"

taskset -c 2-7,14-19 qemu-system-x86_64 \
    -enable-kvm \
    -cpu host,+topoext \
    -smp 12,sockets=1,cores=6,threads=2 \
    -m 14G \
    -drive file="$SCRIPT_DIR/qemu_bench_disk.qcow2",format=qcow2,if=virtio \
    -drive file="$SCRIPT_DIR/seed.iso",format=raw,if=virtio \
    -netdev user,id=net0,hostfwd=tcp::2222-:22 \
    -device virtio-net-pci,netdev=net0 \
    -kernel "$SCRIPT_DIR/vmlinuz" \
    -initrd "$SCRIPT_DIR/initrd.img" \
    -append "root=LABEL=cloudimg-rootfs rw console=ttyS0 rd.fstab=0 rd.lvm=0 rd.md=0 rd.dm=0" \
    -display none \
    -serial file:"$CONSOLE_LOG" \
    -daemonize \
    -pidfile "$VM_PID_FILE"

PID=$(cat "$VM_PID_FILE")
echo "[*] QEMU launched with PID $PID, console output in $CONSOLE_LOG"

# 1:1 Hardware vCPU pinning:
# vCPU 0..3   -> Zen 5 P-cores (CPUs 2, 3, 14, 15 @ 5.16 GHz)
# vCPU 4..11  -> Zen 5c E-cores (CPUs 4, 5, 6, 7, 16, 17, 18, 19 @ 3.29 GHz)
echo "[*] Pinning guest vCPUs 1:1 to dedicated physical P & E silicon cores..."
sleep 1.5
P_CPUS=(2 3 14 15)
E_CPUS=(4 5 6 7 16 17 18 19)
VCPU_TIDS=($(ps -T -p "$PID" -o tid,comm 2>/dev/null | grep "CPU [0-9]" | sort -k2 -V | awk '{print $1}' || true))

if [ ${#VCPU_TIDS[@]} -ge 12 ]; then
    for i in {0..3}; do
        taskset -cp "${P_CPUS[$i]}" "${VCPU_TIDS[$i]}" >/dev/null 2>&1 || true
    done
    for i in {0..7}; do
        idx=$((i + 4))
        taskset -cp "${E_CPUS[$i]}" "${VCPU_TIDS[$idx]}" >/dev/null 2>&1 || true
    done
    echo "[*] 1:1 vCPU pinning complete (4 P-threads + 8 E-threads dedicated to VM)."
else
    echo "[*] General taskset (2-7,14-19) active for all QEMU threads."
fi
