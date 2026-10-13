#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
Isolated QEMU/KVM 16-Workload 3x Benchmark Suite for Linux Schedulers.
Runs complete 3-iteration evaluation for:
- default_cfs_eevdf
- scx_optima
- scx_rusty
- scx_rlfifo
"""

import os
import sys
import time
import json
import re
import csv
import subprocess
from pathlib import Path

RESULTS_DIR = Path("/root/scx/sch_tests/qemu_results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RAW_LOGS_DIR = RESULTS_DIR / "raw_logs"
RAW_LOGS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_DIR = Path("/root/scx/target/release")
LINUX_DIR = Path("/root/scx/sch_tests/linux")
SCHBENCH_BIN = Path("/root/scx/sch_tests/schbench/schbench")
AUDIO_BIN = Path("/root/scx/benchmarks/production_apps/03_realtime_audio/audio_dsp_sim")
HFT_BIN = Path("/root/scx/benchmarks/production_apps/04_hft_matching/matching_engine")
GAME_SCRIPT = Path("/root/scx/benchmarks/production_apps/05_game_tick_server/game_server.py")
GW_APP_DIR = Path("/root/scx/benchmarks/production_apps/01_api_gateway")
GW_CLIENT_SCRIPT = GW_APP_DIR / "benchmark_client.py"

def run_cmd(cmd, cwd=None, timeout=120):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False
    )

def get_sched_state():
    state_file = Path("/sys/kernel/sched_ext/state")
    if state_file.exists():
        return state_file.read_text().strip()
    return "unknown"

def stop_schedulers():
    subprocess.run(["pkill", "-SIGINT", "-f", "/root/scx/target/release/scx_"], check=False)
    for _ in range(8):
        time.sleep(1)
        if get_sched_state() != "enabled":
            return
    subprocess.run(["pkill", "-9", "-f", "/root/scx/target/release/scx_"], check=False)
    time.sleep(1)

def start_scheduler(sched):
    stop_schedulers()
    if sched == "default_cfs_eevdf":
        print("  [SCHED] Default Linux CFS/EEVDF active (sched_ext disabled).", flush=True)
        return None
    
    bin_path = TARGET_DIR / sched
    if not bin_path.exists():
        raise FileNotFoundError(f"Scheduler binary {bin_path} not found!")
    
    log_file = RAW_LOGS_DIR / f"{sched}_daemon.log"
    out = open(log_file, "a")
    p = subprocess.Popen([str(bin_path)], stdout=out, stderr=subprocess.STDOUT)
    for _ in range(12):
        time.sleep(1)
        if get_sched_state() == "enabled":
            print(f"  [SCHED] {sched} attached successfully to sched_ext.", flush=True)
            return p
    raise RuntimeError(f"Scheduler {sched} failed to attach! Sched_ext state: {get_sched_state()}")

def ensure_daemons():
    # redis
    subprocess.run(["systemctl", "start", "redis-server"], check=False)
    # iperf3 server
    subprocess.run(["pkill", "-9", "-f", "iperf3 -s"], check=False)
    time.sleep(0.5)
    subprocess.run(["iperf3", "-s", "-D"], check=False)
    time.sleep(0.5)
    # uvicorn api gateway
    subprocess.run(["pkill", "-9", "-f", "uvicorn app.main:app"], check=False)
    time.sleep(0.5)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(GW_APP_DIR)
    subprocess.Popen(
        ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18080"],
        cwd=str(GW_APP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    for _ in range(20):
        time.sleep(0.5)
        res = run_cmd(["curl", "-s", "http://127.0.0.1:18080/healthz"])
        if res.returncode == 0 and "UP" in res.stdout:
            break

def run_single_workload(workload_id, sched, run_idx):
    """Executes a single workload and returns dict of metrics."""
    res_metrics = {}
    log_prefix = f"{sched}_run{run_idx}_w{workload_id}"

    if workload_id == 1:
        # Hackbench IPC & Context Switching
        p_proc = run_cmd(["hackbench", "-p", "-g", "10", "-l", "1000"])
        (RAW_LOGS_DIR / f"{log_prefix}_proc.log").write_text(p_proc.stdout + p_proc.stderr)
        m_proc = re.search(r'Time:\s+([\d\.]+)', p_proc.stdout)
        proc_time = float(m_proc.group(1)) if m_proc else 0.0

        p_thr = run_cmd(["hackbench", "-T", "-g", "10", "-l", "1000"])
        (RAW_LOGS_DIR / f"{log_prefix}_thr.log").write_text(p_thr.stdout + p_thr.stderr)
        m_thr = re.search(r'Time:\s+([\d\.]+)', p_thr.stdout)
        thr_time = float(m_thr.group(1)) if m_thr else 0.0

        res_metrics["hackbench_proc_s"] = proc_time
        res_metrics["hackbench_thread_s"] = thr_time

    elif workload_id == 2:
        # Linux Kernel Compilation (make kernel/)
        # Remove kernel/*.o and built-in.a to measure compilation deterministically
        subprocess.run("rm -f kernel/*.o kernel/built-in.a", shell=True, cwd=str(LINUX_DIR))
        t0 = time.time()
        n_threads = str(os.cpu_count() or 12)
        p = run_cmd(["make", f"-j{n_threads}", "kernel/"], cwd=LINUX_DIR, timeout=300)
        compile_time = round(time.time() - t0, 3)
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        res_metrics["kernel_compile_s"] = compile_time

    elif workload_id == 3:
        # Perf Pipe IPC Ping-Pong
        p = run_cmd(["perf", "bench", "sched", "pipe", "-l", "50000"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        m_lat = re.search(r'([\d\.]+)\s+usecs/op', p.stdout)
        m_ops = re.search(r'(\d+)\s+ops/sec', p.stdout)
        res_metrics["perf_pipe_lat_us"] = float(m_lat.group(1)) if m_lat else 0.0
        res_metrics["perf_pipe_throughput_ops"] = float(m_ops.group(1)) if m_ops else 0.0

    elif workload_id == 4:
        # Perf Messaging Storm
        p = run_cmd(["perf", "bench", "sched", "messaging", "-p", "-g", "10", "-t", "20"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        m = re.search(r'Total time:\s+([\d\.]+)', p.stdout)
        res_metrics["perf_msg_time_s"] = float(m.group(1)) if m else 0.0

    elif workload_id == 5:
        # Sysbench CPU Throughput & Fairness
        n_threads = str(os.cpu_count() or 12)
        p = run_cmd(["sysbench", "cpu", "--cpu-max-prime=30000", f"--threads={n_threads}", "--time=10", "run"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        m_eps = re.search(r'events per second:\s+([\d\.]+)', p.stdout)
        m_dev = re.search(r'events \(avg/stddev\):\s+[\d\.]+/([\d\.]+)', p.stdout)
        res_metrics["sysbench_cpu_eps"] = float(m_eps.group(1)) if m_eps else 0.0
        res_metrics["sysbench_cpu_fairness_stddev"] = float(m_dev.group(1)) if m_dev else 0.0

    elif workload_id == 6:
        # Sysbench Memory Saturation
        n_threads = str(os.cpu_count() or 12)
        p = run_cmd(["sysbench", "memory", f"--threads={n_threads}", "--time=10", "run"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        m_ops = re.search(r'Total operations:\s+\d+\s+\(([\d\.]+) per second\)', p.stdout)
        m_bw = re.search(r'([\d\.]+)\s+MiB/sec', p.stdout)
        res_metrics["sysbench_mem_ops"] = float(m_ops.group(1)) if m_ops else 0.0
        res_metrics["sysbench_mem_bw_mibs"] = float(m_bw.group(1)) if m_bw else 0.0

    elif workload_id == 7:
        # Microarchitectural PMU Counters (Cache miss % & IPC)
        n_threads = str(os.cpu_count() or 12)
        p = run_cmd([
            "perf", "stat", "-e", "instructions,cycles,cache-misses,cache-references",
            "--", "sysbench", "cpu", "--cpu-max-prime=25000", f"--threads={n_threads}", "--time=5", "run"
        ])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        m_ins = re.search(r'([\d,]+)\s+instructions', p.stderr)
        m_cyc = re.search(r'([\d,]+)\s+cycles', p.stderr)
        m_mis = re.search(r'([\d,]+)\s+cache-misses', p.stderr)
        m_ref = re.search(r'([\d,]+)\s+cache-references', p.stderr)
        ins = float(m_ins.group(1).replace(',', '')) if m_ins else 0.0
        cyc = float(m_cyc.group(1).replace(',', '')) if m_cyc else 0.0
        mis = float(m_mis.group(1).replace(',', '')) if m_mis else 0.0
        ref = float(m_ref.group(1).replace(',', '')) if m_ref else 0.0
        res_metrics["pmu_ipc"] = round(ins / cyc, 3) if cyc > 0 else 0.0
        res_metrics["pmu_cache_miss_pct"] = round((mis * 100.0) / ref, 2) if ref > 0 else 0.0

    elif workload_id == 8:
        # Local Network Throughput (iperf3)
        p = run_cmd(["iperf3", "-c", "127.0.0.1", "-t", "10", "--json"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        try:
            data = json.loads(p.stdout)
            bps = data["end"]["sum_received"]["bits_per_second"]
            res_metrics["iperf3_throughput_gbps"] = round(bps / 1e9, 2)
        except Exception:
            res_metrics["iperf3_throughput_gbps"] = 0.0

    elif workload_id == 9 or workload_id == 10:
        # Schbench Queuing, Wakeup Tail, Request Latency & RPS
        p = run_cmd([str(SCHBENCH_BIN), "-m", "8", "-t", "4", "-r", "10"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        text = p.stdout
        w_p50, w_p99, r_p50, r_p99, rps = 0.0, 0.0, 0.0, 0.0, 0.0
        try:
            last_w_idx = text.rfind("Wakeup Latencies")
            last_r_idx = text.rfind("Request Latencies")
            last_rps_idx = text.rfind("RPS percentiles")
            if last_w_idx != -1 and last_r_idx != -1:
                w_text = text[last_w_idx:last_r_idx]
                r_text = text[last_r_idx:last_rps_idx] if last_rps_idx != -1 else text[last_r_idx:]
                mw_50 = re.search(r"50\.0th:\s*(\d+)", w_text)
                mw_99 = re.search(r"99\.0th:\s*(\d+)", w_text)
                mr_50 = re.search(r"50\.0th:\s*(\d+)", r_text)
                mr_99 = re.search(r"99\.0th:\s*(\d+)", r_text)
                if mw_50: w_p50 = float(mw_50.group(1))
                if mw_99: w_p99 = float(mw_99.group(1))
                if mr_50: r_p50 = float(mr_50.group(1))
                if mr_99: r_p99 = float(mr_99.group(1))
            m_rps = re.search(r"average rps:\s*([\d\.]+)", text)
            if m_rps:
                rps = float(m_rps.group(1))
            else:
                m_crps = re.search(r"current rps:\s*([\d\.]+)", text)
                if m_crps: rps = float(m_crps.group(1))
        except Exception as e:
            print(f"Error parsing schbench: {e}")

        res_metrics["schbench_wakeup_p50_us"] = w_p50
        res_metrics["schbench_wakeup_p99_us"] = w_p99
        res_metrics["schbench_request_p50_us"] = r_p50
        res_metrics["schbench_request_p99_us"] = r_p99
        res_metrics["schbench_rps"] = rps

    elif workload_id == 11:
        # POSIX Cyclictest Real-Time Jitter
        p = run_cmd(["cyclictest", "--mlockall", "--smp", "--priority=99", "--interval=1000", "--loops=10000", "-q"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        max_spikes = []
        avg_latencies = []
        for line in p.stdout.splitlines():
            m_max = re.search(r'Max:\s+(\d+)', line)
            m_avg = re.search(r'Avg:\s+(\d+)', line)
            if m_max: max_spikes.append(float(m_max.group(1)))
            if m_avg: avg_latencies.append(float(m_avg.group(1)))
        res_metrics["cyclictest_max_spike_us"] = max(max_spikes) if max_spikes else 0.0
        res_metrics["cyclictest_avg_jitter_us"] = round(sum(avg_latencies)/len(avg_latencies), 1) if avg_latencies else 0.0

    elif workload_id == 12:
        # 120 FPS Interactive Game Server Simulation
        out_json = RESULTS_DIR / f"{log_prefix}_game.json"
        p = run_cmd(["python3", str(GAME_SCRIPT), "-c", "500", "-d", "15", "-j", str(out_json)])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        if out_json.exists():
            data = json.loads(out_json.read_text())
            res_metrics["game_tick_p99_us"] = data["tick_duration_us"]["p99"]
            res_metrics["game_tick_p50_us"] = data["tick_duration_us"]["p50"]
            res_metrics["game_frame_drops_pct"] = data["frame_drop_pct"]
        else:
            res_metrics["game_tick_p99_us"] = 0.0
            res_metrics["game_tick_p50_us"] = 0.0
            res_metrics["game_frame_drops_pct"] = 0.0

    elif workload_id == 13:
        # Real-Time Pro-Audio DSP Engine
        out_json = RESULTS_DIR / f"{log_prefix}_audio.json"
        p = run_cmd([str(AUDIO_BIN), "-n", "15000", "-j", str(out_json)])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        if out_json.exists():
            data = json.loads(out_json.read_text())
            res_metrics["audio_turnaround_p99_us"] = data["turnaround_time_us"]["p99"]
            res_metrics["audio_turnaround_p50_us"] = data["turnaround_time_us"]["p50"]
            res_metrics["audio_xruns"] = data["xruns"]
        else:
            res_metrics["audio_turnaround_p99_us"] = 0.0
            res_metrics["audio_turnaround_p50_us"] = 0.0
            res_metrics["audio_xruns"] = 0

    elif workload_id == 14:
        # Production Redis In-Memory Cache
        p = run_cmd(["redis-benchmark", "-h", "127.0.0.1", "-p", "6379", "-t", "get,set", "-n", "50000", "-c", "50", "--csv"])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        get_rps = 0.0
        get_p99 = 0.0
        for line in p.stdout.splitlines():
            row = [c.strip('"') for c in line.split(',')]
            if row and row[0].upper() == 'GET':
                get_rps = float(row[1])
                get_p99 = float(row[6]) * 1000.0 # ms to us
        res_metrics["redis_get_rps"] = get_rps
        res_metrics["redis_get_p99_us"] = get_p99

    elif workload_id == 15:
        # Cloud API Gateway Fan-Out
        out_json = RESULTS_DIR / f"{log_prefix}_gw.json"
        p = run_cmd([
            "python3", str(GW_CLIENT_SCRIPT),
            "--url", "http://127.0.0.1:18080/api/v1/dashboard",
            "--concurrency", "50", "--requests", "1500",
            "--json", str(out_json)
        ])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        if out_json.exists():
            data = json.loads(out_json.read_text())
            res_metrics["gateway_ingress_qps"] = data["throughput_req_s"]
            res_metrics["gateway_p99_ms"] = data["p99_ms"]
            res_metrics["gateway_p50_ms"] = data["p50_ms"]
        else:
            res_metrics["gateway_ingress_qps"] = 0.0
            res_metrics["gateway_p99_ms"] = 0.0
            res_metrics["gateway_p50_ms"] = 0.0

    elif workload_id == 16:
        # HFT Limit Order Book Matching Engine
        out_json = RESULTS_DIR / f"{log_prefix}_hft.json"
        p = run_cmd([str(HFT_BIN), "-n", "100000", "-j", str(out_json)])
        (RAW_LOGS_DIR / f"{log_prefix}.log").write_text(p.stdout + p.stderr)
        if out_json.exists():
            data = json.loads(out_json.read_text())
            res_metrics["hft_order_matching_ops"] = data["throughput_ops"]
            res_metrics["hft_turnaround_p99_us"] = data["latency_turnaround_us"]["p99"]
            res_metrics["hft_turnaround_p50_us"] = data["latency_turnaround_us"]["p50"]
        else:
            res_metrics["hft_order_matching_ops"] = 0.0
            res_metrics["hft_turnaround_p99_us"] = 0.0
            res_metrics["hft_turnaround_p50_us"] = 0.0

    return res_metrics

def main():
    schedulers = ["default_cfs_eevdf", "scx_optima", "scx_rusty", "scx_rlfifo"]
    if len(sys.argv) > 1:
        schedulers = sys.argv[1].split()

    print("================================================================================")
    print("  Isolated QEMU/KVM 16-Workload 3x Repetition Benchmark Harness                 ")
    print(f"  Schedulers: {', '.join(schedulers)}")
    print("  Repetitions: 3 iterations per scheduler")
    print(f"  Results Directory: {RESULTS_DIR}")
    print("================================================================================")

    ensure_daemons()

    raw_results = {s: {} for s in schedulers}
    json_path = RESULTS_DIR / "raw_iterations_results.json"
    if json_path.exists():
        try:
            raw_results = json.loads(json_path.read_text())
        except Exception:
            pass

    for sched in schedulers:
        print(f"\n################################################################################")
        print(f">>> STARTING EVALUATION FOR SCHEDULER: {sched} <<<")
        print(f"################################################################################")

        for run_idx in [1, 2, 3]:
            str_run = str(run_idx)
            if sched in raw_results and str_run in raw_results[sched] and len(raw_results[sched][str_run]) >= 20:
                print(f"  [Run {run_idx}/3] Already completed, skipping...")
                continue

            print(f"\n--- [Iteration {run_idx}/3] Scheduler: {sched} ---", flush=True)
            sched_proc = start_scheduler(sched)

            run_metrics = {}
            for wid in range(1, 17):
                if wid == 10:
                    continue # 10 is populated with 9
                print(f"    -> Executing Workload {wid}/16...", flush=True)
                metrics = run_single_workload(wid, sched, run_idx)
                run_metrics.update(metrics)
                print(f"       Metrics: {metrics}", flush=True)

            if sched not in raw_results:
                raw_results[sched] = {}
            raw_results[sched][str_run] = run_metrics

            # Save progress incrementally
            json_path.write_text(json.dumps(raw_results, indent=2))
            print(f"  [Iteration {run_idx}/3] Finished & saved incrementally.", flush=True)

            stop_schedulers()
            time.sleep(2)

    print("\n[+] All 3 iterations completed across all schedulers!", flush=True)
    generate_summary_report(raw_results)

def generate_summary_report(raw_results):
    import math
    print("\n[*] Computing 3-run statistical aggregates and generating report...", flush=True)
    all_metrics = set()
    for s, runs in raw_results.items():
        for r, m in runs.items():
            all_metrics.update(m.keys())
    metric_list = sorted(list(all_metrics))

    # Generate CSV
    csv_path = RESULTS_DIR / "qemu_3x_results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["scheduler", "metric", "mean", "stddev", "min", "max", "run1", "run2", "run3"])
        for s, runs in raw_results.items():
            for m in metric_list:
                vals = [runs[r][m] for r in ["1", "2", "3"] if r in runs and m in runs[r]]
                if not vals:
                    continue
                mean = sum(vals) / len(vals)
                stddev = math.sqrt(sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
                r1 = vals[0] if len(vals) > 0 else ""
                r2 = vals[1] if len(vals) > 1 else ""
                r3 = vals[2] if len(vals) > 2 else ""
                writer.writerow([s, m, f"{mean:.4f}", f"{stddev:.4f}", f"{min(vals):.4f}", f"{max(vals):.4f}", r1, r2, r3])

    # Generate Markdown Report
    report_path = RESULTS_DIR / "QEMU_16_WORKLOADS_3X_REPORT.md"
    lines = [
        "# Rigorous Isolated QEMU/KVM 16-Workload 3x Benchmark Report",
        "",
        "**Host Microarchitecture:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 physical cores, 24 threads)",
        "**Host Partitioning:** Cores 0,1 (P) + 8-11 (E) (12 threads), L3 Cache 00ff, Device IRQs f03f03 (Zero host tasks on VM cores)",
        "**Guest VM Configuration:** 12 vCPUs pinned 1:1 to isolated silicon cores (2-7, 14-19: 2 P-cores @ 5.16GHz + 4 E-cores @ 3.29GHz), 14GB RAM, Kernel 7.0.0-38-generic, L3 Cache ff00",
        "**Statistical Integrity:** 3 complete independent iterations per scheduler with Mean, StdDev (Variance), Min, and Max.",
        "",
        "---",
        "",
        "## Statistical Performance Summary Across 3 Repetitions",
        "",
        "| Benchmark Workload | Metric | CFS / EEVDF (Mean ± σ) | `scx_optima` (Mean ± σ) | `scx_rusty` (Mean ± σ) | `scx_rlfifo` (Mean ± σ) | Advantage (`scx_optima` vs CFS) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]

    for m in metric_list:
        cfs_vals = [raw_results.get("default_cfs_eevdf", {}).get(r, {}).get(m) for r in ["1", "2", "3"] if m in raw_results.get("default_cfs_eevdf", {}).get(r, {})]
        opt_vals = [raw_results.get("scx_optima", {}).get(r, {}).get(m) for r in ["1", "2", "3"] if m in raw_results.get("scx_optima", {}).get(r, {})]
        rus_vals = [raw_results.get("scx_rusty", {}).get(r, {}).get(m) for r in ["1", "2", "3"] if m in raw_results.get("scx_rusty", {}).get(r, {})]
        rlf_vals = [raw_results.get("scx_rlfifo", {}).get(r, {}).get(m) for r in ["1", "2", "3"] if m in raw_results.get("scx_rlfifo", {}).get(r, {})]

        def fmt(vals):
            if not vals:
                return "N/A"
            avg = sum(vals) / len(vals)
            sd = math.sqrt(sum((x - avg) ** 2 for x in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
            return f"{avg:.2f} ± {sd:.2f}"

        cfs_fmt = fmt(cfs_vals)
        opt_fmt = fmt(opt_vals)
        rus_fmt = fmt(rus_vals)
        rlf_fmt = fmt(rlf_vals)

        cfs_avg = sum(cfs_vals) / len(cfs_vals) if cfs_vals else 0
        opt_avg = sum(opt_vals) / len(opt_vals) if opt_vals else 0
        if cfs_avg > 0 and opt_avg > 0:
            diff = ((opt_avg - cfs_avg) / cfs_avg) * 100
            diff_str = f"{diff:+.1f}%"
        else:
            diff_str = "--"

        lines.append(f"| `{m}` | Mean ± StdDev | {cfs_fmt} | **{opt_fmt}** | {rus_fmt} | {rlf_fmt} | **{diff_str}** |")

    report_path.write_text("\n".join(lines) + "\n")
    print(f"[+] Summary written to {csv_path} and {report_path}", flush=True)

if __name__ == "__main__":
    main()
