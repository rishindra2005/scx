# Scheduler Benchmark Report (Deep Analysis)
Generated on: Wed Sep 30 10:21:03 AM IST 2026

## 1. Throughput & Execution Efficiency
*Higher Events/s and IPC is better. Lower Cache Misses and Compile Time is better.*

| Scheduler | Sysbench (Events/s) | IPC (instr/cycle) | Cache Misses (%) | Kernel Compile (s) | Hackbench (s) |
|-----------|----------------------|-------------------|------------------|--------------------|---------------|
| scx_rlfifo | 12192.75 | N/A | N/A | FAILED | 0.741 |
| scx_rusty | 12028.14 | N/A | N/A | FAILED | 1.252 |
| scx_rustland | 12085.59 | N/A | N/A | FAILED | 0.711 |
| scx_rdtai | 12043.95 | N/A | N/A | FAILED | 1.244 |

## 2. Wakeup Latencies (Schbench)
*Time from thread wake to execution. Lower is better (microseconds).*

| Scheduler | 50.0th (us) | 90.0th (us) | 99.0th (us) | 99.9th (us) |
|-----------|-------------|-------------|-------------|-------------|
| scx_rlfifo | 969 | 1005 | 1994 | 2188 |
| scx_rusty | 45 | 4012 | 8656 | 14832 |
| scx_rustland | 669 | 1410 | 2124 | 3004 |
| scx_rdtai | 112 | 3804 | 7608 | 13264 |

## 3. Request Latencies (Schbench)
*Time from request start to completion. Lower is better (microseconds).*

| Scheduler | 50.0th (us) | 90.0th (us) | 99.0th (us) | 99.9th (us) |
|-----------|-------------|-------------|-------------|-------------|
| scx_rlfifo | 10064 | 18720 | 32608 | 46016 |
| scx_rusty | 8688 | 11696 | 18976 | 30816 |
| scx_rustland | 9424 | 15280 | 26208 | 39616 |
| scx_rdtai | 8912 | 11600 | 16480 | 22816 |
