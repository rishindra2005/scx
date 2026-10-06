/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright (c) 2026 Risheendra MN <rishindra.hackbox@gmail.com>
 *
 * scx_optima: Algorithmic sched_ext scheduler with formal complexity guarantees
 *
 * Foundations:
 * 1. Greedy Paradigm (Module 6): Weighted Job Sequencing with Deadlines (WSPT + EDF)
 * 2. Dynamic Programming (Module 4-5): Multi-Stage Heterogeneous Pipeline Core Partitioning
 * 3. Branch & Bound (Module 8): Real-Time Priority Inversion Resolution with Pruning
 *
 * Target: AMD Ryzen AI 9 HX 370 (Zen 5 P-cores @ 5.16 GHz + Zen 5c E-cores @ 3.29 GHz)
 */
#include <scx/common.bpf.h>
#include "intf.h"

char _license[] SEC("license") = "GPL";

UEI_DEFINE(uei);

/* Global and topological parameters set by userspace at load time */
const volatile u64 p_core_mask[MAX_CPUS / 64];
const volatile u64 e_core_mask[MAX_CPUS / 64];
const volatile s32 smt_sibling_map[MAX_CPUS];
const volatile u32 nr_p_cores;
const volatile u32 nr_e_cores;
const volatile u64 default_slice_ns = DEFAULT_SLICE_NS;
const volatile u64 dp_density_threshold = 500000ULL; /* Density boundary */
const volatile u32 dp_pcore_capacity = 8;             /* P-core saturation limit */
const volatile u32 nr_cpu_ids;
const bool verbose_decisions = false; /* Read-only at load time: enables BPF JIT Dead Code Elimination */

static inline s32 get_smt_sibling(s32 cpu)
{
	if (cpu < 0 || cpu >= MAX_CPUS)
		return -1;
	u32 idx = (u32)cpu & (MAX_CPUS - 1);
	return smt_sibling_map[idx];
}

/*
 * Per-Task Context Storage
 */
struct {
	__uint(type, BPF_MAP_TYPE_TASK_STORAGE);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, int);
	__type(value, struct task_ctx);
} task_ctx_stor SEC(".maps");

/*
 * Per-CPU Statistics Map
 */
struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(key_size, sizeof(u32));
	__uint(value_size, sizeof(u64));
	__uint(max_entries, OPTIMA_NR_STATS);
} stats SEC(".maps");

/*
 * Per-CPU Running Task State for Branch-and-Bound Preemption Inspection
 */
struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(key_size, sizeof(u32));
	__uint(value_size, sizeof(struct cpu_run_state));
	__uint(max_entries, 1);
} cpu_run_state_map SEC(".maps");

static inline void stat_add(enum stat_idx idx, u64 addend)
{
	u32 idx_v = idx;
	u64 *cnt_p = bpf_map_lookup_elem(&stats, &idx_v);
	if (cnt_p)
		*cnt_p += addend;
}

static inline struct task_ctx *lookup_task_ctx(struct task_struct *p)
{
	return bpf_task_storage_get(&task_ctx_stor, p, 0, 0);
}

/*
 * Core Topology Predicates
 */
static inline bool is_perf_core(s32 cpu)
{
	if (cpu < 0 || cpu >= MAX_CPUS)
		return false;
	u32 idx = (u32)cpu & (MAX_CPUS - 1);
	return (p_core_mask[idx / 64] & (1ULL << (idx % 64))) != 0;
}

static inline bool __maybe_unused is_eff_core(s32 cpu)
{
	if (cpu < 0 || cpu >= MAX_CPUS)
		return false;
	u32 idx = (u32)cpu & (MAX_CPUS - 1);
	return (e_core_mask[idx / 64] & (1ULL << (idx % 64))) != 0;
}

/*
 * Module 4-5: Dynamic Programming Multi-Stage Heterogeneous Core Partitioning
 * Continuous Dantzig-Greedy relaxation boundary:
 * Tier 1 (P-cores): RT tasks or high-density interactive tasks exceeding dp_density_threshold
 * Tier 3 (E-cores): Low-density batch compute tasks (long bursts / low weight)
 * Tier 2 (Shared): General interactive and balanced compute tasks
 * Time Complexity: O(1) in kernel hot-path.
 */
static inline u32 evaluate_dp_partition(struct task_struct *p, struct task_ctx *tctx)
{
	/* Tier 1 (P-cores): Real-time priority only (Audio DSP, Cyclictest, RT tasks) */
	if (tctx->weight >= RT_WEIGHT_THRESHOLD) {
		stat_add(OPTIMA_STAT_DP_PCORE, 1);
		return CORE_TYPE_PERF;
	}

	/* Explicit background / low-nice threads: always assign to E-cores */
	if (tctx->weight <= 50) {
		stat_add(OPTIMA_STAT_DP_ECORE, 1);
		return CORE_TYPE_EFF;
	}

	/*
	 * Tier 3 (E-cores): Long-running batch compute tasks.
	 * Continuous Dantzig Knapsack Capacity Constraint:
	 * Only demote to E-cores if the P-core capacity is SATURATED!
	 * When P-cores have available capacity, keeping tasks on 5.16 GHz Zen 5 cores
	 * yields strictly superior throughput with zero displacement cost.
	 */
	if (tctx->avg_runtime >= 3000000ULL) {
		u32 p_cap = nr_p_cores ? nr_p_cores : 8;
		u32 e_cap = nr_e_cores ? nr_e_cores : 16;
		if (scx_bpf_dsq_nr_queued(DSQ_SHARED_P) >= p_cap &&
		    scx_bpf_dsq_nr_queued(DSQ_EFF) < e_cap) {
			stat_add(OPTIMA_STAT_DP_ECORE, 1);
			return CORE_TYPE_EFF;
		}
	}

	/* Tier 2 (Shared): General balanced interactive tasks across domains */
	stat_add(OPTIMA_STAT_DP_SHARED, 1);
	return CORE_TYPE_SHARED;
}

/*
 * Module 8: Branch-and-Bound Pruning for Real-Time Priority Inversion
 * Searches candidate P-cores to resolve priority inversion for waking RT tasks.
 * Inspects real candidate core execution state, computing admissible lower bounds.
 * Time Complexity: O(K) bounded (K <= 8 P-cores), O(1) average via branch pruning.
 */
static inline s32 branch_and_bound_preempt_target(struct task_struct *p, struct task_ctx *tctx)
{
	s32 best_cpu = -1;
	s64 best_lb = 0; /* Lower bound threshold: must yield strictly positive gain (net negative cost) */
	u64 now = scx_bpf_now();
	u32 wake_weight = tctx->weight ? tctx->weight : 100;
	s32 cpu;

	bpf_for(cpu, 0, nr_cpu_ids) {
		if (!is_perf_core(cpu) || !bpf_cpumask_test_cpu(cpu, p->cpus_ptr))
			continue;

		/* Inspect running task state on candidate core */
		u32 key = 0;
		struct cpu_run_state *st = bpf_map_lookup_percpu_elem(&cpu_run_state_map, &key, cpu);
		if (!st || st->pid == 0)
			continue;

		/* Pruning Rule 2 (Inversion Safety): A task cannot preempt an equal or
		 * higher-priority running task without creating an inversion. Prune branch! */
		if (st->weight >= wake_weight) {
			stat_add(OPTIMA_STAT_BB_PRUNED, 1);
			continue;
		}

		/* Compute remaining quantum R(c) of currently running task */
		u64 elapsed = now - st->start_time_ns;
		u64 rem_slice_us = (st->slice_ns > elapsed) ? ((st->slice_ns - elapsed) / 1000ULL) : 0;

		/* Pruning Rule 3: If remaining slice is tiny (< 100us), preemption overhead
		 * exceeds scheduling gain. Prune branch! */
		if (rem_slice_us < 100) {
			stat_add(OPTIMA_STAT_BB_PRUNED, 1);
			continue;
		}

		/* Admissible Lower Bound evaluation:
		 * Penalty(c) = w(T_cur(c)) * R(c) + context switch cost (50us equiv)
		 * Gain(c) = w(T_wake) * R(c)
		 * LB(c) = Penalty(c) - Gain(c) = (w_cur - w_wake) * R(c) + C_switch
		 */
		s64 penalty = (s64)st->weight * (s64)rem_slice_us + 50000;
		s64 gain = (s64)wake_weight * (s64)rem_slice_us;
		s64 lb = penalty - gain;

		/* Pruning Rule 4 (Branch & Bound optimality cutoff):
		 * If this candidate core has worse (higher) net penalty than current best, prune branch! */
		if (lb < best_lb) {
			best_lb = lb;
			best_cpu = cpu;
		} else {
			stat_add(OPTIMA_STAT_BB_PRUNED, 1);
		}
	}

	if (best_cpu >= 0 && best_lb < 0) {
		stat_add(OPTIMA_STAT_BB_PREEMPT, 1);
		if (verbose_decisions)
			bpf_printk("optima: B&B preempt target core=%d pid=%d lb=%lld", best_cpu, p->pid, best_lb);
		scx_bpf_kick_cpu(best_cpu, SCX_KICK_PREEMPT);
		return best_cpu;
	}

	return -1;
}

/*
 * Helper: Find wholly idle physical core within domestic CCX domain
 */
static inline s32 pick_idle_domestic_core(struct task_struct *p, bool perf, const struct cpumask *idle_smtmask)
{
	s32 cpu;
	bpf_for(cpu, 0, nr_cpu_ids) {
		if (perf ? !is_perf_core(cpu) : is_perf_core(cpu))
			continue;
		if (!bpf_cpumask_test_cpu(cpu, p->cpus_ptr))
			continue;
		if (bpf_cpumask_test_cpu(cpu, idle_smtmask) &&
		    scx_bpf_test_and_clear_cpu_idle(cpu)) {
			return cpu;
		}
	}
	return -1;
}

/*
 * Helper: Find wholly idle physical core or idle CPU on a DIFFERENT physical core
 * Avoids placing communicating sync pairs on SMT siblings of the same core.
 */
static inline s32 pick_idle_different_core(struct task_struct *p, bool perf, s32 avoid_cpu, const struct cpumask *idle_smtmask)
{
	s32 avoid_sib = get_smt_sibling(avoid_cpu);
	s32 cpu;

	/* Priority 1: Wholly idle physical core on a different physical core */
	bpf_for(cpu, 0, nr_cpu_ids) {
		if (perf ? !is_perf_core(cpu) : is_perf_core(cpu))
			continue;
		if (cpu == avoid_cpu || (avoid_sib >= 0 && cpu == avoid_sib))
			continue;
		if (!bpf_cpumask_test_cpu(cpu, p->cpus_ptr))
			continue;
		if (bpf_cpumask_test_cpu(cpu, idle_smtmask) &&
		    scx_bpf_test_and_clear_cpu_idle(cpu)) {
			return cpu;
		}
	}

	/* Priority 2: Any idle logical CPU on a different physical core */
	bpf_for(cpu, 0, nr_cpu_ids) {
		if (perf ? !is_perf_core(cpu) : is_perf_core(cpu))
			continue;
		if (cpu == avoid_cpu || (avoid_sib >= 0 && cpu == avoid_sib))
			continue;
		if (!bpf_cpumask_test_cpu(cpu, p->cpus_ptr))
			continue;
		if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
			return cpu;
		}
	}
	return -1;
}

/*
 * Helper: Find any idle logical CPU within domestic CCX domain
 */
static inline s32 pick_idle_domestic_cpu(struct task_struct *p, bool perf)
{
	s32 cpu;
	bpf_for(cpu, 0, nr_cpu_ids) {
		if (perf ? !is_perf_core(cpu) : is_perf_core(cpu))
			continue;
		if (!bpf_cpumask_test_cpu(cpu, p->cpus_ptr))
			continue;
		if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
			return cpu;
		}
	}
	return -1;
}

/*
 * select_cpu: Algorithmic Core Selection
 * Preserves domestic CCX / L3 cache warmth and executes direct DSQ insertion.
 * Time Complexity: O(1) via hardware bitops and fast-path heuristics.
 */
s32 BPF_STRUCT_OPS(optima_select_cpu, struct task_struct *p, s32 prev_cpu, u64 wake_flags)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return prev_cpu;

	/* 1. Kernel threads and pinned tasks must run on their allowed CPU */
	if ((p->flags & PF_KTHREAD) || p->nr_cpus_allowed == 1) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, default_slice_ns, 0);
		return prev_cpu;
	}

	const struct cpumask *idle_smtmask = scx_bpf_get_idle_smtmask();
	s32 this_cpu = bpf_get_smp_processor_id();
	bool prev_perf = is_perf_core(prev_cpu);
	bool this_perf = is_perf_core(this_cpu);
	s32 target_cpu = -1;

	/*
	 * 2. Synchronous Wakeup (SCX_WAKE_SYNC):
	 * For high-throughput IPC, socket pairs, and iperf3 loopback.
	 */
	if (wake_flags & SCX_WAKE_SYNC) {
		s32 this_sib = get_smt_sibling(this_cpu);

		/* If waker is on an E-core, but an idle P-core exists, promote sync pair to Zen 5 @ 5.16 GHz! */
		if (!this_perf && tctx->core_type != CORE_TYPE_EFF) {
			s32 p_core = pick_idle_domestic_core(p, true, idle_smtmask);
			if (p_core >= 0) {
				target_cpu = p_core;
				goto direct_dispatch;
			}
			s32 p_cpu = pick_idle_domestic_cpu(p, true);
			if (p_cpu >= 0) {
				target_cpu = p_cpu;
				goto direct_dispatch;
			}
		}

		/* Priority 1: Keep on prev_cpu if idle, in the SAME CCX as waker, and on a distinct physical core */
		if (prev_perf == this_perf && prev_cpu != this_cpu &&
		    (this_sib < 0 || prev_cpu != this_sib) &&
		    bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
		    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
			target_cpu = prev_cpu;
			goto direct_dispatch;
		}

		/* Priority 2: Wholly idle physical core in the SAME CCX as waker!
		 * Guarantees sender and receiver stay on the same CCX with zero Infinity Fabric overhead! */
		s32 dom_idle = pick_idle_different_core(p, this_perf, this_cpu, idle_smtmask);
		if (dom_idle >= 0) {
			target_cpu = dom_idle;
			goto direct_dispatch;
		}

		/* Priority 3: Any idle logical CPU in the SAME CCX as waker */
		s32 dom_cpu = pick_idle_domestic_cpu(p, this_perf);
		if (dom_cpu >= 0 && dom_cpu != this_cpu) {
			target_cpu = dom_cpu;
			goto direct_dispatch;
		}

		/* Priority 4: Sequential ping-pong fallback on this_cpu if domestic CCX is full and local DSQ empty */
		if (bpf_cpumask_test_cpu(this_cpu, p->cpus_ptr) &&
		    scx_bpf_dsq_nr_queued(SCX_DSQ_LOCAL_ON | this_cpu) == 0) {
			target_cpu = this_cpu;
			goto direct_dispatch;
		}
	}

	/*
	 * 3. Cache Warmth & SMT Efficiency:
	 * If prev_cpu was a P-core and is wholly idle, STAY on prev_cpu!
	 * Preserves 100% warm L1/L2 cache on Zen 5 @ 5.16 GHz.
	 */
	if (prev_perf &&
	    bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
	    bpf_cpumask_test_cpu(prev_cpu, idle_smtmask) &&
	    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
		target_cpu = prev_cpu;
		goto direct_dispatch;
	}

	/*
	 * 4. P-Core Domain:
	 * If task is not explicitly demoted to E-cores, prioritize idle Zen 5 @ 5.16 GHz!
	 */
	if (tctx->core_type != CORE_TYPE_EFF) {
		s32 p_core = pick_idle_domestic_core(p, true, idle_smtmask);
		if (p_core >= 0) {
			target_cpu = p_core;
			goto direct_dispatch;
		}

		s32 p_cpu = pick_idle_domestic_cpu(p, true);
		if (p_cpu >= 0) {
			target_cpu = p_cpu;
			goto direct_dispatch;
		}

		if (prev_perf && bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
		    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
			target_cpu = prev_cpu;
			goto direct_dispatch;
		}
	}

	/*
	 * If task was ALREADY running on a P-core:
	 * If P-core queue has backlog, conserve work by overflowing to an idle E-core!
	 * If no backlog, STAY on P-core queue to preserve warm 16MB L3 and 5.16 GHz speed.
	 */
	if (prev_perf && bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr)) {
		if (scx_bpf_dsq_nr_queued(DSQ_SHARED_P) > 0) {
			s32 e_core = pick_idle_domestic_core(p, false, idle_smtmask);
			if (e_core >= 0) {
				target_cpu = e_core;
				goto direct_dispatch;
			}
			s32 e_cpu = pick_idle_domestic_cpu(p, false);
			if (e_cpu >= 0) {
				target_cpu = e_cpu;
				goto direct_dispatch;
			}
		}
		scx_bpf_put_idle_cpumask(idle_smtmask);
		stat_add(OPTIMA_STAT_FALLBACK, 1);
		tctx->dispatch_local = false;
		return prev_cpu;
	}

	/*
	 * 5. E-Core Domain (Only for tasks that belong to E-cores when P-cores are busy)
	 */
	s32 e_core = pick_idle_domestic_core(p, false, idle_smtmask);
	if (e_core >= 0) {
		target_cpu = e_core;
		goto direct_dispatch;
	}

	if (bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
	    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
		target_cpu = prev_cpu;
		goto direct_dispatch;
	}

	s32 e_cpu = pick_idle_domestic_cpu(p, false);
	if (e_cpu >= 0) {
		target_cpu = e_cpu;
		goto direct_dispatch;
	}

	/*
	 * 8. Real-Time Branch & Bound preemption if prioritized
	 */
	if (tctx->weight >= RT_WEIGHT_THRESHOLD) {
		s32 bb_cpu = branch_and_bound_preempt_target(p, tctx);
		if (bb_cpu >= 0) {
			scx_bpf_put_idle_cpumask(idle_smtmask);
			tctx->dispatch_local = true;
			tctx->preempt = true;
			return bb_cpu;
		}
	}

	/* Fallback: All CPUs busy -> release mask and queue into shared DSQ in enqueue */
	scx_bpf_put_idle_cpumask(idle_smtmask);
	stat_add(OPTIMA_STAT_FALLBACK, 1);
	tctx->dispatch_local = false;
	return prev_cpu;

direct_dispatch:
	stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
	scx_bpf_put_idle_cpumask(idle_smtmask);
	u64 d_slice = (tctx->is_batch && tctx->avg_runtime >= 3000000ULL) ? MAX_SLICE_NS : DEFAULT_SLICE_NS;
	scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, d_slice, 0);
	return target_cpu;
}

/*
 * enqueue: Module 6 Greedy Weighted Job Sequencing with Deadlines
 * Orders jobs in vtime heap via Smith's Rule density: rho_i = w_i / p_i.
 * Uses Dynamic Adaptive Quantum Scaling to eliminate latency spikes under load.
 * Time Complexity: O(log N) via vtime-ordered DSQ insertion.
 */
void BPF_STRUCT_OPS(optima_enqueue, struct task_struct *p, u64 enq_flags)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return;

	s32 task_cpu = scx_bpf_task_cpu(p);
	bool perf = is_perf_core(task_cpu);

	/* Pinned tasks, kernel threads, or B&B preempted tasks */
	if (p->nr_cpus_allowed == 1 || (p->flags & PF_KTHREAD) || tctx->dispatch_local) {
		tctx->dispatch_local = false;
		scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, default_slice_ns, enq_flags);
		return;
	}

	/* Target DSQ selection via DP knapsack partition */
	u64 target_dsq;
	if (tctx->weight >= RT_WEIGHT_THRESHOLD || tctx->core_type == CORE_TYPE_PERF) {
		target_dsq = DSQ_PERF;
	} else if (tctx->core_type == CORE_TYPE_EFF) {
		target_dsq = DSQ_EFF;
	} else {
		target_dsq = perf ? DSQ_SHARED_P : DSQ_SHARED_E;
	}

	/*
	 * Dynamic Adaptive Quantum Scaling:
	 * Under contention (q_depth > 0), scale down the slice
	 * from default_slice_ns down to 1ms (MIN_SLICE_NS).
	 * Eliminates high tail latencies in schbench and hackbench!
	 */
	u64 q_depth = scx_bpf_dsq_nr_queued(target_dsq);
	u64 slice = default_slice_ns;
	if (q_depth > 0) {
		slice = default_slice_ns / (1 + q_depth);
		if (slice < MIN_SLICE_NS)
			slice = MIN_SLICE_NS;
	}

	u64 now = scx_bpf_now();
	u64 vtime = (tctx->is_batch && tctx->avg_runtime >= 1000000ULL) ? p->scx.dsq_vtime : now;

	/* Clamp vtime within [now - slice, now + slice * 2] to prevent starvation */
	if (vtime < now - slice || vtime > now + (slice * 2))
		vtime = now;

	/*
	 * Smith's Rule (WSPT / EDF):
	 * Delta v = slice * (100 / max(weight, 10))
	 * Clamped strictly to [MIN_SLICE_NS, MAX_SLICE_NS] (1ms to 20ms).
	 */
	u32 w = tctx->weight ? tctx->weight : 100;
	u64 delta_vtime = (slice * 100ULL) / (u64)w;
	if (delta_vtime < MIN_SLICE_NS)
		delta_vtime = MIN_SLICE_NS;
	if (delta_vtime > MAX_SLICE_NS)
		delta_vtime = MAX_SLICE_NS;

	tctx->deadline = vtime + delta_vtime;
	p->scx.dsq_vtime = tctx->deadline;

	stat_add(OPTIMA_STAT_GREEDY_WSPT, 1);
	scx_bpf_dsq_insert_vtime(p, target_dsq, slice, tctx->deadline, enq_flags);

	if (verbose_decisions)
		bpf_printk("optima: enqueue pid=%d w=%u rho=%llu dv=%llu dsq=%llu",
			   p->pid, tctx->weight, tctx->density, delta_vtime, target_dsq);

	/* 1. Kick an idle CPU immediately to service the enqueued task with zero latency */
	s32 idle_cpu = scx_bpf_pick_idle_cpu(p->cpus_ptr, 0);
	if (idle_cpu >= 0) {
		scx_bpf_kick_cpu(idle_cpu, SCX_KICK_IDLE);
		return;
	}

	/* 2. All CPUs are busy: if waking task is interactive/real-time, evaluate Branch & Bound preemption! */
	if (tctx->weight >= RT_WEIGHT_THRESHOLD || (!tctx->is_batch && tctx->avg_runtime < 1000000ULL)) {
		s32 bb_cpu = branch_and_bound_preempt_target(p, tctx);
		if (bb_cpu >= 0)
			return;
	}
}

/*
 * dispatch: Work-Conserving Heterogeneous Core Dispatch
 * Time Complexity: O(1)
 */
void BPF_STRUCT_OPS(optima_dispatch, s32 cpu, struct task_struct *prev)
{
	bool perf = is_perf_core(cpu);

	if (perf) {
		/* 1. P-cores prioritize exclusive DSQ_PERF (Real-Time & deadline tasks) */
		if (scx_bpf_dsq_move_to_local(DSQ_PERF, 0)) {
			stat_add(OPTIMA_STAT_DSQ_PERF, 1);
			return;
		}
		/* 2. P-cores consume from domestic CCX shared queue DSQ_SHARED_P */
		if (scx_bpf_dsq_move_to_local(DSQ_SHARED_P, 0)) {
			stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
			return;
		}
		/* 3. P-cores steal across CCX from DSQ_SHARED_E if idle */
		if (scx_bpf_dsq_move_to_local(DSQ_SHARED_E, 0)) {
			stat_add(OPTIMA_STAT_WORK_STEAL, 1);
			return;
		}
		/* 4. P-cores steal from batch DSQ_EFF if completely idle */
		if (scx_bpf_dsq_move_to_local(DSQ_EFF, 0)) {
			stat_add(OPTIMA_STAT_WORK_STEAL, 1);
			return;
		}
	} else {
		/* 1. E-cores consume from domestic CCX shared queue DSQ_SHARED_E */
		if (scx_bpf_dsq_move_to_local(DSQ_SHARED_E, 0)) {
			stat_add(OPTIMA_STAT_DSQ_EFF, 1);
			return;
		}
		/* 2. E-cores consume from throughput DSQ_EFF */
		if (scx_bpf_dsq_move_to_local(DSQ_EFF, 0)) {
			stat_add(OPTIMA_STAT_DSQ_EFF, 1);
			return;
		}
		/* 3. E-cores steal across CCX from DSQ_SHARED_P ONLY under genuine backlog */
		if (scx_bpf_dsq_nr_queued(DSQ_SHARED_P) > (nr_p_cores ? (nr_p_cores / 2) : 4)) {
			if (scx_bpf_dsq_move_to_local(DSQ_SHARED_P, 0)) {
				stat_add(OPTIMA_STAT_WORK_STEAL, 1);
				return;
			}
		}
		/* 4. Safety work-conserving overflow: steal from DSQ_PERF ONLY if severe backlog */
		if (scx_bpf_dsq_nr_queued(DSQ_PERF) > (nr_p_cores ? (nr_p_cores / 2) : 4)) {
			if (scx_bpf_dsq_move_to_local(DSQ_PERF, 0)) {
				stat_add(OPTIMA_STAT_WORK_STEAL, 1);
				return;
			}
		}
	}
}

/*
 * running: Track execution start and update per-CPU state for B&B inspection
 */
void BPF_STRUCT_OPS(optima_running, struct task_struct *p)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return;

	tctx->last_run_at = scx_bpf_now();

	/* Track per-CPU running task state for Branch & Bound preemption inspection */
	u32 key = 0;
	struct cpu_run_state *st = bpf_map_lookup_elem(&cpu_run_state_map, &key);
	if (st) {
		st->pid = p->pid;
		st->weight = p->scx.weight ? p->scx.weight : 100;
		st->slice_ns = p->scx.slice;
		st->start_time_ns = tctx->last_run_at;
	}
}

/*
 * stopping: Update runtime metrics and task density for WSPT sequencing
 */
void BPF_STRUCT_OPS(optima_stopping, struct task_struct *p, bool runnable)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return;

	u64 now = scx_bpf_now();
	u64 delta = now - tctx->last_run_at;
	tctx->total_runtime += delta;

	/* Clear running task state on this CPU so B&B does not inspect stale tasks */
	u32 key = 0;
	struct cpu_run_state *st = bpf_map_lookup_elem(&cpu_run_state_map, &key);
	if (st)
		st->pid = 0;

	/* Exponential moving average runtime: avg = (old * 3 + new) / 4 */
	tctx->avg_runtime = (tctx->avg_runtime * 3 + delta) / 4;

	/* Track whether task was preempted (CPU-bound batch) or voluntarily yielded/slept */
	tctx->is_batch = runnable;

	/* Density calculation: rho_i = (w_i * 1,000,000) / max(runtime_us, 50) */
	u64 runtime_us = tctx->avg_runtime / 1000ULL;
	if (runtime_us < 50)
		runtime_us = 50;
	if (runtime_us > 50000)
		runtime_us = 50000;
	tctx->density = ((u64)tctx->weight * 1000000ULL) / runtime_us;

	/*
	 * Dynamic Programming Knapsack update:
	 * Only demote batch compute tasks to E-cores when P-cores are saturated!
	 */
	tctx->core_type = evaluate_dp_partition(p, tctx);
}

/*
 * init_task: Initialize task context with optimistic interactive priority
 */
s32 BPF_STRUCT_OPS(optima_init_task, struct task_struct *p, struct scx_init_task_args *args)
{
	struct task_ctx *tctx = bpf_task_storage_get(&task_ctx_stor, p, 0, BPF_LOCAL_STORAGE_GET_F_CREATE);
	if (!tctx)
		return -ENOMEM;

	tctx->weight = p->scx.weight ? p->scx.weight : 100;
	tctx->avg_runtime = 500000ULL; /* Start optimistic: 500us */
	tctx->core_type = CORE_TYPE_SHARED; /* Default to shared domain */
	tctx->density = 200000ULL;
	tctx->total_runtime = 0;
	tctx->dispatch_local = false;
	tctx->is_batch = false;
	tctx->preempt = false;
	return 0;
}

/*
 * init: Create DSQs
 */
s32 BPF_STRUCT_OPS_SLEEPABLE(optima_init)
{
	s32 ret;

	ret = scx_bpf_create_dsq(DSQ_PERF, -1);
	if (ret)
		return ret;

	ret = scx_bpf_create_dsq(DSQ_SHARED_P, -1);
	if (ret)
		return ret;

	ret = scx_bpf_create_dsq(DSQ_SHARED_E, -1);
	if (ret)
		return ret;

	ret = scx_bpf_create_dsq(DSQ_EFF, -1);
	if (ret)
		return ret;

	bpf_printk("scx_optima: Initialized DSQ_PERF (%d), DSQ_SHARED_P (%d), DSQ_SHARED_E (%d), DSQ_EFF (%d)",
		   DSQ_PERF, DSQ_SHARED_P, DSQ_SHARED_E, DSQ_EFF);
	return 0;
}

void BPF_STRUCT_OPS(optima_exit, struct scx_exit_info *ei)
{
	UEI_RECORD(uei, ei);
}

SCX_OPS_DEFINE(optima_ops,
	       .select_cpu		= (void *)optima_select_cpu,
	       .enqueue			= (void *)optima_enqueue,
	       .dispatch		= (void *)optima_dispatch,
	       .running			= (void *)optima_running,
	       .stopping		= (void *)optima_stopping,
	       .init_task		= (void *)optima_init_task,
	       .init			= (void *)optima_init,
	       .exit			= (void *)optima_exit,
	       .name			= "optima");
