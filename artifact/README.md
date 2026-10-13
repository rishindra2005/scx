# Artifact Evaluation: OPTIMA

**Title:** OPTIMA: Density-Aware Task Scheduling and Low-Latency IPC on Asymmetric Multiprocessors with Linux sched_ext  
**Author:** Risheendra MN (`rishindra.hackbox@gmail.com`)  
**Target Badges:** Artifact Available, Artifact Functional, Results Reproduced  
**Target Venues:** USENIX Annual Technical Conference (ATC) / ACM European Conference on Computer Systems (EuroSys)

---

## 1. Overview & System Requirements

OPTIMA is an algorithmic extensible operating system scheduler implemented in Linux `sched_ext` (eBPF). It enforces density-based WSPT sequencing, an Online Admissible Preemption Filter (OAPF) with adaptive peer-preemption hysteresis, and a synchronous IPC zero-lag fast-path across heterogeneous asymmetric multiprocessors (AMP).

### Hardware Requirements
- **Host Processor:** Asymmetric multi-core processor with Performance (P) and Efficiency (E) cores.
  - *Primary Testbed:* AMD Ryzen AI 9 HX 370 (Strix Point: 12 physical cores, 24 hardware threads; 4 Zen 5 P-cores @ 5.16 GHz with 16 MB L3 + 8 Zen 5c E-cores @ 3.29 GHz with 8 MB L3; dual-CCX layout; speedup factor $\mu = 1.57$).
  - *Compatible Platforms:* Intel Hybrid 12th--14th Gen (Alder Lake / Raptor Lake, e.g., Core i7-12700H, i9-13900K); ARM big.LITTLE / DynamIQ (e.g., Rockchip RK3588, Ampere, Apple Silicon M-series).
- **RAM:** Minimum 8 GB (16 GB recommended).
- **Energy Counters:** Intel/AMD RAPL hardware energy counters supported via sysfs (`/sys/class/powercap/intel-rapl:0/energy_uj`) or Linux `perf` PMU (`power/energy-pkg/`).

### Software Requirements
- **Operating System:** Linux Kernel $\ge 6.12$ compiled with:
  - `CONFIG_SCHED_CLASS_EXT=y`
  - `CONFIG_BPF_SCHED_EXT=y`
  - `CONFIG_DEBUG_INFO_BTF=y`
  - `CONFIG_INTEL_RAPL=y` or `CONFIG_AMD_ENERGY=m`
  *(See `artifact/kernel_config_sched_ext.config` for complete kernel options).*
- **Compilers & Toolchain:**
  - Rust 1.75+ (Cargo)
  - LLVM / Clang 16+
  - `bpftool`, `libbpf-dev`
- **Benchmarking Tools:** `sysbench`, `perf`, `rt-tests` (`hackbench`, `cyclictest`), `iperf3`.
- **Python:** Python 3.8+ (standard library only).

---

## 2. Directory Structure

```text
scx/
├── artifact/
│   ├── README.md                          <- This evaluation guide
│   ├── setup_env.sh                       <- Prerequisite validation & build script
│   ├── run_artifact_eval.sh               <- Execution harness (smoke, rapl, full)
│   ├── parse_results.py                   <- Results aggregator & LaTeX/Markdown table formatter
│   └── kernel_config_sched_ext.config     <- Recommended kernel config fragment
├── docs/
│   ├── paper/
│   │   ├── scx_optima_paper.tex           <- Full conference paper (LaTeX source)
│   │   ├── scx_optima_paper_usenix.tex    <- USENIX ATC compliant paper variant
│   │   ├── scx_optima_paper.md            <- Complete Markdown paper mirror
│   │   └── references.bib                 <- Audited bibliography
│   └── optima_theory_proofs.tex           <- Complete formal mathematical proofs
├── scheds/rust/scx_optima/
│   ├── src/bpf/main.bpf.c                 <- Kernel eBPF implementation (WSPT, OAPF, IPC bypass)
│   ├── src/main.rs                        <- User-space topology detection & BPF loader
│   └── Cargo.toml
├── sch_tests/
│   ├── qemu_results/                      <- Isolated 16-workload 3x repetition dataset
│   │   ├── qemu_3x_results.csv            <- Raw CSV dataset (mean, stddev, min, max)
│   │   ├── raw_iterations_results.json    <- Full iteration logs for all 34 metrics
│   │   └── QEMU_16_WORKLOADS_3X_REPORT.md <- Markdown summary table with error bars
│   └── rapl_energy_results.json           <- Multi-run hardware RAPL energy data
└── scripts/
    └── measure_rapl_energy.py             <- Automated 5-repetition RAPL energy benchmark
```

---

## 3. Quick Start: Smoke Test (< 1 Minute)

To verify that the build environment is sound and that `scx_optima` attaches to `sched_ext` successfully:

```bash
# 1. Run environment sanity check
./artifact/setup_env.sh

# 2. Run quick smoke test
./artifact/run_artifact_eval.sh smoke
```

**Expected Smoke Test Output:**
- `scx_optima` detects host P-core and E-core topology:
  - P-cores: `[0, 1, 2, 3, 12, 13, 14, 15]`
  - E-cores: `[4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18, 19, 20, 21, 22, 23]`
- Attaches to `sched_ext` kernel struct-ops with zero verifier errors.
- Runs bidirectional pipe ping-pong latency test:
  - Baseline CFS: ~16.06 $\mu$s
  - `scx_optima`: ~2.55 $\mu$s (**84.1% latency reduction**)

---

## 4. Hardware RAPL Energy Evaluation (Step-by-Step)

To measure hardware package/core energy (Joules), average power (Watts), and Energy-Delay Product (EDP, $\text{J}\cdot\text{s}$) across CFS, `scx_optima`, `scx_rusty`, and `scx_rlfifo`:

```bash
# Run 5 repetitions per workload across all 4 schedulers (~3.5 minutes)
./artifact/run_artifact_eval.sh rapl
```

The script measures:
1. `sysbench_cpu` (12 threads, compute-heavy prime calculation)
2. `sysbench_mem` (12 threads, memory bandwidth saturation)
3. `pipe_pingpong` (100k bidirectional IPC operations)
4. `hackbench_proc` (Process group IPC churn)

Output tables will report:
- `Pkg Energy (J)` ($\text{Mean} \pm \sigma$)
- `Core Energy (J)` ($\text{Mean} \pm \sigma$)
- `Avg Power (W)` ($\text{Mean} \pm \sigma$)
- `EDP (J·s)` ($\text{Mean} \pm \sigma$)

---

## 5. Reproducing Paper Table II (16-Workload Benchmark Suite)

The repository includes the full isolated multi-run dataset across all 16 production workloads and 34 evaluated metrics in `sch_tests/qemu_results/`:

```bash
# View Table II with Mean ± StdDev for all 34 evaluated metrics:
python3 artifact/parse_results.py
```

### Key Claims and Reproduction Targets:
1. **IPC Ping-Pong Latency:** `perf_pipe_lat_us` drops from $16.06 \pm 0.65\,\mu\text{s}$ (CFS) to $2.55 \pm 0.05\,\mu\text{s}$ (OPTIMA), a **-84.1% reduction** with a 6.3$\times$ throughput surge to $392,691 \pm 7,314\,\text{ops/s}$.
2. **Network Loopback Bandwidth:** `iperf3_throughput_gbps` increases from $57.58 \pm 2.51\,\text{Gbps}$ (CFS) to $111.47 \pm 5.19\,\text{Gbps}$ (OPTIMA), a **+93.6% gain**.
3. **Real-Time Jitter Spike:** `cyclictest_max_spike_us` drops from $8,383.00 \pm 8,269.70\,\mu\text{s}$ (CFS) to $450.00 \pm 339.69\,\mu\text{s}$ (OPTIMA), a **-94.6% reduction**.
4. **Zero Audio Underruns & Game Drops:** `audio_xruns` is $0.00 \pm 0.00$ (vs $3.67 \pm 0.58$ on CFS); `game_frame_drops_pct` is $0.000\%$.
5. **Compilation Trade-off Transparency:** `kernel_compile_s` on OPTIMA is $6.03 \pm 0.08\,\text{s}$, matching extensible peer schedulers ($5.67 \pm 0.16\,\text{s}$ on `scx_rusty`, $5.83 \pm 0.06\,\text{s}$ on `scx_rlfifo`), while trailing native in-kernel CFS ($3.29 \pm 0.11\,\text{s}$) due to eBPF struct-ops trampoline overhead during fork/exec storms.
6. **High-Concurrency Request Tails:** In `schbench` and Redis, standard OAPF incurred queuing delays due to equal-priority worker non-preemption ($w_{\mathrm{curr}} = w_{\mathrm{wake}}$). OAPF-Adaptive remediates this via soft peer-preemption hysteresis after $1\,\text{ms}$ of progress.

---

## 6. Paper and Formal Theory Compilation

All formal mathematical proofs and paper variants are fully compilable from source:

```bash
# 1. Compile IEEE Conference Paper
pdflatex docs/paper/scx_optima_paper.tex
bibtex scx_optima_paper
pdflatex docs/paper/scx_optima_paper.tex
pdflatex docs/paper/scx_optima_paper.tex

# 2. Compile USENIX ATC Paper
pdflatex docs/paper/scx_optima_paper_usenix.tex
bibtex scx_optima_paper_usenix
pdflatex docs/paper/scx_optima_paper_usenix.tex
pdflatex docs/paper/scx_optima_paper_usenix.tex

# 3. Compile Standalone Theory Proofs
pdflatex docs/optima_theory_proofs.tex
```

---

## 7. Contact & Support

For questions, issues, or assistance during Artifact Evaluation:
- **Author:** Risheendra MN
- **Email:** `rishindra.hackbox@gmail.com`
- **Repository Branch:** `DAA`
