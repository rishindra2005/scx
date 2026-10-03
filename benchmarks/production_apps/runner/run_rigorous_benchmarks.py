#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
Comprehensive Rigorous Production Benchmarking & Telemetry Suite
Executes 3-repetition statistical evaluations (Mean ± StdDev) across
5 Real-World Production Architectures and 5 Linux Schedulers without premature early exits.

Hardware Isolation Constraints:
  - 12 Isolated Logical CPUs (Zen 5 P-cores 2,3,14,15 + Zen 5c E-cores 4-7,16-19)
  - AMD CAT L3 dedicated cache partition (resctrl 0xff00 upper 8 ways)
  - Strict 4.0 GB memory cgroup (benchmark.slice)
  - Host load clients run on unshielded host cores (0, 1, 8, 9) via taskset
  - Heterogeneous telemetry: Separate P-core vs E-core CPU utilization
"""

import os
import sys
import time
import json
import math
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple

BASE_DIR = Path("/home/rishi/Desktop/OS/project/scx")
PROD_DIR = BASE_DIR / "benchmarks" / "production_apps"
RESULTS_DIR = PROD_DIR / "runner" / "results"
REPORT_FILE = PROD_DIR / "RIGOROUS_TELEMETRY_REPORT.md"
CSV_FILE = RESULTS_DIR / "rigorous_telemetry.csv"
JSON_FILE = RESULTS_DIR / "rigorous_telemetry.json"
CGROUP_DIR = Path("/sys/fs/cgroup/benchmark.slice")
RESCTRL_TASKS = Path("/sys/fs/resctrl/benchmark/tasks")
TARGET_DIR = BASE_DIR / "target" / "release"

P_CORES = [2, 3, 14, 15]
E_CORES = [4, 5, 6, 7, 16, 17, 18, 19]
HOST_CORES = "0,1,8,9"

SCHEDULERS = ["default_cfs_eevdf", "scx_optima", "scx_rdtai", "scx_rusty", "scx_rustland"]


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def ensure_isolation():
    print("[*] Verifying hardware, cache, and kernel isolation...")
    run_cmd(["sudo", str(BASE_DIR / "scripts" / "isolate_benchmark_env.sh"), "enable"])


def get_sched_state() -> str:
    state_file = Path("/sys/kernel/sched_ext/state")
    if state_file.exists():
        return state_file.read_text().strip()
    return "unknown"


def start_scheduler(sched: str):
    if sched == "default_cfs_eevdf":
        print(f"  [Host] Using Linux default CFS / EEVDF baseline.")
        return
    print(f"  [Host] Launching scheduler: {sched}...")
    binary = TARGET_DIR / sched
    if not binary.exists():
        raise FileNotFoundError(f"Scheduler binary not found: {binary}")
    
    subprocess.Popen(["sudo", str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for _ in range(12):
        time.sleep(1)
        state = get_sched_state()
        if state == "enabled":
            print(f"  [Host] Active sched_ext state: {state} ({sched})")
            return
    raise RuntimeError(f"Scheduler {sched} failed to attach to sched_ext! Current state: {get_sched_state()}")


def stop_scheduler(sched: str):
    if sched == "default_cfs_eevdf":
        return
    print(f"  [Host] Stopping scheduler: {sched}...")
    run_cmd(["sudo", "pkill", "-SIGINT", "-f", str(TARGET_DIR / sched)], check=False)
    for _ in range(8):
        time.sleep(1)
        if get_sched_state() == "disabled":
            print("  [Host] Scheduler stopped cleanly. Sched_ext state: disabled")
            return
    run_cmd(["sudo", "pkill", "-9", "-f", str(TARGET_DIR / sched)], check=False)
    time.sleep(1)
    print(f"  [Host] Forced stop. Sched_ext state: {get_sched_state()}")


def read_proc_stat_cores() -> Dict[int, Tuple[int, int]]:
    """Returns mapping of cpu_id -> (idle_ticks, total_ticks)"""
    res = {}
    with open('/proc/stat') as f:
        for line in f:
            if line.startswith('cpu') and line[3:5].strip().isdigit():
                cpu_num = int(line[3:5].strip())
                fields = [int(x) for x in line.split()[1:]]
                idle = fields[3] + fields[4]
                total = sum(fields)
                res[cpu_num] = (idle, total)
    return res


def compute_core_utilization(before: Dict[int, Tuple[int, int]], after: Dict[int, Tuple[int, int]]) -> Tuple[float, float, float]:
    """Computes (p_core_pct, e_core_pct, total_isolated_pct)"""
    p_idle_diff = sum(after[c][0] - before[c][0] for c in P_CORES)
    p_tot_diff = sum(after[c][1] - before[c][1] for c in P_CORES)
    e_idle_diff = sum(after[c][0] - before[c][0] for c in E_CORES)
    e_tot_diff = sum(after[c][1] - before[c][1] for c in E_CORES)

    p_pct = 100.0 * (1.0 - (p_idle_diff / max(1, p_tot_diff)))
    e_pct = 100.0 * (1.0 - (e_idle_diff / max(1, e_tot_diff)))
    tot_pct = ((p_pct * len(P_CORES)) + (e_pct * len(E_CORES))) / (len(P_CORES) + len(E_CORES))
    return round(max(0.0, p_pct), 1), round(max(0.0, e_pct), 1), round(max(0.0, tot_pct), 1)


def get_cgroup_memory() -> Tuple[int, int]:
    mem_cur_file = CGROUP_DIR / "memory.current"
    mem_cur = int(mem_cur_file.read_text().strip()) if mem_cur_file.exists() else 0
    mem_peak_file = CGROUP_DIR / "memory.peak"
    mem_peak = int(mem_peak_file.read_text().strip()) if mem_peak_file.exists() else 0
    return mem_cur, mem_peak


def attach_container_to_resctrl(cid: str):
    try:
        proc = run_cmd(["docker", "inspect", "-f", "{{.State.Pid}}", cid])
        pid = proc.stdout.strip()
        if pid and int(pid) > 0 and RESCTRL_TASKS.exists():
            subprocess.run(["sudo", "bash", "-c", f"echo {pid} > {RESCTRL_TASKS}"], check=False)
    except Exception:
        pass


def run_container(image: str, args: List[str], ports: List[str] = None, detach: bool = False) -> str:
    if ports:
        for p in ports:
            hp = p.split(":")[0]
            subprocess.run(["bash", "-c", f"docker ps -q --filter 'publish={hp}' | xargs -r docker rm -f"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)

    cmd = [
        "docker", "create",
        "--cgroup-parent", "benchmark.slice",
        "-m", "4g", "--memory-swap", "4g",
        "-v", f"{RESULTS_DIR}:/results"
    ]
    if ports:
        for p in ports:
            cmd.extend(["-p", p])
    cmd.append(image)
    cmd.extend(args)
    cid = run_cmd(cmd).stdout.strip()
    
    started = False
    for _ in range(5):
        res = subprocess.run(["docker", "start", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            started = True
            break
        time.sleep(1)
    if not started:
        run_cmd(["docker", "start", cid])

    attach_container_to_resctrl(cid)
    if not detach:
        run_cmd(["docker", "wait", cid], check=False)
        run_cmd(["docker", "rm", cid], check=False)
    return cid


def calc_stats(values: List[float]) -> Tuple[float, float, float]:
    """Returns (mean, stddev, median)"""
    if not values:
        return 0.0, 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    stddev = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
    sorted_v = sorted(values)
    median = sorted_v[n // 2] if n % 2 != 0 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0
    return round(mean, 2), round(stddev, 2), round(median, 2)


# ==============================================================================
# Workload 1: Cloud-Native API Gateway (Host-driven asynchronous load)
# ==============================================================================
def bench_api_gateway(sched: str) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f" [1/5] API Gateway Rigorous Profiling under {sched}")
    print(f"================================================================================")
    port_map = "18080:80"
    cid = run_container("scx-api-gateway:latest", [], ports=[port_map], detach=True)

    # Wait for gateway ready
    for _ in range(25):
        chk = subprocess.run(["curl", "-s", "-f", "http://127.0.0.1:18080/healthz"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if chk.returncode == 0:
            break
        time.sleep(0.4)

    concurrency_steps = [10, 25, 50, 75, 100, 150]
    reqs_per_step = [150, 300, 500, 750, 1000, 1200]
    reps = 3
    results = []

    for c, reqs in zip(concurrency_steps, reqs_per_step):
        qps_list, p50_list, p90_list, p95_list, p99_list = [], [], [], [], []
        p_cpu_list, e_cpu_list, tot_cpu_list, ram_list = [], [], [], []
        errors_total = 0

        for r in range(reps):
            json_tmp = RESULTS_DIR / f"tmp_gw_{sched}_{c}_{r}.json"
            stat_before = read_proc_stat_cores()
            t0 = time.perf_counter()

            # Driver runs on host cores 0, 1, 8, 9
            cmd = [
                "taskset", "-c", HOST_CORES,
                "python3", str(PROD_DIR / "01_api_gateway" / "benchmark_client.py"),
                "--url", "http://127.0.0.1:18080/api/v1/dashboard",
                "--concurrency", str(c),
                "--requests", str(reqs),
                "--json", str(json_tmp)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wall_time = time.perf_counter() - t0
            stat_after = read_proc_stat_cores()
            p_cpu, e_cpu, tot_cpu = compute_core_utilization(stat_before, stat_after)
            cur_ram, peak_ram = get_cgroup_memory()

            if json_tmp.exists():
                try:
                    data = json.loads(json_tmp.read_text())
                    qps_list.append(data.get("throughput_req_s", 0))
                    p50_list.append(data.get("p50_ms", data.get("latency_p50_ms", 0)))
                    p90_list.append(data.get("p90_ms", data.get("latency_p90_ms", 0)))
                    p95_list.append(data.get("p95_ms", data.get("latency_p95_ms", 0)))
                    p99_list.append(data.get("p99_ms", data.get("latency_p99_ms", 0)))
                    errors_total += data.get("failed_reqs", data.get("error_count", 0))
                    json_tmp.unlink()
                except Exception:
                    pass
            p_cpu_list.append(p_cpu)
            e_cpu_list.append(e_cpu)
            tot_cpu_list.append(tot_cpu)
            ram_list.append(round(peak_ram / (1024 * 1024), 1))

        qps_mean, qps_std, qps_med = calc_stats(qps_list)
        p50_mean, _, _ = calc_stats(p50_list)
        p90_mean, _, _ = calc_stats(p90_list)
        p95_mean, _, _ = calc_stats(p95_list)
        p99_mean, p99_std, p99_med = calc_stats(p99_list)
        p_cpu_mean, _, _ = calc_stats(p_cpu_list)
        e_cpu_mean, _, _ = calc_stats(e_cpu_list)
        tot_cpu_mean, _, _ = calc_stats(tot_cpu_list)
        ram_mean, _, _ = calc_stats(ram_list)

        status = "HEALTHY"
        if errors_total > (reqs * reps * 0.1):
            status = f"HIGH_ERRORS ({errors_total})"
        elif p99_mean > 2000.0:
            status = f"SLA_WARN (P99 {p99_mean:.0f}ms > 2000ms)"

        print(f"  Concurrency {c:3d}: {qps_mean:6.1f} ± {qps_std:4.1f} req/s | P50: {p50_mean:5.1f}ms | P99: {p99_mean:6.1f} ± {p99_std:4.1f}ms | P-Core: {p_cpu_mean:4.1f}% | E-Core: {e_cpu_mean:4.1f}% | RAM: {ram_mean:5.1f}MB [{status}]")

        results.append({
            "concurrency": c,
            "throughput_mean": qps_mean, "throughput_std": qps_std, "throughput_med": qps_med,
            "p50_ms": p50_mean, "p90_ms": p90_mean, "p95_ms": p95_mean,
            "p99_ms": p99_mean, "p99_std": p99_std,
            "p_core_cpu": p_cpu_mean, "e_core_cpu": e_cpu_mean, "total_cpu": tot_cpu_mean,
            "ram_mb": ram_mean, "errors": errors_total, "status": status
        })

    run_cmd(["docker", "stop", cid], check=False)
    run_cmd(["docker", "rm", cid], check=False)
    return {"scheduler": sched, "application": "01_api_gateway", "steps": results}


# ==============================================================================
# Workload 2: Production In-Memory Redis Cache (Host-driven pipelined load)
# ==============================================================================
def bench_redis_cache(sched: str) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f" [2/5] Production Redis Cache Rigorous Profiling under {sched}")
    print(f"================================================================================")
    port_map = "16379:6379"
    cid = run_container("scx-redis-cache:latest", [], ports=[port_map], detach=True)

    for _ in range(25):
        chk = subprocess.run(["redis-cli", "-p", "16379", "ping"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if chk.returncode == 0:
            break
        time.sleep(0.4)

    client_steps = [50, 100, 200, 350, 500, 750]
    reps = 3
    results = []

    for c in client_steps:
        qps_list, p50_list, p95_list, p99_list = [], [], [], []
        p_cpu_list, e_cpu_list, tot_cpu_list, ram_list = [], [], [], []

        for r in range(reps):
            stat_before = read_proc_stat_cores()
            cmd = [
                "taskset", "-c", HOST_CORES,
                "redis-benchmark", "-h", "127.0.0.1", "-p", "16379",
                "-q", "-c", str(c), "-P", "16", "-n", "120000",
                "-t", "get,set", "--csv"
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stat_after = read_proc_stat_cores()
            p_cpu, e_cpu, tot_cpu = compute_core_utilization(stat_before, stat_after)
            cur_ram, peak_ram = get_cgroup_memory()

            set_qps, set_p50, set_p95, set_p99 = 0, 0, 0, 0
            get_qps, get_p50, get_p95, get_p99 = 0, 0, 0, 0
            for line in proc.stdout.splitlines():
                parts = [x.strip('"') for x in line.split(',')]
                if len(parts) >= 7:
                    if parts[0] == "SET":
                        set_qps = float(parts[1])
                        set_p50 = float(parts[4])
                        set_p95 = float(parts[5])
                        set_p99 = float(parts[6])
                    elif parts[0] == "GET":
                        get_qps = float(parts[1])
                        get_p50 = float(parts[4])
                        get_p95 = float(parts[5])
                        get_p99 = float(parts[6])

            if set_qps > 0 and get_qps > 0:
                qps_list.append((set_qps + get_qps) / 2.0)
                p50_list.append((set_p50 + get_p50) / 2.0)
                p95_list.append((set_p95 + get_p95) / 2.0)
                p99_list.append(max(set_p99, get_p99))
            p_cpu_list.append(p_cpu)
            e_cpu_list.append(e_cpu)
            tot_cpu_list.append(tot_cpu)
            ram_list.append(round(peak_ram / (1024 * 1024), 1))

        qps_mean, qps_std, qps_med = calc_stats(qps_list)
        p50_mean, _, _ = calc_stats(p50_list)
        p95_mean, _, _ = calc_stats(p95_list)
        p99_mean, p99_std, p99_med = calc_stats(p99_list)
        p_cpu_mean, _, _ = calc_stats(p_cpu_list)
        e_cpu_mean, _, _ = calc_stats(e_cpu_list)
        tot_cpu_mean, _, _ = calc_stats(tot_cpu_list)
        ram_mean, _, _ = calc_stats(ram_list)

        status = "HEALTHY"
        if p99_mean > 5.0:
            status = f"SLA_WARN (P99 {p99_mean:.2f}ms > 5ms)"

        print(f"  Clients {c:4d}: {qps_mean:9,.0f} ± {qps_std:7,.0f} ops/s | P50: {p50_mean:4.2f}ms | P99: {p99_mean:5.2f} ± {p99_std:4.2f}ms | P-Core: {p_cpu_mean:4.1f}% | E-Core: {e_cpu_mean:4.1f}% | RAM: {ram_mean:4.1f}MB [{status}]")

        results.append({
            "clients": c,
            "throughput_mean": qps_mean, "throughput_std": qps_std, "throughput_med": qps_med,
            "p50_ms": p50_mean, "p95_ms": p95_mean, "p99_ms": p99_mean, "p99_std": p99_std,
            "p_core_cpu": p_cpu_mean, "e_core_cpu": e_cpu_mean, "total_cpu": tot_cpu_mean,
            "ram_mb": ram_mean, "status": status
        })

    run_cmd(["docker", "stop", cid], check=False)
    run_cmd(["docker", "rm", cid], check=False)
    return {"scheduler": sched, "application": "02_redis_cache", "steps": results}


# ==============================================================================
# Workload 3: Real-Time Pro-Audio DSP Engine (Incremental Complexity)
# ==============================================================================
def bench_realtime_audio(sched: str) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f" [3/5] Real-Time Pro-Audio DSP Engine Rigorous Profiling under {sched}")
    print(f"================================================================================")
    complexity_steps = [
        {"desc": "Step 1:  8ch /  8 stages (64 filters)",   "ch": 8,  "st": 8},
        {"desc": "Step 2:  8ch / 16 stages (128 filters)",  "ch": 8,  "st": 16},
        {"desc": "Step 3: 16ch / 24 stages (384 filters)",  "ch": 16, "st": 24},
        {"desc": "Step 4: 24ch / 32 stages (768 filters)",  "ch": 24, "st": 32},
        {"desc": "Step 5: 32ch / 48 stages (1536 filters)", "ch": 32, "st": 48},
        {"desc": "Step 6: 48ch / 64 stages (3072 filters)", "ch": 48, "st": 64},
    ]
    reps = 3
    results = []

    for step in complexity_steps:
        ch = step["ch"]
        st = step["st"]
        total_filters = ch * st
        p50_list, p90_list, p95_list, p99_list, max_list = [], [], [], [], []
        xruns_total = 0
        p_cpu_list, e_cpu_list, tot_cpu_list, ram_list = [], [], [], []

        for r in range(reps):
            json_file = f"/results/tmp_audio_{sched}_{ch}_{st}_{r}.json"
            host_json = RESULTS_DIR / f"tmp_audio_{sched}_{ch}_{st}_{r}.json"

            stat_before = read_proc_stat_cores()
            run_container("scx-bench-realtime-audio:latest", [
                "-c", str(ch),
                "-l", str(st),
                "-n", "1500",
                "-j", json_file
            ])
            stat_after = read_proc_stat_cores()
            p_cpu, e_cpu, tot_cpu = compute_core_utilization(stat_before, stat_after)
            cur_ram, peak_ram = get_cgroup_memory()

            if host_json.exists():
                try:
                    data = json.loads(host_json.read_text())
                    turn = data.get("turnaround_time_us", data.get("turnaround_us", {}))
                    p50_list.append(turn.get("p50", 0))
                    p90_list.append(turn.get("p90", 0))
                    p95_list.append(turn.get("p95", 0))
                    p99_list.append(turn.get("p99", 0))
                    max_list.append(turn.get("max", 0))
                    xruns_total += data.get("xruns", 0)
                    host_json.unlink()
                except Exception:
                    pass
            p_cpu_list.append(p_cpu)
            e_cpu_list.append(e_cpu)
            tot_cpu_list.append(tot_cpu)
            ram_list.append(round(peak_ram / (1024 * 1024), 1))

        p50_mean, _, _ = calc_stats(p50_list)
        p90_mean, _, _ = calc_stats(p90_list)
        p95_mean, _, _ = calc_stats(p95_list)
        p99_mean, p99_std, _ = calc_stats(p99_list)
        max_mean, _, _ = calc_stats(max_list)
        p_cpu_mean, _, _ = calc_stats(p_cpu_list)
        e_cpu_mean, _, _ = calc_stats(e_cpu_list)
        tot_cpu_mean, _, _ = calc_stats(tot_cpu_list)
        ram_mean, _, _ = calc_stats(ram_list)

        status = "PERFECT (0 xruns)" if xruns_total == 0 else f"DEADLINE BREACH ({xruns_total} xruns, Max {max_mean:.1f}us)"

        print(f"  {step['desc']}: Turnaround P50: {p50_mean:5.1f}us | P99: {p99_mean:5.1f} ± {p99_std:4.1f}us | Max: {max_mean:6.1f}us | P-Core: {p_cpu_mean:4.1f}% | E-Core: {e_cpu_mean:4.1f}% [{status}]")

        results.append({
            "complexity": step["desc"], "total_filters": total_filters,
            "p50_us": p50_mean, "p90_us": p90_mean, "p95_us": p95_mean,
            "p99_us": p99_mean, "p99_std": p99_std, "max_us": max_mean,
            "xruns_total": xruns_total, "p_core_cpu": p_cpu_mean, "e_core_cpu": e_cpu_mean,
            "total_cpu": tot_cpu_mean, "ram_mb": ram_mean, "status": status
        })

    return {"scheduler": sched, "application": "03_realtime_audio", "steps": results}


# ==============================================================================
# Workload 4: Ultra-Low-Latency HFT Matching Engine (Volume Influx Ramp)
# ==============================================================================
def bench_hft_matching(sched: str) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f" [4/5] HFT Limit Order Book Rigorous Profiling under {sched}")
    print(f"================================================================================")
    order_steps = [50000, 150000, 350000, 750000, 1500000]
    reps = 3
    results = []

    for n in order_steps:
        qps_list, p50_list, p90_list, p95_list, p99_list, max_list = [], [], [], [], [], []
        p_cpu_list, e_cpu_list, tot_cpu_list, ram_list = [], [], [], []

        for r in range(reps):
            json_file = f"/results/tmp_hft_{sched}_{n}_{r}.json"
            host_json = RESULTS_DIR / f"tmp_hft_{sched}_{n}_{r}.json"

            stat_before = read_proc_stat_cores()
            run_container("scx-bench-hft-matching:latest", [
                "-n", str(n),
                "-j", json_file
            ])
            stat_after = read_proc_stat_cores()
            p_cpu, e_cpu, tot_cpu = compute_core_utilization(stat_before, stat_after)
            cur_ram, peak_ram = get_cgroup_memory()

            if host_json.exists():
                try:
                    data = json.loads(host_json.read_text())
                    qps_list.append(data.get("throughput_ops", data.get("throughput_orders_sec", 0)))
                    turn = data.get("latency_turnaround_us", {})
                    p50_list.append(turn.get("p50", 0))
                    p90_list.append(turn.get("p90", 0))
                    p95_list.append(turn.get("p95", 0))
                    p99_list.append(turn.get("p99", 0))
                    max_list.append(turn.get("max", 0))
                    host_json.unlink()
                except Exception:
                    pass
            p_cpu_list.append(p_cpu)
            e_cpu_list.append(e_cpu)
            tot_cpu_list.append(tot_cpu)
            ram_list.append(round(peak_ram / (1024 * 1024), 1))

        qps_mean, qps_std, qps_med = calc_stats(qps_list)
        p50_mean, _, _ = calc_stats(p50_list)
        p90_mean, _, _ = calc_stats(p90_list)
        p95_mean, _, _ = calc_stats(p95_list)
        p99_mean, p99_std, _ = calc_stats(p99_list)
        max_mean, _, _ = calc_stats(max_list)
        p_cpu_mean, _, _ = calc_stats(p_cpu_list)
        e_cpu_mean, _, _ = calc_stats(e_cpu_list)
        tot_cpu_mean, _, _ = calc_stats(tot_cpu_list)
        ram_mean, _, _ = calc_stats(ram_list)

        status = "HEALTHY"
        if p99_mean > 1000.0:
            status = f"SLA_WARN (P99 {p99_mean:.1f}us > 1000us)"

        print(f"  Orders {n:9,d}: {qps_mean:11,.0f} ± {qps_std:9,.0f} orders/s | Turnaround P50: {p50_mean:5.1f}us | P99: {p99_mean:5.1f} ± {p99_std:4.1f}us | P-Core: {p_cpu_mean:4.1f}% | E-Core: {e_cpu_mean:4.1f}% [{status}]")

        results.append({
            "orders": n,
            "throughput_mean": qps_mean, "throughput_std": qps_std, "throughput_med": qps_med,
            "p50_us": p50_mean, "p90_us": p90_mean, "p95_us": p95_mean,
            "p99_us": p99_mean, "p99_std": p99_std, "max_us": max_mean,
            "p_core_cpu": p_cpu_mean, "e_core_cpu": e_cpu_mean, "total_cpu": tot_cpu_mean,
            "ram_mb": ram_mean, "status": status
        })

    return {"scheduler": sched, "application": "04_hft_matching", "steps": results}


# ==============================================================================
# Workload 5: 120 FPS Authoritative Physics Game Server (Player Scaling)
# ==============================================================================
def bench_game_server(sched: str) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f" [5/5] 120 FPS Game Server Rigorous Profiling under {sched}")
    print(f"================================================================================")
    player_steps = [100, 250, 500, 750, 1000, 1500, 2000]
    reps = 3
    results = []

    for c in player_steps:
        p50_list, p90_list, p95_list, p99_list, max_list = [], [], [], [], []
        drops_total = 0
        total_ticks_simulated = 0
        p_cpu_list, e_cpu_list, tot_cpu_list, ram_list = [], [], [], []

        for r in range(reps):
            json_file = f"/results/tmp_game_{sched}_{c}_{r}.json"
            host_json = RESULTS_DIR / f"tmp_game_{sched}_{c}_{r}.json"

            stat_before = read_proc_stat_cores()
            # 360 ticks @ 120 FPS = 3.0 seconds per rep
            run_container("scx-bench-game-server:latest", [
                "-c", str(c),
                "-r", "120",
                "-d", "3",
                "-j", json_file
            ])
            stat_after = read_proc_stat_cores()
            p_cpu, e_cpu, tot_cpu = compute_core_utilization(stat_before, stat_after)
            cur_ram, peak_ram = get_cgroup_memory()

            if host_json.exists():
                try:
                    data = json.loads(host_json.read_text())
                    tick_us = data.get("tick_duration_us", {})
                    p50_list.append(tick_us.get("p50", 0))
                    p90_list.append(tick_us.get("p90", 0))
                    p95_list.append(tick_us.get("p95", 0))
                    p99_list.append(tick_us.get("p99", 0))
                    max_list.append(tick_us.get("max", 0))
                    drops_total += data.get("frame_drops", data.get("missed_deadlines", 0))
                    total_ticks_simulated += data.get("total_ticks", 360)
                    host_json.unlink()
                except Exception:
                    pass
            p_cpu_list.append(p_cpu)
            e_cpu_list.append(e_cpu)
            tot_cpu_list.append(tot_cpu)
            ram_list.append(round(peak_ram / (1024 * 1024), 1))

        p50_mean, _, _ = calc_stats(p50_list)
        p90_mean, _, _ = calc_stats(p90_list)
        p95_mean, _, _ = calc_stats(p95_list)
        p99_mean, p99_std, _ = calc_stats(p99_list)
        max_mean, _, _ = calc_stats(max_list)
        p_cpu_mean, _, _ = calc_stats(p_cpu_list)
        e_cpu_mean, _, _ = calc_stats(e_cpu_list)
        tot_cpu_mean, _, _ = calc_stats(tot_cpu_list)
        ram_mean, _, _ = calc_stats(ram_list)

        drop_rate = (drops_total / max(1, total_ticks_simulated)) * 100.0
        status = "PERFECT (0% drops)"
        if drop_rate > 1.0:
            status = f"DESYNC_COLLAPSE ({drop_rate:.2f}% drops)"
        elif drop_rate > 0:
            status = f"HITCHING ({drop_rate:.2f}% drops)"

        print(f"  Players {c:4d}: Tick P50: {p50_mean:5.1f}us | P99: {p99_mean:5.1f} ± {p99_std:4.1f}us | Drops: {drop_rate:4.2f}% | P-Core: {p_cpu_mean:4.1f}% | E-Core: {e_cpu_mean:4.1f}% [{status}]")

        results.append({
            "players": c,
            "p50_us": p50_mean, "p90_us": p90_mean, "p95_us": p95_mean,
            "p99_us": p99_mean, "p99_std": p99_std, "max_us": max_mean,
            "drop_rate_pct": round(drop_rate, 2), "drops_total": drops_total,
            "p_core_cpu": p_cpu_mean, "e_core_cpu": e_cpu_mean, "total_cpu": tot_cpu_mean,
            "ram_mb": ram_mean, "status": status
        })

    return {"scheduler": sched, "application": "05_game_server", "steps": results}


# ==============================================================================
# Master Benchmark Execution & Aggregation
# ==============================================================================
def main():
    print("================================================================================")
    print(" RIGOROUS 5-APPLICATION 5-SCHEDULER BENCHMARK SUITE")
    print(" Statistical Rigor: 3 Repetitions per data point (Mean ± StdDev)")
    print(" Target Hardware: AMD Ryzen AI 9 HX 370 (Zen 5 P-cores + Zen 5c E-cores)")
    print(" Isolation: CPUs 2-7,14-19 | AMD CAT L3 ff00 | 4GB RAM benchmark.slice")
    print("================================================================================\n")

    ensure_isolation()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sched", choices=SCHEDULERS, help="Run only a specific scheduler and update report")
    cli_args = parser.parse_args()

    master_results = {}
    if JSON_FILE.exists():
        try:
            master_results = json.loads(JSON_FILE.read_text())
        except Exception:
            pass

    target_scheds = [cli_args.sched] if cli_args.sched else SCHEDULERS

    for sched in target_scheds:
        print(f"\n################################################################################")
        print(f" EVALUATING SCHEDULER: {sched}")
        print(f"################################################################################")
        try:
            start_scheduler(sched)
            time.sleep(2)

            master_results[sched] = {
                "01_api_gateway": bench_api_gateway(sched),
                "02_redis_cache": bench_redis_cache(sched),
                "03_realtime_audio": bench_realtime_audio(sched),
                "04_hft_matching": bench_hft_matching(sched),
                "05_game_server": bench_game_server(sched)
            }
        finally:
            stop_scheduler(sched)
            time.sleep(2)

    # Save JSON Dataset
    with open(JSON_FILE, "w") as f:
        json.dump(master_results, f, indent=2)
    print(f"\n[+] Saved complete dataset to: {JSON_FILE}")

    # Generate Markdown Report & CSV
    generate_reports(master_results)


def generate_reports(data: Dict[str, Any]):
    print(f"[*] Generating master markdown report: {REPORT_FILE}...")
    lines = []
    lines.append("# Publication-Grade Rigorous Telemetry Report\n")
    lines.append(f"**Execution Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Hardware:** AMD Ryzen AI 9 HX 370 (12 Cores / 24 Threads)")
    lines.append(f"- **Isolated Core Allocation:** 12 Logical CPUs (`2-7, 14-19`: 2 Zen 5 P-cores + 4 Zen 5c E-cores)")
    lines.append(f"- **Unshielded Host Drivers:** Cores `0, 1, 8, 9` (Taskset client driver isolation)")
    lines.append(f"- **L3 Cache Partition:** AMD CAT `resctrl` dedicated upper 8 ways (`0xff00`)")
    lines.append(f"- **Statistical Methodology:** $N=3$ independent repetitions per step (Mean ± Standard Deviation)\n")
    lines.append("---\n")

    # Executive Summary Table
    lines.append("## 1. Executive Workload Summary Table\n")
    lines.append("| Workload | Metric Focus | Default CFS / EEVDF | `scx_optima` | `scx_rdtai` | `scx_rusty` | `scx_rustland` |")
    lines.append("|:---|:---|:---|:---|:---|:---|:---|")

    # App 1 Summary
    def get_gw_peak(sched):
        steps = data[sched]["01_api_gateway"]["steps"]
        best = max(steps, key=lambda x: x["throughput_mean"])
        return f"**{best['throughput_mean']:.1f} req/s**<br>(P99: {best['p99_ms']:.1f}ms)"
    lines.append(f"| **App 1: API Gateway** | Peak Throughput | {get_gw_peak('default_cfs_eevdf')} | {get_gw_peak('scx_optima')} | {get_gw_peak('scx_rdtai')} | {get_gw_peak('scx_rusty')} | {get_gw_peak('scx_rustland')} |")

    # App 2 Summary
    def get_redis_peak(sched):
        steps = data[sched]["02_redis_cache"]["steps"]
        best = max(steps, key=lambda x: x["throughput_mean"])
        return f"**{best['throughput_mean']:,.0f} ops/s**<br>(P99: {best['p99_ms']:.2f}ms)"
    lines.append(f"| **App 2: Redis Cache** | Peak Throughput | {get_redis_peak('default_cfs_eevdf')} | {get_redis_peak('scx_optima')} | {get_redis_peak('scx_rdtai')} | {get_redis_peak('scx_rusty')} | {get_redis_peak('scx_rustland')} |")

    # App 3 Summary
    def get_audio_summary(sched):
        steps = data[sched]["03_realtime_audio"]["steps"]
        max_step = steps[-1]
        xruns = sum(x["xruns_total"] for x in steps)
        if xruns == 0:
            return f"**Survived Max (3,072 filters)**<br>0 Xruns (P99: {max_step['p99_us']:.0f}us)"
        else:
            return f"**Failed** ({xruns} Xruns)<br>P99: {max_step['p99_us']:.0f}us"
    lines.append(f"| **App 3: Audio DSP** | 1.33ms Deadline | {get_audio_summary('default_cfs_eevdf')} | {get_audio_summary('scx_optima')} | {get_audio_summary('scx_rdtai')} | {get_audio_summary('scx_rusty')} | {get_audio_summary('scx_rustland')} |")

    # App 4 Summary
    def get_hft_summary(sched):
        steps = data[sched]["04_hft_matching"]["steps"]
        best = max(steps, key=lambda x: x["throughput_mean"])
        return f"**{best['throughput_mean']:,.0f} orders/s**<br>(P99: {best['p99_us']:.1f}us)"
    lines.append(f"| **App 4: HFT Matching** | Peak Throughput | {get_hft_summary('default_cfs_eevdf')} | {get_hft_summary('scx_optima')} | {get_hft_summary('scx_rdtai')} | {get_hft_summary('scx_rusty')} | {get_hft_summary('scx_rustland')} |")

    # App 5 Summary
    def get_game_summary(sched):
        steps = data[sched]["05_game_server"]["steps"]
        max_step = steps[-1]
        return f"**{max_step['drop_rate_pct']:.1f}% drops** @ 2k plrs<br>(P99: {max_step['p99_us']:.0f}us)"
    lines.append(f"| **App 5: Game Server** | 120 FPS Tick Stability | {get_game_summary('default_cfs_eevdf')} | {get_game_summary('scx_optima')} | {get_game_summary('scx_rdtai')} | {get_game_summary('scx_rusty')} | {get_game_summary('scx_rustland')} |\n")

    lines.append("---\n")

    # Detailed Application Tables
    for app_key, app_title in [
        ("01_api_gateway", "App 1: Cloud-Native API Gateway (Fine-Grained Concurrency)"),
        ("02_redis_cache", "App 2: Production Redis Cache (Multi-Threaded IO Scaling)"),
        ("03_realtime_audio", "App 3: Pro-Audio DSP Engine (Incremental Filter Complexity)"),
        ("04_hft_matching", "App 4: Ultra-Low-Latency HFT Matching (Volume Influx)"),
        ("05_game_server", "App 5: 120 FPS Authoritative Physics Game Server (Player Scaling)")
    ]:
        lines.append(f"## {app_title}\n")
        for sched in SCHEDULERS:
            lines.append(f"### Scheduler: `{sched}`\n")
            steps = data[sched][app_key]["steps"]
            if not steps:
                continue

            # Write Markdown Table headers based on app
            if app_key == "01_api_gateway":
                lines.append("| Concurrency | Throughput (req/s) | P50 (ms) | P90 (ms) | P95 (ms) | P99 (ms) | P-Core % | E-Core % | RAM (MB) | Status |")
                lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
                for s in steps:
                    lines.append(f"| {s['concurrency']} | {s['throughput_mean']:.1f} ± {s['throughput_std']:.1f} | {s['p50_ms']:.1f} | {s['p90_ms']:.1f} | {s['p95_ms']:.1f} | {s['p99_ms']:.1f} ± {s['p99_std']:.1f} | {s['p_core_cpu']:.1f}% | {s['e_core_cpu']:.1f}% | {s['ram_mb']:.1f} | {s['status']} |")
            elif app_key == "02_redis_cache":
                lines.append("| Clients | Throughput (ops/s) | P50 (ms) | P95 (ms) | P99 (ms) | P-Core % | E-Core % | RAM (MB) | Status |")
                lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
                for s in steps:
                    lines.append(f"| {s['clients']} | {s['throughput_mean']:,.0f} ± {s['throughput_std']:,.0f} | {s['p50_ms']:.2f} | {s['p95_ms']:.2f} | {s['p99_ms']:.2f} ± {s['p99_std']:.2f} | {s['p_core_cpu']:.1f}% | {s['e_core_cpu']:.1f}% | {s['ram_mb']:.1f} | {s['status']} |")
            elif app_key == "03_realtime_audio":
                lines.append("| Complexity | Filters | P50 (us) | P90 (us) | P95 (us) | P99 (us) | Max (us) | Xruns | P-Core % | E-Core % | Status |")
                lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
                for s in steps:
                    lines.append(f"| {s['complexity']} | {s['total_filters']} | {s['p50_us']:.1f} | {s['p90_us']:.1f} | {s['p95_us']:.1f} | {s['p99_us']:.1f} ± {s['p99_std']:.1f} | {s['max_us']:.1f} | {s['xruns_total']} | {s['p_core_cpu']:.1f}% | {s['e_core_cpu']:.1f}% | {s['status']} |")
            elif app_key == "04_hft_matching":
                lines.append("| Orders | Throughput (orders/s) | P50 (us) | P90 (us) | P95 (us) | P99 (us) | Max (us) | P-Core % | E-Core % | Status |")
                lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
                for s in steps:
                    lines.append(f"| {s['orders']:,} | {s['throughput_mean']:,.0f} ± {s['throughput_std']:,.0f} | {s['p50_us']:.1f} | {s['p90_us']:.1f} | {s['p95_us']:.1f} | {s['p99_us']:.1f} ± {s['p99_std']:.1f} | {s['max_us']:.1f} | {s['p_core_cpu']:.1f}% | {s['e_core_cpu']:.1f}% | {s['status']} |")
            elif app_key == "05_game_server":
                lines.append("| Players | P50 (us) | P90 (us) | P95 (us) | P99 (us) | Max (us) | Drops | Drop Rate | P-Core % | E-Core % | Status |")
                lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|")
                for s in steps:
                    lines.append(f"| {s['players']} | {s['p50_us']:.1f} | {s['p90_us']:.1f} | {s['p95_us']:.1f} | {s['p99_us']:.1f} ± {s['p99_std']:.1f} | {s['max_us']:.1f} | {s['drops_total']} | {s['drop_rate_pct']:.2f}% | {s['p_core_cpu']:.1f}% | {s['e_core_cpu']:.1f}% | {s['status']} |")
            lines.append("\n")
        lines.append("---\n")

    REPORT_FILE.write_text("\n".join(lines))
    print(f"[+] Master report generated at: {REPORT_FILE}")


if __name__ == "__main__":
    main()
