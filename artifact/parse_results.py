#!/usr/bin/env python3
"""
artifact/parse_results.py: Formats raw benchmark data and RAPL energy measurements into publication tables with error bars.
"""

import json
import math
import sys
from pathlib import Path

def parse_rapl():
    rapl_file = Path("sch_tests/rapl_energy_results.json")
    if not rapl_file.exists():
        print(f"[-] RAPL results file {rapl_file} not found. Run scripts/measure_rapl_energy.py first.")
        return

    data = json.loads(rapl_file.read_text())
    print("\n=========================================================================================")
    print(" HARDWARE RAPL ENERGY PROFILING RESULTS (AMD Ryzen AI 9 HX 370, 5 Repetitions)")
    print("=========================================================================================")
    print(f"{'Workload':<18} | {'Metric':<14} | {'CFS / EEVDF':<16} | {'scx_optima':<16} | {'scx_rusty':<16} | {'scx_rlfifo':<16}")
    print("-" * 105)

    workloads = ["sysbench_cpu", "sysbench_mem", "pipe_pingpong", "hackbench_proc"]
    metrics = [
        ("pkg_joules", "Pkg Energy (J)"),
        ("core_joules", "Core Energy (J)"),
        ("pkg_watts", "Avg Power (W)"),
        ("edp_js", "EDP (J·s)")
    ]

    for wl in workloads:
        print(f"\n--- Workload: {wl} ---")
        for m_key, m_label in metrics:
            row_vals = []
            for s in ["cfs", "scx_optima", "scx_rusty", "scx_rlfifo"]:
                if s in data and wl in data[s]:
                    vals = [x[m_key] for x in data[s][wl]]
                    mean = sum(vals) / len(vals)
                    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
                    row_vals.append(f"{mean:.2f} ± {sd:.2f}")
                else:
                    row_vals.append("N/A")
            print(f"{wl:<18} | {m_label:<14} | {row_vals[0]:<16} | {row_vals[1]:<16} | {row_vals[2]:<16} | {row_vals[3]:<16}")

def parse_all():
    parse_rapl()
    report_file = Path("sch_tests/qemu_results/QEMU_16_WORKLOADS_3X_REPORT.md")
    if report_file.exists():
        print("\n" + "=" * 105)
        print(" 16-WORKLOAD ISOLATED BENCHMARK SUMMARY (Table II Mean ± σ)")
        print("=" * 105)
        print(report_file.read_text()[:2500])

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rapl":
        parse_rapl()
    else:
        parse_all()
