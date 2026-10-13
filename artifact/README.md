# Artifact Evaluation: OPTIMA

**Title:** OPTIMA: Density-Aware Task Scheduling and Low-Latency IPC on Asymmetric Multiprocessors with Linux sched_ext  
**Author:** Risheendra MN (`rishindra.hackbox@gmail.com`)  
**ORCID:** [0009-0004-5568-614X](https://orcid.org/0009-0004-5568-614X)  
**Target Badges:** Artifact Available, Artifact Functional, Results Reproduced  
**Target Venues:** USENIX Annual Technical Conference (ATC) / ACM European Conference on Computer Systems (EuroSys)  
**Public Repository:** [https://github.com/rishindra2005/scx_optima](https://github.com/rishindra2005/scx_optima)  

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
scx_optima/
├── artifact/                              <- Complete Artifact Evaluation Bundle
│   ├── README.md                          <- This comprehensive replication guide
│   ├── setup_env.sh                       <- Prerequisite validation & build script
│   ├── run_artifact_eval.sh               <- Execution harness (smoke, rapl, full)
│   ├── parse_results.py                   <- Results aggregator & publication table formatter
│   ├── kernel_config_sched_ext.config     <- Recommended kernel config fragment
│   ├── data/                              <- Complete raw benchmark datasets & logs
│   │   ├── qemu_3x_results.csv            <- 16-workload 3x repetition dataset (Mean, σ, Min, Max)
│   │   ├── raw_iterations_results.json    <- Full per-iteration raw metrics
│   │   ├── QEMU_16_WORKLOADS_3X_REPORT.md <- Markdown summary table with error bars
│   │   └── rapl_energy_results.json       <- 5-repetition hardware RAPL energy data
│   └── papers/                            <- Compiled camera-ready PDFs & proofs
│       ├── scx_optima_paper_usenix.pdf    <- USENIX ATC compliant 12-page paper
│       ├── scx_optima_paper.pdf           <- IEEE conference format paper
│       └── optima_theory_proofs.pdf       <- Complete standalone formal proofs
├── docs/
│   ├── paper/                             <- Full LaTeX source files & bibliography
│   │   ├── scx_optima_paper_usenix.tex    <- USENIX ATC LaTeX source
│   │   ├── scx_optima_paper.tex           <- IEEE format LaTeX source
│   │   ├── scx_optima_paper.md            <- Full Markdown paper mirror
│   │   └── references.bib                 <- Audited bibliography
│   └── optima_theory_proofs.tex           <- Standalone formal proofs LaTeX source
├── scheds/rust/scx_optima/
│   ├── src/bpf/main.bpf.c                 <- Kernel eBPF implementation (WSPT, OAPF, IPC bypass)
│   ├── src/main.rs                        <- User-space topology detection & BPF loader
│   └── Cargo.toml
├── sch_tests/                             <- Workload benchmarks & harness
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

## 4. Hardware RAPL Energy Evaluation (Table IV)

To measure hardware package/core energy (Joules), average power (Watts), and Energy-Delay Product (EDP, $\text{J}\cdot\text{s}$) across CFS, `scx_optima`, `scx_rusty`, and `scx_rlfifo`:

```bash
# Run 5 repetitions per workload across all 4 schedulers (~3.5 minutes):
./artifact/run_artifact_eval.sh rapl
```

Or view the pre-collected 5-repetition RAPL energy table directly:
```bash
python3 artifact/parse_results.py --rapl
```

### Measured Energy Targets (Table IV in Paper):
- **Synchronous IPC (`pipe_pingpong`):** OPTIMA drops package energy from $4.01 \pm 0.62\,\text{J}$ to $3.10 \pm 0.17\,\text{J}$ (**-22.7%**), core energy from $0.96 \pm 0.18\,\text{J}$ to $0.71 \pm 0.51\,\text{J}$ (**-26.0%**), and EDP from $0.58 \pm 0.19\,\text{J}\cdot\text{s}$ to $0.42 \pm 0.02\,\text{J}\cdot\text{s}$ (**-27.6%**).
- **Process Churn (`hackbench_proc`):** OPTIMA cuts package energy from $6.73 \pm 0.78\,\text{J}$ to $4.12 \pm 0.18\,\text{J}$ (**-38.8%**) and slashes EDP by **-65.3%** ($1.01 \pm 0.23\,\text{J}\cdot\text{s}$ vs $0.35 \pm 0.04\,\text{J}\cdot\text{s}$).

---

## 5. Reproducing Paper Table II (16-Workload Benchmark Suite)

The repository includes the full isolated multi-run dataset across all 16 production workloads and 34 evaluated metrics in `artifact/data/`:

```bash
# View Table II with Mean ± StdDev for all 34 evaluated metrics:
python3 artifact/parse_results.py --benchmarks
```

### Key Claims and Reproduction Targets:
1. **IPC Ping-Pong Latency:** `perf_pipe_lat_us` drops from $16.06 \pm 0.65\,\mu\text{s}$ (CFS) to $2.55 \pm 0.05\,\mu\text{s}$ (OPTIMA), a **-84.1% reduction** with a 6.3$\times$ throughput surge to $392,691 \pm 7,314\,\text{ops/s}$.
2. **Network Loopback Bandwidth:** `iperf3_throughput_gbps` increases from $57.58 \pm 2.51\,\text{Gbps}$ (CFS) to $111.47 \pm 5.19\,\text{Gbps}$ (OPTIMA), a **+93.6% gain**.
3. **Real-Time Jitter Spike:** `cyclictest_max_spike_us` drops from $8,383.00 \pm 8,269.70\,\mu\text{s}$ (CFS) to $450.00 \pm 339.69\,\mu\text{s}$ (OPTIMA), a **-94.6% reduction**.
4. **Zero Audio Underruns & Game Drops:** `audio_xruns` is $0.00 \pm 0.00$ (vs $3.67 \pm 0.58$ on CFS); `game_frame_drops_pct` is $0.000\%$.
5. **Compilation Trade-off Transparency:** `kernel_compile_s` on OPTIMA is $6.03 \pm 0.08\,\text{s}$, matching extensible peer schedulers ($5.67 \pm 0.16\,\text{s}$ on `scx_rusty`, $5.83 \pm 0.06\,\text{s}$ on `scx_rlfifo`), while trailing native in-kernel CFS ($3.29 \pm 0.11\,\text{s}$) due to eBPF struct-ops trampoline overhead during fork/exec storms.
6. **High-Concurrency Request Tails:** In `schbench` and Redis, standard OAPF incurred queuing delays due to equal-priority worker non-preemption ($w_{\mathrm{curr}} = w_{\mathrm{wake}}$). OAPF-Adaptive remediates this via soft peer-preemption hysteresis after $1\,\text{ms}$ of progress.

---

## 6. Accessing Pre-Compiled Papers and Proofs

Pre-compiled PDFs are bundled directly in `artifact/papers/`:
- `artifact/papers/scx_optima_paper_usenix.pdf`: USENIX ATC compliant 12-page main-body conference paper.
- `artifact/papers/scx_optima_paper.pdf`: IEEE double-column conference paper.
- `artifact/papers/optima_theory_proofs.pdf`: Complete standalone formal mathematical proofs (Theorems 1–4, Kawaguchi-Kyan context, Lyapunov drift derivation, and starvation bounds).

To recompile from LaTeX source:
```bash
# 1. Compile USENIX ATC Paper
cd docs/paper
pdflatex -interaction=nonstopmode scx_optima_paper_usenix.tex
bibtex scx_optima_paper_usenix
pdflatex -interaction=nonstopmode scx_optima_paper_usenix.tex
pdflatex -interaction=nonstopmode scx_optima_paper_usenix.tex

# 2. Compile Standalone Theory Proofs
cd ../
pdflatex -interaction=nonstopmode optima_theory_proofs.tex
```

---

## 7. Contact & Support

For questions, issues, or assistance during Artifact Evaluation:
- **Author:** Risheendra MN
- **Email:** `rishindra.hackbox@gmail.com`
- **ORCID:** [0009-0004-5568-614X](https://orcid.org/0009-0004-5568-614X)
- **GitHub Repository:** [https://github.com/rishindra2005/scx_optima](https://github.com/rishindra2005/scx_optima)
