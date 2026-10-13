#!/usr/bin/env python3
import glob, re, json, math, csv
from pathlib import Path

raw_json_path = Path('sch_tests/qemu_results/raw_iterations_results.json')
data = json.loads(raw_json_path.read_text())

logs = sorted(glob.glob('sch_tests/qemu_results/raw_logs/*_w9.log'))
for l in logs:
    fname = Path(l).name
    m = re.match(r'(.*)_run(\d+)_w9\.log', fname)
    if not m:
        continue
    sched, run = m.group(1), m.group(2)
    text = open(l).read()
    last_w = text.rfind('Wakeup Latencies')
    last_r = text.rfind('Request Latencies')
    last_rps = text.rfind('RPS percentiles')
    w_text = text[last_w:last_r]
    r_text = text[last_r:last_rps] if last_rps != -1 else text[last_r:]
    w_50 = re.search(r'50\.0th:\s*(\d+)', w_text)
    w_99 = re.search(r'99\.0th:\s*(\d+)', w_text)
    r_50 = re.search(r'50\.0th:\s*(\d+)', r_text)
    r_99 = re.search(r'99\.0th:\s*(\d+)', r_text)
    rps = re.search(r'average rps:\s*([\d\.]+)', text)
    if not rps:
        rps = re.search(r'current rps:\s*([\d\.]+)', text)

    if sched in data and run in data[sched]:
        data[sched][run]['schbench_wakeup_p50_us'] = float(w_50.group(1)) if w_50 else 0.0
        data[sched][run]['schbench_wakeup_p99_us'] = float(w_99.group(1)) if w_99 else 0.0
        data[sched][run]['schbench_request_p50_us'] = float(r_50.group(1)) if r_50 else 0.0
        data[sched][run]['schbench_request_p99_us'] = float(r_99.group(1)) if r_99 else 0.0
        data[sched][run]['schbench_rps'] = float(rps.group(1)) if rps else 0.0

raw_json_path.write_text(json.dumps(data, indent=2))
print('Successfully updated raw_iterations_results.json with schbench metrics!')

RESULTS_DIR = Path('sch_tests/qemu_results')
all_metrics = set()
for s, runs in data.items():
    for r, m in runs.items():
        all_metrics.update(m.keys())
metric_list = sorted(list(all_metrics))

csv_path = RESULTS_DIR / 'qemu_3x_results.csv'
with open(csv_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['scheduler', 'metric', 'mean', 'stddev', 'min', 'max', 'run1', 'run2', 'run3'])
    for s, runs in data.items():
        for m in metric_list:
            vals = [runs[r][m] for r in ['1', '2', '3'] if r in runs and m in runs[r]]
            if not vals:
                continue
            mean = sum(vals) / len(vals)
            stddev = math.sqrt(sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
            r1 = vals[0] if len(vals) > 0 else ''
            r2 = vals[1] if len(vals) > 1 else ''
            r3 = vals[2] if len(vals) > 2 else ''
            writer.writerow([s, m, f'{mean:.4f}', f'{stddev:.4f}', f'{min(vals):.4f}', f'{max(vals):.4f}', r1, r2, r3])

report_path = RESULTS_DIR / 'QEMU_16_WORKLOADS_3X_REPORT.md'
lines = [
    '# Rigorous Isolated QEMU/KVM 16-Workload 3x Benchmark Report',
    '',
    '**Host Microarchitecture:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 physical cores, 24 threads)',
    '**Host Partitioning:** Cores 0,1 (P) + 8-11 (E) (12 threads), L3 Cache 00ff, Device IRQs f03f03 (Zero host tasks on VM cores)',
    '**Guest VM Configuration:** 12 vCPUs pinned 1:1 to isolated silicon cores (2-7, 14-19: 2 P-cores @ 5.16GHz + 4 E-cores @ 3.29GHz), 14GB RAM, Kernel 7.0.0-38-generic, L3 Cache ff00',
    '**Statistical Integrity:** 3 complete independent iterations per scheduler with Mean, StdDev (Variance), Min, and Max.',
    '',
    '---',
    '',
    '## Statistical Performance Summary Across 3 Repetitions',
    '',
    '| Benchmark Workload | Metric | CFS / EEVDF (Mean ± σ) | `scx_optima` (Mean ± σ) | `scx_rusty` (Mean ± σ) | `scx_rlfifo` (Mean ± σ) | Advantage (`scx_optima` vs CFS) |',
    '| :--- | :--- | :--- | :--- | :--- | :--- | :--- |'
]

for m in metric_list:
    cfs_vals = [data.get('default_cfs_eevdf', {}).get(r, {}).get(m) for r in ['1', '2', '3'] if m in data.get('default_cfs_eevdf', {}).get(r, {})]
    opt_vals = [data.get('scx_optima', {}).get(r, {}).get(m) for r in ['1', '2', '3'] if m in data.get('scx_optima', {}).get(r, {})]
    rus_vals = [data.get('scx_rusty', {}).get(r, {}).get(m) for r in ['1', '2', '3'] if m in data.get('scx_rusty', {}).get(r, {})]
    rlf_vals = [data.get('scx_rlfifo', {}).get(r, {}).get(m) for r in ['1', '2', '3'] if m in data.get('scx_rlfifo', {}).get(r, {})]

    def fmt(vals):
        if not vals:
            return 'N/A'
        avg = sum(vals) / len(vals)
        sd = math.sqrt(sum((x - avg) ** 2 for x in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
        return f'{avg:.2f} ± {sd:.2f}'

    cfs_fmt = fmt(cfs_vals)
    opt_fmt = fmt(opt_vals)
    rus_fmt = fmt(rus_vals)
    rlf_fmt = fmt(rlf_vals)

    cfs_avg = sum(cfs_vals) / len(cfs_vals) if cfs_vals else 0
    opt_avg = sum(opt_vals) / len(opt_vals) if opt_vals else 0
    if cfs_avg > 0 and opt_avg > 0:
        diff = ((opt_avg - cfs_avg) / cfs_avg) * 100
        diff_str = f'{diff:+.1f}%'
    else:
        diff_str = '--'

    lines.append(f'| `{m}` | Mean ± StdDev | {cfs_fmt} | **{opt_fmt}** | {rus_fmt} | {rlf_fmt} | **{diff_str}** |')

report_path.write_text('\n'.join(lines) + '\n')
print(f'[+] Clean updated report written to {report_path}')
