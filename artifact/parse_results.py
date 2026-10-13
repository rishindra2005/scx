#!/usr/bin/env python3
"""
artifact/parse_results.py: Formats raw benchmark data and RAPL energy measurements into publication tables with error bars.
Works independently from any working directory by locating artifact/data or sch_tests.
"""

import json
import math
import sys
from pathlib import Path

def find_file(filenames):
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    for fname in filenames:
        candidates = [
            script_dir / "data" / fname,
            repo_root / "sch_tests" / fname,
            repo_root / "sch_tests" / "qemu_results" / fname,
            Path(fname),
            Path("sch_tests") / fname,
            Path("sch_tests/qemu_results") / fname,
            Path("artifact/data") / fname,
        ]
        for c in candidates:
            if c.exists():
                return c
    return None

def parse_rapl():
    rapl_file = find_file(["rapl_energy_results.json"])
    if not rapl_file:
        print("[-] RAPL results file rapl_energy_results.json not found.")
        return

    data = json.loads(rapl_file.read_text())
    print("\n" + "=" * 105)
    print(" HARDWARE RAPL ENERGY PROFILING & EDP (AMD Ryzen AI 9 HX 370, 5 Repetitions - Table IV)")
    print("=" * 105)
    print(f"{'Workload':<18} | {'Metric':<16} | {'CFS / EEVDF':<16} | {'scx_optima':<16} | {'scx_rusty':<16} | {'scx_rlfifo':<16}")
    print("-" * 105)

    workloads = [
        ("sysbench_cpu", "Sysbench CPU (12 thr, 5s)"),
        ("sysbench_mem", "Sysbench Mem (12 thr, 5s)"),
        ("pipe_pingpong", "Pipe Ping-Pong (100k ops)"),
        ("hackbench_proc", "Hackbench (Process churn)"),
    ]
    metrics = [
        ("pkg_joules", "Pkg Energy (J) ↓"),
        ("core_joules", "Core Energy (J) ↓"),
        ("pkg_watts", "Avg Power (W)"),
        ("edp_js", "EDP (J·s) ↓"),
    ]

    for wl_key, wl_name in workloads:
        print(f"\n--- {wl_name} ---")
        for m_key, m_label in metrics:
            row_vals = []
            cfs_mean = None
            opt_mean = None
            for s in ["cfs", "scx_optima", "scx_rusty", "scx_rlfifo"]:
                if s in data and wl_key in data[s]:
                    vals = [x[m_key] for x in data[s][wl_key]]
                    mean = sum(vals) / len(vals)
                    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
                    row_vals.append(f"{mean:.2f} ± {sd:.2f}")
                    if s == "cfs":
                        cfs_mean = mean
                    elif s == "scx_optima":
                        opt_mean = mean
                else:
                    row_vals.append("N/A")
            delta_str = ""
            if cfs_mean and opt_mean and cfs_mean > 0:
                pct = ((opt_mean - cfs_mean) / cfs_mean) * 100
                delta_str = f" [{pct:+.1f}% vs CFS]"
            print(f"{wl_key:<18} | {m_label:<16} | {row_vals[0]:<16} | {row_vals[1]:<16} | {row_vals[2]:<16} | {row_vals[3]:<16}{delta_str}")

def parse_benchmarks():
    report_file = find_file(["QEMU_16_WORKLOADS_3X_REPORT.md"])
    if not report_file:
        print("[-] Benchmark report QEMU_16_WORKLOADS_3X_REPORT.md not found.")
        return

    print("\n" + "=" * 105)
    print(" 16-WORKLOAD ISOLATED BENCHMARK SUMMARY (Table II Mean ± σ across 3 Repetitions)")
    print("=" * 105)
    print(report_file.read_text())

def parse_all():
    parse_rapl()
    parse_benchmarks()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rapl":
        parse_rapl()
    elif len(sys.argv) > 1 and sys.argv[1] == "--benchmarks":
        parse_benchmarks()
    else:
        parse_all()
