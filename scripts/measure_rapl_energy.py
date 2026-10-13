#!/usr/bin/env python3
"""
measure_rapl_energy.py: Collects hardware RAPL energy data across schedulers.
Schedulers: CFS (EEVDF default), scx_optima, scx_rusty, scx_rlfifo
Workloads:
  1. Sysbench CPU (12 threads, prime limit 10,000, 5 seconds)
  2. Sysbench Memory (12 threads, 5 seconds)
  3. IPC Pipe Ping-Pong (perf bench sched pipe, 100,000 ops)
  4. Hackbench Process (hackbench -p -l 1000 -g 4)
"""

import subprocess
import time
import json
import os
import signal
import re
from pathlib import Path

SCHEDULERS = {
    "cfs": None,
    "scx_optima": "./target/release/scx_optima",
    "scx_rusty": "./target/release/scx_rusty",
    "scx_rlfifo": "./target/release/scx_rlfifo"
}

WORKLOADS = {
    "sysbench_cpu": ["sysbench", "cpu", "--threads=12", "--time=5", "run"],
    "sysbench_mem": ["sysbench", "memory", "--threads=12", "--time=5", "run"],
    "pipe_pingpong": ["perf", "bench", "sched", "pipe", "-l", "100000"],
    "hackbench_proc": ["hackbench", "-p", "-l", "1000", "-g", "4"]
}

REPETITIONS = 5
OUTPUT_JSON = Path("sch_tests/rapl_energy_results.json")

def read_rapl():
    # Read sysfs energy_uj
    res = subprocess.run(["sudo", "cat", "/sys/class/powercap/intel-rapl:0/energy_uj"],
                         capture_output=True, text=True, check=True)
    pkg = int(res.stdout.strip())
    res_c = subprocess.run(["sudo", "cat", "/sys/class/powercap/intel-rapl:0:0/energy_uj"],
                           capture_output=True, text=True, check=True)
    core = int(res_c.stdout.strip())
    return pkg, core

def start_scheduler(sched_bin):
    if not sched_bin:
        return None
    proc = subprocess.Popen(["sudo", sched_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)  # Wait for scheduler attachment
    return proc

def stop_scheduler(proc):
    if proc is None:
        return
    subprocess.run(["sudo", "kill", "-INT", str(proc.pid)], check=False)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        subprocess.run(["sudo", "kill", "-KILL", str(proc.pid)], check=False)
        proc.wait()
    time.sleep(1.0)

def run_workload(cmd):
    pkg_start, core_start = read_rapl()
    t_start = time.perf_counter()
    res = subprocess.run(cmd, capture_output=True, text=True)
    t_end = time.perf_counter()
    pkg_end, core_end = read_rapl()

    elapsed = t_end - t_start
    # AMD RAPL counters wrap around at 2^32 uj (~4294.9 Joules)
    pkg_uj = (pkg_end - pkg_start) if pkg_end >= pkg_start else (pkg_end + (1 << 32) - pkg_start)
    core_uj = (core_end - core_start) if core_end >= core_start else (core_end + (1 << 32) - core_start)

    pkg_joules = pkg_uj / 1e6
    core_joules = core_uj / 1e6
    pkg_watts = pkg_joules / elapsed if elapsed > 0 else 0
    core_watts = core_joules / elapsed if elapsed > 0 else 0
    edp = pkg_joules * elapsed

    return {
        "elapsed_s": elapsed,
        "pkg_joules": pkg_joules,
        "core_joules": core_joules,
        "pkg_watts": pkg_watts,
        "core_watts": core_watts,
        "edp_js": edp,
        "stdout": res.stdout[:200]
    }

def main():
    print(f"Starting RAPL Energy Profiling across {len(SCHEDULERS)} schedulers, {len(WORKLOADS)} workloads, {REPETITIONS} reps...")
    results = {}

    for sched_name, sched_bin in SCHEDULERS.items():
        print(f"\n==========================================")
        print(f"Testing Scheduler: {sched_name}")
        print(f"==========================================")
        results[sched_name] = {}

        proc = start_scheduler(sched_bin)
        try:
            for wl_name, wl_cmd in WORKLOADS.items():
                print(f"  -> Running workload: {wl_name} ({REPETITIONS} runs)...")
                results[sched_name][wl_name] = []
                for rep in range(1, REPETITIONS + 1):
                    time.sleep(0.5)
                    metrics = run_workload(wl_cmd)
                    results[sched_name][wl_name].append(metrics)
                    print(f"     Run {rep}: {metrics['elapsed_s']:.3f}s | Pkg: {metrics['pkg_joules']:.2f} J | Core: {metrics['core_joules']:.2f} J | Power: {metrics['pkg_watts']:.2f} W | EDP: {metrics['edp_js']:.2f} J·s")
        finally:
            stop_scheduler(proc)

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved raw RAPL results to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
