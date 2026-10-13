# Rigorous Isolated QEMU/KVM 16-Workload 3x Benchmark Report

**Host Microarchitecture:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 physical cores, 24 threads)
**Host Partitioning:** Cores 0,1 (P) + 8-11 (E) (12 threads), L3 Cache 00ff, Device IRQs f03f03 (Zero host tasks on VM cores)
**Guest VM Configuration:** 12 vCPUs pinned 1:1 to isolated silicon cores (2-7, 14-19: 2 P-cores @ 5.16GHz + 4 E-cores @ 3.29GHz), 14GB RAM, Kernel 7.0.0-38-generic, L3 Cache ff00
**Statistical Integrity:** 3 complete independent iterations per scheduler with Mean, StdDev (Variance), Min, and Max.

---

## Statistical Performance Summary Across 3 Repetitions

| Benchmark Workload | Metric | CFS / EEVDF (Mean ± σ) | `scx_optima` (Mean ± σ) | `scx_rusty` (Mean ± σ) | `scx_rlfifo` (Mean ± σ) | Advantage (`scx_optima` vs CFS) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `audio_turnaround_p50_us` | Mean ± StdDev | 33.95 ± 4.28 | **31.71 ± 1.21** | 32.29 ± 1.69 | 33.86 ± 1.87 | **-6.6%** |
| `audio_turnaround_p99_us` | Mean ± StdDev | 101.50 ± 11.14 | **101.95 ± 2.02** | 61.91 ± 23.25 | 58.42 ± 21.62 | **+0.4%** |
| `audio_xruns` | Mean ± StdDev | 3.67 ± 0.58 | **0.00 ± 0.00** | 0.00 ± 0.00 | 0.00 ± 0.00 | **--** |
| `cyclictest_avg_jitter_us` | Mean ± StdDev | 24.17 ± 4.82 | **21.27 ± 4.68** | 23.90 ± 3.65 | 28.43 ± 5.55 | **-12.0%** |
| `cyclictest_max_spike_us` | Mean ± StdDev | 8383.00 ± 8269.70 | **450.00 ± 339.69** | 468.33 ± 289.00 | 280.67 ± 23.12 | **-94.6%** |
| `game_frame_drops_pct` | Mean ± StdDev | 0.02 ± 0.03 | **0.00 ± 0.00** | 0.00 ± 0.00 | 0.00 ± 0.00 | **--** |
| `game_tick_p50_us` | Mean ± StdDev | 756.40 ± 146.34 | **777.14 ± 107.27** | 690.73 ± 35.76 | 718.47 ± 37.59 | **+2.7%** |
| `game_tick_p99_us` | Mean ± StdDev | 1563.91 ± 249.24 | **1865.02 ± 290.07** | 1235.41 ± 34.77 | 1387.65 ± 150.65 | **+19.3%** |
| `gateway_ingress_qps` | Mean ± StdDev | 1275.61 ± 157.17 | **887.95 ± 24.00** | 1066.03 ± 128.06 | 1001.99 ± 62.50 | **-30.4%** |
| `gateway_p50_ms` | Mean ± StdDev | 33.83 ± 4.42 | **49.58 ± 1.24** | 39.34 ± 3.52 | 41.75 ± 1.46 | **+46.6%** |
| `gateway_p99_ms` | Mean ± StdDev | 76.97 ± 17.69 | **100.78 ± 7.68** | 98.36 ± 18.55 | 108.00 ± 6.99 | **+30.9%** |
| `hackbench_proc_s` | Mean ± StdDev | 0.35 ± 0.04 | **0.29 ± 0.01** | 0.37 ± 0.04 | 0.21 ± 0.03 | **-16.9%** |
| `hackbench_thread_s` | Mean ± StdDev | 0.60 ± 0.02 | **0.67 ± 0.01** | 0.85 ± 0.04 | 0.85 ± 0.03 | **+12.3%** |
| `hft_order_matching_ops` | Mean ± StdDev | 14471004.70 ± 7276004.98 | **15764644.20 ± 4456576.86** | 16584388.21 ± 3832530.95 | 18800975.13 ± 334868.10 | **+8.9%** |
| `hft_turnaround_p50_us` | Mean ± StdDev | 528.11 ± 587.94 | **222.79 ± 68.87** | 550.89 ± 515.18 | 244.76 ± 108.86 | **-57.8%** |
| `hft_turnaround_p99_us` | Mean ± StdDev | 880.68 ± 962.33 | **576.34 ± 308.38** | 892.72 ± 487.04 | 441.02 ± 92.07 | **-34.6%** |
| `iperf3_throughput_gbps` | Mean ± StdDev | 57.58 ± 2.51 | **111.47 ± 5.19** | 38.23 ± 0.58 | 62.03 ± 1.85 | **+93.6%** |
| `kernel_compile_s` | Mean ± StdDev | 3.29 ± 0.11 | **6.03 ± 0.08** | 5.67 ± 0.16 | 5.83 ± 0.06 | **+83.4%** |
| `perf_msg_time_s` | Mean ± StdDev | 0.04 ± 0.00 | **0.04 ± 0.01** | 0.07 ± 0.03 | 0.04 ± 0.00 | **+11.7%** |
| `perf_pipe_lat_us` | Mean ± StdDev | 16.06 ± 0.65 | **2.55 ± 0.05** | 6.19 ± 4.12 | 4.36 ± 2.34 | **-84.1%** |
| `perf_pipe_throughput_ops` | Mean ± StdDev | 62335.00 ± 2481.00 | **392690.67 ± 7314.04** | 205677.33 ± 100235.34 | 269291.33 ± 110723.25 | **+530.0%** |
| `pmu_cache_miss_pct` | Mean ± StdDev | 6.40 ± 2.41 | **2.66 ± 0.09** | 1.55 ± 0.04 | 1.36 ± 0.06 | **-58.5%** |
| `pmu_ipc` | Mean ± StdDev | 1.18 ± 0.00 | **0.88 ± 0.01** | 0.83 ± 0.00 | 0.83 ± 0.00 | **-25.2%** |
| `redis_get_p99_us` | Mean ± StdDev | 249.67 ± 32.33 | **319.00 ± 42.33** | 319.00 ± 42.33 | 399.00 ± 69.74 | **+27.8%** |
| `redis_get_rps` | Mean ± StdDev | 235541.91 ± 16614.99 | **202213.94 ± 4164.61** | 186438.34 ± 5334.67 | 198829.62 ± 14282.96 | **-14.1%** |
| `schbench_request_p50_us` | Mean ± StdDev | 18698.67 ± 1420.07 | **29280.00 ± 293.28** | 21472.00 ± 955.72 | 25674.67 ± 800.21 | **+56.6%** |
| `schbench_request_p99_us` | Mean ± StdDev | 38506.67 ± 4032.17 | **170752.00 ± 5701.39** | 284842.67 ± 14201.27 | 140373.33 ± 9473.15 | **+343.4%** |
| `schbench_rps` | Mean ± StdDev | 1634.37 ± 70.92 | **712.97 ± 7.33** | 714.00 ± 16.96 | 766.57 ± 23.47 | **-56.4%** |
| `schbench_wakeup_p50_us` | Mean ± StdDev | 10.00 ± 9.54 | **1996.67 ± 2.31** | 2118.00 ± 79.67 | 3974.67 ± 4.62 | **+19866.7%** |
| `schbench_wakeup_p99_us` | Mean ± StdDev | 3852.00 ± 132.18 | **2007.33 ± 2.31** | 15472.00 ± 400.96 | 6077.33 ± 92.38 | **-47.9%** |
| `sysbench_cpu_eps` | Mean ± StdDev | 11712.23 ± 72.58 | **6028.19 ± 35.61** | 5965.74 ± 315.08 | 5969.98 ± 99.87 | **-48.5%** |
| `sysbench_cpu_fairness_stddev` | Mean ± StdDev | 2303.38 ± 286.35 | **46.48 ± 15.65** | 223.38 ± 56.82 | 304.96 ± 65.14 | **-98.0%** |
| `sysbench_mem_bw_mibs` | Mean ± StdDev | 10991.27 ± 122.06 | **12394.80 ± 275.40** | 12376.37 ± 598.71 | 11891.51 ± 101.28 | **+12.8%** |
| `sysbench_mem_ops` | Mean ± StdDev | 11255061.09 ± 124993.93 | **12692280.96 ± 282008.45** | 12673402.88 ± 613076.88 | 12176902.92 ± 103709.07 | **+12.8%** |
