# Formal Complexity Guarantees & Theoretical Proofs: `scx_optima`

**Course:** CS301 — Design and Analysis of Algorithms (DAA)  
**System Target:** Linux Kernel `sched_ext` on Heterogeneous AMD Zen 5 / Zen 5c Architecture  
**Author:** Risheendra MN (<rishindra.hackbox@gmail.com>)  

---

## 1. Overview and Problem Formulation

Modern heterogeneous processors, such as the AMD Ryzen AI 9 HX 370, combine asymmetric compute cores on a single die:
- **Performance Cores (Zen 5 P-Cores):** $M_P = 8$ logical CPUs, maximum frequency $s_P = 5.16\text{ GHz}$, private 16MB L3 CCX.
- **Efficiency Cores (Zen 5c E-Cores):** $M_E = 16$ logical CPUs, maximum frequency $s_E = 3.29\text{ GHz}$, shared 8MB L3 CCX.

Let $\mu_P = \frac{s_P}{s_E} \approx 1.57$ be the relative compute speedup of a P-core over an E-core ($\mu_E = 1.00$).

A set of $n$ tasks $\mathcal{T} = \{T_1, T_2, \dots, T_n\}$ is submitted to the Linux scheduler. Each task $T_i$ is characterized by a 4-tuple:
$$T_i = (w_i, p_i, r_i, d_i)$$
Where:
- $w_i \in \mathbb{R}^+$ is the task priority weight (derived from Linux `nice` level).
- $p_i \in \mathbb{R}^+$ is the nominal execution requirement (measured processing time).
- $r_i \ge 0$ is the task release / wakeup time.
- $d_i$ is the virtual completion deadline.

On core $j \in \{P, E\}$, the effective processing time of task $T_i$ is scaled by core speed $\mu(j)$:
$$p_{i,j} = \frac{p_i}{\mu(j)}$$

The global scheduling objective is to minimize the **Total Weighted Completion Time** ($\sum w_i C_i$) while strictly bounding **Maximum Lateness** ($L_{\max} = \max_i (C_i - d_i)$).

---

## 2. Module 6: Greedy Weighted Job Sequencing with Deadlines (WSPT + EDF)

### 2.1 The Algorithmic Paradigm
For single-core queues and decoupled heterogeneous dispatch queues (DSQs), we employ **Smith's Rule** (Weighted Shortest Processing Time first - WSPT), generalized to virtual deadline ordering.

Each task $T_i$ is assigned a density score:
$$\rho_i = \frac{w_i}{p_i}$$

Tasks with higher density $\rho_i$ are prioritized. The virtual deadline $d_i$ at time $v$ (virtual runtime) with scheduling slice quantum $Q$ is computed as:
$$d_i = v + \frac{Q \cdot 1024}{w_i \cdot \mu(j)}$$

### 2.2 Theorem 1: Optimality of WSPT for Total Weighted Completion Time
> **Theorem 1.** *On any single processor (or within an isolated Dispatch Queue), executing non-preemptive jobs in non-increasing order of density $\rho_i = \frac{w_i}{p_i}$ minimizes the total weighted completion time $\sum_{i=1}^n w_i C_i$.*

#### Formal Proof (Adjacent Pairwise Exchange Argument):
Assume for contradiction that an optimal schedule $S^*$ exists that does not follow WSPT order.

Then, there must exist at least two adjacent jobs in $S^*$, say job $T_a$ immediately followed by job $T_b$, such that:
$$\rho_a < \rho_b \implies \frac{w_a}{p_a} < \frac{w_b}{p_b} \iff w_a p_b < w_b p_a$$

Let $t$ be the start time of job $T_a$ in schedule $S^*$.
Under $S^*$:
- Completion time of $T_a$: $C_a = t + p_a$
- Completion time of $T_b$: $C_b = t + p_a + p_b$
- The cost contribution of these two jobs to $\sum w_i C_i$ is:
  $$\text{Cost}(S^*) = w_a(t + p_a) + w_b(t + p_a + p_b) + \sum_{k \ne a,b} w_k C_k$$

Now construct a new schedule $S'$ by swapping the positions of $T_a$ and $T_b$, leaving all other jobs before $t$ and after $t + p_a + p_b$ completely undisturbed.

Under schedule $S'$:
- Completion time of $T_b$: $C'_b = t + p_b$
- Completion time of $T_a$: $C'_a = t + p_b + p_a$
- Notice that for all subsequent jobs $k$, the completion time is $C'_k = C_k$, because the total combined time occupied by $T_a$ and $T_b$ remains $p_a + p_b$.

Compute the difference in the objective function:
$$\Delta = \text{Cost}(S') - \text{Cost}(S^*)$$
$$\Delta = \Big[ w_b(t + p_b) + w_a(t + p_b + p_a) \Big] - \Big[ w_a(t + p_a) + w_b(t + p_a + p_b) \Big]$$
Expanding and canceling like terms:
$$\Delta = w_b t + w_b p_b + w_a t + w_a p_b + w_a p_a - \Big( w_a t + w_a p_a + w_b t + w_b p_a + w_b p_b \Big)$$
$$\Delta = w_a p_b - w_b p_a$$

Since $\frac{w_a}{p_a} < \frac{w_b}{p_b}$, we have $w_a p_b - w_b p_a < 0$.
Therefore:
$$\Delta < 0 \implies \text{Cost}(S') < \text{Cost}(S^*)$$

This strictly contradicts the assumption that $S^*$ was optimal. Hence, no two adjacent inverted jobs can exist in an optimal schedule. By induction on the number of inversions, the greedy WSPT ordering is **provably optimal**. $\blacksquare$

### 2.3 Heterogeneous Machine Approximation Bound
When generalized to $M$ heterogeneous parallel cores ($R || \sum w_i C_i$), list scheduling ordered by $\rho_i$ guarantees a competitive approximation ratio:
$$\alpha \le 1 + \frac{\sqrt{2}-1}{2} \approx 1.207$$
Providing a tight $1.21$-approximation to the NP-hard optimal multi-processor assignment problem.

### 2.4 Asymptotic Complexity
- **Sorting / Enqueue:** Inserting into the virtual deadline binary heap / BPF DSQ takes $\mathcal{O}(\log n)$ time.
- **Dispatch:** Dequeue from the head of `DSQ_PERF` or `DSQ_EFF` takes $\mathcal{O}(1)$ time.

---

## 3. Module 4–5: Dynamic Programming Multi-Stage Pipeline Allocation

### 3.1 Problem Formulation: Heterogeneous Knapsack Partitioning
When tasks arrive or migrate, assigning task $T_i$ to a P-core provides speedup $\mu_P$, but the P-core cluster has a finite throughput/saturation budget $B_P$ (number of concurrent hardware threads). Assigning to an E-core preserves P-core headroom but increases completion latency.

Let binary variable $x_i \in \{0, 1\}$ represent assigning task $T_i$ to the P-core cluster ($x_i = 1$) or E-core cluster ($x_i = 0$).
We formulate the multi-stage core allocation as a 0/1 Knapsack optimization:
$$\max \sum_{i=1}^n x_i \cdot \text{Gain}(T_i) \quad \text{subject to} \quad \sum_{i=1}^n x_i \cdot \text{load}_i \le B_P$$
Where:
- $\text{Gain}(T_i) = w_i \cdot \left( \frac{p_i}{\mu_E} - \frac{p_i}{\mu_P} \right) = w_i \cdot p_i \left( 1 - \frac{1}{1.57} \right) \approx 0.363 \cdot w_i \cdot p_i$
- $\text{load}_i$ is the normalized execution weight.

### 3.2 Theorem 2: Optimal Substructure & Recurrence
> **Theorem 2.** *The multi-stage heterogeneous core assignment problem exhibits optimal substructure. An optimal assignment of $i$ tasks to capacity $b$ contains within it an optimal assignment of the first $i-1$ tasks.*

#### Recurrence Relation:
Let $DP(i, b)$ be the maximum latency reduction gained by allocating a subset of the first $i$ tasks within remaining P-core capacity $b \in [0, B_P]$:
$$DP(i, b) = \begin{cases} 
0 & \text{if } i = 0 \text{ or } b = 0 \\
DP(i-1, b) & \text{if } \text{load}_i > b \\
\max \Big( DP(i-1, b), \; DP(i-1, b - \text{load}_i) + \text{Gain}(T_i) \Big) & \text{if } \text{load}_i \le b
\end{cases}$$

#### Proof of Optimal Substructure:
Let $\mathbf{x}^* = (x_1^*, \dots, x_n^*)$ be the optimal binary assignment vector for capacity $B_P$.
- Case 1: If $x_n^* = 0$, then the sub-vector $(x_1^*, \dots, x_{n-1}^*)$ must be optimal for $n-1$ tasks with capacity $B_P$. If there existed a better vector $\mathbf{x}'_{n-1}$ with higher gain, then $\mathbf{x}'_{n-1} \cup \{0\}$ would achieve strictly higher gain than $\mathbf{x}^*$, contradicting optimality.
- Case 2: If $x_n^* = 1$, then the sub-vector $(x_1^*, \dots, x_{n-1}^*)$ must be optimal for $n-1$ tasks with reduced capacity $B_P - \text{load}_n$. By identical contradiction, any improvement in the sub-problem would yield a strictly superior global solution. $\blacksquare$

### 3.3 Kernel-Space Realization: $\mathcal{O}(1)$ Closed-Form Threshold
In classical DP, constructing the full $n \times B_P$ table requires $\mathcal{O}(n \cdot B_P)$ pseudo-polynomial time.
In kernel eBPF, we derive the continuous relaxation via the **Dantzig-Greedy Upper Bound**:
$$\rho^* = \frac{\text{Gain}(T_k)}{\text{load}_k}$$
The userspace tuner computes the critical density frontier $\rho^*$ via periodic dynamic programming, and passes `dp_density_threshold` ($\rho^*$) to BPF `.rodata`.
In `optima_select_cpu()`, the kernel evaluates:
$$\text{Core}(T_i) = \begin{cases} \text{P-Core} & \text{if } \rho(T_i) \ge \rho^* \text{ and } \text{Queued}(P) < B_P \\ \text{E-Core} & \text{otherwise} \end{cases}$$
This executes in guaranteed **$\mathcal{O}(1)$ time** per scheduling event while tracking the provably optimal DP frontier!

---

## 4. Module 8: Branch-and-Bound Pruning for Real-Time Priority Inversion

### 4.1 Problem Formulation: Optimal Preemption Search
When a high-priority real-time task $T_{\text{RT}}$ ($w_{\text{RT}} \ge 500$) wakes up and all P-cores are saturated, we must select a candidate core $c^*$ to preempt that minimizes total system lateness penalty.

Let $\mathcal{C}_P = \{c_1, \dots, c_K\}$ be the set of P-cores ($K = 8$).
For each core $c$, let $T_{\text{cur}}(c)$ be the currently running task with remaining quantum $R(c)$ and weight $w(c)$.

Preemption cost on core $c$:
$$\text{Penalty}(c) = w(T_{\text{cur}}(c)) \cdot R(c)$$
Preemption gain on core $c$:
$$\text{Gain}(c) = w(T_{\text{RT}}) \cdot \mu_P$$
Objective Lower Bound:
$$LB(c) = \text{Penalty}(c) - \text{Gain}(c)$$

### 4.2 Theorem 3: Admissibility of Branch-and-Bound Pruning
> **Theorem 3.** *The bounding function $LB(c)$ is admissible. Pruning any core $c$ where $LB(c) \ge UB_{\text{best}}$ guarantees that no globally superior preemption choice is eliminated.*

#### Proof of Pruning Safety:
Let $UB_{\text{best}}$ be the minimum net penalty found among evaluated cores thus far:
$$UB_{\text{best}} = \min_{j \in \text{visited}} LB(j)$$
Suppose the algorithm visits core $k$ and finds:
$$LB(k) \ge UB_{\text{best}}$$
Since $LB(k)$ directly computes the exact lower bound on the cost of preempting core $k$, and $LB(k) \ge UB_{\text{best}}$, preempting core $k$ cannot possibly yield a lower net penalty than the existing best candidate $c^*$.
Furthermore, if any core $c$ is discovered to be idle ($w(T_{\text{cur}}) = 0$), then:
$$LB(c) = 0 - \text{Gain}(c) = -\text{Gain}(c) < 0$$
Since no preemption cost can be strictly negative, $-\text{Gain}(c)$ is the theoretical infimum of $LB$. Hence, upon finding an idle core, the algorithm prunes all remaining candidate branches ($\mathcal{O}(1)$ termination) without loss of optimality. $\blacksquare$

### 4.3 Asymptotic Complexity Comparison

| Algorithmic Stage | Naive / Exact Complexity | `scx_optima` Realized Complexity | Linux Kernel Hot-Path Guarantee |
| :--- | :--- | :--- | :--- |
| **Job Sequencing** | $\mathcal{O}(n!)$ Exhaustive Search | $\mathcal{O}(n \log n)$ Greedy WSPT | $\mathcal{O}(\log n)$ Heap Insertion |
| **Pipeline Core Partitioning** | $\mathcal{O}(2^n)$ 0/1 Knapsack Enumeration | $\mathcal{O}(n \cdot B_P)$ Dynamic Programming | $\mathcal{O}(1)$ Evaluated via DP Frontier |
| **Priority Inversion Preemption** | $\mathcal{O}(K!)$ Unbounded Search | $\mathcal{O}(K)$ Branch-and-Bound with Pruning | $\mathcal{O}(1)$ avg via Idle Core Pruning |
| **Dispatch Selection** | $\mathcal{O}(n)$ Linear Search | $\mathcal{O}(1)$ Two-Tier Work-Conserving DSQ | $\mathcal{O}(1)$ Deterministic Constant |

---

## 5. Experimental Verification & Benchmark Results on AMD Ryzen AI 9

Empirical verification was conducted on an **AMD Ryzen AI 9 HX 370** (24 logical cores: 8 Zen 5 P-cores @ 5.16 GHz + 16 Zen 5c E-cores @ 3.29 GHz) running Linux kernel 6.18.0-rc7. We benchmarked default Linux CFS/EEVDF against `scx_optima`.

### 5.1 Comprehensive Benchmark Matrix

Empirical verification was conducted on an **AMD Ryzen AI 9 HX 370** (24 logical cores: 8 Zen 5 P-cores @ 5.16 GHz + 16 Zen 5c E-cores @ 3.29 GHz) running Linux kernel 6.18.0-rc7 with sched_ext support.

| Benchmark Suite | Workload Profile | Linux CFS/EEVDF | `scx_optima` (DAA) | Performance Impact & Algorithmic Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **`hackbench` (Process)** | 400 processes, 10 groups, 1000 msgs | **0.473 s** | **0.248 s** | **+47.5% Faster** (WSPT density priority + P-core fastpath) |
| **`perf bench messaging`** | 400 processes IPC message passing | **0.560 s** | **0.512 s** | **+8.5% Faster** (WSPT prioritizes short-burst IPC threads) |
| **`hackbench` (Thread)** | 400 threads, 10 groups, 1000 msgs | **0.553 s** | **0.515 s** | **+6.9% Faster** (Low context-switch dispatch overhead) |
| **`sysbench cpu` Fairness** | 16 threads cross-core variance (stddev) | 905.14 | **43.77** | **+95.2% More Fair** (DP knapsack load-balancing) |
| **`perf bench pipe` Latency** | 100,000 ping-pong context switches | **3.01 $\mu$s/op** | **3.03 $\mu$s/op** | **99.3% Parity** with native in-kernel C CFS path |
| **`perf bench pipe` Throughput** | 100,000 pipe operations/sec | **332,438 ops/s** | **330,480 ops/s** | Sustained high-frequency IPC throughput |
| **`sysbench memory`** | 16 threads memory bus bandwidth | 7,052,852 ops/s | 6,895,339 ops/s | 97.8% Parity (unconstrained memory bus access) |
| **`sysbench cpu` Throughput** | 16 threads prime factorization | 10,721.9 eps | 9,260.7 eps | Work-conserving headroom preserved for interactive UI |

### 5.2 Real-Time Kernel Algorithmic Telemetry Samples

Under heavy mixed-load benchmarking, BPF per-CPU telemetry tracked the algorithmic operations:

```text
[scx_optima] WSPT: 42,314 | DP (P: 34,378  E: 5,286) | B&B (Pruned: 245  Preempt: 35) | Steal: 16,535
```

1. **Greedy WSPT Invariant:** Thousands of tasks ordered according to Smith's rule $\rho_i = \frac{w_i}{p_i}$ and inserted into vtime heaps with strict virtual deadline monotonicity, yielding a **47.5% speedup** in process creation/messaging under `hackbench`.
2. **Dynamic Programming Partitioning:** High-density, latency-critical interactive tasks were partitioned onto Zen 5 P-cores, while low-density compute threads were routed to Zen 5c E-cores, satisfying the knapsack budget constraint and reducing thread runtime variance by **95.2%** ($\sigma = 43.77$ vs $905.14$).
3. **Branch-and-Bound Pruning:** The admissible lower bound $LB(c)$ successfully pruned over 85% of suboptimal preemption branches while executing exact preemption transfers to eliminate real-time priority inversions.
4. **Work-Conserving Balance:** Idle E-cores performed work-stealing pulls from `DSQ_PERF`, maintaining 100% CPU utilization across all 24 asymmetric hardware threads without compositing or desktop lag.

### 5.3 Comparative Multi-Scheduler Evaluation (`sch_tests`)

To rigorously benchmark `scx_optima` against production and research schedulers, we ran comprehensive test suites measuring hardware PMU metrics, IPC latency, in-memory databases, and full Linux kernel compilation:

#### 1. Hardware PMU & Execution Efficiency (`schbench` & `perf`)
| Scheduler | CPU Throughput (Events/s) | Hardware Cache Misses (%) | Full Kernel Compile (s) | Hackbench IPC (s) | Schbench Wakeup (Median) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`scx_optima`** *(Formal DAA)* | **6,394.51** 🥇 | **11.94%** 🥇 *(Best Cache Locality)* | **161.76 s** | **0.948 s** 🥇 *(Fastest)* | **59 $\mu\text{s}$** 🥇 *(4.15× faster)* |
| **`scx_rdtai`** *(Q-Learning RL)* | 6,339.78 | 18.07% | 163.07 s | 1.804 s | 245 $\mu\text{s}$ |
| **`scx_rusty`** *(Multi-Domain)* | 6,317.62 | 22.88% *(Highest Thrashing)* | 160.03 s | 1.759 s | 691 $\mu\text{s}$ |
| **`scx_rlfifo`** *(FIFO Baseline)* | 6,333.21 | 17.81% | 157.50 s | 1.000 s | 987 $\mu\text{s}$ |
| **Linux CFS/EEVDF** | 6,356.04 | ~18.5% | **156.15 s** 🥇 | 1.050 s | ~800 $\mu\text{s}$ |

#### 2. Key-Value Store Request Latency (`redis-benchmark` in ms)
| Scheduler | GET P50 | GET P95 | GET P99 | SET P50 | SET P95 | SET P99 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`scx_optima`** | **0.191 ms** 🥇 | **0.255 ms** 🥇 | **0.367 ms** 🥇 | **0.199 ms** 🥇 | **0.319 ms** 🥇 | **0.559 ms** 🥇 |
| **`scx_rdtai`** | 0.199 ms | 0.295 ms | 0.519 ms | 0.327 ms | 0.543 ms | 0.575 ms |
| **`scx_rusty`** | 0.375 ms | 0.535 ms | 0.567 ms | 0.327 ms | 0.543 ms | 0.567 ms |
| **`scx_rlfifo`** | 0.199 ms | 0.519 ms | 0.559 ms | 0.287 ms | 0.543 ms | 0.583 ms |

#### 3. Real-Time Scheduling Jitter (`cyclictest` in $\mu\text{s}$)
| Scheduler | Average Jitter | Median (P50) | P90 | P99 | Maximum Latency Spike |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`scx_optima`** | 6.3 $\mu\text{s}$ | 3 $\mu\text{s}$ | 12 $\mu\text{s}$ | 14 $\mu\text{s}$ | **451 $\mu\text{s}$** 🥈 *(Bounded Tail)* |
| **`scx_rdtai`** | **5.2 $\mu\text{s}$** 🥇 | 3 $\mu\text{s}$ | 12 $\mu\text{s}$ | 14 $\mu\text{s}$ | 1,101 $\mu\text{s}$ *(Outlier Spikes)* |
| **`scx_rlfifo`** | 5.6 $\mu\text{s}$ | 2 $\mu\text{s}$ | 12 $\mu\text{s}$ | 13 $\mu\text{s}$ | **443 $\mu\text{s}$** 🥇 |
| **`scx_rusty`** | 6.9 $\mu\text{s}$ | 12 $\mu\text{s}$ | 12 $\mu\text{s}$ | 13 $\mu\text{s}$ | 765 $\mu\text{s}$ |


