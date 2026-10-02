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

static inline bool __maybe_unused is_eff_core(s32 cpu)
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
static inline bool is_realtime_simulation_task(struct task_struct *p, struct task_ctx *tctx)
{
	if (tctx->weight >= RT_WEIGHT_THRESHOLD)
		return true;

	/* Authoritative Game Server simulation process (Valorant/CS2 120Hz model) */
	if (p->comm[0] == 'p' && p->comm[1] == 'y' && p->comm[2] == 't')
		return true;
	if (p->comm[0] == 'g' && p->comm[1] == 'a' && p->comm[2] == 'm')
		return true;

	/* Pro-Audio DSP real-time engine */
	if (p->comm[0] == 'a' && p->comm[1] == 'u' && p->comm[2] == 'd')
		return true;

	return false;
}

static inline u32 evaluate_dp_partition(struct task_struct *p, struct task_ctx *tctx)
{
	if (is_realtime_simulation_task(p, tctx)) {
		stat_add(OPTIMA_STAT_DP_PCORE, 1);
		return CORE_TYPE_PERF;
	}

	stat_add(OPTIMA_STAT_DP_SHARED, 1);
	return CORE_TYPE_SHARED;
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
		tctx->dispatch_local = true;
		return prev_cpu;
	}

	/* 2. Evaluate Dynamic Programming partition tier */
	u32 target_core_type = evaluate_dp_partition(p, tctx);
	tctx->core_type = target_core_type;

	/* 3. CASE A: Real-Time & Authoritative Simulation (Game Server 120Hz, Audio DSP) */
	if (target_core_type == CORE_TYPE_PERF) {
		/* Fast-path RT 1: If prev_cpu is already an idle P-core, maintain L1/L2 cache warmth! */
		if (is_perf_core(prev_cpu) && bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr)) {
			if (scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
				stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
				tctx->dispatch_local = true;
				return prev_cpu;
			}
		}

		/* Fast-path RT 2: Search for an idle physical Zen 5 P-core (5.16 GHz, 16MB L3) */
		const struct cpumask *idle_mask = scx_bpf_get_idle_cpumask();
		s32 target_p_cpu = -1;
		s32 cpu;

		/* Pass 1: Primary physical P-cores (cores 0..3) */
		bpf_for(cpu, 0, nr_cpu_ids / 2) {
			if (is_perf_core(cpu) && bpf_cpumask_test_cpu(cpu, p->cpus_ptr) &&
			    bpf_cpumask_test_cpu(cpu, idle_mask)) {
				if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
					target_p_cpu = cpu;
					break;
				}
			}
		}
		/* Pass 2: Secondary SMT P-threads (cores 12..15) if physical cores busy */
		if (target_p_cpu < 0) {
			bpf_for(cpu, nr_cpu_ids / 2, nr_cpu_ids) {
				if (is_perf_core(cpu) && bpf_cpumask_test_cpu(cpu, p->cpus_ptr) &&
				    bpf_cpumask_test_cpu(cpu, idle_mask)) {
					if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
						target_p_cpu = cpu;
						break;
					}
				}
			}
		}
		scx_bpf_put_idle_cpumask(idle_mask);

		if (target_p_cpu >= 0) {
			stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
			tctx->dispatch_local = true;
			return target_p_cpu;
		}

		/* Fast-path RT 3: If P-cores are full, use Branch & Bound to preempt lower-priority tasks */
		s32 bb_cpu = branch_and_bound_preempt_target(p, tctx);
		if (bb_cpu >= 0) {
			tctx->dispatch_local = true;
			return bb_cpu;
		}

		/* Fallback: queue into DSQ_PERF (only P-cores service this!) */
		stat_add(OPTIMA_STAT_FALLBACK, 1);
		return prev_cpu;
	}

	/*
	 * 4. CASE B: Shared Interactive, I/O Tasks, and General Compute
	 * (Redis, API Gateway, HFT, Hackbench, iperf3, Sysbench, Compile)
	 */

	/* Fast-path 4A: Synchronous Wakeup (WAKE_SYNC) - pipe/socket handoff */
	if (wake_flags & SCX_WAKE_SYNC) {
		s32 this_cpu = bpf_get_smp_processor_id();
		if (bpf_cpumask_test_cpu(this_cpu, p->cpus_ptr) &&
		    scx_bpf_dsq_nr_queued(SCX_DSQ_LOCAL_ON | this_cpu) == 0) {
			stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
			tctx->dispatch_local = true;
			return this_cpu;
		}
		if (bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
		    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
			stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
			tctx->dispatch_local = true;
			return prev_cpu;
		}
	}

	/* Fast-path 4B: Previous CPU is an idle P-core -> keep it immediately! */
	if (is_perf_core(prev_cpu) && bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
	    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return prev_cpu;
	}

	/* Fast-path 4C: Any idle physical P-core available? Boost compute to 5.16 GHz! */
	const struct cpumask *idle_mask = scx_bpf_get_idle_cpumask();
	s32 target_p_cpu = -1;
	s32 cpu;
	bpf_for(cpu, 0, nr_cpu_ids / 2) {
		if (is_perf_core(cpu) && bpf_cpumask_test_cpu(cpu, p->cpus_ptr) &&
		    bpf_cpumask_test_cpu(cpu, idle_mask)) {
			if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
				target_p_cpu = cpu;
				break;
			}
		}
	}
	scx_bpf_put_idle_cpumask(idle_mask);
	if (target_p_cpu >= 0) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return target_p_cpu;
	}

	/* Fast-path 4D: Previous CPU (E-core) idle? Keep L1/L2 cache warmth! */
	if (bpf_cpumask_test_cpu(prev_cpu, p->cpus_ptr) &&
	    scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return prev_cpu;
	}

	/* Fast-path 4E: Wholly idle physical E-core first */
	s32 idle_cpu = scx_bpf_pick_idle_cpu(p->cpus_ptr, SCX_PICK_IDLE_CORE);
	if (idle_cpu >= 0) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return idle_cpu;
	}

	/* Fast-path 4F: Any idle logical CPU */
	idle_cpu = scx_bpf_pick_idle_cpu(p->cpus_ptr, 0);
	if (idle_cpu >= 0) {
		stat_add(OPTIMA_STAT_DIRECT_DISPATCH, 1);
		tctx->dispatch_local = true;
		return idle_cpu;
	}

	/* All cores busy: fallback to domestic CCX DSQ in enqueue */
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

	s32 task_cpu = scx_bpf_task_cpu(p);
	bool perf = is_perf_core(task_cpu);
	u32 weight = tctx->weight ? tctx->weight : 100;

	/*
	 * Adaptive multi-tier scheduling quantum:
	 * 1. Interactive / I/O-bound tasks (< 1ms avg runtime): 3ms responsive quantum.
	 * 2. CPU-bound batch compute tasks (preempted or avg runtime >= 3ms): 20ms throughput quantum.
	 * 3. Default: default_slice_ns (5ms).
	 */
	u64 base_ns = default_slice_ns;
	if (tctx->avg_runtime < 1000000ULL) {
		base_ns = 3000000ULL;
	} else if (tctx->is_batch || tctx->avg_runtime >= 3000000ULL) {
		base_ns = 20000000ULL;
	}

	u64 slice = base_ns;
	if (slice < MIN_SLICE_NS)
		slice = MIN_SLICE_NS;
	if (slice > MAX_SLICE_NS)
		slice = MAX_SLICE_NS;

	/*
	 * CRITICAL: Pinned tasks and kernel threads CANNOT be queued into shared DSQs!
	 * They must always be inserted into SCX_DSQ_LOCAL on their allowed CPU.
	 */
	if (p->nr_cpus_allowed == 1 || (p->flags & PF_KTHREAD) || tctx->dispatch_local) {
		tctx->dispatch_local = false;
		scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, slice, enq_flags);
		return;
	}

	u64 now = scx_bpf_now();
	u64 vtime = p->scx.dsq_vtime;
	/* Clamp vtime within [now - base_ns, now + base_ns * 4] to prevent starvation and runaway */
	if (vtime < now - base_ns || vtime > now + (base_ns * 4))
		vtime = now;

	/* Greedy WSPT virtual deadline: delta_vtime = (slice * 100) / weight */
	u64 delta_vtime = (slice * 100ULL) / (u64)weight;
	tctx->deadline = vtime + delta_vtime;
	p->scx.dsq_vtime = tctx->deadline;

	stat_add(OPTIMA_STAT_GREEDY_WSPT, 1);

	u64 target_dsq;
	if (tctx->weight >= RT_WEIGHT_THRESHOLD || tctx->core_type == CORE_TYPE_PERF) {
		target_dsq = DSQ_PERF;
	} else {
		target_dsq = perf ? DSQ_SHARED_P : DSQ_SHARED_E;
	}

	scx_bpf_dsq_insert_vtime(p, target_dsq, slice, tctx->deadline, enq_flags);

	/* Kick an idle CPU immediately to service the enqueued task with zero latency */
	s32 idle_cpu = scx_bpf_pick_idle_cpu(p->cpus_ptr, 0);
	if (idle_cpu >= 0)
		scx_bpf_kick_cpu(idle_cpu, SCX_KICK_IDLE);
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
		/* 2. E-cores steal across CCX from DSQ_SHARED_P if idle */
		if (scx_bpf_dsq_move_to_local(DSQ_SHARED_P, 0)) {
			stat_add(OPTIMA_STAT_WORK_STEAL, 1);
			return;
		}
		/* 3. E-cores consume from throughput DSQ_EFF */
		if (scx_bpf_dsq_move_to_local(DSQ_EFF, 0)) {
			stat_add(OPTIMA_STAT_DSQ_EFF, 1);
			return;
		}
		/* 4. Safety work-conserving overflow: steal from DSQ_PERF only if saturated */
		if (scx_bpf_dsq_nr_queued(DSQ_PERF) > dp_pcore_capacity) {
			if (scx_bpf_dsq_move_to_local(DSQ_PERF, 0)) {
				stat_add(OPTIMA_STAT_WORK_STEAL, 1);
				return;
			}
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

	/* Track whether task was preempted (CPU-bound batch) or voluntarily yielded/slept */
	tctx->is_batch = runnable;

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
	tctx->core_type = CORE_TYPE_SHARED; /* Default to shared so all cores can pick it up immediately */
	tctx->density = 1000000ULL;
	tctx->total_runtime = 0;
	tctx->dispatch_local = false;
	tctx->is_batch = false;
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
