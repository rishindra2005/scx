#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Hardware & Kernel Isolation Tool for Deterministic Scheduler Benchmarking
# Tailored for AMD Ryzen AI 9 HX 370 (Strix Point: 4 Zen 5 P-cores + 8 Zen 5c E-cores)
#
# Author: Risheendra MN <rishindra.hackbox@gmail.com>
#
# Isolated Topology (Benchmark Environment - 12 Logical CPUs):
#   - 2 P-Cores: Cores 2, 3 -> Logical CPUs 2, 3 + SMT siblings 14, 15 (Zen 5 @ up to 5.16 GHz)
#   - 4 E-Cores: Cores 4, 5, 6, 7 -> Logical CPUs 4, 5, 6, 7 + SMT siblings 16, 17, 18, 19 (Zen 5c @ up to 3.29 GHz)
#   - L1/L2 Caches: 100% physically private to physical cores 2, 3, 4, 5, 6, 7
#   - L3 Cache: AMD CAT (Cache Allocation Technology) via /sys/fs/resctrl (upper 8 ways: ff00)
#   - Kernel Scheduler: cgroup v2 isolated partition (rebuilds sched_domains; stripped from CFS/EEVDF)
#
# Shielded Host Topology (Desktop / OS / Background - 12 Logical CPUs):
#   - 2 P-Cores: Cores 0, 1 -> Logical CPUs 0, 1 + SMT siblings 12, 13 (Zen 5 @ up to 5.16 GHz)
#   - 4 E-Cores: Cores 8, 9, 10, 11 -> Logical CPUs 8, 9, 10, 11 + SMT siblings 20, 21, 22, 23
#   - L3 Cache: AMD CAT lower 8 ways (00ff)
#   - All host interrupts (IRQs), desktop GUI, Wayland, and system services remain responsive here.

set -euo pipefail

ISO_CPUS="2-7,14-19"
HOST_CPUS="0-1,8-13,20-23"
HOST_IRQ_MASK="f03f03"
DEFAULT_IRQ_MASK="ffffff"

CGROUP_PATH="/sys/fs/cgroup/benchmark.slice"
RESCTRL_PATH="/sys/fs/resctrl/benchmark"

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "[!] Error: Root privileges required. Re-running with sudo..." >&2
        exec sudo "$0" "$@"
    fi
}

enable_isolation() {
    check_root
    echo "================================================================="
    echo "  Activating Hardware, Cache, & Kernel Isolation for Benchmarks  "
    echo "================================================================="

    echo "[1/5] Uncapping All CPU Frequencies to Silicon Hardware Max..."
    echo "performance" > /sys/firmware/acpi/platform_profile 2>/dev/null || true
    for c in /sys/devices/system/cpu/cpu*/cpufreq; do
        echo "performance" > "$c/scaling_governor" 2>/dev/null || true
        echo "performance" > "$c/energy_performance_preference" 2>/dev/null || true
        echo 6000000 > "$c/scaling_max_freq" 2>/dev/null || true
    done
    echo "      Platform profile set to performance."
    echo "      P-cores uncapped to $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null || echo '5.16GHz') kHz"
    echo "      E-cores uncapped to $(cat /sys/devices/system/cpu/cpu4/cpufreq/scaling_max_freq 2>/dev/null || echo '3.29GHz') kHz"

    echo "[2/5] Configuring AMD CAT L3 Cache Allocation..."
    if ! findmnt /sys/fs/resctrl >/dev/null 2>&1; then
        mount -t resctrl resctrl /sys/fs/resctrl
    fi

    # Confine Host OS to lower 8 cache ways (00ff) across CCX0 (P) and CCX1 (E)
    echo "L3:0=00ff;1=00ff" > /sys/fs/resctrl/schemata

    # Grant benchmark group exclusive upper 8 cache ways (ff00)
    mkdir -p "$RESCTRL_PATH"
    echo "L3:0=ff00;1=ff00" > "$RESCTRL_PATH/schemata"
    echo "      Host OS L3 Mask:    $(grep L3 /sys/fs/resctrl/schemata | tr -d ' ')"
    echo "      Benchmark L3 Mask:  $(grep L3 "$RESCTRL_PATH/schemata" | tr -d ' ')"

    echo "[2/4] Initializing Cgroup v2 Isolated Kernel Partition..."
    mkdir -p "$CGROUP_PATH"
    echo "$ISO_CPUS" > "$CGROUP_PATH/cpuset.cpus.exclusive"
    echo "$ISO_CPUS" > "$CGROUP_PATH/cpuset.cpus"
    echo "isolated" > "$CGROUP_PATH/cpuset.cpus.partition"

    # Enable controllers so nested sub-cgroups (and Docker containers) inherit them
    echo "+cpuset +cpu +memory +io" > "$CGROUP_PATH/cgroup.subtree_control"

    echo "      Kernel sched_domain rebuilt. cpuset.cpus.isolated = $(cat /sys/fs/cgroup/cpuset.cpus.isolated)"
    echo "      benchmark.slice partition state                  = $(cat "$CGROUP_PATH/cpuset.cpus.partition")"

    echo "[3/4] Rerouting Hardware Device Interrupts (IRQs)..."
    echo "$HOST_IRQ_MASK" > /proc/irq/default_smp_affinity 2>/dev/null || true
    echo "      Default IRQ affinity mask set to host cores ($HOST_CPUS)."

    echo "[4/4] Isolation Fully Operational!"
    echo "      Dedicated CPUs: $ISO_CPUS (4 P-threads, 8 E-threads)"
    echo "      Host OS CPUs:   $HOST_CPUS (Desktop & OS noise-free)"
    echo "================================================================="
}

disable_isolation() {
    check_root
    echo "================================================================="
    echo "  Restoring System to Standard SMP & Shared Cache Configuration  "
    echo "================================================================="

    if [ -d "$CGROUP_PATH" ]; then
        # Terminate any remaining processes or move them to root cgroup
        if [ -s "$CGROUP_PATH/cgroup.procs" ]; then
            while read -r pid; do
                echo "$pid" > /sys/fs/cgroup/cgroup.procs 2>/dev/null || true
            done < "$CGROUP_PATH/cgroup.procs"
        fi
        echo "member" > "$CGROUP_PATH/cpuset.cpus.partition" 2>/dev/null || true
        # Remove any sub-cgroups created by Docker
        find "$CGROUP_PATH" -mindepth 1 -maxdepth 2 -type d 2>/dev/null | tac | xargs -r -n1 rmdir 2>/dev/null || true
        rmdir "$CGROUP_PATH" 2>/dev/null || true
    fi

    if [ -d "$RESCTRL_PATH" ]; then
        echo "L3:0=ffff;1=ffff" > /sys/fs/resctrl/schemata 2>/dev/null || true
        rmdir "$RESCTRL_PATH" 2>/dev/null || true
    fi

    echo "$DEFAULT_IRQ_MASK" > /proc/irq/default_smp_affinity 2>/dev/null || true

    echo "  Kernel cpuset.cpus.isolated: $(cat /sys/fs/cgroup/cpuset.cpus.isolated 2>/dev/null || echo 'None')"
    echo "  L3 Cache Schemata:           $(grep L3 /sys/fs/resctrl/schemata 2>/dev/null | tr -d ' ' || echo 'Default')"
    echo "System restored to unpartitioned SMP state."
    echo "================================================================="
}

run_command_isolated() {
    check_root
    if [ ! -d "$CGROUP_PATH" ]; then
        enable_isolation
    fi

    echo "[*] Launching native command in isolated domain: $*"
    # Run in subshell, place into isolated cgroup and resctrl group
    (
        echo $BASHPID > "$CGROUP_PATH/cgroup.procs"
        echo $BASHPID > "$RESCTRL_PATH/tasks"
        exec "$@"
    )
}

run_docker_isolated() {
    if [ ! -d "$CGROUP_PATH" ]; then
        echo "[*] Isolation partition not yet initialized. Initializing now..."
        check_root
        enable_isolation
    fi

    # Strip optional leading 'run'
    if [ "${1:-}" = "run" ]; then
        shift
    fi

    echo "[*] Launching Docker container inside benchmark.slice ($ISO_CPUS)..."
    local CID
    CID=$(docker create --cgroup-parent benchmark.slice "$@")

    docker start "$CID" >/dev/null
    local CPID
    CPID=$(docker inspect -f '{{.State.Pid}}' "$CID")

    if [ -n "$CPID" ] && [ "$CPID" -gt 0 ]; then
        sudo bash -c "echo $CPID > $RESCTRL_PATH/tasks" 2>/dev/null || true
    fi

    echo "[*] Container $CID running in isolated kernel partition & AMD CAT L3 cache (PID: $CPID)."
    docker attach "$CID"
}

status_isolation() {
    echo "=================== System Isolation Status ==================="
    local ISOLATED_CPUS
    ISOLATED_CPUS="$(cat /sys/fs/cgroup/cpuset.cpus.isolated 2>/dev/null || echo '')"
    if [ -n "$ISOLATED_CPUS" ]; then
        echo "Kernel Isolated CPUs:     $ISOLATED_CPUS (Excluded from CFS/EEVDF load balancing)"
    else
        echo "Kernel Isolated CPUs:     None (Standard SMP mode)"
    fi

    if [ -d "$CGROUP_PATH" ]; then
        echo "Cgroup Partition:         ACTIVE"
        echo "  - Partition Type:       $(cat "$CGROUP_PATH/cpuset.cpus.partition")"
        echo "  - Exclusive CPUs:       $(cat "$CGROUP_PATH/cpuset.cpus.exclusive.effective" 2>/dev/null || echo "$ISO_CPUS")"
        echo "  - Allowed CPUs:         $(cat "$CGROUP_PATH/cpuset.cpus.effective" 2>/dev/null || echo "$ISO_CPUS")"
        echo "  - Active Tasks in Slice: $(wc -l < "$CGROUP_PATH/cgroup.procs" 2>/dev/null || echo 0)"
    else
        echo "Cgroup Partition:         INACTIVE"
    fi

    if findmnt /sys/fs/resctrl >/dev/null 2>&1; then
        echo "Resctrl (AMD CAT L3):     MOUNTED"
        echo "  - Default/Host L3:      $(grep L3 /sys/fs/resctrl/schemata 2>/dev/null | tr -d ' ')"
        if [ -d "$RESCTRL_PATH" ]; then
            echo "  - Benchmark L3:         $(grep L3 "$RESCTRL_PATH/schemata" 2>/dev/null | tr -d ' ')"
            echo "  - Benchmark Tasks:      $(wc -l < "$RESCTRL_PATH/tasks" 2>/dev/null || echo 0)"
        else
            echo "  - Benchmark L3:         INACTIVE"
        fi
    else
        echo "Resctrl (AMD CAT L3):     NOT MOUNTED"
    fi
    echo "Default IRQ Affinity:     $(cat /proc/irq/default_smp_affinity 2>/dev/null || echo 'Unknown')"
    echo "==============================================================="
}

case "${1:-}" in
    enable)
        enable_isolation
        ;;
    disable)
        disable_isolation
        ;;
    status)
        status_isolation
        ;;
    run)
        shift
        run_command_isolated "$@"
        ;;
    docker)
        shift
        run_docker_isolated "$@"
        ;;
    *)
        echo "Usage: $0 {enable|disable|status|run <command...>|docker <docker run args...>}"
        echo ""
        echo "Commands:"
        echo "  sudo $0 enable                  # Turn on CPU, L3 cache, and IRQ isolation"
        echo "  $0 status                       # Check current hardware & kernel isolation status"
        echo "  sudo $0 run <bench_command...>  # Run native benchmark inside isolated environment"
        echo "  $0 docker [run] <docker_args...># Run Docker container inside isolated environment"
        echo "  sudo $0 disable                 # Restore system to full SMP and shared cache"
        exit 1
        ;;
esac
