#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
Automated Stress-to-Failure & Breaking Point Profiler
Incrementally ramps up load across 5 Production Architectures until the scheduler
and container hit the breaking point / overload cliff.

Evaluates 5 Linux Schedulers:
  1. default_cfs_eevdf
  2. scx_optima
  3. scx_rdtai
  4. scx_rusty
  5. scx_rustland

Workloads & Breaking Criteria:
  1. API Gateway: Ramp concurrency until 5xx errors or P99 > 2,000ms.
  2. Redis Cache: Ramp clients until P99 > 5.0ms or QPS collapse.
  3. Pro-Audio DSP: Ramp filter complexity until first audio XRUN (>1.33ms breach).
  4. HFT Matching: Ramp order flow until P99 turnaround > 1,000 us.
  5. 120 FPS Game: Ramp active players until frame drop rate > 1.0%.

Outputs:
  - runner/results/stress_to_failure_telemetry.json
  - runner/results/stress_to_failure_telemetry.csv
  - BREAKING_POINT_TELEMETRY_REPORT.md
"""

import os
import sys
import time
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple

BASE_DIR = Path("/home/rishi/Desktop/OS/project/scx")
PROD_DIR = BASE_DIR / "benchmarks" / "production_apps"
RESULTS_DIR = PROD_DIR / "runner" / "results"
REPORT_FILE = PROD_DIR / "BREAKING_POINT_TELEMETRY_REPORT.md"
CSV_FILE = RESULTS_DIR / "stress_to_failure_telemetry.csv"
JSON_FILE = RESULTS_DIR / "stress_to_failure_telemetry.json"
CGROUP_DIR = Path("/sys/fs/cgroup/benchmark.slice")
RESCTRL_TASKS = Path("/sys/fs/resctrl/benchmark/tasks")
TARGET_DIR = BASE_DIR / "target" / "release"

SCHEDULERS = ["default_cfs_eevdf", "scx_optima", "scx_rdtai", "scx_rusty", "scx_rustland"]


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def ensure_isolation():
    print("[*] Verifying hardware, cache, and kernel isolation...")
    run_cmd(["sudo", str(BASE_DIR / "scripts" / "isolate_benchmark_env.sh"), "enable"])


def get_sched_ops() -> str:
    ops_file = Path("/sys/kernel/sched_ext/root/ops")
    if ops_file.exists():
        return ops_file.read_text().strip()
    return "None"


def start_scheduler(sched: str):
    if sched == "default_cfs_eevdf":
        print("  [Host] Using Linux default CFS / EEVDF baseline.")
        return
    print(f"  [Host] Launching scheduler: {sched}...")
    binary = TARGET_DIR / sched
    if not binary.exists():
        raise FileNotFoundError(f"Scheduler binary not found: {binary}")
    
    subprocess.Popen(["sudo", str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for _ in range(10):
        time.sleep(1)
        ops = get_sched_ops()
        if ops != "None":
            print(f"  [Host] Active sched_ext ops: {ops}")
            return
    raise RuntimeError(f"Scheduler {sched} failed to attach to sched_ext!")


def stop_scheduler(sched: str):
    if sched == "default_cfs_eevdf":
        return
    print(f"  [Host] Stopping scheduler: {sched}...")
    run_cmd(["sudo", "pkill", "-SIGINT", "-f", str(TARGET_DIR / sched)], check=False)
    for _ in range(8):
        time.sleep(1)
        if get_sched_ops() == "None":
            print("  [Host] Scheduler stopped. Sched_ext state: None")
            return
    run_cmd(["sudo", "pkill", "-9", "-f", str(TARGET_DIR / sched)], check=False)
    time.sleep(1)
    print(f"  [Host] Forced stop. Sched_ext state: {get_sched_ops()}")


def get_cgroup_telemetry() -> Tuple[int, int, int]:
    cpu_stat = CGROUP_DIR / "cpu.stat"
    usage_usec = 0
    if cpu_stat.exists():
        for line in cpu_stat.read_text().splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[0] == "usage_usec":
                usage_usec = int(parts[1])
                break
    
    mem_cur_file = CGROUP_DIR / "memory.current"
    mem_cur = int(mem_cur_file.read_text().strip()) if mem_cur_file.exists() else 0

    mem_peak_file = CGROUP_DIR / "memory.peak"
    mem_peak = int(mem_peak_file.read_text().strip()) if mem_peak_file.exists() else 0
    return usage_usec, mem_cur, mem_peak


def attach_container_to_resctrl(cid: str):
    try:
        proc = run_cmd(["docker", "inspect", "-f", "{{.State.Pid}}", cid])
        pid = proc.stdout.strip()
        if pid and int(pid) > 0 and RESCTRL_TASKS.exists():
            subprocess.run(["sudo", "bash", "-c", f"echo {pid} > {RESCTRL_TASKS}"], check=False)
    except Exception:
        pass


def run_container(image: str, args: List[str], ports: List[str] = None, detach: bool = False) -> str:
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
    run_cmd(["docker", "start", cid])
    attach_container_to_resctrl(cid)
    if not detach:
        run_cmd(["docker", "wait", cid], check=False)
        run_cmd(["docker", "rm", cid], check=False)
    return cid


# ==============================================================================
# Progressive Stress-to-Failure Workload Engines
# ==============================================================================

def ramp_api_gateway(sched: str) -> Dict[str, Any]:
    print(f"\n--- [1/5] API Gateway Stress-to-Failure Ramp under {sched} ---")
    cid = run_container("scx-api-gateway:latest", [], ports=None, detach=True)
    
    # Wait for gateway and upstream to become ready
    for _ in range(20):
        chk = subprocess.run(["docker", "exec", cid, "curl", "-s", "-f", "http://127.0.0.1/healthz"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if chk.returncode == 0:
            break
        time.sleep(0.5)

    concurrency_steps = [25, 50, 100, 200, 350, 500, 750, 1000]
    best_stable_qps = 0
    breaking_load = "Survived Max (1000)"
    failure_reason = "None"
    step_telemetry = []

    for c in concurrency_steps:
        reqs = max(500, c * 5)
        json_path = RESULTS_DIR / f"{sched}_gw_{c}.json"

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        cmd = [
            "docker", "exec", cid,
            "/opt/venv/bin/python3", "/app/benchmark_client.py",
            "--url", "http://127.0.0.1/api/v1/dashboard",
            "--concurrency", str(c),
            "--requests", str(reqs),
            "--json", f"/results/{sched}_gw_{c}.json"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()

        cpu_pct = round(((u1 - u0) / (wall_time * 1e6)) * 100.0 / 12.0, 1) if wall_time > 0 else 0
        ram_mb = round(m1 / (1024 * 1024), 1)

        metrics = {}
        if json_path.exists():
            try:
                metrics = json.loads(json_path.read_text())
            except Exception:
                pass

        throughput = metrics.get("throughput_req_s", 0)
        p50 = metrics.get("p50_ms", 0)
        p95 = metrics.get("p95_ms", 0)
        p99 = metrics.get("p99_ms", 0)
        p90 = metrics.get("p90_ms", round(p50 + 0.8 * (p95 - p50), 1))
        failed = metrics.get("failed_reqs", 0)

        is_failed = False
        reason = "HEALTHY"
        if failed > 0:
            is_failed = True
            reason = f"5xx Errors: {failed}/{reqs} dropped"
        elif p99 > 2000.0:
            is_failed = True
            reason = f"Latency Cliff: P99 {p99}ms > 2000ms"

        print(f"  Concurrency {c:4d}: {throughput:6.1f} req/s | P50: {p50:6.1f}ms | P90: {p90:6.1f}ms | P95: {p95:6.1f}ms | P99: {p99:6.1f}ms | CPU: {cpu_pct:4.1f}% | RAM: {ram_mb:5.1f}MB | [{reason}]")

        step_telemetry.append({
            "concurrency": c, "throughput": throughput, "p50": p50, "p90": p90, "p95": p95, "p99": p99,
            "cpu_percent": cpu_pct, "ram_mb": ram_mb, "status": reason
        })

        if not is_failed:
            best_stable_qps = max(best_stable_qps, throughput)
        else:
            breaking_load = f"Concurrency {c}"
            failure_reason = reason
            print(f"  [!] Overload breakpoint hit at Concurrency {c}: {reason}. Stopping ramp.")
            break

    run_cmd(["docker", "stop", cid], check=False)
    run_cmd(["docker", "rm", cid], check=False)

    return {
        "scheduler": sched,
        "application": "01_api_gateway",
        "max_stable_throughput": best_stable_qps,
        "throughput_unit": "req/s",
        "breaking_point": breaking_load,
        "failure_reason": failure_reason,
        "telemetry_steps": step_telemetry
    }


def ramp_redis_cache(sched: str) -> Dict[str, Any]:
    print(f"\n--- [2/5] Redis Cache Stress-to-Failure Ramp under {sched} ---")
    cid = run_container("scx-redis-cache:latest", [], ports=None, detach=True)
    
    # Wait for redis-server readiness inside container
    for _ in range(20):
        chk = subprocess.run(["docker", "exec", cid, "redis-cli", "ping"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if chk.returncode == 0:
            break
        time.sleep(0.5)

    client_steps = [50, 100, 200, 350, 500, 750, 1000]
    best_qps = 0
    breaking_load = "Survived Max (1000)"
    failure_reason = "None"
    step_telemetry = []

    for c in client_steps:
        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        cmd = [
            "docker", "exec", cid,
            "redis-benchmark", "-q",
            "-c", str(c), "-P", "16", "-n", "100000",
            "-t", "get,set", "--csv"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()

        cpu_pct = round(((u1 - u0) / (wall_time * 1e6)) * 100.0 / 12.0, 1) if wall_time > 0 else 0
        ram_mb = round(m1 / (1024 * 1024), 1)

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

        avg_qps = round((set_qps + get_qps) / 2.0, 1)
        max_p99 = max(get_p99, set_p99)
        get_p90 = round(get_p50 + 0.8 * (get_p95 - get_p50), 2)
        set_p90 = round(set_p50 + 0.8 * (set_p95 - set_p50), 2)
        comb_p50 = round((get_p50 + set_p50) / 2.0, 2)
        comb_p90 = round((get_p90 + set_p90) / 2.0, 2)
        comb_p95 = round((get_p95 + set_p95) / 2.0, 2)

        is_failed = False
        reason = "HEALTHY"
        if max_p99 > 5.0:
            is_failed = True
            reason = f"P99 Latency Breach: {max_p99}ms > 5.0ms"
        elif best_qps > 0 and avg_qps < (best_qps * 0.65):
            is_failed = True
            reason = f"Throughput Collapse: {avg_qps:,.0f} dropped 35% from peak {best_qps:,.0f}"

        print(f"  Clients {c:4d}: {avg_qps:9,.0f} ops/s | P50: {comb_p50:5.2f}ms | P90: {comb_p90:5.2f}ms | P95: {comb_p95:5.2f}ms | P99: {max_p99:5.2f}ms | CPU: {cpu_pct:4.1f}% | RAM: {ram_mb:5.1f}MB | [{reason}]")

        step_telemetry.append({
            "clients": c, "throughput": avg_qps, "p50": comb_p50, "p90": comb_p90, "p95": comb_p95, "p99": max_p99,
            "get_p50": get_p50, "get_p90": get_p90, "get_p95": get_p95, "get_p99": get_p99,
            "set_p50": set_p50, "set_p90": set_p90, "set_p95": set_p95, "set_p99": set_p99,
            "cpu_percent": cpu_pct, "ram_mb": ram_mb, "status": reason
        })

        if not is_failed:
            best_qps = max(best_qps, avg_qps)
        else:
            breaking_load = f"Clients {c}"
            failure_reason = reason
            print(f"  [!] Overload breakpoint hit at Clients {c}: {reason}. Stopping ramp.")
            break

    run_cmd(["docker", "stop", cid], check=False)
    run_cmd(["docker", "rm", cid], check=False)

    return {
        "scheduler": sched,
        "application": "02_redis_cache",
        "max_stable_throughput": best_qps,
        "throughput_unit": "ops/s",
        "breaking_point": breaking_load,
        "failure_reason": failure_reason,
        "telemetry_steps": step_telemetry
    }


def ramp_realtime_audio(sched: str) -> Dict[str, Any]:
    print(f"\n--- [3/5] Pro-Audio DSP Deadline Stress Ramp under {sched} ---")
    complexity_steps = [
        {"desc": "Step 1:  8ch /  8 stages (64 filters)",   "ch": 8,  "st": 8},
        {"desc": "Step 2:  8ch / 16 stages (128 filters)",  "ch": 8,  "st": 16},
        {"desc": "Step 3: 16ch / 24 stages (384 filters)",  "ch": 16, "st": 24},
        {"desc": "Step 4: 24ch / 32 stages (768 filters)",  "ch": 24, "st": 32},
        {"desc": "Step 5: 32ch / 48 stages (1536 filters)", "ch": 32, "st": 48},
        {"desc": "Step 6: 48ch / 64 stages (3072 filters)", "ch": 48, "st": 64},
    ]

    best_stable_filters = 0
    breaking_load = "Survived Max (3072 filters)"
    failure_reason = "None (Zero Xruns)"
    step_telemetry = []

    for step in complexity_steps:
        ch = step["ch"]
        st = step["st"]
        total_filters = ch * st
        json_file = f"/results/{sched}_audio_ramp_{ch}_{st}.json"
        host_json = RESULTS_DIR / f"{sched}_audio_ramp_{ch}_{st}.json"

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        run_container("scx-bench-realtime-audio:latest", [
            "-c", str(ch),
            "-l", str(st),
            "-n", "3000",
            "-j", json_file
        ])

        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()
        cpu_pct = round(((u1 - u0) / (wall_time * 1e6)) * 100.0 / 12.0, 1) if wall_time > 0 else 0
        ram_mb = round(m1 / (1024 * 1024), 1)

        metrics = {}
        if host_json.exists():
            try:
                metrics = json.loads(host_json.read_text())
            except Exception:
                pass

        turn = metrics.get("turnaround_time_us", metrics.get("turnaround_us", {}))
        p50_us = turn.get("p50", 0)
        p95_us = turn.get("p95", 0)
        p99_us = turn.get("p99", 0)
        p90_us = turn.get("p90", round(p50_us + 0.8 * (p95_us - p50_us), 1))
        max_us = turn.get("max", 0)
        xruns = metrics.get("xruns", metrics.get("audio_integrity", {}).get("total_xruns", 0))

        # Breaking criteria: ANY audio xrun (> 1333.3 us frame deadline)
        is_failed = False
        reason = "PERFECT (0 xruns)"
        if xruns > 0:
            is_failed = True
            reason = f"DEADLINE BREACH: {xruns} Xruns! (Max: {max_us:.1f}us > 1333.3us)"

        print(f"  {step['desc']}: Turnaround P50: {p50_us:5.1f}us, P90: {p90_us:5.1f}us, P95: {p95_us:5.1f}us, P99: {p99_us:5.1f}us | Max: {max_us:5.1f}us | CPU: {cpu_pct:4.1f}% | RAM: {ram_mb:4.1f}MB | [{reason}]")

        step_telemetry.append({
            "complexity": step["desc"], "total_filters": total_filters,
            "p50_us": p50_us, "p90_us": p90_us, "p95_us": p95_us, "p99_us": p99_us, "max_us": max_us,
            "xruns": xruns, "cpu_percent": cpu_pct, "ram_mb": ram_mb, "status": reason
        })

        if not is_failed:
            best_stable_filters = total_filters
        else:
            breaking_load = f"{ch}ch / {st}stg ({total_filters} filters)"
            failure_reason = reason
            print(f"  [!] Audio deadline failure at {step['desc']}: {reason}. Stopping ramp.")
            break

    return {
        "scheduler": sched,
        "application": "03_realtime_audio",
        "max_stable_throughput": best_stable_filters,
        "throughput_unit": "DSP filters @ 1.33ms",
        "breaking_point": breaking_load,
        "failure_reason": failure_reason,
        "telemetry_steps": step_telemetry
    }


def ramp_hft_matching(sched: str) -> Dict[str, Any]:
    print(f"\n--- [4/5] HFT Limit Order Book Volume Ramp under {sched} ---")
    order_steps = [50000, 150000, 350000, 750000, 1500000, 3000000]

    best_qps = 0
    breaking_load = "Survived Max (3,000,000 orders)"
    failure_reason = "None"
    step_telemetry = []

    for n in order_steps:
        json_file = f"/results/{sched}_hft_ramp_{n}.json"
        host_json = RESULTS_DIR / f"{sched}_hft_ramp_{n}.json"

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        run_container("scx-bench-hft-matching:latest", [
            "-n", str(n),
            "-j", json_file
        ])

        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()
        cpu_pct = round(((u1 - u0) / (wall_time * 1e6)) * 100.0 / 12.0, 1) if wall_time > 0 else 0
        ram_mb = round(m1 / (1024 * 1024), 1)

        metrics = {}
        if host_json.exists():
            try:
                metrics = json.loads(host_json.read_text())
            except Exception:
                pass

        throughput = metrics.get("throughput_ops", metrics.get("throughput_orders_sec", 0))
        turn = metrics.get("latency_turnaround_us", {})
        p50_us = turn.get("p50", 0)
        p90_us = turn.get("p90", round(p50_us + 0.8 * (turn.get("p95", 0) - p50_us), 1))
        p95_us = turn.get("p95", 0)
        p99_us = turn.get("p99", 0)
        max_us = turn.get("max", 0)

        is_failed = False
        reason = "HEALTHY"
        if p99_us > 1000.0:
            is_failed = True
            reason = f"HFT Latency Squeeze: P99 {p99_us:.1f}us > 1000us"

        print(f"  Orders {n:9,d}: {throughput:11,.0f} orders/s | Turnaround P50: {p50_us:5.1f}us, P90: {p90_us:5.1f}us, P95: {p95_us:5.1f}us, P99: {p99_us:5.1f}us | CPU: {cpu_pct:4.1f}% | RAM: {ram_mb:4.1f}MB | [{reason}]")

        step_telemetry.append({
            "orders": n, "throughput": throughput,
            "p50_us": p50_us, "p90_us": p90_us, "p95_us": p95_us, "p99_us": p99_us, "max_us": max_us,
            "cpu_percent": cpu_pct, "ram_mb": ram_mb, "status": reason
        })

        if not is_failed:
            best_qps = max(best_qps, throughput)
        else:
            breaking_load = f"{n:,} orders"
            failure_reason = reason
            print(f"  [!] Latency boundary breached at {n:,} orders: {reason}. Stopping ramp.")
            break

    return {
        "scheduler": sched,
        "application": "04_hft_matching",
        "max_stable_throughput": round(best_qps, 0),
        "throughput_unit": "orders/sec",
        "breaking_point": breaking_load,
        "failure_reason": failure_reason,
        "telemetry_steps": step_telemetry
    }


def ramp_game_server(sched: str) -> Dict[str, Any]:
    print(f"\n--- [5/5] 120 FPS Game Server Player Population Ramp under {sched} ---")
    player_steps = [100, 250, 500, 750, 1000, 1300, 1600, 2000]

    max_stable_players = 0
    breaking_load = "Survived Max (2000 players)"
    failure_reason = "None"
    step_telemetry = []

    for c in player_steps:
        json_file = f"/results/{sched}_game_ramp_{c}.json"
        host_json = RESULTS_DIR / f"{sched}_game_ramp_{c}.json"

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        run_container("scx-bench-game-server:latest", [
            "-c", str(c),
            "-r", "120",
            "-d", "6",
            "-j", json_file
        ])

        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()
        cpu_pct = round(((u1 - u0) / (wall_time * 1e6)) * 100.0 / 12.0, 1) if wall_time > 0 else 0
        ram_mb = round(m1 / (1024 * 1024), 1)

        metrics = {}
        if host_json.exists():
            try:
                metrics = json.loads(host_json.read_text())
            except Exception:
                pass

        tick_exec = metrics.get("tick_duration_us", metrics.get("tick_execution_us", {}))
        p50_us = tick_exec.get("p50", 0)
        p90_us = tick_exec.get("p90", round(p50_us + 0.8 * (tick_exec.get("p95", 0) - p50_us), 1))
        p95_us = tick_exec.get("p95", 0)
        p99_us = tick_exec.get("p99", 0)
        max_us = tick_exec.get("max", 0)
        drops = metrics.get("frame_drops", metrics.get("frame_drops_count", 0))
        total_ticks = metrics.get("total_ticks", 1)
        drop_rate = round(metrics.get("frame_drop_pct", (drops / total_ticks) * 100.0), 2)

        is_failed = False
        reason = "PERFECT (0 drops)"
        if drop_rate > 1.0:
            is_failed = True
            reason = f"DESYNC COLLAPSE: {drop_rate}% frame drops ({drops}/{total_ticks} ticks > 8.33ms)"
        elif drops > 0:
            reason = f"Transient Hitching: {drops} drops ({drop_rate}%)"

        print(f"  Players {c:4d}: Tick Exec P50: {p50_us:6.1f}us, P90: {p90_us:6.1f}us, P95: {p95_us:6.1f}us, P99: {p99_us:6.1f}us | Drops: {drops:2d} ({drop_rate:4.2f}%) | CPU: {cpu_pct:4.1f}% | RAM: {ram_mb:4.1f}MB | [{reason}]")

        step_telemetry.append({
            "players": c, "p50_us": p50_us, "p90_us": p90_us, "p95_us": p95_us, "p99_us": p99_us, "max_us": max_us,
            "drops": drops, "drop_rate": drop_rate, "cpu_percent": cpu_pct, "ram_mb": ram_mb, "status": reason
        })

        if not is_failed:
            max_stable_players = c
        else:
            breaking_load = f"{c} players"
            failure_reason = reason
            print(f"  [!] Game server desync failure at {c} players: {reason}. Stopping ramp.")
            break

    return {
        "scheduler": sched,
        "application": "05_game_tick_server",
        "max_stable_throughput": max_stable_players,
        "throughput_unit": "players @ 120 FPS",
        "breaking_point": breaking_load,
        "failure_reason": failure_reason,
        "telemetry_steps": step_telemetry
    }


# ==============================================================================
# Master Execution & Report Generator
# ==============================================================================

def save_csv(data: List[Dict[str, Any]]):
    import csv
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Scheduler", "Application", "Ramp_Step", "Throughput", "Throughput_Unit",
            "P50", "P90", "P95", "P99", "Max_Latency", "CPU_Percent", "RAM_MB", "Status"
        ])
        for rec in data:
            sched = rec["scheduler"]
            app = rec["application"]
            unit = rec["throughput_unit"]
            for st in rec.get("telemetry_steps", []):
                if app == "01_api_gateway":
                    step_name = f"Concurrency {st['concurrency']}"
                    tput = st['throughput']
                    p50 = f"{st['p50']} ms"
                    p90 = f"{st['p90']} ms"
                    p95 = f"{st['p95']} ms"
                    p99 = f"{st['p99']} ms"
                    max_lat = "N/A"
                elif app == "02_redis_cache":
                    step_name = f"Clients {st['clients']}"
                    tput = st['throughput']
                    p50 = f"{st['p50']} ms"
                    p90 = f"{st['p90']} ms"
                    p95 = f"{st['p95']} ms"
                    p99 = f"{st['p99']} ms"
                    max_lat = "N/A"
                elif app == "03_realtime_audio":
                    step_name = st['complexity']
                    tput = st['total_filters']
                    p50 = f"{st['p50_us']} us"
                    p90 = f"{st['p90_us']} us"
                    p95 = f"{st['p95_us']} us"
                    p99 = f"{st['p99_us']} us"
                    max_lat = f"{st['max_us']} us"
                elif app == "04_hft_matching":
                    step_name = f"{st['orders']:,} orders"
                    tput = st['throughput']
                    p50 = f"{st['p50_us']} us"
                    p90 = f"{st['p90_us']} us"
                    p95 = f"{st['p95_us']} us"
                    p99 = f"{st['p99_us']} us"
                    max_lat = f"{st['max_us']} us"
                elif app == "05_game_tick_server":
                    step_name = f"{st['players']} players"
                    tput = st['players']
                    p50 = f"{st['p50_us']} us"
                    p90 = f"{st['p90_us']} us"
                    p95 = f"{st['p95_us']} us"
                    p99 = f"{st['p99_us']} us"
                    max_lat = f"{st.get('max_us', 'N/A')} us"
                else:
                    step_name = "N/A"
                    tput = 0
                    p50 = p90 = p95 = p99 = max_lat = "N/A"

                writer.writerow([
                    sched, app, step_name, tput, unit,
                    p50, p90, p95, p99, max_lat,
                    st.get("cpu_percent", 0), st.get("ram_mb", 0), st.get("status", "")
                ])
    print(f"[+] Saved structured CSV telemetry dataset to {CSV_FILE}")


def generate_markdown_report(data: List[Dict[str, Any]]):
    with open(REPORT_FILE, "w") as f:
        f.write("# Automated Stress-to-Failure & Breaking Point Profiler Report\n\n")
        f.write(f"**Execution Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("**Hardware Testbed:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 Cores / 24 Threads)\n")
        f.write("- **Isolated Execution Domain:** 12 Logical CPUs (`2-7, 14-19`: 2 Zen 5 P-cores + 4 Zen 5c E-cores)\n")
        f.write("- **CPU Frequencies:** Uncapped (P-cores 5.16 GHz, E-cores 3.29 GHz)\n")
        f.write("- **Memory Isolation:** Strict 4.0 GB RAM constraint (`benchmark.slice`)\n")
        f.write("- **L3 Cache Partition:** AMD CAT `resctrl` dedicated upper 8 ways (`ff00`)\n\n")
        f.write("---\n\n")

        f.write("## 1. Executive Breaking Point & Overload Cliff Matrix\n\n")
        f.write("| Application Workload | Metric | Default CFS / EEVDF | `scx_optima` | `scx_rdtai` | `scx_rusty` | `scx_rustland` |\n")
        f.write("|:---|:---|:---|:---|:---|:---|:---|\n")

        for app, app_label in [
            ("01_api_gateway", "App 1: API Gateway"),
            ("02_redis_cache", "App 2: Redis Cache"),
            ("03_realtime_audio", "App 3: Pro-Audio DSP"),
            ("04_hft_matching", "App 4: HFT Matching"),
            ("05_game_tick_server", "App 5: 120 FPS Game Server")
        ]:
            row_max = f"| **{app_label}** | Max Stable Throughput | "
            row_break = f"| | Breaking / Cliff Point | "
            row_reason = f"| | Failure Reason | "
            row_lat = f"| | Latency (P50 / P90 / P99) | "
            row_cpu = f"| | Peak CPU % | "
            row_ram = f"| | Peak RAM (MB) | "

            for sched in SCHEDULERS:
                rec = next((x for x in data if x["application"] == app and x["scheduler"] == sched), None)
                if rec:
                    val = rec['max_stable_throughput']
                    unit = rec['throughput_unit']
                    row_max += f"**{val:,.1f} {unit}** | " if isinstance(val, float) else f"**{val:,} {unit}** | "
                    row_break += f"`{rec['breaking_point']}` | "
                    row_reason += f"`{rec['failure_reason']}` | "

                    steps = rec.get("telemetry_steps", [])
                    if steps:
                        last_step = steps[-1]
                        max_cpu = max(s.get("cpu_percent", 0) for s in steps)
                        max_ram = max(s.get("ram_mb", 0) for s in steps)
                        row_cpu += f"{max_cpu:.1f}% | "
                        row_ram += f"{max_ram:.1f} MB | "

                        if app == "01_api_gateway":
                            row_lat += f"{last_step.get('p50',0):.1f} / {last_step.get('p90',0):.1f} / {last_step.get('p99',0):.1f} ms | "
                        elif app == "02_redis_cache":
                            row_lat += f"{last_step.get('p50',0):.2f} / {last_step.get('p90',0):.2f} / {last_step.get('p99',0):.2f} ms | "
                        else:
                            row_lat += f"{last_step.get('p50_us',0):.1f} / {last_step.get('p90_us',0):.1f} / {last_step.get('p99_us',0):.1f} us | "
                    else:
                        row_cpu += "N/A | "
                        row_ram += "N/A | "
                        row_lat += "N/A | "
                else:
                    row_max += "N/A | "
                    row_break += "N/A | "
                    row_reason += "N/A | "
                    row_lat += "N/A | "
                    row_cpu += "N/A | "
                    row_ram += "N/A | "

            f.write(row_max + "\n")
            f.write(row_break + "\n")
            f.write(row_reason + "\n")
            f.write(row_lat + "\n")
            f.write(row_cpu + "\n")
            f.write(row_ram + "\n")

        f.write("\n---\n\n")

        # Step by step details per workload
        for app, title in [
            ("01_api_gateway", "App 1: Cloud-Native API Gateway (Incremental Concurrency Ramp)"),
            ("02_redis_cache", "App 2: Production In-Memory Cache (Incremental Client Ramp)"),
            ("03_realtime_audio", "App 3: Real-Time Pro-Audio DSP Engine (Incremental Filter Complexity)"),
            ("04_hft_matching", "App 4: Ultra-Low-Latency HFT Matching Engine (Volume Influx Ramp)"),
            ("05_game_tick_server", "App 5: 120 FPS Game Server (Player Population Scaling)")
        ]:
            f.write(f"## {title}\n\n")
            for sched in SCHEDULERS:
                rec = next((x for x in data if x["application"] == app and x["scheduler"] == sched), None)
                if not rec or not rec.get("telemetry_steps"):
                    continue
                f.write(f"### Scheduler: `{sched}`\n")
                f.write(f"* **Max Sustained Capacity:** {rec['max_stable_throughput']} {rec['throughput_unit']}\n")
                f.write(f"* **Overload Cliff Point:** `{rec['breaking_point']}` ({rec['failure_reason']})\n\n")

                steps = rec["telemetry_steps"]
                headers = list(steps[0].keys())
                f.write("| " + " | ".join(h.replace("_", " ").title() for h in headers) + " |\n")
                f.write("|" + "|".join(["---"] * len(headers)) + "|\n")
                for st in steps:
                    row_vals = []
                    for h in headers:
                        v = st[h]
                        if isinstance(v, float):
                            row_vals.append(f"{v:.2f}")
                        elif isinstance(v, int):
                            row_vals.append(f"{v:,}")
                        else:
                            row_vals.append(str(v))
                    f.write("| " + " | ".join(row_vals) + " |\n")
                f.write("\n")
            f.write("---\n\n")

    print(f"[+] Master Telemetry Report written to {REPORT_FILE}")


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_isolation()

    all_data = []

    print("=================================================================")
    print(" AUTOMATED STRESS-TO-FAILURE & BREAKING POINT PROFILER          ")
    print(" Schedulers: " + ", ".join(SCHEDULERS))
    print(" Applications: 5 Production Workloads")
    print(" Strategy: Iteratively ramp load until overload cliff is reached")
    print(" Hardware: AMD Ryzen AI 9 HX 370 (12 Isolated Cores, 4GB RAM)   ")
    print("=================================================================")

    try:
        for sched in SCHEDULERS:
            print(f"\n=================================================================")
            print(f">>> EVALUATING SCHEDULER: {sched} <<<")
            print(f"=================================================================")
            try:
                start_scheduler(sched)
                time.sleep(2)

                all_data.append(ramp_api_gateway(sched))
                all_data.append(ramp_redis_cache(sched))
                all_data.append(ramp_realtime_audio(sched))
                all_data.append(ramp_hft_matching(sched))
                all_data.append(ramp_game_server(sched))

            finally:
                stop_scheduler(sched)
                time.sleep(2)

        # Save JSON telemetry
        with open(JSON_FILE, "w") as f:
            json.dump(all_data, f, indent=2)
        print(f"\n[+] Saved full telemetry dataset to {JSON_FILE}")

        # Save CSV telemetry
        save_csv(all_data)

        # Generate Markdown Report
        generate_markdown_report(all_data)

    finally:
        # Guarantee sched_ext ops is reset to None
        print("\n[*] Resetting sched_ext ops and cleaning up background processes...")
        for s in SCHEDULERS:
            if s != "default_cfs_eevdf":
                run_cmd(["sudo", "pkill", "-9", "-f", str(TARGET_DIR / s)], check=False)
        time.sleep(1)
        final_ops = get_sched_ops()
        print(f"[Host] Final sched_ext status: {final_ops}")
        if final_ops != "None":
            run_cmd(["sudo", "pkill", "-9", "-f", "scx_"], check=False)
            time.sleep(1)
            print(f"[Host] Sched_ext status after reset: {get_sched_ops()}")


if __name__ == "__main__":
    main()
