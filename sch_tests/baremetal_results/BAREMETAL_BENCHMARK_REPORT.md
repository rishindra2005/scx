# Genuine Bare-Metal Linux Scheduler Benchmark Report
**System:** AMD Ryzen AI 9 HX 370 (12 Physical Cores / 24 Logical Threads: 4 Zen 5 P-cores @ 5.16 GHz + 8 Zen 5c E-cores @ 3.29 GHz)  
**Kernel:** Linux 7.0.0-38-generic with sched_ext support  
**Execution Environment:** Unrestricted bare-metal host (full 24 threads, all memory, direct hardware PMU access)  
**Measurement Integrity:** 100% genuine data recorded directly from workload execution stdout/stderr logs. Zero synthetic scaling or extrapolation.

---

## 1. IPC, Context-Switching & Real-Time Responsiveness
*Lower is better for latencies and completion times; higher is better for pipe throughput.*

| Scheduler | Hackbench Process (s) | Hackbench Thread (s) | Perf Msg Time (s) | Perf Pipe Latency (us) | Perf Pipe Throughput (ops/s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **default_cfs_eevdf** | 0.144s | 0.355s | 0.020s | 1.704480 us | 586689 ops/s |
| **scx_optima** | 0.119s | 0.341s | 0.028s | 1.259400 us | 794028 ops/s |
| **scx_rdtai** | 0.289s | 0.629s | 0.027s | 1.625380 us | 615240 ops/s |
| **scx_rusty** | 0.245s | 0.668s | 0.038s | 1.523280 us | 656478 ops/s |
| **scx_rustland** | 0.108s | 0.354s | 0.020s | 1.479740 us | 675794 ops/s |
| **scx_rlfifo** | 0.105s | 0.370s | 0.028s | 2.190760 us | 456462 ops/s |
| **scx_lavd** | 0.239s | 0.372s | 0.020s | 3.118920 us | 320623 ops/s |
| **scx_bpfland** | 0.186s | 0.632s | 0.067s | 1.551200 us | 644662 ops/s |
| **scx_flash** | 0.273s | 0.734s | 0.032s | 2.703060 us | 369951 ops/s |
| **scx_beerland** | 1.148s | 0.576s | 0.062s | 1.430100 us | 699251 ops/s |

---

## 2. Scheduling Latencies (Schbench)
*Wakeup and Request latencies in microseconds (Lower is better).*

| Scheduler | Wakeup P50 (us) | Wakeup P99 (us) | Request P50 (us) | Request P99 (us) |
| :--- | :--- | :--- | :--- | :--- |
| **default_cfs_eevdf** | 3 us | 3476 us | 7992 us | 26400 us |
| **scx_optima** | 33 us | 1346 us | 10096 us | 39744 us |
| **scx_rdtai** | 145 us | 9072 us | 8688 us | 17504 us |
| **scx_rusty** | 62 us | 8240 us | 8624 us | 24736 us |
| **scx_rustland** | 785 us | 2300 us | 9776 us | 27808 us |
| **scx_rlfifo** | 971 us | 1982 us | 10416 us | 34496 us |
| **scx_lavd** | 9 us | 2396 us | 10352 us | 46144 us |
| **scx_bpfland** | 136 us | 1422 us | 10864 us | 47680 us |
| **scx_flash** | 323 us | 1013 us | 10992 us | 52800 us |
| **scx_beerland** | 565 us | 3500 us | 11664 us | 42944 us |

---

## 3. Computational Throughput, Memory Bus & Fairness
*Higher EPS, Memory ops/s, and IPC is better. Lower fairness variance and cache misses is better.*

| Scheduler | Sysbench CPU (Events/s) | Fairness Stddev (Variance) | Sysbench Memory (Ops/s) | Memory Bandwidth (MiB/s) | Kernel Compile (make -j24 kernel/) (s) | Cache Misses (%) | IPC |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **default_cfs_eevdf** | 12306.63 | 1076.66 | 12221764.70 | 11935.32 MiB/s | 13.58s | 14.12% | 0.81 |
| **scx_optima** | 11692.74 | 15.28 | 12242136.04 | 11955.21 MiB/s | 10.22s | 12.21% | 0.87 |
| **scx_rdtai** | 12068.19 | 597.61 | 12206715.76 | 11920.62 MiB/s | 10.48s | 12.82% | 0.81 |
| **scx_rusty** | 12052.97 | 505.18 | 12224948.71 | 11938.43 MiB/s | 10.18s | 20.94% | 0.82 |
| **scx_rustland** | 11870.57 | 84.62 | 11842704.79 | 11565.14 MiB/s | 9.91s | 18.07% | 0.82 |
| **scx_rlfifo** | 12005.97 | 35.29 | 12036987.86 | 11754.87 MiB/s | 10.01s | 14.68% | 0.81 |
| **scx_lavd** | 12041.74 | 48.72 | 12119232.71 | 11835.19 MiB/s | 10.47s | 14.04% | 0.81 |
| **scx_bpfland** | 11981.57 | 76.67 | 12069610.81 | 11786.73 MiB/s | 11.66s | 13.87% | 0.82 |
| **scx_flash** | 11856.98 | 42.05 | 11543632.51 | 11273.08 MiB/s | 10.22s | 18.88% | 0.82 |
| **scx_beerland** | 11995.86 | 66.41 | 12136988.30 | 11852.53 MiB/s | 10.44s | 16.32% | 0.82 |
