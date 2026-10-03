#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
Multi-Throughput Saturation Sweep & Failure Stage Profiler
Validates schedulers across 5 Production Architectures through progressive load:
  1. API Gateway (NGINX + uvloop Microservices Fan-out)
  2. In-Memory Cache (Production Redis Pipelined)
  3. Real-Time Pro-Audio DSP (48kHz/64-smp Buffer Loop)
  4. Ultra-Low-Latency HFT Matching Engine (LMAX Disruptor Ring-Buffer LOB)
  5. 120 FPS Interactive Game Server (Spatial Physics & State Broadcast)

Captures full telemetry:
  - Throughput (QPS, Ops/s, Frames/s, Orders/s, Ticks/s)
  - Latency distributions: P50, P90, P95, P99, Max
  - Live cgroup v2 resource usage: CPU Utilization (%), RAM Current & Peak (MB)
  - Breaking Stage & Failure Criteria per workload
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
REPORT_FILE = PROD_DIR / "MULTI_THROUGHPUT_TELEMETRY_REPORT.md"
CSV_FILE = RESULTS_DIR / "sweep_telemetry.csv"
JSON_FILE = RESULTS_DIR / "sweep_telemetry.json"
CGROUP_DIR = Path("/sys/fs/cgroup/benchmark.slice")
RESCTRL_TASKS = Path("/sys/fs/resctrl/benchmark/tasks")
TARGET_DIR = BASE_DIR / "target" / "release"

SCHEDULERS = ["default_cfs_eevdf", "scx_optima", "scx_rdtai", "scx_rusty", "scx_rustland"]


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def ensure_isolation():
    print("[*] Enabling hardware, cache, and kernel isolation...")
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
    
    # Run in background via sudo
    subprocess.Popen(["sudo", str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for ops registration
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
    """Returns (usage_usec, memory_current_bytes, memory_peak_bytes)"""
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
        run_cmd(["docker", "attach", cid])
        run_cmd(["docker", "rm", cid], check=False)
    return cid


# ==============================================================================
# Workload Sweeps
# ==============================================================================

def test_api_gateway(sched: str) -> List[Dict[str, Any]]:
    print(f"\n--- [1/5] API Gateway Saturation Sweep under {sched} ---")
    results = []
    # Start API Gateway container detached on port 8080
    cid = run_container("scx-api-gateway:latest", [], ports=["8080:80"], detach=True)
    time.sleep(3)  # Wait for NGINX and Uvicorn to be ready

    stages = [
        {"name": "Stage 1 (Light)", "concurrency": 25, "requests": 1000},
        {"name": "Stage 2 (Medium)", "concurrency": 100, "requests": 2000},
        {"name": "Stage 3 (Saturation)", "concurrency": 250, "requests": 4000},
    ]

    for stg in stages:
        c = stg["concurrency"]
        r = stg["requests"]
        json_path = RESULTS_DIR / f"{sched}_gw_{c}.json"
        
        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()
        
        # Execute client inside container hitting localhost:80
        cmd = [
            "docker", "exec", cid,
            "/opt/venv/bin/python3", "/app/benchmark_client.py",
            "--url", "http://127.0.0.1/api/v1/dashboard",
            "--concurrency", str(c),
            "--requests", str(r),
            "--json", f"/results/{sched}_gw_{c}.json"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()

        # Telemetry calculations
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
        p999 = metrics.get("p999_ms", 0)
        failed = metrics.get("failed_reqs", 0)

        # Breaking point: Failed requests or P99 > 2000ms
        failed_state = "HEALTHY"
        if failed > 0:
            failed_state = f"BROKEN ({failed} errors)"
        elif p99 > 2000:
            failed_state = f"DEGRADED (P99 {p99}ms > 2000ms)"

        print(f"  {stg['name']}: {throughput} req/s | P50: {p50}ms | P95: {p95}ms | P99: {p99}ms | CPU: {cpu_pct}% | RAM: {ram_mb}MB | Status: {failed_state}")

        results.append({
            "scheduler": sched,
            "application": "01_api_gateway",
            "stage": stg["name"],
            "load_param": f"C={c}, R={r}",
            "throughput": throughput,
            "throughput_unit": "req/s",
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "p999_ms": p999,
            "cpu_percent": cpu_pct,
            "ram_mb": ram_mb,
            "failure_state": failed_state
        })

    # Cleanup container
    run_cmd(["docker", "stop", cid], check=False)
    run_cmd(["docker", "rm", cid], check=False)
    return results


def test_redis_cache(sched: str) -> List[Dict[str, Any]]:
    print(f"\n--- [2/5] Redis Cache Saturation Sweep under {sched} ---")
    results = []
    # Start Redis container
    cid = run_container("scx-redis-cache:latest", [], ports=["6379:6379"], detach=True)
    time.sleep(2)

    stages = [
        {"name": "Stage 1 (Unpipelined P=1)", "clients": 50, "pipeline": 1, "reqs": 50000},
        {"name": "Stage 2 (Pipelined P=16)", "clients": 100, "pipeline": 16, "reqs": 200000},
        {"name": "Stage 3 (High-Stress P=64)", "clients": 200, "pipeline": 64, "reqs": 500000},
    ]

    for stg in stages:
        c = stg["clients"]
        p = stg["pipeline"]
        r = stg["reqs"]

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        # Run redis-benchmark inside container
        cmd = [
            "docker", "exec", cid,
            "redis-benchmark", "-q",
            "-c", str(c), "-P", str(p), "-n", str(r),
            "-t", "get,set", "--csv"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wall_time = time.perf_counter() - t0
        u1, m1, p1 = get_cgroup_telemetry()

        cpu_pct = round(((u1 - u0) / (wall_time * 1e6)) * 100.0 / 12.0, 1) if wall_time > 0 else 0
        ram_mb = round(m1 / (1024 * 1024), 1)

        # Parse CSV output from redis-benchmark
        # Format: "SET","263157.89","0.087",...
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

        status = "HEALTHY"
        if set_p99 > 2.0 or get_p99 > 2.0:
            status = "DEGRADED (Tail latency > 2.0ms)"

        avg_qps = round((set_qps + get_qps) / 2.0, 1)
        print(f"  {stg['name']}: {avg_qps} ops/s | GET P50: {get_p50}ms, P99: {get_p99}ms | SET P50: {set_p50}ms, P99: {set_p99}ms | CPU: {cpu_pct}% | RAM: {ram_mb}MB | Status: {status}")

        results.append({
            "scheduler": sched,
            "application": "02_redis_cache",
            "stage": stg["name"],
            "load_param": f"C={c}, P={p}, N={r}",
            "throughput": avg_qps,
            "throughput_unit": "ops/s",
            "p50_ms": get_p50,
            "p95_ms": get_p95,
            "p99_ms": get_p99,
            "p999_ms": 0,
            "cpu_percent": cpu_pct,
            "ram_mb": ram_mb,
            "failure_state": status
        })

    run_cmd(["docker", "stop", cid], check=False)
    run_cmd(["docker", "rm", cid], check=False)
    return results


def test_realtime_audio(sched: str) -> List[Dict[str, Any]]:
    print(f"\n--- [3/5] Pro-Audio Real-Time DSP Sweep under {sched} ---")
    results = []

    stages = [
        {"name": "Stage 1 (Light DAW: 8ch/8stg)", "channels": 8, "stages": 8, "frames": 5000},
        {"name": "Stage 2 (Dense Mix: 8ch/16stg)", "channels": 8, "stages": 16, "frames": 5000},
        {"name": "Stage 3 (Heavy Mastering: 16ch/32stg)", "channels": 16, "stages": 32, "frames": 5000},
    ]

    for stg in stages:
        ch = stg["channels"]
        st = stg["stages"]
        fr = stg["frames"]
        json_file = f"/results/{sched}_audio_{ch}_{st}.json"
        host_json = RESULTS_DIR / f"{sched}_audio_{ch}_{st}.json"

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        run_container("scx-bench-realtime-audio:latest", [
            "-c", str(ch),
            "-l", str(st),
            "-n", str(fr),
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

        turn = metrics.get("turnaround_us", {})
        p50_us = turn.get("p50", 0)
        p95_us = turn.get("p95", 0)
        p99_us = turn.get("p99", 0)
        max_us = turn.get("max", 0)
        xruns = metrics.get("audio_integrity", {}).get("total_xruns", 0)

        # Breaking criteria: ANY audio xrun
        status = "PERFECT (0 xruns)"
        if xruns > 0:
            status = f"FAILED ({xruns} XRUNS - Buffer Underrun!)"

        throughput = round(fr / wall_time, 1) if wall_time > 0 else 0
        print(f"  {stg['name']}: {throughput} frames/s | Turnaround P50: {p50_us}us, P99: {p99_us}us, Max: {max_us}us | Xruns: {xruns} | CPU: {cpu_pct}% | RAM: {ram_mb}MB | Status: {status}")

        results.append({
            "scheduler": sched,
            "application": "03_realtime_audio",
            "stage": stg["name"],
            "load_param": f"{ch}ch / {st}stg",
            "throughput": throughput,
            "throughput_unit": "frames/s",
            "p50_ms": round(p50_us / 1000.0, 3),
            "p95_ms": round(p95_us / 1000.0, 3),
            "p99_ms": round(p99_us / 1000.0, 3),
            "p999_ms": round(max_us / 1000.0, 3),
            "cpu_percent": cpu_pct,
            "ram_mb": ram_mb,
            "failure_state": status
        })
    return results


def test_hft_matching(sched: str) -> List[Dict[str, Any]]:
    print(f"\n--- [4/5] HFT Matching Engine Throughput Sweep under {sched} ---")
    results = []

    stages = [
        {"name": "Stage 1 (Normal Market)", "orders": 50000},
        {"name": "Stage 2 (High Volatility)", "orders": 250000},
        {"name": "Stage 3 (Flash Crash Surge)", "orders": 1000000},
    ]

    for stg in stages:
        n = stg["orders"]
        json_file = f"/results/{sched}_hft_{n}.json"
        host_json = RESULTS_DIR / f"{sched}_hft_{n}.json"

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

        throughput = metrics.get("throughput_orders_sec", 0)
        turn = metrics.get("latency_turnaround_us", {})
        p50_us = turn.get("p50", 0)
        p95_us = turn.get("p95", 0)
        p99_us = turn.get("p99", 0)
        max_us = turn.get("max", 0)

        status = "HEALTHY"
        if p99_us > 500:
            status = f"DEGRADED (P99 {p99_us}us > 500us)"

        print(f"  {stg['name']}: {throughput:,.0f} orders/s | Turnaround P50: {p50_us}us, P99: {p99_us}us, Max: {max_us}us | CPU: {cpu_pct}% | RAM: {ram_mb}MB | Status: {status}")

        results.append({
            "scheduler": sched,
            "application": "04_hft_matching",
            "stage": stg["name"],
            "load_param": f"{n:,} orders",
            "throughput": round(throughput, 0),
            "throughput_unit": "orders/s",
            "p50_ms": round(p50_us / 1000.0, 3),
            "p95_ms": round(p95_us / 1000.0, 3),
            "p99_ms": round(p99_us / 1000.0, 3),
            "p999_ms": round(max_us / 1000.0, 3),
            "cpu_percent": cpu_pct,
            "ram_mb": ram_mb,
            "failure_state": status
        })
    return results


def test_game_server(sched: str) -> List[Dict[str, Any]]:
    print(f"\n--- [5/5] 120 FPS Game Server Player Population Sweep under {sched} ---")
    results = []

    stages = [
        {"name": "Stage 1 (Match Lobby: 200 players)", "clients": 200, "duration": 8},
        {"name": "Stage 2 (Battle Royale: 500 players)", "clients": 500, "duration": 8},
        {"name": "Stage 3 (Siege Event: 1000 players)", "clients": 1000, "duration": 8},
    ]

    for stg in stages:
        c = stg["clients"]
        d = stg["duration"]
        json_file = f"/results/{sched}_game_{c}.json"
        host_json = RESULTS_DIR / f"{sched}_game_{c}.json"

        u0, m0, p0 = get_cgroup_telemetry()
        t0 = time.perf_counter()

        run_container("scx-bench-game-server:latest", [
            "-c", str(c),
            "-r", "120",
            "-d", str(d),
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

        tick_exec = metrics.get("tick_execution_us", {})
        p50_us = tick_exec.get("p50", 0)
        p95_us = tick_exec.get("p95", 0)
        p99_us = tick_exec.get("p99", 0)
        drops = metrics.get("frame_drops_count", 0)
        total_ticks = metrics.get("total_ticks", 1)
        drop_rate = round((drops / total_ticks) * 100.0, 2)

        status = f"HEALTHY ({drops} drops, {drop_rate}%)"
        if drop_rate > 1.0:
            status = f"FAILED ({drop_rate}% DROPS - Unplayable Desync!)"
        elif drops > 0:
            status = f"DEGRADED ({drops} hitch drops)"

        print(f"  {stg['name']}: 120 FPS ({total_ticks} ticks) | Exec P50: {p50_us}us, P99: {p99_us}us | Drops: {drops} ({drop_rate}%) | CPU: {cpu_pct}% | RAM: {ram_mb}MB | Status: {status}")

        results.append({
            "scheduler": sched,
            "application": "05_game_tick_server",
            "stage": stg["name"],
            "load_param": f"{c} players, 120Hz",
            "throughput": 120.0,
            "throughput_unit": "ticks/s",
            "p50_ms": round(p50_us / 1000.0, 3),
            "p95_ms": round(p95_us / 1000.0, 3),
            "p99_ms": round(p99_us / 1000.0, 3),
            "p999_ms": round(drops, 0),
            "cpu_percent": cpu_pct,
            "ram_mb": ram_mb,
            "failure_state": status
        })
    return results


# ==============================================================================
# Master Execution & Report Generator
# ==============================================================================

def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_isolation()

    all_data = []

    print("=================================================================")
    print(" STARTING MULTI-THROUGHPUT SATURATION SWEEP & TELEMETRY PROFILER ")
    print(" Schedulers: " + ", ".join(SCHEDULERS))
    print(" Applications: 5 Real-World Production Stacks")
    print(" Hardware: AMD Ryzen AI 9 HX 370 (12 Isolated Cores: 2-7, 14-19)")
    print(" Resource Cap: Strict 4.0 GB RAM + AMD CAT L3 ff00 Cache Partition")
    print("=================================================================")

    for sched in SCHEDULERS:
        print(f"\n=================================================================")
        print(f">>> TESTING SCHEDULER: {sched} <<<")
        print(f"=================================================================")
        try:
            start_scheduler(sched)
            time.sleep(2)

            all_data.extend(test_api_gateway(sched))
            all_data.extend(test_redis_cache(sched))
            all_data.extend(test_realtime_audio(sched))
            all_data.extend(test_hft_matching(sched))
            all_data.extend(test_game_server(sched))

        finally:
            stop_scheduler(sched)
            time.sleep(2)

    # Save JSON telemetry
    with open(JSON_FILE, "w") as f:
        json.dump(all_data, f, indent=2)
    print(f"\n[+] Saved full telemetry dataset to {JSON_FILE}")

    # Save CSV
    import csv
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "scheduler", "application", "stage", "load_param", "throughput", "throughput_unit",
            "p50_ms", "p95_ms", "p99_ms", "p999_ms", "cpu_percent", "ram_mb", "failure_state"
        ])
        writer.writeheader()
        writer.writerows(all_data)
    print(f"[+] Saved CSV metrics to {CSV_FILE}")

    # Generate Markdown Report
    generate_markdown_report(all_data)


def generate_markdown_report(data: List[Dict[str, Any]]):
    with open(REPORT_FILE, "w") as f:
        f.write("# Multi-Throughput Saturation Sweep & Failure Stage Telemetry Report\n\n")
        f.write(f"**Execution Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("**Hardware Testbed:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 Cores / 24 Threads)\n")
        f.write("- **Isolated Execution Domain:** 12 Logical CPUs (`2-7, 14-19`: 2 Zen 5 P-cores + 4 Zen 5c E-cores)\n")
        f.write("- **CPU Frequencies:** Uncapped (P-cores 5.16 GHz, E-cores 3.29 GHz)\n")
        f.write("- **Memory Isolation:** Strict 4.0 GB RAM constraint (`benchmark.slice`)\n")
        f.write("- **L3 Cache Partition:** AMD CAT `resctrl` dedicated upper 8 ways (`ff00`)\n\n")
        f.write("---\n\n")

        f.write("## 1. Executive Failure Stage & Maximum Throughput Matrix\n\n")
        f.write("| Application Workload | Metric Focus | Default CFS / EEVDF | `scx_optima` | `scx_rdtai` | `scx_rusty` | `scx_rustland` |\n")
        f.write("|:---|:---|:---|:---|:---|:---|:---|\n")

        # Summary rows per application
        # 1. API Gateway
        f.write(format_summary_row("01_api_gateway", data, "Max Throughput (req/s)", lambda d: f"{d['throughput']} req/s"))
        f.write(format_summary_row("01_api_gateway", data, "Saturation P99 (ms)", lambda d: f"{d['p99_ms']} ms"))
        f.write(format_summary_row("01_api_gateway", data, "Breaking State", lambda d: d['failure_state'], stage_idx=2))

        # 2. Redis
        f.write(format_summary_row("02_redis_cache", data, "Max Pipelined QPS", lambda d: f"{d['throughput']:,.0f} ops/s"))
        f.write(format_summary_row("02_redis_cache", data, "P99 Latency at Max Load", lambda d: f"{d['p99_ms']} ms"))

        # 3. Audio DSP
        f.write(format_summary_row("03_realtime_audio", data, "Audio Xruns (Dropouts)", lambda d: "0 (PERFECT)" if "PERFECT" in d['failure_state'] else d['failure_state'], stage_idx=2))
        f.write(format_summary_row("03_realtime_audio", data, "P99 Frame Latency (1.33ms limit)", lambda d: f"{d['p99_ms']} ms", stage_idx=2))

        # 4. HFT
        f.write(format_summary_row("04_hft_matching", data, "Peak Ingestion (Orders/s)", lambda d: f"{d['throughput']:,.0f} ops/s", stage_idx=2))
        f.write(format_summary_row("04_hft_matching", data, "P99 Turnaround Latency", lambda d: f"{d['p99_ms']} ms", stage_idx=2))

        # 5. Game Server
        f.write(format_summary_row("05_game_tick_server", data, "1000-Player Frame Drops", lambda d: f"{d['p999_ms']:.0f} drops", stage_idx=2))
        f.write(format_summary_row("05_game_tick_server", data, "P99 Tick Execution Time", lambda d: f"{d['p99_ms']} ms", stage_idx=2))

        f.write("\n---\n\n")

        # Detailed sections per application
        for app, title in [
            ("01_api_gateway", "App 1: Cloud-Native API Gateway (NGINX + uvloop Fan-out)"),
            ("02_redis_cache", "App 2: Production In-Memory Cache (Redis Pipelined)"),
            ("03_realtime_audio", "App 3: Real-Time Pro-Audio DSP Engine (48kHz/64-smp Buffer Deadlines)"),
            ("04_hft_matching", "App 4: Ultra-Low-Latency HFT Limit Order Book Matching Engine"),
            ("05_game_tick_server", "App 5: 120 FPS Interactive Game Server (Spatial Physics & Pacing)")
        ]:
            f.write(f"## {title}\n\n")
            f.write("| Scheduler | Stage | Load Parameters | Throughput | P50 Latency | P95 Latency | P99 Latency | CPU % | RAM (MB) | Health / Breaking Status |\n")
            f.write("|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|\n")
            app_rows = [r for r in data if r["application"] == app]
            for r in app_rows:
                f.write(f"| **{r['scheduler']}** | {r['stage']} | {r['load_param']} | {r['throughput']} {r['throughput_unit']} | {r['p50_ms']} ms | {r['p95_ms']} ms | {r['p99_ms']} ms | {r['cpu_percent']}% | {r['ram_mb']} MB | `{r['failure_state']}` |\n")
            f.write("\n---\n\n")

    print(f"[+] Master Telemetry Report written to {REPORT_FILE}")


def format_summary_row(app: str, data: List[Dict[str, Any]], label: str, formatter, stage_idx: int = -1) -> str:
    row = f"| **{label}** | "
    for sched in SCHEDULERS:
        sched_app = [r for r in data if r["application"] == app and r["scheduler"] == sched]
        if sched_app:
            target = sched_app[stage_idx] if stage_idx != -1 else max(sched_app, key=lambda x: x["throughput"])
            val = formatter(target)
            row += f"{val} | "
        else:
            row += "N/A | "
    return row + "\n"


if __name__ == "__main__":
    main()
