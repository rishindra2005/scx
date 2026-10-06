import json

# Load stress telemetry
with open('benchmarks/production_apps/runner/results/stress_to_failure_telemetry.json') as f:
    stress_data = json.load(f)

# Build rows for stress table
stress_table_rows = ""
for item in stress_data:
    sched = item['scheduler']
    app = item['application']
    max_tp = item['max_stable_throughput']
    unit = item['throughput_unit']
    brk = item['breaking_point']
    reason = item['failure_reason']
    
    badge_class = "text-emerald-700 bg-emerald-50 border border-emerald-200" if ("None" in str(reason) or "PERFECT" in str(reason) or "HEALTHY" in str(reason)) else "text-rose-700 bg-rose-50 border border-rose-200"
    is_optima = "scx_optima" in sched
    row_bg = "bg-blue-50/50 font-bold" if is_optima else "hover:bg-slate-50/60"

    stress_table_rows += f"""
    <tr class="{row_bg}">
      <td class="py-2.5 px-3 font-sans font-semibold text-slate-800">{sched}</td>
      <td class="py-2.5 px-3 text-slate-600">{app.replace('_', ' ')}</td>
      <td class="py-2.5 px-3 font-mono">{max_tp:,.2f} {unit}</td>
      <td class="py-2.5 px-3 font-mono text-slate-700">{brk}</td>
      <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded text-[11px] font-sans {badge_class}">{reason}</span></td>
    </tr>
    """

# Read existing index.html head & tail structure to ensure clean styling
with open('benchmarks/dashboard/results_real.html', 'r') as f:
    old_content = f.read()

# Let's generate the updated results_real.html with the Dual Comparative Lab tab
