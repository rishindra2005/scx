/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright (c) 2026 Risheendra MN <rishindra.hackbox@gmail.com>
 *
 * scx_optima: Algorithmic sched_ext scheduler with formal complexity guarantees
 * Bridges CS301 (Greedy WSPT, Dynamic Programming, Branch & Bound) + Linux eBPF.
 */
#ifndef __INTF_H
#define __INTF_H

#include <stdbool.h>

#define MAX(x, y) ((x) > (y) ? (x) : (y))
#define MIN(x, y) ((x) < (y) ? (x) : (y))
#define CLAMP(val, lo, hi) MIN(MAX(val, lo), hi)

#ifndef __KERNEL__
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef signed char s8;
typedef signed short s16;
typedef int s32;
typedef long long s64;
#endif

enum consts {
	MAX_CPUS		= 512,
	CACHELINE_SIZE		= 64,

	/* DSQ Definitions */
	DSQ_PERF		= 0,	/* High-density / deadline-critical on P-Cores only */
	DSQ_SHARED_P		= 1,	/* High-throughput shared interactive on CCX 0 (P-cores) */
	DSQ_SHARED_E		= 2,	/* High-throughput shared interactive on CCX 1 (E-cores) */
	DSQ_EFF			= 3,	/* Batch / background on E-Cores */

	/* Core Types for Heterogeneous Scheduling */
	CORE_TYPE_PERF		= 0,	/* Zen 5 Big Core (5.16 GHz, 16MB L3) */
	CORE_TYPE_SHARED	= 1,	/* Work-sharing between P-cores and E-cores */
	CORE_TYPE_EFF		= 2,	/* Zen 5c Dense Core (3.29 GHz, 8MB L3) */

	/* Heterogeneous Core Speed Scales (100 = 1.00x) */
	P_CORE_SPEED_SCALE	= 157,	/* 5.16 GHz / 3.29 GHz = ~1.57x */
	E_CORE_SPEED_SCALE	= 100,

	/* Default Algorithmic Parameters */
	DEFAULT_SLICE_NS	= 3000000ULL,	/* 3ms base time slice (low latency, high interactivity) */
	MIN_SLICE_NS		= 500000ULL,	/* 500us minimum time slice (sub-millisecond turnaround) */
	MAX_SLICE_NS		= 8000000ULL,	/* 8ms maximum time slice for batch compute */

	/* Real-Time Priority Inversion Threshold */
	RT_WEIGHT_THRESHOLD	= 500,		/* High-priority task threshold */
};

/*
 * Task context tracked across scheduler lifecycle
 */
struct task_ctx {
	u32 weight;		/* Task weight w_i (derived from nice) */
	u32 core_type;		/* Assigned core tier (CORE_TYPE_PERF, CORE_TYPE_SHARED, CORE_TYPE_EFF) */
	u64 avg_runtime;	/* Exponential moving average runtime p_i (ns) */
	u64 deadline;		/* Virtual deadline d_i (vtime-based EDF) */
	u64 last_run_at;	/* Timestamp when task started last slice */
	u64 density;		/* Task density rho_i = (w_i * 1000) / max(p_i_us, 1) */
	u64 total_runtime;	/* Total cumulative execution time */
	bool dispatch_local;	/* Direct dispatch flag */
	bool is_batch;		/* True if last slice ended by preemption/quantum expiry */
	bool preempt;		/* Immediate preemption required on local dispatch */
};

/*
 * Per-CPU running task state for Branch-and-Bound preemption inspection
 */
struct cpu_run_state {
	u32 pid;
	u32 weight;
	u64 slice_ns;
	u64 start_time_ns;
};

/*
 * Dynamic Programming Tuning parameters pushed from userspace
 */
struct dp_tuning {
	u64 dp_density_threshold; /* Density threshold separating P-core vs E-core */
	u32 dp_pcore_capacity;    /* Maximum queued tasks allowed on P-core DSQ */
	u64 slice_ns;             /* Base scheduling quantum */
};

/*
 * Scheduler Statistics
 */
enum stat_idx {
	OPTIMA_STAT_GREEDY_WSPT,	/* Tasks ordered and dispatched via Greedy WSPT */
	OPTIMA_STAT_DP_PCORE,		/* Tasks partitioned to P-core domain via DP */
	OPTIMA_STAT_DP_SHARED,		/* Tasks partitioned to shared domain via DP */
	OPTIMA_STAT_DP_ECORE,		/* Tasks partitioned to E-core domain via DP */
	OPTIMA_STAT_BB_PRUNED,		/* B&B candidate branches safely pruned */
	OPTIMA_STAT_BB_PREEMPT,		/* Priority inversions resolved via B&B preemption */
	OPTIMA_STAT_DIRECT_DISPATCH,	/* Direct dispatches to idle CPU */
	OPTIMA_STAT_DSQ_PERF,		/* Dispatches from P-core DSQ */
	OPTIMA_STAT_DSQ_EFF,		/* Dispatches from E-core DSQ */
	OPTIMA_STAT_WORK_STEAL,		/* Work-conserving inter-core thefts */
	OPTIMA_STAT_FALLBACK,		/* Fallback dispatches */

	OPTIMA_NR_STATS,
};

#endif /* __INTF_H */
