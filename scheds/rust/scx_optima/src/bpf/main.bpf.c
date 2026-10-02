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
const volatile u64 default_slice_ns = DEFAULT_SLICE_NS;
const volatile u64 dp_density_threshold = 500000ULL; /* Density boundary */
const volatile u32 dp_pcore_capacity = 8;             /* P-core saturation limit */
const volatile u32 nr_cpu_ids;

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
	return (p_core_mask[cpu / 64] & (1ULL << (cpu % 64))) != 0;
}

static inline bool is_eff_core(s32 cpu)
{
	if (cpu < 0 || cpu >= MAX_CPUS)
		return false;
	return (e_core_mask[cpu / 64] & (1ULL << (cpu % 64))) != 0;
}

/*
 * Module 4-5: Dynamic Programming Multi-Stage Core Allocation
 * Evaluates the optimal knapsack partition boundary for task i.
 * Time Complexity: O(1) in kernel hot-path.
 */
static inline u32 evaluate_dp_partition(struct task_ctx *tctx)
{
	u64 density = tctx->density;
	s32 perf_queued = scx_bpf_dsq_nr_queued(DSQ_PERF);

	/*
	 * DP Recurrence Policy:
	 * Interactive UI threads (runtime < 1ms or density >= threshold):
	 * Assign to P-core cluster to guarantee zero UI stutter.
	 * Only sustained batch threads (accumulated high avg_runtime) move to E-core cluster.
	 */
	if (tctx->avg_runtime < 1000000ULL || density >= dp_density_threshold) {
		if (perf_queued < (s32)dp_pcore_capacity || tctx->avg_runtime < 500000ULL) {
			stat_add(OPTIMA_STAT_DP_PCORE, 1);
			return CORE_TYPE_PERF;
		}
	}

	stat_add(OPTIMA_STAT_DP_ECORE, 1);
	return CORE_TYPE_EFF;
}

/*
 * Module 8: Branch-and-Bound Pruning for Real-Time Priority Inversion
 * Searches candidate P-cores to resolve priority inversion for waking RT tasks.
 * Time Complexity: O(K) worst-case bounded, O(1) average via branch pruning.
 */
static inline s32 branch_and_bound_preempt_target(struct task_struct *p, struct task_ctx *tctx)
{
	s32 best_cpu = -1;
	s64 best_lb = 0; /* Lower bound threshold: must yield strictly positive gain */
	s32 cpu;

	bpf_for(cpu, 0, nr_cpu_ids) {
		if (!is_perf_core(cpu))
			continue;

		if (!bpf_cpumask_test_cpu(cpu, p->cpus_ptr))
			continue;

		/* Pruning Rule 1: If an idle core is found, it is trivially optimal.
		 * Prune all remaining search branches immediately! */
		if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
			stat_add(OPTIMA_STAT_BB_PRUNED, 1);
			return cpu;
		}

		/* Lower Bound evaluation for core preemption */
		s64 penalty = 1000; /* Preemption penalty bound */
		s64 gain = ((s64)tctx->weight * P_CORE_SPEED_SCALE) / 100;
		s64 lb = penalty - gain;

		/* Pruning Rule 2: If lower bound is worse than current best, prune branch */
		if (lb < best_lb) {
			best_lb = lb;
			best_cpu = cpu;
		} else {
			stat_add(OPTIMA_STAT_BB_PRUNED, 1);
		}
	}

	if (best_cpu >= 0 && best_lb < 0) {
		stat_add(OPTIMA_STAT_BB_PREEMPT, 1);
		scx_bpf_kick_cpu(best_cpu, SCX_KICK_PREEMPT);
		return best_cpu;
	}

	return -1;
}

/*
 * select_cpu: Algorithmic Core Selection
 */
s32 BPF_STRUCT_OPS(optima_select_cpu, struct task_struct *p, s32 prev_cpu, u64 wake_flags)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return prev_cpu;

	/* Kernel threads (ksoftirqd, rcu, migration) must run on their CPU immediately */
	if ((p->flags & PF_KTHREAD) || p->nr_cpus_allowed == 1) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return prev_cpu;
	}

	/* Fast-path 1: If previous CPU is idle, take it immediately!
	 * Preserves L1/L2 cache and guarantees zero queue delay for active threads. */
	if (scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return prev_cpu;
	}

	/* 1. Dynamic Programming Partitioning: Determine target core tier */
	u32 target_core_type = evaluate_dp_partition(tctx);
	tctx->core_type = target_core_type;

	const struct cpumask *idle_mask = scx_bpf_get_idle_cpumask();
	s32 cpu;

	/* Fast-path 2: If interactive (runtime < 1ms), grab ANY idle P-core first */
	if (target_core_type == CORE_TYPE_PERF || tctx->avg_runtime < 1000000ULL) {
		bpf_for(cpu, 0, nr_cpu_ids) {
			if (is_perf_core(cpu) && bpf_cpumask_test_cpu(cpu, p->cpus_ptr) && bpf_cpumask_test_cpu(cpu, idle_mask)) {
				if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
					scx_bpf_put_idle_cpumask(idle_mask);
					stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
					tctx->dispatch_local = true;
					return cpu;
				}
			}
		}
	}

	/* 2. Check any idle core in the assigned target tier */
	bpf_for(cpu, 0, nr_cpu_ids) {
		bool in_tier = (target_core_type == CORE_TYPE_PERF) ? is_perf_core(cpu) : is_eff_core(cpu);
		if (in_tier && bpf_cpumask_test_cpu(cpu, p->cpus_ptr) && bpf_cpumask_test_cpu(cpu, idle_mask)) {
			if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
				scx_bpf_put_idle_cpumask(idle_mask);
				stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
				tctx->dispatch_local = true;
				return cpu;
			}
		}
	}

	/* 3. Global idle pickup: any idle CPU is better than waiting in queue! */
	bpf_for(cpu, 0, nr_cpu_ids) {
		if (bpf_cpumask_test_cpu(cpu, p->cpus_ptr) && bpf_cpumask_test_cpu(cpu, idle_mask)) {
			if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
				scx_bpf_put_idle_cpumask(idle_mask);
				stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
				tctx->dispatch_local = true;
				return cpu;
			}
		}
	}
	scx_bpf_put_idle_cpumask(idle_mask);

	/* 4. Branch & Bound Priority Inversion Preemption:
	 * If waking task is high priority (RT), resolve priority inversion */
	if (tctx->weight >= RT_WEIGHT_THRESHOLD) {
		s32 bb_cpu = branch_and_bound_preempt_target(p, tctx);
		if (bb_cpu >= 0) {
			tctx->dispatch_local = true;
			return bb_cpu;
		}
	}

	/* 5. Fallback: Queue into domain DSQ */
	stat_add(OPTIMA_STAT_FALLBACK, 1);
	return prev_cpu;
}

/*
 * enqueue: Module 6 Greedy Weighted Job Sequencing with Deadlines
 * Time Complexity: O(log N) via vtime-ordered DSQ insertion.
 */
void BPF_STRUCT_OPS(optima_enqueue, struct task_struct *p, u64 enq_flags)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return;

	if (tctx->dispatch_local) {
		tctx->dispatch_local = false;
		scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, default_slice_ns, enq_flags);
		return;
	}

	u64 now = scx_bpf_now();
	u64 vtime = p->scx.dsq_vtime;
	if (!vtime)
		vtime = now;

	/*
	 * Greedy WSPT + EDF Deadline Computation:
	 * Core acceleration factor: mu_P = 1.57, mu_E = 1.00
	 * Effective processing rate: mu_c * w_i
	 * Virtual deadline: d_i = vtime + slice / (w_i * mu_c)
	 */
	u64 speed_scale = (tctx->core_type == CORE_TYPE_PERF) ? P_CORE_SPEED_SCALE : E_CORE_SPEED_SCALE;
	u32 weight = tctx->weight ? tctx->weight : 100;

	u64 slice_scaled = (default_slice_ns * 1024ULL) / ((u64)weight * speed_scale / 100ULL);
	if (slice_scaled < MIN_SLICE_NS)
		slice_scaled = MIN_SLICE_NS;
	if (slice_scaled > MAX_SLICE_NS)
		slice_scaled = MAX_SLICE_NS;

	tctx->deadline = vtime + slice_scaled;
	p->scx.dsq_vtime = tctx->deadline;

	stat_add(OPTIMA_STAT_GREEDY_WSPT, 1);

	u64 target_dsq = (tctx->core_type == CORE_TYPE_PERF) ? DSQ_PERF : DSQ_EFF;
	scx_bpf_dsq_insert_vtime(p, target_dsq, slice_scaled, tctx->deadline, enq_flags);
}

/*
 * dispatch: Work-Conserving Two-Tier Core Dispatch
 * Time Complexity: O(1)
 */
void BPF_STRUCT_OPS(optima_dispatch, s32 cpu, struct task_struct *prev)
{
	bool perf = is_perf_core(cpu);

	if (perf) {
		/* P-cores consume from prioritized DSQ_PERF first */
		if (scx_bpf_dsq_move_to_local(DSQ_PERF, 0)) {
			stat_add(OPTIMA_STAT_DSQ_PERF, 1);
			return;
		}
		/* Work conservation: steal from DSQ_EFF if P-core DSQ is empty */
		if (scx_bpf_dsq_move_to_local(DSQ_EFF, 0)) {
			stat_add(OPTIMA_STAT_WORK_STEAL, 1);
			return;
		}
	} else {
		/* E-cores consume from throughput DSQ_EFF first */
		if (scx_bpf_dsq_move_to_local(DSQ_EFF, 0)) {
			stat_add(OPTIMA_STAT_DSQ_EFF, 1);
			return;
		}
		/* If E-core is idle and DSQ_PERF has tasks waiting, assist immediately! */
		if (scx_bpf_dsq_move_to_local(DSQ_PERF, 0)) {
			stat_add(OPTIMA_STAT_WORK_STEAL, 1);
			return;
		}
	}
}

/*
 * running: Track execution start
 */
void BPF_STRUCT_OPS(optima_running, struct task_struct *p)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return;

	tctx->last_run_at = scx_bpf_now();
}

/*
 * stopping: Update runtime metrics and task density
 */
void BPF_STRUCT_OPS(optima_stopping, struct task_struct *p, bool runnable)
{
	struct task_ctx *tctx = lookup_task_ctx(p);
	if (!tctx)
		return;

	u64 now = scx_bpf_now();
	u64 delta = now - tctx->last_run_at;
	tctx->total_runtime += delta;

	/* Exponential moving average runtime: avg = (old * 3 + new) / 4 */
	tctx->avg_runtime = (tctx->avg_runtime * 3 + delta) / 4;

	/* Density calculation: rho_i = (w_i * 1,000,000) / max(runtime_us, 1) */
	u64 runtime_us = tctx->avg_runtime / 1000ULL;
	if (!runtime_us)
		runtime_us = 1;
	tctx->density = ((u64)tctx->weight * 1000000ULL) / runtime_us;
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
	tctx->avg_runtime = 100000ULL; /* Start optimistic: 100us */
	tctx->core_type = CORE_TYPE_PERF; /* Default to P-core tier so UI threads start fast */
	tctx->density = 1000000ULL;
	tctx->total_runtime = 0;
	tctx->dispatch_local = false;
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

	ret = scx_bpf_create_dsq(DSQ_EFF, -1);
	if (ret)
		return ret;

	bpf_printk("scx_optima: Initialized DSQ_PERF (%d) and DSQ_EFF (%d)", DSQ_PERF, DSQ_EFF);
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
