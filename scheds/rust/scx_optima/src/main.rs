// SPDX-License-Identifier: GPL-2.0
//
// Copyright (c) 2026 Risheendra MN <rishindra.hackbox@gmail.com>
//
// scx_optima: Algorithmic sched_ext scheduler with formal complexity guarantees
//
// Theoretical Foundations (CS301 / DAA):
// - Module 6 (Greedy): Weighted Job Sequencing with Deadlines (Smith's WSPT + EDF)
// - Module 4-5 (DP): Multi-Stage Heterogeneous Pipeline Allocation (0/1 Knapsack Partition)
// - Module 8 (Branch & Bound): Real-Time Priority Inversion Resolution with Pruning

mod bpf_skel;
pub use bpf_skel::*;
pub mod bpf_intf;
pub use bpf_intf::*;
mod stats;
pub use stats::*;

use std::fs;
use std::mem::MaybeUninit;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::Parser;
use libbpf_rs::MapCore;
use log::info;
use scx_utils::{
    build_id, compat, scx_ops_attach, scx_ops_load, scx_ops_open, try_set_rlimit_infinity,
    uei_exited, uei_report, NR_CPU_IDS,
};

pub const SCHEDULER_NAME: &str = "scx_optima";

#[derive(Debug, Parser)]
#[clap(
    name = "scx_optima",
    about = "Algorithmic sched_ext scheduler with formal complexity guarantees (Greedy WSPT, DP, Branch & Bound)"
)]
struct Opts {
    /// Base time slice in microseconds.
    #[clap(short = 's', long, default_value = "5000")]
    slice_us: u64,

    /// Task density threshold for Dynamic Programming P-core partitioning.
    #[clap(long, default_value = "500000")]
    dp_threshold: u64,

    /// P-core capacity limit (maximum queue depth) before DP shifts load to E-cores.
    #[clap(long, default_value = "8")]
    dp_capacity: u32,

    /// Display live algorithmic statistics every interval (seconds).
    #[clap(long, default_value = "1")]
    stats: u64,

    /// Verbose BPF debug output.
    #[clap(short = 'v', long, action = clap::ArgAction::Count)]
    verbose: u8,
}

/// Detects heterogeneous core topology by querying CPU max frequencies from sysfs.
/// On AMD Ryzen AI 9 (Strix Point), P-cores (Zen 5) run at ~5.16 GHz and E-cores (Zen 5c) run at ~3.29 GHz.
fn detect_heterogeneous_topology() -> (Vec<usize>, Vec<usize>, [u64; 8], [u64; 8]) {
    let nr_cpus = *NR_CPU_IDS;
    let mut p_cores = Vec::new();
    let mut e_cores = Vec::new();
    let mut freqs = Vec::new();

    let mut max_freq = 0u64;
    for cpu in 0..nr_cpus {
        let freq_path = format!("/sys/devices/system/cpu/cpu{}/cpufreq/cpuinfo_max_freq", cpu);
        let freq = fs::read_to_string(&freq_path)
            .ok()
            .and_then(|s| s.trim().parse::<u64>().ok())
            .unwrap_or(0);
        freqs.push(freq);
        if freq > max_freq {
            max_freq = freq;
        }
    }

    let mut p_mask = [0u64; 8];
    let mut e_mask = [0u64; 8];

    // If heterogeneous frequency difference detected (e.g. >15% delta)
    let freq_threshold = (max_freq as f64 * 0.85) as u64;
    for (cpu, freq) in freqs.iter().enumerate() {
        if *freq >= freq_threshold && max_freq > 0 {
            p_cores.push(cpu);
            p_mask[cpu / 64] |= 1u64 << (cpu % 64);
        } else {
            e_cores.push(cpu);
            e_mask[cpu / 64] |= 1u64 << (cpu % 64);
        }
    }

    // Fallback if all cores are homogeneous: partition first half to P, second to E
    if e_cores.is_empty() {
        p_cores.clear();
        e_cores.clear();
        p_mask = [0u64; 8];
        e_mask = [0u64; 8];
        for cpu in 0..nr_cpus {
            if cpu < nr_cpus / 2 {
                p_cores.push(cpu);
                p_mask[cpu / 64] |= 1u64 << (cpu % 64);
            } else {
                e_cores.push(cpu);
                e_mask[cpu / 64] |= 1u64 << (cpu % 64);
            }
        }
    }

    (p_cores, e_cores, p_mask, e_mask)
}

fn read_aggregated_stats(skel: &BpfSkel) -> Result<OptimaStats> {
    let stats_map = &skel.maps.stats;
    let mut res = OptimaStats::default();

    let num_cpus = libbpf_rs::num_possible_cpus()?;

    for idx in 0..bpf_intf::stat_idx_OPTIMA_NR_STATS {
        let key = idx.to_ne_bytes();
        if let Ok(Some(percpu_vals)) = stats_map.lookup_percpu(&key, libbpf_rs::MapFlags::ANY) {
            let mut sum = 0u64;
            for cpu_val in percpu_vals.iter().take(num_cpus) {
                if cpu_val.len() >= 8 {
                    let mut b = [0u8; 8];
                    b.copy_from_slice(&cpu_val[0..8]);
                    sum += u64::from_ne_bytes(b);
                }
            }
            match idx {
                bpf_intf::stat_idx_OPTIMA_STAT_GREEDY_WSPT => res.greedy_wspt = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_DP_PCORE => res.dp_pcore = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_DP_SHARED => res.dp_shared = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_DP_ECORE => res.dp_ecore = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_BB_PRUNED => res.bb_pruned = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_BB_PREEMPT => res.bb_preempt = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_DIRECT_DISPATCH => res.direct_dispatch = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_DSQ_PERF => res.dsq_perf = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_DSQ_EFF => res.dsq_eff = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_WORK_STEAL => res.work_steal = sum,
                bpf_intf::stat_idx_OPTIMA_STAT_FALLBACK => res.fallback = sum,
                _ => {}
            }
        }
    }

    Ok(res)
}

fn main() -> Result<()> {
    let opts = Opts::parse();

    let mut lcfg = simplelog::ConfigBuilder::new();
    lcfg.set_time_offset_to_local()
        .expect("Failed to set local time offset")
        .set_time_level(simplelog::LevelFilter::Error)
        .set_location_level(simplelog::LevelFilter::Off)
        .set_target_level(simplelog::LevelFilter::Off)
        .set_thread_level(simplelog::LevelFilter::Off);

    simplelog::TermLogger::init(
        simplelog::LevelFilter::Info,
        lcfg.build(),
        simplelog::TerminalMode::Stderr,
        simplelog::ColorChoice::Auto,
    )?;

    info!(
        "{} v{} (Formal Algorithmic Kernel Scheduler)",
        SCHEDULER_NAME,
        build_id::full_version(env!("CARGO_PKG_VERSION"))
    );

    try_set_rlimit_infinity();

    // Heterogeneous Core Discovery
    let (p_cores, e_cores, p_mask, e_mask) = detect_heterogeneous_topology();
    info!(
        "Heterogeneous Hardware Topology Detected on {} CPUs:",
        *NR_CPU_IDS
    );
    info!("  -> P-Cores (Zen 5 Performance, 5.16 GHz): {:?}", p_cores);
    info!("  -> E-Cores (Zen 5c Efficiency, 3.29 GHz): {:?}", e_cores);

    // BPF Skeleton Initialization
    let mut skel_builder = BpfSkelBuilder::default();
    skel_builder.obj_builder.debug(opts.verbose > 0);

    let mut open_object = MaybeUninit::uninit();
    let mut skel = scx_ops_open!(skel_builder, &mut open_object, optima_ops, Default::default())?;

    // Set Read-Only Parameters in .rodata
    let rodata = skel
        .maps
        .rodata_data
        .as_mut()
        .context("Failed to get rodata")?;
    rodata.p_core_mask = p_mask;
    rodata.e_core_mask = e_mask;
    rodata.default_slice_ns = opts.slice_us * 1000;
    rodata.dp_density_threshold = opts.dp_threshold;
    rodata.dp_pcore_capacity = opts.dp_capacity;
    rodata.nr_cpu_ids = *NR_CPU_IDS as u32;

    // Sched-ext Flags
    skel.struct_ops.optima_ops_mut().flags = *compat::SCX_OPS_ENQ_EXITING
        | *compat::SCX_OPS_ENQ_LAST
        | *compat::SCX_OPS_ALLOW_QUEUED_WAKEUP;

    // Load BPF Verification
    let mut skel = scx_ops_load!(skel, optima_ops, uei)?;

    // Attach to Linux Kernel
    let struct_ops = Some(scx_ops_attach!(skel, optima_ops)?);
    info!(
        "{} attached successfully to sched-ext!",
        SCHEDULER_NAME
    );
    info!(
        "Algorithmic Policies Active: Greedy WSPT (EDF), DP Knapsack Partition, B&B Pruning"
    );

    let shutdown = Arc::new(AtomicBool::new(false));
    let shutdown_clone = shutdown.clone();

    ctrlc::set_handler(move || {
        info!("Termination signal received. Unregistering scheduler...");
        shutdown_clone.store(true, Ordering::Relaxed);
    })
    .context("Error setting Ctrl-C handler")?;

    let mut prev_stats = OptimaStats::default();

    // Main Algorithmic Monitoring Loop
    while !shutdown.load(Ordering::Relaxed) && !uei_exited!(&skel, uei) {
        thread::sleep(Duration::from_secs(opts.stats));

        if let Ok(curr_stats) = read_aggregated_stats(&skel) {
            let delta = curr_stats.delta(&prev_stats);
            let _ = delta.format(&mut std::io::stdout());
            prev_stats = curr_stats;
        }
    }

    drop(struct_ops);
    let _ = uei_report!(&skel, uei);
    Ok(())
}
