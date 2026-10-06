# Scheduler Benchmark Report (Deep Analysis)
Generated on: Tue Oct  6 07:43:32 PM IST 2026

## 1. Throughput & Execution Efficiency
*Higher Events/s and IPC is better. Lower Cache Misses and Compile Time is better.*

| Scheduler | Sysbench (Events/s) | IPC (instr/cycle) | Cache Misses (%) | Kernel Compile (s) | Hackbench (s) |
|-----------|----------------------|-------------------|------------------|--------------------|---------------|
| scx_rlfifo | 6217.70 | 0.80 | 12.30 | FAILED | 1.371 |
| scx_optima | 6189.45 | 0.81 | 13.56 | FAILED | 1.428 |

## 2. Wakeup Latencies (Schbench)
*Time from thread wake to execution. Lower is better (microseconds).*

| Scheduler | 50.0th (us) | 90.0th (us) | 99.0th (us) | 99.9th (us) |
|-----------|-------------|-------------|-------------|-------------|
| scx_rlfifo | 2964 | 3996 | 5000 | 6008 |
| scx_optima | 1094 | 5528 | 30816 | 465408 |

## 3. Request Latencies (Schbench)
*Time from request start to completion. Lower is better (microseconds).*

| Scheduler | 50.0th (us) | 90.0th (us) | 99.0th (us) | 99.9th (us) |
|-----------|-------------|-------------|-------------|-------------|
| scx_rlfifo | 22816 | 64448 | 122752 | 182016 |
| scx_optima | 10000 | 26144 | 596992 | 4190208 |
