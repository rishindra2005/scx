# OPTIMA: Density-Aware Task Scheduling and Low-Latency IPC on Asymmetric Multiprocessors with Linux sched_ext

**Author:** Risheendra MN  
**Affiliation:** Systems Research  
**Email:** rishindra.hackbox@gmail.com  

---

## Abstract

Asymmetric chip-multiprocessors, combining high-throughput Performance (P) cores and power-efficient Efficiency (E) cores with disparate compute capacities and private cache topologies, present fundamental challenges for general-purpose operating system schedulers. While the Linux kernel incorporates capacity-aware scheduling (`sched-capacity`) and Energy-Aware Scheduling (EAS) to account for core compute differentials, standard fair-share classes (CFS, EEVDF) balance execution primarily by task virtual runtime lag and runnable load rather than execution density ($\rho = w / p$). Furthermore, existing schedulers lack a synchronous IPC bypass, inducing cross-CCX cache invalidation and high tail latencies when real-time and compute-bound tasks contend for heterogeneous execution resources.

In this paper, we present **OPTIMA**, an extensible operating system scheduler implemented in Linux `sched_ext` eBPF that provides principled algorithmic scheduling foundations for asymmetric multiprocessors. OPTIMA synthesizes three foundational mechanisms directly within the kernel hot-path: (1) a density-based adaptation of Smith's Rule (Weighted Shortest Processing Time, WSPT) that provably minimizes total weighted completion time $\sum w_j C_j$ on dedicated single-core queues and serves as an online approximation heuristic across asymmetric cores; (2) an Online Admissible Preemption Filter (OAPF) evaluating a dimensionally consistent one-step dispatch delay proxy $\Delta\mathrm{DelayProxy}(c) = w_{\mathrm{curr}}(c) \cdot (\tau_{\mathrm{slice}} + C_{\mathrm{switch}}) - w_{\mathrm{wake}} \cdot R(c)$ in $\mathcal{O}(M_P)$ kernel time to suppress preemption thrashing and guarantee local delay reduction; and (3) a synchronous IPC zero-lag fast-path that routes waking communicating tasks to local dispatch queues, substantially reducing cross-CCX migrations (from 48.7% to 0.2%) and preserving L1/L2 cache locality. Using quadratic Lyapunov drift with an exact algebraic cross-term expansion, we formally prove that OPTIMA achieves conditional multi-queue stability under aggregate admissible capacity, while virtual deadline bounding ($v_j \le t + 3\tau_{\max}$) protects batch tasks against starvation.

We evaluate OPTIMA across 16 diverse workloads (pro-audio DSP, high-frequency financial order matching, game physics server, API gateway fan-out, Redis caching, microbenchmarks, IPC ping-pong, and parallel Linux kernel compilation) against CFS/EEVDF, `scx_rusty`, and `scx_rlfifo`. OPTIMA exhibits a bimodal performance profile: delivering an **84.1% reduction in IPC ping-pong latency** (2.55 $\mu$s vs 16.06 $\mu$s on CFS) with a **6.3$\times$ throughput increase** (392,691 ops/s), a **93.6% increase in network loopback bandwidth** (111.47 Gbps), **zero audio underruns**, **zero game desyncs**, and a **94.6% reduction in worst-case real-time latency spikes**. Conversely, on parallel software compilation (`make kernel/`), OPTIMA takes 6.03s, matching other `sched_ext` baselines (5.67s--5.83s) while trailing native in-kernel CFS (3.29s) due to eBPF struct-ops trampoline overhead during fork/exec process storms, and shows moderate throughput regressions (14--30%) under high-concurrency request multiplexing (Redis, API Gateway). We present an in-depth architectural analysis characterizing these workload trade-offs and identifying the microarchitectural boundaries between specialized eBPF dispatch and in-kernel heuristics.

---

## 1. Introduction

The relentless demands of modern computing workloads---ranging from sub-millisecond audio digital signal processing and algorithmic trading to massively parallel software compilation and microservices---have driven microarchitectural design toward single-ISA asymmetric multiprocessors (AMP). Modern multi-core processors integrate heterogeneous core types: high-frequency, wide-issue Performance cores (P-cores) designed for single-thread burst performance, and high-density, power-efficient Efficiency cores (E-cores) designed to maximize parallel compute density. These cores typically feature disparate clock frequencies, asymmetrical instruction throughput per cycle (IPC), and non-uniform cache topologies organized into discrete core complex (CCX) clusters.

Despite these architectural advances, general-purpose operating system CPU scheduling remains rooted in symmetric multiprocessor (SMP) fair-share abstractions. Although Linux incorporates capacity-aware scheduling (`sched-capacity`) and Energy-Aware Scheduling (EAS) on supported platforms to track CPU capacity differentials, the default fair-share class (CFS and EEVDF) balances execution primarily by task virtual runtime lag and runnable load rather than execution density ($\rho = w / p$). Consequently, existing kernel schedulers suffer from three distinct operational limitations under heterogeneous workloads:
1. **Capacity and Density Mismatch:** While capacity-fitness checks migrate heavy tasks toward higher-capacity cores, they lack an execution-density metric to co-optimize priority weights against observed burst durations. High-priority interactive threads can experience capacity inversions or queueing delays behind batch jobs on Performance cores.
2. **Context-Switch Thrashing vs. Tail Latency:** When latency-critical tasks wake, CFS's virtual runtime preemption heuristics lack a dimensionally consistent preemption filter that accounts for context-switch and cache refill overheads, leading to either preemption storms or quantum wait spikes.
3. **Cross-Domain Cache Invalidation during Synchronous IPC:** In synchronous IPC and socket communication (e.g., pipe ping-pong, RPC fan-out), the default balancer searches for foreign idle cores across CCX boundaries, evicting hot L1/L2 caches and introducing costly inter-processor interrupt (IPI) latencies.

The recent introduction of `sched_ext` into upstream Linux 6.12 enables user-space and eBPF developers to replace the default kernel scheduling class with custom, dynamically loaded BPF struct-ops programs. While early sched-ext implementations, such as `scx_rusty`, `scx_lavd`, and the reference FIFO baseline `scx_rlfifo`, demonstrate the viability of eBPF scheduling, they rely primarily on heuristic balancing, user-space coordination daemons, or empirical policies that lack formal mathematical guarantees.

In this paper, we present **OPTIMA**, an extensible eBPF operating system scheduler that resolves these challenges through formal algorithmic synthesis. OPTIMA introduces mathematically grounded scheduling invariants directly into the kernel execution path:
- **Density-Based WSPT Sequencing (Smith's Rule):** We prove that ordering tasks by execution density $\rho_j = w_j / p_j$ is optimal for single-core dedicated queues ($1 || \sum w_j C_j$), with ties permitted. While the general asymmetric problem with release dates ($R | r_j, \text{pmtn} | \sum w_j C_j$) is strongly NP-hard, OPTIMA leverages classical identical-machine results (Kawaguchi-Kyan reference bound) to formulate density-based virtual deadline scheduling as an online approximation heuristic, maximizing instantaneous backlog weight depletion via the Rearrangement Inequality.
- **Online Admissible Preemption Filtering (OAPF):** Rather than unconstrained heuristics, OPTIMA evaluates an online preemption filter over candidate Performance cores using the dimensionally consistent delay metric $\Delta\mathrm{DelayProxy}(c) = w_{\mathrm{curr}(c)} \cdot (\tau_{\mathrm{slice}} + C_{\mathrm{switch}}) - w_{\mathrm{wake}} \cdot R(c)$. In $\mathcal{O}(M_P)$ bounded kernel time, OPTIMA provably preempts only when the one-step weighted delay is strictly reduced, suppressing preemption thrashing and enforcing bounded priority inversion ($\le \tau_{\max}$).
- **Density-Guided Heterogeneous-Core Placement:** Core allocation between P- and E-cores is formulated via continuous fluid relaxation, preserving P-cores for high-density burst tasks and offloading long-running batch jobs ($p_j \ge 3\,\text{ms}$) to Efficiency queues.
- **Synchronous IPC Zero-Lag Local Dispatch:** By intercepting synchronous wakeup flags (`SCX_WAKE_SYNC`) and dispatching communicating partners directly into local hardware runqueues (`SCX_DSQ_LOCAL`), OPTIMA achieves 2.55 $\mu$s IPC turnaround and 392k ops/s throughput.
- **Conditional Lyapunov Stability & Starvation Protection:** We prove that adaptive quantum scaling combined with work-conserving work-stealing guarantees negative Lyapunov drift under a per-queue service condition, while virtual deadline bounding ($v_j \le t + 3\tau_{\max}$) protects batch tasks against starvation.

---

## 2. Literature Survey & Comparative Landscape

### 2.1 Extensible and User-Space Schedulers
Google ghOSt demonstrated that delegating scheduling decisions to user-space daemons via shared-memory queues enables specialized microsecond-scale policies for warehouse workloads. However, ghOSt pays a communication penalty: cross-privilege context switches and thread yields incur $1\text{--}2\,\mu\text{s}$ overheads. Linux `sched_ext` overcomes this boundary by compiling verified BPF programs directly into kernel space. Early community schedulers explore distinct trade-offs: `scx_rusty` uses a hybrid model with a multi-threaded Rust user daemon coordinating BPF dispatch queues; `scx_lavd` targets gaming and desktop interactivity through empirical critical-path deadline tracking; and `scx_rlfifo` serves as a reference Round-Robin / FIFO baseline built on `scx_rustland_core`. OPTIMA differs fundamentally from these systems by enforcing formal combinatorial optimization algorithms directly in kernel bytecode without user-space daemon overhead.

### 2.2 General-Purpose Kernel Schedulers
The Completely Fair Scheduler (CFS) implemented Generalized Processor Sharing (GPS) via red-black trees ordered by virtual runtime ($v_{\text{runtime}}$). While CFS guarantees asymptotic fairness on symmetric systems, its SMP load balancer relies on coarse heuristic averages that fail to account for asymmetric core speedups. Linux 6.6 introduced EEVDF, replacing CFS's latency heuristics with Stoica and Abdel-Wahab's formal virtual deadline theory. EEVDF provides provable lag bounds on symmetric machines, but does not address asymmetric core partitioning or synchronous IPC cache locality. For hard real-time workloads, `SCHED_DEADLINE` implements Earliest Deadline First (EDF) coupled with the Constant Bandwidth Server (CBS). While CBS guarantees temporal isolation, it requires static reservations ($Q, P$) and rejects unadmitted workloads.

### 2.3 Theoretical Foundations of Scheduling
Smith's Rule established that sequencing independent tasks in non-increasing order of density $\rho_i = w_i / p_i$ strictly minimizes total weighted completion time on an isolated single-core queue ($1 || \sum w_i C_i$). However, in generalized multiprocessing, Lenstra et al. (1977) and Garey and Johnson (1979) proved that scheduling with dynamic release dates, preemption, and asymmetric parallel machines ($R | r_j, \text{pmtn} | \sum w_j C_j$) is strongly NP-hard (while Bruno et al. (1974) proved NP-completeness for precedence-constrained tasks on identical processors). Classical approximation bounds, such as the Kawaguchi-Kyan bound ($\alpha \le (1+\sqrt{2})/2 \approx 1.207$), apply strictly to identical parallel machines ($P || \sum w_j C_j$) without release dates. Consequently, OPTIMA utilizes density-based list scheduling as a principled online approximation heuristic.

In online scheduling theory, Hall et al., Megow and Schulz, and Phillips et al. studied list scheduling guided by WSPT and linear programming relaxations. For preemption, rather than an intractable multi-stage branch-and-bound search tree, OPTIMA implements an Online Admissible Preemption Filter (OAPF) evaluated in $\mathcal{O}(M_P)$ kernel time. Finally, Neely's stochastic network optimization and Lyapunov drift analysis provide the theoretical machinery to prove queue stability and bounded delay in stochastic queuing systems. Table 1 summarizes how OPTIMA advances beyond existing schedulers.

### 2.4 Comprehensive Scheduler Comparison Matrix

| Scheduler | Asymmetry Awareness | Synchronous IPC Fast-Path | Preemption Provability | Tail-Latency Guarantees | Memory Footprint | Reconfig Overhead |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **OPTIMA** *(This Work)* | **Hardware-native multi-tier**; dynamic sysfs topology detection; speedup $\mu = c_P/c_E$. | **Direct `SCX_DSQ_LOCAL` on `SCX_WAKE_SYNC`** (2.55\,$\mu$s); SMT locality. | **Online Admissible Preemption Filter (OAPF)**: $\Delta\mathrm{DelayProxy} < 0$; bounded priority inversion ($\le \tau_{\max}$). | **Density-based WSPT + virtual deadlines**; Lyapunov drift queue stabilization. | **BPF `task_storage`** (~64 B); per-CPU array maps (~16 KB total). | $\mathcal{O}(1)$ atomic BPF `.rodata` live tuning; lightweight user topology controller. |
| **CFS / EEVDF** *(Linux Default)* | Capacity-aware heuristics (`sched-capacity`); lacks execution density metrics. | Traverses full scheduling domain; cross-CCX IPI latency overhead. | Heuristic latency target (CFS); SMP lag bounds only (EEVDF). | Proportional lag bounded; susceptible to tail latency spikes under asymmetric contention and cross-domain load balancing. | Embedded in kernel `task_struct`; `sched_domain` tree. | Coarse sysctl tunables; requires kernel recompilation for logic changes. |
| **Google ghOSt** *(SOSP '21)* | User-space policy dependent; lacks dynamic hardware speed models. | Cross-privilege boundary context switches ($1\text{--}2\,\mu\text{s}$ penalty). | Policy dependent; preemption delayed by user-agent wakeup. | Application-specific tail tuning; fragile under heavy preemption. | High; multi-MB shared memory queues and user thread pools. | Medium; requires updating user daemon or restarting agents. |
| **`scx_rusty`** *(SREcon24)* | Coarse domain clustering; does not compute dynamic core speedups. | BPF dispatch for local tasks; falls back to user daemon on imbalance. | Heuristic work-stealing based on queue depth ratios; no proof. | Moderate variance; tail spikes during user-space rebalancing. | Dual footprint: BPF maps plus Rust runtime memory heaps. | Low/Medium; CLI parameters modifiable via IPC channels. |
| **`scx_rlfifo`** *(sched-ext)* | Unaware; global shared queue without core differentiation. | Generic dispatch; lacks synchronous waker-affinity routing. | Round-robin / FIFO; periodic slice preemption without priority differentiation. | Susceptible to head-of-line blocking and severe tail spikes under mixed workloads. | Minimal BPF dispatch queue state (~8 KB). | Static FIFO policy; requires restarting scheduler to modify parameters. |
| **`scx_lavd`** *(LPC '24)* | Topology-informed; tracks core frequencies for gaming interactivity. | Wakes latency-critical render threads on warm cores. | Heuristic critical-path tracking; lacks combinatorial bounds. | Low frame-time jitter; can penalize background batch throughput. | Low; in-kernel BPF maps for critical path tracking. | Low; live tuning of target frame rate deadlines. |
| **ARM EAS** *(Linux Kernel)* | Energy-model integrated; Capacity-Aware big.LITTLE scheduling. | Standard energy differential ($\Delta E$) evaluation; no IPC bypass. | None; preemption driven by standard CFS heuristic rules. | Energy prioritized; defers to CFS load balancer under load. | Embedded in kernel source; static ACPI/device-tree tables. | Rigid; modifying models requires device-tree reconfiguration. |

---

## 3. System Architecture & Implementation

OPTIMA is structured as a dual-plane operating system scheduler: a user-space telemetry and topology controller implemented in Rust, and a high-performance in-kernel scheduling engine implemented in eBPF via `sched_ext` struct-ops.

### 3.1 Heterogeneous Multi-Queue DSQ Hierarchy
Rather than maintaining a single global runqueue or uncoordinated per-CPU queues, OPTIMA partitions execution state across a hierarchical dispatch queue (DSQ) topology:
- `DSQ_PERF` (64-bit ID `0x100`): Dedicated exclusively to hard real-time and latency-critical tasks ($w_j \ge 500$ or pro-audio DSP). Executed with absolute priority by Performance cores.
- `DSQ_SHARED_P` (64-bit ID `0x101`): Shared domestic queue for general interactive and high-density tasks executing within the Performance core CCX domain.
- `DSQ_SHARED_E` (64-bit ID `0x102`): Shared domestic queue for balanced and throughput-oriented tasks executing within the Efficiency core CCX domain.
- `DSQ_EFF` (64-bit ID `0x103`): Long-running batch compute tasks ($p_j \ge 3\,\text{ms}$) demoted from P-cores.
- `SCX_DSQ_LOCAL`: Per-CPU hardware dispatch queue into which tasks are directly committed for immediate zero-latency dispatch without global lock contention.

### 3.2 Dynamic Topology Discovery
Upon loading, the user-space Rust controller inspects the sysfs microarchitectural hierarchy (`/sys/devices/system/cpu/cpu*/`). It extracts core frequency ceilings, physical package topologies, and hardware thread sibling maps. Cores are classified into Performance partition $\mathcal{P}$ and Efficiency partition $\mathcal{E}$. The controller initializes eBPF read-only global masks (`p_core_mask`, `e_core_mask`, `smt_sibling_map`), allowing the kernel BPF JIT compiler to perform dead-code elimination and optimize dispatch branches into constant bitwise operations.

### 3.3 Synchronous IPC Zero-Lag Fast-Path
In synchronous client-server patterns (e.g., pipes, Redis GET/SET, microservice RPCs), the waking task and waker execute in lockstep. OPTIMA intercepts the `SCX_WAKE_SYNC` flag in `ops.select_cpu`. If the calling CPU (`this_cpu`) is allowed and has an empty local runqueue, OPTIMA immediately places the waking task into `SCX_DSQ_LOCAL` of `this_cpu`. If `this_cpu` is occupied, it attempts the SMT sibling (`this_sib`) or previous CPU (`prev_cpu`). This substantially reduces cross-CCX migrations (from 48.7% to 0.2%) and associated IPI interrupts, cutting ping-pong latency from 16.06 $\mu$s down to 2.55 $\mu$s.

### 3.4 Hierarchical Work-Conserving Work-Stealing
To prevent core starvation during parallel compute bursts (e.g., parallel software compilation), OPTIMA implements an asymmetric work-stealing protocol in `ops.dispatch`. Idle P-cores prioritize `DSQ_PERF` and `DSQ_SHARED_P`, but steal from `DSQ_SHARED_E` and `DSQ_EFF` when domestic queues are empty. Conversely, idle E-cores aggressively drain `DSQ_SHARED_P` without threshold barriers, preventing core underutilization and mitigating starvation under asymmetric compute bursts.

---

## 4. Mathematical Theory & Formal Guarantees

### 4.1 System Model and Problem Formulation
We consider an asymmetric multiprocessor system $\mathcal{M} = \mathcal{P} \cup \mathcal{E}$ with $M_P$ Performance cores of capacity $c_P$ and $M_E$ Efficiency cores of capacity $c_E$, where $\mu = c_P / c_E > 1$. The aggregate compute capacity rate is:
$$C_{\mathrm{total}} \triangleq M_P c_P + M_E c_E$$
Online tasks $\mathcal{J} = \{J_1, J_2, \dots\}$ arrive dynamically, specified by 5-tuples $J_j = (r_j, p_j, w_j, d_j, \mathcal{T}_j)$ with nominal processing requirement $p_j$ (such that executing on core $C_k$ with capacity $c_k$ requires execution duration $p_j / c_k$), task density $\rho_j = w_j / p_j$, and priority weight $w_j \in [w_{\min}, w_{\max}]$ ($w_{\min} \ge 10$). The primary scheduling objective is to minimize total weighted completion time:
$$\min_{\mathcal{S}} \quad \Phi(\mathcal{S}) \triangleq \sum_{J_j \in \mathcal{J}} w_j C_j(\mathcal{S})$$
subject to causality ($S_j \ge r_j$) and core exclusivity ($\sum_{J_j} \mathbf{1}_{\{J_j \text{ on } C_k \text{ at } t\}} \le 1$).

**Remark 1 (Latency-Sensitive Prioritization and Real-Time Scope):** In an open, general-purpose operating system without hard admission control, arbitrary arrival bursts can exceed hardware processing capacity. Under unconstrained overload, guaranteeing hard real-time deadlines ($C_j \le d_j$) for all tasks is mathematically impossible. OPTIMA provides latency-critical prioritization with zero-lag local dispatch (`SCX_DSQ_LOCAL`) and priority virtual deadlines ($v_j = t$) for latency-sensitive tasks ($w_j \ge w_{\mathrm{RT}}$), minimizing queueing delay under non-overloaded regimes, while providing graceful degradation under overload via density-weighted completion time heuristics.

### 4.2 Theorem 1: WSPT Sequencing, NP-Hardness, and Asymmetric Partitioning

**Theorem 1 (Smith's Rule Optimality on Single-Core Dedicated Queues):**  
Let $\mathcal{J}_k = \{J_1, \dots, J_n\}$ be a set of $n$ independent tasks available simultaneously at time $t_0$ ($r_j = t_0$) on an isolated processor core $C_k \in \mathcal{M}$. Any non-preemptive schedule ordering tasks in non-increasing density order:
$$\rho_{(1)} \ge \rho_{(2)} \ge \dots \ge \rho_{(n)}$$
strictly minimizes the total weighted completion time $\sum_{j=1}^n w_j C_j$. (Arbitrary tie-breaking among tasks of equal density preserves identical objective value.)

*Proof:*  
Assume for contradiction that an optimal schedule $\mathcal{S}^*$ does not follow non-increasing density order. There must exist adjacent tasks $J_a, J_b$ where $J_a$ immediately precedes $J_b$ with $\rho_a < \rho_b$, meaning $w_a p_b < w_b p_a$. 

Let $t \ge t_0$ be the start time of $J_a$. Under $\mathcal{S}^*$, completion times are $C_a = t + \frac{p_a}{c_k}$ and $C_b = t + \frac{p_a + p_b}{c_k}$. Interchanging $J_a$ and $J_b$ produces schedule $\mathcal{S}'$ without affecting any other tasks. For the swapped pair:
$$\Delta \Phi = \Big[ w_b C_b(\mathcal{S}') + w_a C_a(\mathcal{S}') \Big] - \Big[ w_a C_a(\mathcal{S}^*) + w_b C_b(\mathcal{S}^*) \Big] = \frac{w_a p_b - w_b p_a}{c_k} < 0$$
Because $\Delta \Phi < 0$, $\mathcal{S}'$ achieves a strictly lower cost, contradicting optimality of $\mathcal{S}^*$. Finite adjacent transpositions transform any schedule into monotonic density order, establishing that Smith's ordering is optimal for single-core non-preemptive queues at time zero. $\blacksquare$

**Proposition 1 (Computational Complexity & Online Information Barrier):**  
Minimizing total weighted completion time with dynamic release dates on parallel machines ($P | r_j | \sum w_j C_j$ or $R | r_j, \text{pmtn} | \sum w_j C_j$) is strongly NP-hard (Lenstra et al. 1977, Garey & Johnson 1979). Even on a single core, preemptive scheduling with arbitrary release dates ($1 | r_j, \text{pmtn} | \sum w_j C_j$) is strongly NP-hard (Lenstra et al. 1977). Furthermore, for online scheduling with unannounced arrivals, no online algorithm can achieve an optimal offline schedule regardless of computational power, as online decision-makers lack knowledge of future arrival sequences.

**Remark 2 (Context of the Kawaguchi-Kyan Bound):**  
Kawaguchi and Kyan proved that for identical parallel machines without release dates ($P || \sum w_j C_j$), WSPT list scheduling achieves an approximation factor of $\alpha \le (1+\sqrt{2})/2 \approx 1.207$. This bound applies strictly to identical machines ($P$) at time zero. For asymmetric architectures ($Q$ or $R$) and dynamic arrivals ($r_j$), OPTIMA uses density-based virtual deadline list scheduling as a principled online approximation heuristic.

**Theorem 2 (Continuous Fluid Relaxation for Density-Guided Placement):**  
Under a continuous fluid relaxation where active jobs make continuous progress and priority weight is retired at a rate proportional to instantaneous compute bandwidth, the fluid weight retirement rate is $-\dot{W}_{\mathrm{fluid}}(t) = \sum_{k=1}^M \rho_{\sigma(k)} c_k$. By the Hardy-Littlewood-Pólya Rearrangement Inequality, this fluid retirement rate is maximized when tasks with higher density are matched monotonically to faster cores:
$$\min_{k \in \mathcal{P}} \rho_{\sigma(k)} \ge \max_{k' \in \mathcal{E}} \rho_{\sigma(k')}$$

**Definition 1 (Task Density, Reference Normalization $\rho_{\mathrm{ref}}$, and Virtual Deadline Increment):**  
For each task $J_j$ with priority weight $w_j \in [w_{\min}, w_{\max}]$ ($w_{\min} \ge 10$) and estimated burst runtime $p_{\mathrm{est},j} \triangleq \max(\mathrm{avg\_runtime}_j, \tau_{\min})$, its execution density is defined as $\rho_j \triangleq \frac{w_j}{p_{\mathrm{est},j}}$ with physical dimensions of $[\mathrm{weight}/\mathrm{time}]$ (or $[\mu\mathrm{s}^{-1}]$). To preserve dimensional consistency when translating density into temporal virtual deadline advances, we introduce an explicit reference density $\rho_{\mathrm{ref}} \triangleq \frac{w_{\mathrm{ref}}}{p_{\mathrm{ref}}} = \frac{100}{1000\,\mu\mathrm{s}} = 0.1\,\mu\mathrm{s}^{-1}$, representing a baseline interactive thread with default nice priority ($w_{\mathrm{ref}} = 100$) and a 1\,ms execution burst ($p_{\mathrm{ref}} = 1000\,\mu\mathrm{s}$). The virtual deadline increment assigned during enqueue is:
$$\Delta v_j \triangleq \operatorname{clamp}\left( \tau_j \cdot \frac{\rho_{\mathrm{ref}}}{\rho_j}, \; \tau_{\min}, \; \tau_{\max} \right) = \operatorname{clamp}\left( \frac{\tau_j p_{\mathrm{est},j}}{10 \max(w_j, 10)}, \; \tau_{\min}, \; \tau_{\max} \right)$$
Because the normalized density ratio $\rho_{\mathrm{ref}} / \rho_j$ is strictly dimensionless, $\Delta v_j$ has exact physical dimensions of time $[\mu\mathrm{s}]$, matching scheduling quantum $\tau_j$.

**Remark 3 (Decoupling Domain Placement from Intra-Queue Virtual-Deadline Ordering):**  
We explicitly decouple coarse-grained domain placement from fine-grained intra-queue scheduling. First, *Domain Placement (Algorithm 3)* assigns tasks to target DSQs using heuristic thresholds: tasks with $w_j \ge w_{\mathrm{RT}}$ route to `DSQ_PERF`, long-running batch jobs ($p_{\mathrm{est},j} \ge 3\,\mathrm{ms}$ or `is_batch`) route to `DSQ_EFF`, and general interactive tasks route to domestic shared queues (`DSQ_SHARED_P` or `DSQ_SHARED_E`) preserving CPU stickiness; domain placement does not evaluate $\rho = w/p$ directly. Second, *Intra-Queue Scheduling (Algorithm 3)* evaluates task density $\rho_j$ and reference density $\rho_{\mathrm{ref}}$ exclusively within the virtual deadline calculus ($\Delta v_j \propto \rho_{\mathrm{ref}}/\rho_j$) to establish the execution order ($\le_v$) within shared dispatch queues, prioritizing higher-density tasks ahead of lower-density competitors.

### 4.3 Theorem 3: Online Admissible Preemption Filtering (OAPF)
When a latency-critical task $J_{\mathrm{wake}}$ arrives and all P-cores are busy, OPTIMA evaluates an **Online Admissible Preemption Filter (OAPF)** greedy decision rule over candidate cores $c \in \mathcal{P}$ in $\mathcal{O}(M_P)$ kernel time, rather than an intractable multi-stage offline search tree.

**Definition 2 (Weighted One-Step Dispatch Delay Proxy):**  
Let $J_{\mathrm{curr}}(c)$ be the task executing on core $c \in \mathcal{P}$ with weight $w_{\mathrm{curr}}(c)$ and remaining quantum $R(c)$. Let waking task $J_{\mathrm{wake}}$ have weight $w_{\mathrm{wake}}$ and allocated time slice $\tau_{\mathrm{slice}} = \tau_{\min}$. Let $C_{\mathrm{switch}}$ denote the context-switch and cache invalidation penalty in time units.

If the scheduler waits, $J_{\mathrm{wake}}$ is delayed by $R(c)$, incurring weighted dispatch delay $w_{\mathrm{wake}} \cdot R(c)$. If the scheduler preempts, $J_{\mathrm{curr}}(c)$ is suspended during $J_{\mathrm{wake}}$'s slice ($\tau_{\mathrm{slice}}$) plus context-switch overhead $C_{\mathrm{switch}}$, incurring weighted delay penalty $w_{\mathrm{curr}}(c) \cdot (\tau_{\mathrm{slice}} + C_{\mathrm{switch}})$. The net differential in one-step weighted dispatch delay is:
$$\Delta\mathrm{DelayProxy}(c) \triangleq w_{\mathrm{curr}}(c) \cdot (\tau_{\mathrm{slice}} + C_{\mathrm{switch}}) - w_{\mathrm{wake}} \cdot R(c)$$
Both terms have unified physical dimensions of $[\mathrm{weight} \times \mathrm{time}]$ (priority-weighted delay units).

**Theorem 3 (Local One-Step Dispatch Delay Reduction):**  
Let $\mathcal{P}_{\mathrm{admissible}} \subseteq \mathcal{P}$ be the set of candidate cores satisfying:
1. Priority superiority: $w_{\mathrm{wake}} > w_{\mathrm{curr}}(c)$;
2. Anti-thrashing remaining runtime: $R(c) > \delta_{\min}(c)$, where
$$\delta_{\min}(c) \triangleq \frac{w_{\mathrm{curr}}(c) \cdot (\tau_{\mathrm{slice}} + C_{\mathrm{switch}})}{w_{\mathrm{wake}}}$$

If $\mathcal{P}_{\mathrm{admissible}} \ne \emptyset$, selecting core $c^* = \arg\min_{c \in \mathcal{P}_{\mathrm{admissible}}} \Delta\mathrm{DelayProxy}(c)$ and preempting $J_{\mathrm{curr}}(c^*)$ guarantees that the one-step weighted dispatch delay is strictly reduced:
$$\Delta\mathrm{DelayProxy}(c^*) < 0$$
Conversely, if $\mathcal{P}_{\mathrm{admissible}} = \emptyset$ or candidate cores satisfy $R(c) \le \delta_{\min}(c)$, preemption is suppressed, bounding priority inversion to at most the running thread's quantum $\tau(c) \le \tau_{\max}$.

*Proof:*  
Under non-preemptive wait, $J_{\mathrm{wake}}$ incurs waiting delay $R(c^*)$, while $J_{\mathrm{curr}}(c^*)$ incurs 0 delay, yielding weighted delay $\mathrm{Delay}_{\mathrm{wait}} = w_{\mathrm{wake}} R(c^*)$. Under preemption, $J_{\mathrm{wake}}$ incurs 0 dispatch delay, while $J_{\mathrm{curr}}(c^*)$ incurs delay $(\tau_{\mathrm{slice}} + C_{\mathrm{switch}})$, yielding weighted delay $\mathrm{Delay}_{\mathrm{preempt}} = w_{\mathrm{curr}}(c^*) (\tau_{\mathrm{slice}} + C_{\mathrm{switch}})$. Computing $\Delta\mathrm{DelayProxy}(c^*) = \mathrm{Delay}_{\mathrm{preempt}} - \mathrm{Delay}_{\mathrm{wait}}$ yields:
$$\Delta\mathrm{DelayProxy}(c^*) = w_{\mathrm{curr}}(c^*) \cdot (\tau_{\mathrm{slice}} + C_{\mathrm{switch}}) - w_{\mathrm{wake}} \cdot R(c^*)$$
Since $c^* \in \mathcal{P}_{\mathrm{admissible}}$, $R(c^*) > \delta_{\min}(c^*)$, which implies $w_{\mathrm{wake}} R(c^*) > w_{\mathrm{curr}}(c^*) (\tau_{\mathrm{slice}} + C_{\mathrm{switch}})$, ensuring $\Delta\mathrm{DelayProxy}(c^*) < 0$.

When preemption is suppressed (e.g., $w_{\mathrm{curr}}(c) \ge w_{\mathrm{wake}}$ or $R(c) \le \delta_{\min}(c)$), the waking task waits for quantum expiration. Because each executing slice is clamped to $\tau(c) \le \tau_{\max}$, the maximum waiting delay is bounded by $\tau_{\max}$, guaranteeing bounded priority inversion while protecting cache locality and pipeline execution. $\blacksquare$

**Remark 4 (Tunable Anti-Thrashing Dampening Penalty $C_{\mathrm{switch}} = 50\,\mu\text{s}$):**  
While raw CPU register context switches require only $1\text{--}2\,\mu\text{s}$ and individual cache line reloads require tens to hundreds of nanoseconds, repeatedly displacing and reloading a memory-intensive working set introduces cumulative pipeline stalls across thousands of cycles. In OPTIMA, $C_{\mathrm{switch}}$ serves strictly as a *tunable anti-thrashing dampening penalty* nominally set to $50\,\mu\text{s}$ (with an effective operational sensitivity range of $20\text{--}100\,\mu\text{s}$). Rather than modeling a single hardware cache miss, $C_{\mathrm{switch}}$ enforces an algorithmic hysteresis barrier, ensuring that preemption is triggered only when the waking task's priority advantage is sufficiently pronounced to justify evicting the incumbent thread and disrupting pipeline throughput.

### 4.4 OAPF-Adaptive: Peer Preemption Hysteresis
While Theorem 3 formally prevents lower-priority tasks from interrupting higher-priority incumbents, a naive interpretation where $w_{\mathrm{curr}} \ge w_{\mathrm{wake}}$ unconditionally prunes preemption introduces head-of-line (HoL) blocking in homogeneous-priority worker pools. In microservices, cache daemons (Redis), and benchmark harness workers (`schbench`), client and worker threads typically execute at identical default user priorities ($w_{\mathrm{curr}} = w_{\mathrm{wake}} = 100$). If an incumbent worker begins a lengthy compute burst up to $\tau_{\mathrm{slice}} = 3\,\mathrm{ms}$, newly arriving request threads with identical weights cannot preempt the running core under strict priority superiority ($w_{\mathrm{wake}} > w_{\mathrm{curr}}$), stalling in shared dispatch queues and inducing the multi-millisecond request P99 latency tails.

To resolve this latency cliff without inducing context-switch thrashing, OPTIMA incorporates **OAPF-Adaptive**:
- **Admissible Peer Preemption Criterion:** Let $J_{\mathrm{curr}}(c)$ and $J_{\mathrm{wake}}$ belong to equal priority classes ($w_{\mathrm{curr}}(c) = w_{\mathrm{wake}}$). Peer preemption is admissible if and only if the incumbent thread has attained forward execution progress of at least $\tau_{\mathrm{soft}} = 1{,}000\,\mu\text{s}$ and possesses remaining quantum $R(c) > \tau_{\min} = 500\,\mu\text{s}$. The peer dispatch delay differential is defined as:
  $$\Delta\mathrm{DelayPeer}(c) \triangleq C_{\mathrm{switch}} - w_{\mathrm{wake}} \cdot (R(c) - \tau_{\min})$$
  Core $c^*$ is preempted if $\min_{c \in \mathcal{P}} \Delta\mathrm{DelayPeer}(c) < 0$.
- **Anti-Thrashing Forward Progress:** Because $E(c) \ge 1{,}000\,\mu\text{s} \gg C_{\mathrm{switch}} = 50\,\mu\text{s}$, the incumbent thread is guaranteed to have amortized its initial cache refill overhead before yielding, bounding the maximum switching frequency to at most $1\,\mathrm{kHz}$ per core.
- **Head-of-Line Queue Delay Curtailment:** By preempting when remaining quantum exceeds $\tau_{\min}$, the maximum waiting delay experienced by peer request threads is curtailed from $\tau_{\mathrm{slice}} = 3\,\mathrm{ms}$ down to $\le 1\,\mathrm{ms}$, directly mitigating request tail spikes in high-concurrency request pools.

### 4.5 Theorem 4 & Proposition 2: Conditional Queue Stability and Starvation Protection
We model the multi-queue system state vector $\mathbf{Q}(t) = (q_1(t), q_2(t), q_3(t), q_4(t))^T$ evolving via $q_k(t+1) = \max(0, q_k(t) - s_k(t)) + a_k(t)$ with bounded second moments $\mathbb{E}[a_k(t)^2 + s_k(t)^2 \mid \mathbf{Q}(t)] \le M_{\mathrm{sec}} < \infty$. We define the quadratic Lyapunov function:
$$L(\mathbf{Q}(t)) \triangleq \frac{1}{2} \sum_{k=1}^4 q_k(t)^2$$

**Definition 3 (Aggregate Admissible Workload Region):**  
The arrival workload is aggregate-admissible if aggregate workload demand is within total multiprocessor compute capacity:
$$\sum_{k=1}^4 \lambda_k \bar{p}_k \le C_{\mathrm{total}} - \epsilon_0 = (M_P c_P + M_E c_E) - \epsilon_0, \quad \epsilon_0 > 0$$
where $\lambda_k = \mathbb{E}[a_k(t)]$ and $\bar{p}_k$ is mean task execution work.

**Theorem 4 (Conditional Multi-Queue Stability via Foster-Lyapunov Drift):**  
Let the multi-queue backlog $\mathbf{Q}(t)$ evolve under an aggregate-admissible workload (Definition 3). If the dispatch and hierarchical work-stealing policy guarantees a positive service slack across all active non-empty queues:
$$\mathbb{E}\big[ s_k(t) - a_k(t) \;\big|\; \mathbf{Q}(t) \big] \ge \delta > 0, \quad \forall k: q_k(t) > 0$$
then the quadratic Lyapunov function satisfies the negative drift condition:
$$\Delta L(\mathbf{Q}(t)) \le B - \delta \sum_{k=1}^4 q_k(t)$$
for finite positive constant $B = \frac{1}{2} \sum_{k=1}^4 \mathbb{E}[a_k(t)^2 + s_k(t)^2 \mid \mathbf{Q}(t)] < \infty$. Consequently, the multi-queue system is Foster-Lyapunov stable (positive recurrent), with bounded time-average queue depth:
$$\limsup_{T \to \infty} \frac{1}{T} \sum_{t=0}^{T-1} \sum_{k=1}^4 \mathbb{E}[q_k(t)] \le \frac{B}{\delta} < \infty$$

*Proof:*  
Expanding $q_k(t+1)^2$:
$$q_k(t+1)^2 = \left(\max(0, q_k(t) - s_k(t))\right)^2 + a_k(t)^2 + 2 a_k(t) \max(0, q_k(t) - s_k(t))$$
Using $(\max(0, x))^2 \le x^2$, we bound the square by $(q_k - s_k)^2 = q_k^2 - 2q_k s_k + s_k^2$. For the cross-term, since $a_k \ge 0$ and $\max(0, q_k - s_k) \le q_k$, we have $2 a_k \max(0, q_k - s_k) \le 2 q_k a_k$. Combining both bounds:
$$q_k(t+1)^2 \le q_k(t)^2 + a_k(t)^2 + s_k(t)^2 - 2 q_k(t)\big(s_k(t) - a_k(t)\big)$$
Subtracting $q_k(t)^2$, dividing by 2, and taking conditional expectations:
$$\Delta L(\mathbf{Q}(t)) \le B - \sum_{k=1}^4 q_k(t) \, \mathbb{E}\big[ s_k(t) - a_k(t) \;\big|\; \mathbf{Q}(t) \big]$$
Under the stated per-queue service condition, $\mathbb{E}[s_k(t) - a_k(t) \mid \mathbf{Q}(t)] \ge \delta > 0$ for non-empty queues. Substituting this into the drift bound yields $\Delta L(\mathbf{Q}(t)) \le B - \delta \sum_k q_k(t)$. By the Foster-Lyapunov stability criterion, this establishes positive recurrence and strong aggregate queue stability. $\blacksquare$

**Remark 5 (Architectural Realization of the Service Slack Condition):**  
In multi-queue systems with strict priorities, global capacity admissibility ($\sum \lambda_k \bar{p}_k \le C_{\mathrm{total}} - \epsilon_0$) is a necessary condition for aggregate stability, but does not independently ensure per-queue service slack if high-priority queues can monopolize all cores. OPTIMA establishes this per-queue condition through structural partitioning:
1. dedicated Efficiency cores provide guaranteed domestic compute capacity to batch queues (`DSQ_EFF`), preventing high-priority P-core traffic from completely starving throughput work; and
2. dynamic quantum scaling $\tau(q_k) = \max(\frac{Q_{\mathrm{base}}}{1+q_k}, \tau_{\min})$ dynamically compresses time slices during congestion, accelerating task dispatch and preemption frequency.

**Proposition 2 (Virtual Deadline Bounding and Starvation Protection):**  
Under OPTIMA's virtual deadline calculus (Algorithm 3), for every task $J_j$ enqueued into a batch or interactive queue (`DSQ_SHARED_P`, `DSQ_SHARED_E`, `DSQ_EFF`) at time $t$, its assigned virtual deadline satisfies:
$$v_j(t) = \operatorname{clamp}(v_{\mathrm{time}}, t, t + 2\tau_j) + \Delta v_j \le t + 3\tau_{\max}$$
since $\tau_j \le \tau_{\max}$ and $\Delta v_j \le \tau_{\max}$. Any subsequent batch task $J_{j'}$ arriving at timestamp $t' > t + 3\tau_{\max}$ receives $v_{j'}(t') \ge t' > v_j(t)$ and cannot be scheduled ahead of $J_j$. While a batch task arriving earlier ($t' \le t + 3\tau_{\max}$) with higher density may receive an earlier deadline, $v_j(t) \le t + 3\tau_{\max}$ strictly bounds relative queue advancement lag. Under the conditional service rate condition ($\mathbb{E}[s_k - a_k \mid \mathbf{Q}] \ge \delta > 0$) of Theorem 4, every admitted batch task is guaranteed dispatch within finite time. Furthermore, latency-critical tasks ($w \ge w_{\mathrm{RT}}$) receive immediate priority ($v_j = t$) and always supersede batch tasks; when preemption is suppressed by OAPF, the immediate waiting delay imposed by the running task is bounded by $\tau_{\max}$, ensuring bounded priority inversion.

---

### 4.6 Algorithmic Specifications

```text
Algorithm 1: OPTIMA-SELECT-CPU (IPC Fast-Path & Domain Selection)
Input:  Task p, previous CPU prev_cpu, wakeup flags wake_flags
Output: Target CPU target_cpu

 1: tctx <- lookup_task_ctx(p)
 2: if p is pinned or p is kernel thread then
 3:     Dispatch to prev_cpu via SCX_DSQ_LOCAL; return prev_cpu
 4: end if
 5: this_cpu <- smp_processor_id(), idle_mask <- get_idle_smtmask()
 6: if wake_flags & SCX_WAKE_SYNC then
 7:     if this_cpu in p.cpus_allowed and dsq_nr_queued(this_cpu) == 0 then
 8:         goto direct_dispatch with target_cpu <- this_cpu
 9:     end if
10:     this_sib <- get_smt_sibling(this_cpu)
11:     if this_sib in p.cpus_allowed and dsq_nr_queued(this_sib) == 0 then
12:         goto direct_dispatch with target_cpu <- this_sib
13:     end if
14: end if
15: if is_perf(prev_cpu) and prev_cpu in idle_mask and test_and_clear_idle(prev_cpu) then
16:     goto direct_dispatch with target_cpu <- prev_cpu
17: end if
18: if tctx.core_type != CORE_TYPE_EFF then
19:     c <- pick_idle_domestic_core(p, is_perf=TRUE, idle_mask)
20:     if c >= 0 then goto direct_dispatch with target_cpu <- c; end if
21: end if
22: if tctx.weight >= w_RT then
23:     oapf_cpu <- OPTIMA-OAPF(p, tctx, P)
24:     if oapf_cpu >= 0 then tctx.dispatch_local <- TRUE; return oapf_cpu; end if
25: end if
26: tctx.dispatch_local <- FALSE; return prev_cpu
27: direct_dispatch:
28: Compute slice tau based on tctx.is_batch and tctx.avg_runtime
29: scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, tau, 0)
30: return target_cpu
```

```text
Algorithm 2: OPTIMA-OAPF (Online Admissible Preemption Filter)
Input:  Waking task p, context tctx, candidate core set P
Output: Target CPU c* (or -1 if wait is optimal)

 1: c* <- -1, DeltaProxy* <- 0, now <- scx_bpf_now()
 2: w_wake <- max(tctx.weight, 100), tau_slice <- tau_min, C_switch <- 50 us
 3: for each core c in P do
 4:     if c not in p.cpus_allowed then continue; end if
 5:     st <- lookup_cpu_run_state(c)
 6:     if st == NULL or st.pid == 0 then return c; end if  // Rule 1: Idle core
 7:     if st.weight >= w_wake then continue; end if        // Rule 2: Inversion safety
 8:     el <- now - st.start_time, R(c) <- (st.slice > el) ? (st.slice - el) : 0
 9:     delta_min <- (st.weight * (tau_slice + C_switch)) / w_wake
10:     if R(c) <= delta_min then continue; end if          // Rule 3: Anti-thrashing
11:     DeltaDelayProxy <- st.weight * (tau_slice + C_switch) - w_wake * R(c)
12:     if DeltaDelayProxy < DeltaProxy* then
13:         DeltaProxy* <- DeltaDelayProxy; c* <- c
14:     end if
15: end for
16: if c* >= 0 and DeltaProxy* < 0 then
17:     scx_bpf_kick_cpu(c*, SCX_KICK_PREEMPT); return c*
18: end if
19: return -1
```

```text
Algorithm 3: OPTIMA-ENQUEUE (Deadline Calculus & Preemption Kick)
Input:  Task p, enqueue flags enq_flags

 1: tctx <- lookup_task_ctx(p)
 2: if p is pinned or p is kernel thread or tctx.dispatch_local then
 3:     scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, tau_min, enq_flags); return
 4: end if
 5: // Domain Placement Target DSQ Selection (Heuristic Thresholds)
 6: if tctx.weight >= w_RT then
 7:     target_dsq <- DSQ_PERF
 8: else if tctx.avg_runtime >= 3ms or tctx.is_batch then
 9:     target_dsq <- DSQ_EFF
10: else
11:     target_dsq <- is_perf(task_cpu) ? DSQ_SHARED_P : DSQ_SHARED_E
12: end if
13: is_rt <- (tctx.weight >= w_RT or (not tctx.is_batch and tctx.avg_runtime < 1ms))
14: now <- scx_bpf_now()
15: if is_rt then
16:     tau <- tau_min; deadline <- now
17: else
18:     tau <- (tctx.avg_runtime >= 3ms) ? tau_max : max(Q_base / (1 + q), tau_min)
19:     v <- clamp(p.scx.dsq_vtime, now, now + 2*tau)
20:     p_est <- max(tctx.avg_runtime, tau_min)
21:     rho <- max(tctx.weight, 10) / p_est  // Execution density
22:     Delta_v <- clamp((tau * p_est) / (10 * max(tctx.weight, 10)), tau_min, tau_max)  // Density advance tau * rho_ref / rho, rho_ref = 0.1 us^-1
23:     deadline <- v + Delta_v
24: end if
25: p.scx.dsq_vtime <- deadline
26: scx_bpf_dsq_insert_vtime(p, target_dsq, tau, deadline, enq_flags)
27: idle_cpu <- scx_bpf_pick_idle_cpu(p.cpus_allowed, 0)
28: if idle_cpu >= 0 then
29:     scx_bpf_kick_cpu(idle_cpu, SCX_KICK_IDLE); return
30: end if
31: if is_rt then
32:     oapf_cpu <- OPTIMA-OAPF(p, tctx, P)
33:     if oapf_cpu >= 0 then scx_bpf_kick_cpu(oapf_cpu, SCX_KICK_PREEMPT); end if
34: end if
```

```text
Algorithm 4: OPTIMA-DISPATCH (Asymmetric Work-Stealing)
Input:  Executing core cpu

 1: if is_perf(cpu) then
 2:     if scx_bpf_dsq_move_to_local(DSQ_PERF, 0) then return; end if
 3:     if scx_bpf_dsq_move_to_local(DSQ_SHARED_P, 0) then return; end if
 4:     if scx_bpf_dsq_move_to_local(DSQ_SHARED_E, 0) then return; end if
 5:     if scx_bpf_dsq_move_to_local(DSQ_EFF, 0) then return; end if
 6: else
 7:     if scx_bpf_dsq_move_to_local(DSQ_SHARED_E, 0) then return; end if
 8:     if scx_bpf_dsq_move_to_local(DSQ_EFF, 0) then return; end if
 9:     if scx_bpf_dsq_move_to_local(DSQ_SHARED_P, 0) then return; end if
10:     if scx_bpf_dsq_move_to_local(DSQ_PERF, 0) then return; end if
11: end if
```

---

## 5. Empirical Evaluation

### 5.1 Experimental Methodology & Hardware Microarchitecture

#### 5.1.1 Testbed Microarchitecture: AMD Zen 5 / Zen 5c
All experiments are conducted on a bare-metal, asymmetric multi-core hardware platform powered by the **AMD Ryzen AI 9 HX 370 (Strix Point)** processor. The processor integrates 12 physical execution cores supporting 24 simultaneous hardware threads, architecturally partitioned into two asymmetric Core Complexes (CCXs):
- **Performance CCX (Zen 5):** 4 wide-issue Performance cores (Cores 0--3, threads 0--3 and 12--15) featuring a 6-wide decode execution pipeline, private 48 KB L1 data / 32 KB L1 instruction caches, 1 MB private L2 caches, and a shared 16 MB Level-3 cache slice, capable of boost clocks up to 5.16 GHz.
- **Efficiency CCX (Zen 5c):** 8 compact Efficiency cores (Cores 4--11, threads 4--11 and 16--23) utilizing area-optimized Zen 5c execution units, identical ISA support, private 1 MB L2 caches, and a dedicated 8 MB Level-3 cache slice, operating up to 3.29 GHz.

The compute capacity ratio yields an empirical single-thread compute speedup factor of $\mu = c_P / c_E \approx 1.57$ under frequency-normalized CPU throughput. The platform is equipped with 32 GB LPDDR5x-7500 dual-channel memory and runs Linux 6.12/7.0 with `CONFIG_SCHED_CLASS_EXT=y` and hardware RAPL energy counters. Throughout our evaluation, the baseline labeled "CFS / EEVDF" denotes the Linux 6.12 mainline default fair scheduler class (which implements Earliest Eligible Virtual Deadline First proportional-lag allocation with standard CFS load-balancing heuristics).

#### 5.1.2 Cross-Platform Architectural Generalization
While our primary evaluation is executed on AMD Zen 5/5c silicon, OPTIMA's core abstractions ($\mathcal{P}, \mathcal{E}, \mu = c_P/c_E$) apply universally across modern asymmetric multiprocessor topologies:
- **Intel Hybrid (12th--14th Gen Alder Lake / Raptor Lake):** Intel architectures (e.g., Core i7-12700H, i9-13900K) combine Performance (Golden Cove / Raptor Cove) and Efficiency (Gracemont) cores that share a monolithic, ring-interconnected Level-3 cache. Unlike AMD's dual-CCX layout where L3 is partitioned, Intel's challenge lies in ring-bus cross-traffic and Gracemont cluster-level L2 serialization. OPTIMA's synchronous IPC fast-path directly eliminates ring-bus inter-core bouncing by pinning communicating pairs locally, while WSPT prevents Gracemont clusters from suffering head-of-line request stalls.
- **ARM big.LITTLE & DynamIQ (e.g., Rockchip RK3588, Ampere, Apple Silicon):** In ARM DynamIQ topologies (such as the RK3588 with 4 Cortex-A76 big cores and 4 Cortex-A55 LITTLE cores, or Apple Silicon M-series clusters), core clusters maintain private L2 caches backed by a unified System-Level Cache (SLC). Linux ARM Energy-Aware Scheduling (EAS) relies on static energy curves ($\Delta E$) and reverts to symmetric CFS heuristics during saturation. In contrast, OPTIMA evaluates online task execution density ($\rho = w / p$) dynamically in kernel eBPF, enforcing provable dispatch and preemption invariants without needing offline silicon energy tables.

#### 5.1.3 Execution Isolation & Hardware Energy Profiling
To eliminate external operating system noise, scheduling jitter, and background interference:
- **Isolated Execution Partition:** Benchmark workloads execute within dedicated vCPU partitions pinned 1:1 to isolated physical cores.
- **Hardware Cache Isolation:** Hardware Cache Allocation Technology (CAT) bitmasks partition L3 cache ways, strictly preventing cross-domain cache evictions between host monitoring infrastructure and benchmark threads.
- **Dedicated IRQ Routing:** External device interrupts are affinity-pinned away from benchmark cores.
- **Hardware RAPL Energy Counters:** Package and core energy consumption is measured via non-invasive hardware Running Average Power Limit (RAPL) MSRs/sysfs (`powercap/intel-rapl`) and PMU counters, tracking total Joules, average Watts, and Energy-Delay Product ($\mathrm{EDP} = \mathrm{Joules} \times \mathrm{Time}$).
- **Statistical Rigor:** Every workload is executed through multiple complete independent repetitions (3--5 runs). We report mean values, sample standard deviations ($\pm \sigma$), and relative percentage deltas.

### 5.2 Benchmark Workload Taxonomy
We evaluate 16 diverse production and synthetic workloads covering real-time, throughput, memory, and communication profiles:
1. **Pro-Audio Real-Time DSP:** Simulates low-latency audio processing pipelines, tracking buffer turnaround latencies and buffer underruns (xruns).
2. **Game Physics Server:** Simulates a multi-threaded game server tick loop, measuring physics P50/P99 latency and frame desync rates.
3. **Real-Time POSIX Jitter (`cyclictest`):** Measures high-precision timer jitter and worst-case latency spikes under load.
4. **High-Frequency Trading (HFT):** Simulated limit order book matching engine measuring order matching throughput and roundtrip P50/P99 latency.
5. **Production Redis Cache:** Memory cache benchmark evaluating GET throughput and P99 latency under concurrent pipelining.
6. **Microservices Cloud API Gateway:** Fan-out HTTP ingress gateway measuring roundtrip P50/P99 request latency and ingress rate.
7. **Process and Thread Context Switching (`hackbench`):** Evaluates IPC scheduler overhead across process and thread groups.
8. **IPC Ping-Pong Latency (`perf bench sched pipe`):** Measures bidirectional pipe context-switch latency and operations rate.
9. **Messaging Storm (`perf bench sched messaging`):** Evaluates high-density IPC message exchange duration.
10. **Scheduler Wakeup and Request Latency (`schbench`):** Evaluates multi-threaded request-response latencies and wakeup P99 tails under worker contention.
11. **Compute Throughput & Thread Fairness (`sysbench cpu`):** Measures events per second (EPS) and per-thread standard deviation $\sigma$.
12. **Memory Bandwidth & Operations (`sysbench memory`):** Evaluates memory transfer rates (MiB/s) and operations per second.
13. **Hardware PMU Cache Miss Rates:** Hardware performance counters measuring L3 cache miss percentages.
14. **Hardware Instruction Retirement (IPC):** Measures instructions retired per CPU clock cycle.
15. **Network Loopback Throughput (`iperf3`):** High-bandwidth TCP socket loopback rate in Gbps.
16. **Linux Kernel Compilation (`make kernel/`):** Multi-threaded parallel compilation measuring wall-clock build time.

### 5.3 Comprehensive 3-Repetition Benchmark Results Across 16 Workloads

| Benchmark Workload | Metric Evaluated | Unit | CFS / EEVDF | scx_optima | scx_rusty | scx_rlfifo | Advantage (vs CFS) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Pro-Audio Turnaround Latency | Buffer P50 ($\downarrow$) | $\mu$s | 33.95 ± 4.28 | **31.71 ± 1.21** 🥇 | 32.29 ± 1.69 | 33.86 ± 1.87 | **-6.6%** |
| Pro-Audio Tail Latency | Buffer P99 ($\downarrow$) | $\mu$s | 101.50 ± 11.14 | 101.95 ± 2.02 | 61.91 ± 23.25 | **58.42 ± 21.62** 🥇 | +0.4% |
| Audio Buffer Underruns | Total Xruns ($\downarrow$) | count | 3.67 ± 0.58 | **0.00 ± 0.00** 🥇 | 0.00 ± 0.00 | 0.00 ± 0.00 | **0 drops (Clean)** |
| Game Server Tick Latency | Physics P50 ($\downarrow$) | $\mu$s | 756.40 ± 146.34 | 777.14 ± 107.27 | **690.73 ± 35.76** 🥇 | 718.47 ± 37.59 | +2.7% |
| Game Server Frame Turnaround | Physics P99 ($\downarrow$) | $\mu$s | 1,563.91 ± 249.24 | 1,865.02 ± 290.07 | **1,235.41 ± 34.77** 🥇 | 1,387.65 ± 150.65 | +19.3% |
| Game Server Frame Drops | Desync Rate ($\downarrow$) | % | 0.019 ± 0.03% | **0.000 ± 0.00%** 🥇 | 0.000 ± 0.00% | 0.000 ± 0.00% | **0.00% drops** |
| Real-Time POSIX Jitter | Avg Jitter ($\downarrow$) | $\mu$s | 24.17 ± 4.82 | **21.27 ± 4.68** 🥇 | 23.90 ± 3.65 | 28.43 ± 5.55 | **-12.0%** |
| Real-Time Jitter Max Spike | Worst Spike ($\downarrow$) | $\mu$s | 8,383.00 ± 8,269.70 | 450.00 ± 339.69 | 468.33 ± 289.00 | **280.67 ± 23.12** 🥇 | **-94.6%** |
| HFT Order Matching Rate | Throughput ($\uparrow$) | Mops/s | 14.47 ± 7.28 | 15.76 ± 4.46 | 16.58 ± 3.83 | **18.80 ± 0.33** 🥇 | +8.9% |
| HFT Turnaround P50 Latency | Roundtrip P50 ($\downarrow$) | $\mu$s | 528.11 ± 587.94 | **222.79 ± 68.87** 🥇 | 550.89 ± 515.18 | 244.76 ± 108.86 | **-57.8%** |
| HFT Turnaround P99 Latency | Roundtrip P99 ($\downarrow$) | $\mu$s | 880.68 ± 962.33 | 576.34 ± 308.38 | 892.72 ± 487.04 | **441.02 ± 92.07** 🥇 | **-34.6%** |
| Production Redis Cache | GET Throughput ($\uparrow$) | kRPS | 235.54 ± 16.61 | 202.21 ± 4.16 | 186.44 ± 5.33 | 198.83 ± 14.28 | -14.1% |
| Production Redis Cache | GET Latency P99 ($\downarrow$) | $\mu$s | 249.67 ± 32.33 | 319.00 ± 42.33 | 319.00 ± 42.33 | 399.00 ± 69.74 | +27.8% |
| Cloud API Gateway Fan-Out | Ingress Rate ($\uparrow$) | req/s | 1,275.61 ± 157.17 | 887.95 ± 24.00 | 1,066.03 ± 128.06 | 1,001.99 ± 62.50 | -30.4% |
| Cloud API Gateway Latency | Roundtrip P50 ($\downarrow$) | ms | 33.83 ± 4.42 | 49.58 ± 1.24 | 39.34 ± 3.52 | 41.75 ± 1.46 | +46.6% |
| Cloud API Gateway Tail | Roundtrip P99 ($\downarrow$) | ms | 76.97 ± 17.69 | 100.78 ± 7.68 | 98.36 ± 18.55 | 108.00 ± 6.99 | +30.9% |
| Hackbench Process Context-Switch | Process Time ($\downarrow$) | s | 0.35 ± 0.04 | 0.29 ± 0.01 | 0.37 ± 0.04 | **0.21 ± 0.03** 🥇 | **-16.9%** |
| Hackbench Thread Context-Switch | Thread Time ($\downarrow$) | s | 0.60 ± 0.02 | 0.67 ± 0.01 | 0.85 ± 0.04 | 0.85 ± 0.03 | +12.3% |
| Perf Pipe IPC Latency | Ping-Pong Latency ($\downarrow$) | $\mu$s | 16.06 ± 0.65 | **2.55 ± 0.05** 🥇 | 6.19 ± 4.12 | 4.36 ± 2.34 | **-84.1%** |
| Perf Pipe IPC Throughput | Ping-Pong Rate ($\uparrow$) | ops/s | 62,335 ± 2,481 | **392,691 ± 7,314** 🥇 | 205,677 ± 100,235 | 269,291 ± 110,723 | **+6.3 $\times$** |
| Perf Messaging Storm | Wall Time ($\downarrow$) | s | 0.040 ± 0.003 | 0.045 ± 0.008 | 0.075 ± 0.031 | **0.036 ± 0.002** 🥇 | +11.7% |
| Schbench Wakeup P50 Latency | Wakeup P50 ($\downarrow$) | $\mu$s | 10.00 ± 9.54 | 1,996.67 ± 2.31 | 2,118.00 ± 79.67 | 3,974.67 ± 4.62 | +199.7 $\times$ |
| Schbench Wakeup P99 Tail | Wakeup P99 ($\downarrow$) | $\mu$s | 3,852.00 ± 132.18 | **2,007.33 ± 2.31** 🥇 | 15,472.00 ± 400.96 | 6,077.33 ± 92.38 | **-47.9%** |
| Schbench Request P50 Latency | Request P50 ($\downarrow$) | $\mu$s | 18,698.67 ± 1,420.07 | 29,280.00 ± 293.28 | 21,472.00 ± 955.72 | 25,674.67 ± 800.21 | +56.6% |
| Schbench Request P99 Tail | Request P99 ($\downarrow$) | $\mu$s | 38,506.67 ± 4,032.17 | 170,752.00 ± 5,701.39 | 284,842.67 ± 14,201.27 | 140,373.33 ± 9,473.15 | +343.4% |
| Schbench Service Rate | Throughput ($\uparrow$) | req/s | 1,634.37 ± 70.92 | 712.97 ± 7.33 | 714.00 ± 16.96 | 766.57 ± 23.47 | -56.4% |
| Sysbench CPU Compute | Throughput ($\uparrow$) | EPS | 11,712.23 ± 72.58 | 6,028.19 ± 35.61 | 5,965.74 ± 315.08 | 5,969.98 ± 99.87 | -48.5% |
| Sysbench Thread Fairness | Std Dev $\sigma$ ($\downarrow$) | $\sigma$ | 2,303.38 ± 286.35 | **46.48 ± 15.65** 🥇 | 223.38 ± 56.82 | 304.96 ± 65.14 | **-98.0%** |
| Sysbench Memory Bandwidth | Throughput ($\uparrow$) | MiB/s | 10,991.27 ± 122.06 | **12,394.80 ± 275.40** 🥇 | 12,376.37 ± 598.71 | 11,891.51 ± 101.28 | **+12.8%** |
| Sysbench Memory Operations | Operations Rate ($\uparrow$) | Mops/s | 11.26 ± 0.12 | **12.69 ± 0.28** 🥇 | 12.67 ± 0.61 | 12.18 ± 0.10 | **+12.8%** |
| L3 Cache Miss Rate | Hardware PMU ($\downarrow$) | % | 6.40 ± 2.41% | 2.66 ± 0.09% | 1.55 ± 0.04% | **1.36 ± 0.06%** 🥇 | **-58.5% Misses** |
| Hardware IPC Retirement | Instruction/Cyc ($\uparrow$) | IPC | 1.18 ± 0.00 | 0.88 ± 0.01 | 0.83 ± 0.00 | 0.83 ± 0.00 | -25.2% |
| Network Loopback Throughput | iperf3 Rate ($\uparrow$) | Gbps | 57.58 ± 2.51 | **111.47 ± 5.19** 🥇 | 38.23 ± 0.58 | 62.03 ± 1.85 | **+93.6%** |
| Kernel Build (`make kernel/`) | Wall Time ($\downarrow$) | s | 3.29 ± 0.11 | 6.03 ± 0.08 | 5.67 ± 0.16 | 5.83 ± 0.06 | +83.4% |

*Note:* Values represent mean ± sample standard deviation ($\sigma$) across 3 independent executions. ($\downarrow$) indicates lower is better; ($\uparrow$) indicates higher is better. 🥇 indicates best performance among evaluated schedulers. Kernel build wall time for OPTIMA ($6.03 \pm 0.08$\,s) reflects the barrier-free configuration, matching peer extensible schedulers ($5.67$--$5.83$\,s).

### 5.4 Hardware RAPL Energy Profiling & Energy-Delay Product (EDP)

| Workload Domain | Energy Metric | Unit | CFS / EEVDF | scx_optima | scx_rusty | scx_rlfifo | Advantage (vs CFS) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Sysbench CPU Compute (12 threads) | Package Energy ($\downarrow$) | Joules | 180.89 ± 1.49 | 179.68 ± 1.52 | **176.27 ± 0.64** 🥇 | 176.68 ± 0.50 | -0.7% |
| Sysbench CPU Compute (12 threads) | Core Energy ($\downarrow$) | Joules | 33.69 ± 0.59 | 34.13 ± 0.31 | 33.01 ± 0.64 | **33.00 ± 0.50** 🥇 | +1.3% |
| Sysbench CPU Compute (12 threads) | Average Package Power | Watts | 36.14 ± 0.30 | 35.90 ± 0.30 | **35.22 ± 0.13** 🥇 | 35.30 ± 0.10 | -0.7% |
| Sysbench CPU Compute (12 threads) | Energy-Delay Product (EDP) ($\downarrow$) | J·s | 905.39 ± 7.43 | 899.28 ± 7.65 | **882.22 ± 3.10** 🥇 | 884.36 ± 2.63 | -0.7% |
| Sysbench Memory Bandwidth (12 thr) | Package Energy ($\downarrow$) | Joules | 137.69 ± 2.91 | 138.60 ± 2.02 | **131.24 ± 0.56** 🥇 | 133.87 ± 0.78 | +0.7% |
| Sysbench Memory Bandwidth (12 thr) | Core Energy ($\downarrow$) | Joules | 22.29 ± 0.29 | 23.32 ± 0.58 | **21.87 ± 0.26** 🥇 | 22.25 ± 0.12 | +4.6% |
| Sysbench Memory Bandwidth (12 thr) | Average Package Power | Watts | 27.51 ± 0.58 | 27.69 ± 0.41 | **26.22 ± 0.11** 🥇 | 26.75 ± 0.16 | +0.7% |
| Sysbench Memory Bandwidth (12 thr) | Energy-Delay Product (EDP) ($\downarrow$) | J·s | 689.12 ± 14.52 | 693.74 ± 10.10 | **656.85 ± 2.85** 🥇 | 670.02 ± 3.91 | +0.7% |
| IPC Pipe Ping-Pong (100k ops) | Package Energy ($\downarrow$) | Joules | 4.01 ± 0.62 | **3.10 ± 0.17** 🥇 | 3.44 ± 0.20 | 3.54 ± 0.39 | **-22.7%** |
| IPC Pipe Ping-Pong (100k ops) | Core Energy ($\downarrow$) | Joules | 0.96 ± 0.18 | 0.71 ± 0.51 | 0.91 ± 0.68 | **0.58 ± 0.52** 🥇 | **-26.0%** |
| IPC Pipe Ping-Pong (100k ops) | Average Package Power | Watts | 28.71 ± 2.49 | 22.75 ± 1.32 | 21.10 ± 4.28 | **19.00 ± 3.40** 🥇 | **-20.8%** |
| IPC Pipe Ping-Pong (100k ops) | Energy-Delay Product (EDP) ($\downarrow$) | J·s | 0.58 ± 0.19 | **0.42 ± 0.02** 🥇 | 0.57 ± 0.08 | 0.67 ± 0.15 | **-27.6%** |
| Hackbench Context-Switch (proc) | Package Energy ($\downarrow$) | Joules | 6.73 ± 0.78 | 4.12 ± 0.18 | 9.62 ± 1.58 | **4.00 ± 0.16** 🥇 | **-38.8%** |
| Hackbench Context-Switch (proc) | Core Energy ($\downarrow$) | Joules | 1.14 ± 0.11 | 0.85 ± 0.03 | 1.70 ± 0.28 | **0.69 ± 0.04** 🥇 | **-25.4%** |
| Hackbench Context-Switch (proc) | Average Package Power | Watts | 45.34 ± 0.87 | 48.05 ± 1.16 | **38.06 ± 1.95** 🥇 | 46.76 ± 0.89 | +6.0% |
| Hackbench Context-Switch (proc) | Energy-Delay Product (EDP) ($\downarrow$) | J·s | 1.01 ± 0.23 | **0.35 ± 0.04** 🥇 | 2.53 ± 1.03 | **0.34 ± 0.03** 🥇 | **-65.3%** |

*Note:* Values represent mean ± sample standard deviation ($\sigma$) across 5 independent executions via hardware RAPL MSRs (`intel-rapl` sysfs interface) on the host AMD Zen 5/5c processor. 🥇 indicates lowest energy or EDP.

### 5.5 Ablation Study: Synchronous IPC Fast-Path

#### Table 3: Ablation Analysis of the Synchronous IPC Fast-Path
To isolate the microarchitectural contribution of the synchronous IPC zero-lag fast-path (`SCX_WAKE_SYNC` intercepted in Algorithm 1), we perform a controlled ablation study on the bidirectional pipe ping-pong and network loopback workloads:

| Evaluated Metric | Ablated (No-Sync) | Full OPTIMA | Delta / Gain |
| :--- | :--- | :--- | :--- |
| Pipe Ping-Pong Latency ($\downarrow$) | 17.63 $\mu$s | **2.55 $\mu$s** | **-85.5% (6.9$\times$)** |
| Pipe Ping-Pong Throughput ($\uparrow$) | 56,712 ops/s | **392,691 ops/s** | **+592.4%** |
| Network Loopback Rate ($\uparrow$) | 58.21 Gbps | **111.47 Gbps** | **+91.5%** |
| Cross-CCX Migrations ($\downarrow$) | 48.7% | **0.2%** | **-99.6%** |
| L1 Data Cache Miss Rate ($\downarrow$) | 8.42% | **1.15%** | **-86.3%** |

Disabling the synchronous IPC fast-path causes ping-pong latency to jump from 2.55 $\mu$s to 17.63 $\mu$s (matching CFS's 16.06 $\mu$s), while throughput collapses from 392,691 to 56,712 ops/s (-85.5%). Hardware performance counter traces reveal the root cause: without immediate local enqueue, 48.7% of wakeups migrate across cache complex boundaries, triggering inter-processor interrupts (IPIs) and cache-line invalidation. With the fast-path enabled, communicating thread pairs execute back-to-back on the same physical core or SMT sibling, retaining hot cache lines and driving L1 data misses down from 8.42% to 1.15%.

---

## 6. Discussion and Limitations

Our comprehensive evaluation across 34 metrics reveals both significant microarchitectural strengths and principled trade-offs inherent to specialized eBPF scheduling on asymmetric multiprocessors. In this section, we analyze the underlying systems mechanisms governing these trade-offs and delineate the operational limitations of our approach.

### 6.1 Workload Trade-offs and Architectural Specialization
The empirical results demonstrate a clear bimodal performance profile:
- **Low-Latency IPC and Real-Time Robustness:** In workloads characterized by synchronous communication (`perf bench sched pipe`, `iperf3` socket loopback) and strict timing bounds (pro-audio DSP, game server ticks, POSIX jitter), OPTIMA achieves decisive improvements over CFS: an 84.1% reduction in pipe latency, a 94.6% reduction in jitter spikes, and zero audio underruns or frame drops. Relative to peer extensible schedulers, OPTIMA matches their zero-drop real-time robustness (0.00 xruns, 0.000% desync rate). In audio processing tail turnaround, OPTIMA achieves 101.95\,$\mu$s P99 (closely tracking native CFS at 101.50\,$\mu$s), while peer schedulers `scx_rusty` (61.91\,$\mu$s) and `scx_rlfifo` (58.42\,$\mu$s) demonstrate tighter tail bounds on this micro-benchmark. Concurrently, peer schedulers demonstrate advantages in specific isolated metrics: `scx_rusty` achieves faster game physics turnaround (1,235.41\,$\mu$s P99 vs. OPTIMA's 1,865.02\,$\mu$s and CFS's 1,563.91\,$\mu$s), and `scx_rlfifo` registers the lowest peak jitter spike (280.67\,$\mu$s vs. OPTIMA's 450.00\,$\mu$s, though both represent a massive reduction over CFS's 8,383.00\,$\mu$s spike). These variations reflect the trade-offs of distinct queue designs under pure real-time streaming.
- **High-Concurrency and Memory-Bound Trade-offs:** Conversely, in throughput-oriented database and proxy workloads (Production Redis, Cloud API Gateway), CFS outperforms OPTIMA by 14--30%. These applications exhibit thousands of short, non-blocking requests with highly volatile burst runtimes that challenge our EWMA burst estimation. Under rapid connection multiplexing, static or slow-converging EWMA values misclassify short batch tasks as compute-bound, intermittently routing them to Efficiency cores or incurring queue rebalancing delays.

### 6.2 Peer Preemption and the Schbench Latency Trade-off
A notable finding in Table 2 is the behavior under `schbench`: OPTIMA's wakeup P50 latency is 1,996.67 $\mu$s compared to CFS's 10.00 $\mu$s. Importantly, this $\sim$2\,ms P50 latency floor is shared across all evaluated `sched_ext` schedulers (`scx_rusty` at 2,118.00 $\mu$s and `scx_rlfifo` at 3,974.67 $\mu$s). This phenomenon is an intrinsic consequence of dispatch queue scheduling in `sched_ext`: newly woken threads await executing threads' time slices to elapse before being dispatched from DSQs, whereas native CFS preempts executing threads nearly instantaneously (10 $\mu$s) based on virtual runtime lag ($v_{\text{runtime}}$).

However, under heavy request concurrency, aggressive peer preemption under CFS causes cascading context switches, expanding CFS's wakeup P99 tail to 3,852.00 $\mu$s and causing `scx_rusty`'s tail to explode to 15,472.00 $\mu$s. In contrast, OPTIMA holds its wakeup P99 tail down to **2,007.33 $\mu$s**—a remarkably tight distribution differing from P50 by only 10.66 $\mu$s. This stability is governed directly by **Rule 2 of the Online Admissible Preemption Filter (OAPF)**:
$$w_{\mathrm{curr}}(c) \ge w_{\mathrm{wake}} \implies \text{Prune candidate } c$$

Because all `schbench` worker threads share identical nice levels ($w = 100$), Rule 2 intentionally suppresses peer preemption among equal-priority threads. By guaranteeing that once a thread begins its execution quantum ($\tau_{\min} = 2\,\text{ms}$) it runs without interruption, OPTIMA eliminates thrashing and achieves rock-solid wakeup tail latency predictability.

Nonetheless, this anti-thrashing design imposes a substantial trade-off on end-to-end request-response metrics under worker pool contention. When worker threads cannot preempt peers, queued request-handling tasks must wait for running threads to yield or exhaust full quanta. Consequently, OPTIMA's request P50 latency increases to 29,280.00 $\mu$s (+56.6% vs. CFS's 18,698.67 $\mu$s), request P99 tail latency rises to 170,752.00 $\mu$s (+343.4% vs. CFS's 38,506.67 $\mu$s, though outperforming `scx_rusty`'s 284,842.67 $\mu$s), and aggregate service throughput declines to 712.97 req/s (-56.4% vs. CFS's 1,634.37 req/s, matching `scx_rusty` at 714.00 req/s and `scx_rlfifo` at 766.57 req/s). This illustrates the fundamental duality: protecting thread quanta against peer preemption minimizes wakeup jitter but incurs significant request-level queueing delays during saturated request-response concurrency.

### 6.3 Sysbench Compute Throughput and Thread Fairness
In `sysbench cpu`, all three `sched_ext` schedulers cluster near $\sim$6,000 EPS (OPTIMA: 6,028.19 EPS, `scx_rusty`: 5,965.74 EPS, `scx_rlfifo`: 5,969.98 EPS) within the 12-thread isolated partition, whereas CFS achieves 11,712.23 EPS. This occurs because the pure compute loop of sysbench creates continuous core contention where CFS allows threads to unrestrictedly saturate all core pipelines without queue structure overheads. However, the critical differentiator lies in thread fairness: under CFS, individual thread event completions exhibit extreme variance ($\sigma = 2,303.38$), with some threads starved on slower cores while others surge ahead. Under OPTIMA, per-thread standard deviation drops by **98.0%** down to $\sigma = 46.48$. This demonstrates that OPTIMA's density-guided domain placement ensures uniform compute progress across asymmetric cores at the expense of uncoordinated thread racing.

### 6.4 Extensible Scheduler Overheads in Dynamic Process Lifecycles
In the parallel Linux kernel compilation benchmark (`make kernel/`), the wall-clock build time is 6.03s for OPTIMA, matching `scx_rusty` (5.67s) and `scx_rlfifo` (5.83s), but lagging native CFS (3.29s). In earlier iterations of OPTIMA, build time was 11.63s due to an artificial queue barrier that prevented idle Efficiency cores from stealing tasks enqueued for Performance cores. Removing this barrier and enabling unconstrained asymmetric work-stealing reduced compilation time by **48.2%** (11.63s $\to$ 6.03s).

However, all `sched_ext` schedulers remain $\sim$83% slower than CFS on this workload. The root cause is microarchitectural trampoline overhead: kernel compilation executes tens of thousands of short-lived compiler processes (`gcc`, `as`, `ld`). For each process creation and termination, `sched_ext` invokes BPF struct-ops hooks (`ops.init_task`, `ops.exit_task`, `ops.select_cpu`, `ops.enqueue`). While each invocation costs only a few hundred nanoseconds, the cumulative overhead across hundreds of thousands of process lifecycles imposes a measurable penalty compared to in-kernel CFS C code that optimizes process creation paths natively.

### 6.5 Microarchitectural Asymmetry and Hardware IPC Retirement
In Table 2, OPTIMA registers a hardware IPC retirement of 0.88 (a -25.2% delta vs. CFS at 1.18). Importantly, peer extensible schedulers also exhibit lower retirement rates (both `scx_rusty` and `scx_rlfifo` register 0.83 IPC), demonstrating that this lower instruction retirement is not an isolated side effect of OPTIMA's density steering, but rather reflects general microarchitectural execution characteristics and memory-bound stall patterns across the benchmark suite under `sched_ext`. In OPTIMA, steering batch compute to narrower Efficiency pipelines does contribute to lower per-cycle retirement for those specific threads, but OPTIMA actually achieves slightly higher retirement (0.88) than peer schedulers (0.83) due to improved cache locality (reducing L3 cache misses by 58.5% down to 2.66% vs CFS's 6.40%).

Notably, in Table 2, `scx_rlfifo` registers an even lower L3 miss rate (1.36% vs. 2.66% on OPTIMA and 6.40% on CFS). This occurs because `scx_rlfifo`'s flat, single-queue FIFO design preserves strictly sequential task execution without domain rebalancing or cross-queue work-stealing passes, whereas OPTIMA incurs a minor cache footprint from hierarchical DSQ classification and work-stealing checks across the asymmetric topology.

### 6.6 Limitations and Future Work
Several open challenges remain for extensible asymmetric scheduling:
1. **Hardware Platform Diversity:** Our evaluation focuses on an asymmetric architecture with Performance and Efficiency cores sharing L3 cache. Future work must validate OPTIMA on disparate architectures, including ARM big.LITTLE mobile SoCs, Intel hybrid desktop processors with Hardware Feedback Interface (HFI), and multi-socket NUMA servers.
2. **Energy and Thermal Dynamics:** While our density-guided placement allocates long-running batch tasks to E-cores to maximize parallel energy efficiency, explicit integration with Energy-Aware Scheduling (EAS) models and dynamic voltage/frequency scaling (DVFS) governors remains an active research direction.
3. **Dynamic Burst Estimation in Complex Runtimes:** For JIT-compiled languages and asynchronous actor systems where execution bursts vary non-linearly, incorporating compiler-assisted hinting or statistical quantile tracking into BPF maps would refine task density estimates.

---

### 6.6 Hardware RAPL Energy Efficiency & Energy-Delay Product (EDP)
Asymmetric multi-core processors were fundamentally engineered to optimize energy efficiency by offloading long-running non-interactive workloads to dense, low-power execution units. Table 4 presents hardware RAPL energy measurements collected across 5 independent runs on the host AMD Zen 5/5c processor:
- **Synchronous IPC Energy Dividends:** In `perf bench sched pipe`, OPTIMA reduces total package energy consumption from $4.01 \pm 0.62$\,J on CFS down to **$3.10 \pm 0.17$\,J (-22.7%)**, while core energy drops from $0.96 \pm 0.18$\,J to **$0.71 \pm 0.51$\,J (-26.0%)**. Average package power decreases from $28.71 \pm 2.49$\,W to **$22.75 \pm 1.32$\,W (-20.8%)**, driving the Energy-Delay Product down to **$0.42 \pm 0.02$\,J·s (-27.6% EDP reduction)** vs. CFS's $0.58 \pm 0.19$\,J·s. By confining communicating thread pairs to local cores and eliminating cross-CCX IPI wakeups, OPTIMA minimizes dynamic uncore and interconnect power dissipation.
- **Process Churn & Race-to-Sleep Optimization:** In `hackbench`, OPTIMA reduces package energy from $6.73 \pm 0.78$\,J down to **$4.12 \pm 0.18$\,J (-38.8%)**, slashing EDP by **65.3%** ($1.01 \pm 0.23$\,J·s on CFS vs. **$0.35 \pm 0.04$\,J·s** on OPTIMA). While instantaneous power is modestly higher ($48.05$\,W vs. $45.34$\,W), OPTIMA's zero-lag local enqueue enables processes to finish in nearly half the time ($0.086$\,s vs. $0.148$\,s), achieving substantial race-to-sleep energy savings.
- **Batch Compute and Memory Saturation:** In steady-state batch computation (`sysbench cpu` and `sysbench memory`), energy consumption across all schedulers converges within $\approx 1\text{--}2\%$ of CFS ($179.68$\,J vs. $180.89$\,J on compute; $138.60$\,J vs. $137.69$\,J on memory). In memory bandwidth, OPTIMA delivers 12.8% higher data throughput with negligible energy differential ($+0.7\%$), confirming that density-guided batch partitioning on Efficiency cores preserves energy parity without degrading memory bus saturation.

## 7. Conclusion

In this paper, we presented **OPTIMA**, an extensible operating system scheduler for asymmetric multiprocessors implemented in Linux `sched_ext` eBPF. By replacing empirical fair-share heuristics with formal combinatorial optimization paradigms---Smith's Rule WSPT density ordering, Online Admissible Preemption Filtering (OAPF), density-guided domain placement, and synchronous IPC zero-lag dispatch---OPTIMA delivers principled scheduling foundations. Rigorous isolated empirical evaluation across 16 workloads demonstrates up to a 6.3$\times$ increase in IPC throughput, an 84.1% reduction in pipe latency, a 94.6% reduction in worst-case real-time latency spikes, and zero real-time audio drops. Our transparent analysis of workload trade-offs elucidates the conditions under which extensible scheduling excels, characterizes trampoline overheads under heavy process fork/exec storms and connection multiplexing, and establishes a principled empirical foundation for future asymmetric multiprocessor schedulers.

---

## References

1. I. Molnar, "[PATCH] Modular Scheduler Core and Completely Fair Scheduler (CFS)," *Linux Kernel Mailing List (LKML)*, Apr. 2007.
2. P. Zijlstra, "sched: Earliest Eligible Virtual Deadline First (EEVDF)," *Linux Kernel Mailing List (LKML)*, merged in Linux Kernel 6.6, 2023.
3. I. Stoica and H. Abdel-Wahab, "Earliest Eligible Virtual Deadline First: A Flexible and Accurate Mechanism for Proportional Share Resource Allocation," Department of Computer Science, Old Dominion University, Tech. Rep. TR-95-22, 1995.
4. T. Heo, D. Vernet, A. Righi, and C. Min, "sched_ext: BPF Extensible CPU Scheduling in the Linux Kernel," in *Proc. Linux Plumbers Conference (LPC)*, 2024.
5. D. Hodges, "Scheduling at Scale: eBPF Schedulers with sched_ext," in *Proc. USENIX SREcon24 EMEA*, Dublin, Ireland, Oct. 2024.
6. C. Min, "scx_lavd: Latency-Aware Virtual Deadline Scheduling for High-Framerate Interactive Computing," *Linux sched-ext Ecosystem and Igalia Technical Reports*, Tech. Rep., 2024.
7. A. Righi, T. Heo, D. Vernet, and sched-ext contributors, "sched-ext: scx_rlfifo and scx_rustland_core Reference Implementation and Documentation," Linux Kernel sched-ext Repository, https://github.com/sched-ext/scx, 2024.
8. J. T. Humphries, N. Natu, A. Chaugule, O. Weisse, B. Rhoden, J. Don, L. Rizzo, O. Rombakh, P. Turner, and C. Kozyrakis, "ghOSt: Fast & Flexible User-Space Delegation of Linux Scheduling," in *Proc. 28th ACM Symposium on Operating Systems Principles (SOSP '21)*, pp. 588–604, 2021.
9. A. K. Parekh and R. G. Gallager, "A Generalized Processor Sharing Approach to Flow Control in Integrated Services Networks: The Single-Node Case," *IEEE/ACM Transactions on Networking*, vol. 1, no. 3, pp. 344–357, 1993.
10. I. Stoica, H. Abdel-Wahab, K. Jeffay, S. K Baruah, S. E. Gehrke, and C. G. Plaxton, "A Proportional Share Resource Allocation Algorithm for Real-Time, Time-Shared Systems," in *Proc. 17th IEEE Real-Time Systems Symposium (RTSS)*, pp. 288–299, 1996.
11. J. Lelli, C. Scordino, L. Abeni, and D. Faggioli, "Deadline Scheduling in the Linux Kernel," *Software: Practice and Experience*, vol. 46, no. 6, pp. 821–839, 2016.
12. L. Abeni and G. Buttazzo, "Integrating Multimedia Applications in Hard Real-Time Systems," in *Proc. 19th IEEE Real-Time Systems Symposium (RTSS)*, pp. 4–13, 1998.
13. G. Lipari and S. K. Baruah, "Greedy Reclamation of Unused Bandwidth in Constant Bandwidth Servers," in *Proc. 12th Euromicro Conference on Real-Time Systems (ECRTS)*, pp. 193–200, 2000.
14. C. Kolivas, "The Brain Fuck Scheduler: BFS Introduction," Web publication and Linux Kernel Archives, 2009.
15. C. Kolivas, "Multiple Queue Skiplist Scheduler (MuQSS) for Linux," Web publication and Linux Kernel Archives, 2016.
16. R. Kumar, K. I. Farkas, N. P. Jouppi, P. Ranganathan, and D. M. Tullsen, "Single-ISA Heterogeneous Multi-Core Architectures: The Potential for Processor Power Reduction," in *Proc. 36th Annual IEEE/ACM International Symposium on Microarchitecture (MICRO)*, pp. 81–92, 2003.
17. R. Kumar, D. M. Tullsen, P. Ranganathan, N. P. Jouppi, and K. I. Farkas, "Single-ISA Heterogeneous Multi-Core Architectures for Multithreaded Workload Performance," in *Proc. 31st Annual International Symposium on Computer Architecture (ISCA)*, pp. 64–75, 2004.
18. D. Koufaty, D. Reddy, and S. Hahn, "Bias Scheduling in Heterogeneous Multi-Core Architectures," in *Proc. 5th European Conference on Computer Systems (EuroSys '10)*, pp. 125–138, 2010.
19. S. Blagodurov, S. Zhuravlev, M. Dashti, and A. Fedorova, "A Case for NUMA-Aware Contention Management on Multicore Systems," in *Proc. USENIX Annual Technical Conference (USENIX ATC)*, pp. 1–15, 2011.
20. S. Zhuravlev, S. Blagodurov, and A. Fedorova, "Addressing Shared Resource Contention in Multicore Processors via Scheduling," in *Proc. 15th International Conference on Architectural Support for Programming Languages and Operating Systems (ASPLOS)*, pp. 129–142, 2010.
21. J. C. Sáez, M. Prieto, A. Fedorova, and S. Blagodurov, "A Comprehensive Scheduler for Asymmetric Multicore Systems," in *Proc. 5th European Conference on Computer Systems (EuroSys '10)*, pp. 139–152, 2010.
22. S. Eyerman, K. Du Bois, and L. Eeckhout, "Speedup Stacks: Identifying Scaling Bottlenecks in Multi-Threaded Applications," in *Proc. IEEE International Symposium on Performance Analysis of Systems and Software (ISPASS)*, pp. 145–155, 2012.
23. S. Eyerman and L. Eeckhout, "System-Level Performance Metrics for Multiprogram Workloads," *IEEE Micro*, vol. 28, no. 3, pp. 42–53, 2008.
24. M. Rasmussen, P. Bellasi, D. Eggemann, and Q. Perret, "Energy Aware Scheduling (EAS) in the Linux Kernel," *Linux Kernel Documentation*, Documentation/scheduler/sched-energy.rst, 2018.
25. Intel Corporation, "Intel 64 and IA-32 Architectures Software Developer's Manual, Volume 3B: Hardware Feedback Interface (HFI)," Santa Clara, CA, USA, 2021.
26. R. Neri, "[PATCH v8 0/9] thermal/drivers/intel: Add Support for Intel Hardware Feedback Interface," *Linux Kernel Mailing List (LKML)*, 2022.
27. H. Guo, Q. Chen, M. Guo, and L. Xu, "SAWS: Selective Asymmetry-Aware Work-Stealing for Asymmetric Multi-Core Architectures," in *Proc. IEEE 18th International Conference on High Performance Computing and Communications (HPCC)*, pp. 116–123, 2016.
28. Q. Chen, M. Guo, and H. Guan, "LAWS: Locality-Aware Work-Stealing for Multi-Socket Multi-Core Architectures," in *Proc. 28th ACM International Conference on Supercomputing (ICS '14)*, pp. 3–12, 2014.
29. W. E. Smith, "Various Optimizers for Single-Stage Production," *Naval Research Logistics Quarterly*, vol. 3, no. 1–2, pp. 59–66, 1956.
30. J. K. Lenstra, A. H. G. Rinnooy Kan, and P. Brucker, "Complexity of Machine Scheduling Problems," *Annals of Discrete Mathematics*, vol. 1, pp. 343–362, 1977.
31. J. Bruno, E. G. Coffman, and R. Sethi, "Sequencing Problems with Partially Ordered Tasks and Alternative Processors," *Journal of the ACM*, vol. 21, no. 4, pp. 667–676, 1974.
32. M. R. Garey and D. S. Johnson, *Computers and Intractability: A Guide to the Theory of NP-Completeness*, San Francisco, CA, USA: W. H. Freeman and Company, 1979.
33. T. Kawaguchi and S. Kyan, "Worst Case Bound of an LRF Schedule for the Mean Weighted Flow-Time Problem," *SIAM Journal on Computing*, vol. 15, no. 4, pp. 1119–1129, 1986.
34. L. A. Hall, A. S. Schulz, D. B. Shmoys, and J. Wein, "Scheduling to Minimize Average Completion Time: Off-line and On-line Approximation Algorithms," *Mathematics of Operations Research*, vol. 22, no. 3, pp. 513–544, 1997.
35. N. Megow and A. S. Schulz, "On-line Scheduling to Minimize Average Completion Time Revisited," *Operations Research Letters*, vol. 32, no. 5, pp. 485–490, 2004.
36. C. A. Phillips, C. Stein, and J. Wein, "Minimizing Average Completion Time in the Presence of Release Dates," *Mathematical Programming*, vol. 82, no. 1, pp. 199–223, 1998.
37. M. J. Neely, *Stochastic Network Optimization with Application to Communication and Queueing Systems*, Synthesis Lectures on Communication Networks, Morgan & Claypool Publishers, vol. 3, no. 1, 2010.
38. M. J. Neely, "Energy Optimal Control for Time-Varying Wireless Networks," *IEEE Transactions on Information Theory*, vol. 52, no. 7, pp. 2915–2934, 2006.
39. L. Georgiadis, M. J. Neely, and L. Tassiulas, "Resource Allocation and Cross-Layer Control in Wireless Networks," *Foundations and Trends in Networking*, vol. 1, no. 1, pp. 1–144, 2006.


---

## Appendix: Artifact Evaluation

### A. Artifact Identification
- **Artifact Name:** OPTIMA: Algorithmic Extensible Operating System Scheduler for Asymmetric Multiprocessors
- **Primary Badges Applied:** Artifact Available, Artifact Functional, Results Reproduced
- **Repository:** Branch `DAA`, directory `artifact/`

### B. Hardware & Software Requirements
- **Host CPU:** Asymmetric multi-core processor (AMD Ryzen AI 9 HX 370 with 4 Zen 5 P-cores @ 5.16 GHz + 8 Zen 5c E-cores @ 3.29 GHz; Intel Hybrid 12th--14th Gen; ARM big.LITTLE / DynamIQ RK3588).
- **Kernel:** Linux 6.12+ / 7.0+ with `CONFIG_SCHED_CLASS_EXT=y` and hardware RAPL energy monitoring.
- **Toolchain:** Rust 1.75+, Clang/LLVM 16+, bpftool.

### C. Quick Reproduction Commands
```bash
# 1. Environment validation & build:
./artifact/setup_env.sh

# 2. Quick smoke test (< 1 min):
./artifact/run_artifact_eval.sh smoke

# 3. Hardware RAPL energy evaluation (5 runs):
./artifact/run_artifact_eval.sh rapl

# 4. View complete 34-metric benchmark table with Mean ± σ error bars:
python3 artifact/parse_results.py
```
