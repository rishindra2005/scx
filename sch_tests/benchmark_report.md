# Scheduler Benchmark Report (Deep Analysis)
Generated on: Wed Sep 30 03:12:07 PM IST 2026

## 1. Throughput & Execution Efficiency
*Higher Events/s and IPC is better. Lower Cache Misses and Compile Time is better.*

| Scheduler | Sysbench (Events/s) | IPC (instr/cycle) | Cache Misses (%) | Kernel Compile (s) | Hackbench (s) |
|-----------|----------------------|-------------------|------------------|--------------------|---------------|
| scx_rlfifo | 6333.21 | 0.83 | 17.81 | 157.50 | 1.000 |
| scx_rusty | 6317.62 | 0.84 | 22.88 | 160.03 | 1.710 |
| scx_rustland | 6286.68 | 0.83 | 19.41 | N/A | 1.005 |
| scx_rdtai | 6339.78 | 0.83 | 18.07 | 163.07 | 1.641 |
| scx_optima | 6394.51 | 0.80 | 11.94 | 161.76 | 0.956 |

## 2. Wakeup Latencies (Schbench)
*Time from thread wake to execution. Lower is better (microseconds).*

| Scheduler | 50.0th (us) | 90.0th (us) | 99.0th (us) | 99.9th (us) |
|-----------|-------------|-------------|-------------|-------------|
| scx_rlfifo | 987 | 1670 | 2068 | 3020 |
| scx_rusty | 691 | 2002 | 6184 | 16272 |
| scx_rustland | 1282 | 2364 | 3756 | 4824 |
| scx_rdtai | 245 | 5080 | 12688 | 23840 |
| scx_optima | 59 | 1366 | 4760 | 7976 |

## 3. Request Latencies (Schbench)
*Time from request start to completion. Lower is better (microseconds).*

| Scheduler | 50.0th (us) | 90.0th (us) | 99.0th (us) | 99.9th (us) |
|-----------|-------------|-------------|-------------|-------------|
| scx_rlfifo | 17184 | 35776 | 62528 | 89472 |
| scx_rusty | 15664 | 36160 | 78720 | 135424 |
| scx_rustland | 15024 | 26848 | 49856 | 72064 |
| scx_rdtai | 13360 | 18912 | 27872 | 39488 |
| scx_optima | 15216 | 23392 | 32672 | 58688 |
