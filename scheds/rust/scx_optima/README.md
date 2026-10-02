# scx_optima: Algorithmic Linux Kernel Scheduler with Formal Complexity Guarantees

`scx_optima` is a formally analyzed, deterministic Linux CPU scheduler implemented as an eBPF `sched_ext` kernel module. Designed as a capstone project for **CS301 (Design and Analysis of Algorithms)**, it replaces heuristic and machine-learning approaches with classical algorithmic paradigms that provide provable mathematical guarantees:

1. **Greedy Paradigm (Module 6):** Weighted Shortest Processing Time first (WSPT) job sequencing with virtual deadlines minimizing total weighted completion time ($\sum w_i C_i$) in $\mathcal{O}(n \log n)$.
2. **Dynamic Programming Paradigm (Modules 4–5):** Multi-stage heterogeneous core knapsack allocation partitioning latency-critical working sets to Zen 5 Performance cores (P-cores) and batch tasks to Zen 5c Efficiency cores (E-cores).
3. **Branch-and-Bound Paradigm (Module 8):** Real-time priority inversion resolution via state-space preemption tree search with admissible lower-bound pruning ($LB(c) \ge UB_{\text{best}}$).

---

## 1. Algorithmic Architecture

```
                 +---------------------------------------------+
                 |            Task Wakeup / Enqueue            |
                 +---------------------------------------------+
                                        |
                 +---------------------------------------------+
                 | [Module 4-5] Dynamic Programming Knapsack   |
                 | Partition: Interactive -> P, Compute -> E   |
                 +---------------------------------------------+
                                        |
                 +---------------------------------------------+
                 |   [Module 6] Greedy WSPT Density Ordering   |
                 |   vtime = cur_vtime + slice * (1 / rho_i)   |
                 +---------------------------------------------+
                                        |
                 +----------------------+----------------------+
                 |                                             |
                 v                                             v
        +------------------+                          +------------------+
        | DSQ_PERF (vtime) |                          |  DSQ_EFF (vtime) |
        | Zen 5 P-cores    |                          |  Zen 5c E-cores  |
        +------------------+                          +------------------+
                 |                                             |
                 +----------------------+----------------------+
                                        |
                 +---------------------------------------------+
                 | [Module 8] Branch-and-Bound Priority Guard  |
                 | Prune suboptimal cores; preempt on inversion|
                 +---------------------------------------------+
                                        |
                 +---------------------------------------------+
                 | Work-Conserving Dispatch & Two-Tier Steal   |
                 +---------------------------------------------+
```

---

## 2. Hardware PMU & Comparative Benchmarks

Evaluated on an **AMD Ryzen AI 9 HX 370** (24 logical cores: 8 Zen 5 P-cores @ 5.16 GHz + 16 Zen 5c E-cores @ 3.29 GHz) running Linux 6.18.0-rc7 against standard Linux schedulers:

| Benchmark Metric | `scx_optima` (DAA) | `scx_rdtai` (Q-Learning) | `scx_rusty` | Linux CFS/EEVDF |
| :--- | :--- | :--- | :--- | :--- |
| **Hardware Cache Miss Rate** | **11.94%** 🥇 | 18.07% | 22.88% | ~18.5% |
| **Schbench Wakeup (Median)** | **59 $\mu\text{s}$** 🥇 | 245 $\mu\text{s}$ | 691 $\mu\text{s}$ | ~800 $\mu\text{s}$ |
| **Redis GET P99 Tail Latency** | **0.367 ms** 🥇 | 0.519 ms | 0.567 ms | ~0.55 ms |
| **Redis SET P50 Median Latency** | **0.199 ms** 🥇 | 0.327 ms | 0.327 ms | ~0.35 ms |
| **Hackbench IPC Runtime** | **0.948 s** 🥇 | 1.804 s | 1.759 s | 1.050 s |
| **Maximum Jitter Spike** | **451 $\mu\text{s}$** 🥈 | 1,101 $\mu\text{s}$ | 765 $\mu\text{s}$ | ~600 $\mu\text{s}$ |
| **Full Kernel Compile (`bzImage`)**| 161.76 s | 163.07 s | 160.03 s | **156.15 s** 🥇 |

---

## 3. Mathematical Proofs

Complete mathematical proofs for all theorems are provided in **[`PROOF_DAA.md`](PROOF_DAA.md)**:
* **Theorem 1 (WSPT Optimality):** Proven via adjacent pairwise job exchange contradiction ($\mathcal{O}(n \log n)$).
* **Theorem 2 (DP Optimal Substructure):** Proven via Bellman's principle of optimality on heterogeneous processor capacities.
* **Theorem 3 (B&B Pruning Admissibility):** Formally proven safe against false prunings ($LB(c) \le \text{OPT}$).

---

## 4. Building and Running

### Prerequisites
* Linux kernel 6.12+ with `CONFIG_SCHED_CLASS_EXT=y`
* Rust toolchain (nightly or stable 1.80+)
* `clang` 17+, `llvm`, `libelf`, `zlib`

### Build
```bash
cargo build --release -p scx_optima
```

### Run
```bash
sudo ./target/release/scx_optima
```

### Options
```text
Usage: scx_optima [OPTIONS]

Options:
  -s, --slice-us <SLICE_US>          Base time slice in microseconds [default: 2000]
      --dp-threshold <DP_THRESHOLD>  Task density threshold for DP P-core partitioning [default: 500000]
      --dp-capacity <DP_CAPACITY>    P-core capacity limit before DP offloads to E-cores [default: 8]
      --stats <STATS>                Display live algorithmic statistics interval (s) [default: 1]
  -v, --verbose...                   Verbose BPF debug output
  -h, --help                         Print help
```

---

## 5. Author & License

* **Author:** Risheendra MN (<rishindra.hackbox@gmail.com>)
* **Project:** CS301 (Design and Analysis of Algorithms - DAA)
* **License:** GPL-2.0

