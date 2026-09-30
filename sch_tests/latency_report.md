# Latency & Complex Workload Benchmark Report
Generated on: Wed Sep 30 10:16:38 AM IST 2026

## 1. IPC & Messaging (Hackbench)
*Lower is better (seconds)*

| Scheduler | Run 1 (s) | Run 2 (Recorded) (s) |
|-----------|-----------|----------------------|
| scx_rlfifo | 0.666 | 0.669 |
| scx_rusty | 1.097 | 1.114 |
| scx_rustland | 0.633 | 0.632 |
| scx_rdtai | 1.123 | 1.146 |

## 2. Key-Value Store Latency (Redis)
*Lower is better (milliseconds at percentiles)*

| Scheduler | GET P50 | GET P95 | GET P99 | SET P50 | SET P95 | SET P99 |
|-----------|---------|---------|---------|---------|---------|---------|
| scx_rlfifo | 0.103 | 0.215 | 0.279 | 0.151 | 0.223 | 0.263 |
| scx_rusty | 0.247 | 0.327 | 0.351 | 0.183 | 0.319 | 0.343 |
| scx_rustland | 0.223 | 0.327 | 0.343 | 0.127 | 0.295 | 0.335 |
| scx_rdtai | 0.183 | 0.303 | 0.343 | 0.167 | 0.223 | 0.255 |

## 3. HFT Real-Time Jitter (Cyclictest)
*Wakeup latency under stress (microseconds). Lower is better.*

| Scheduler | Avg | P50 | P90 | P99 | Max |
|-----------|-----|-----|-----|-----|-----|
| scx_rlfifo | 7.9 | 11 | 11 | 12 | 328 |
| scx_rusty | 8.4 | 11 | 11 | 12 | 1622 |
| scx_rustland | 7.4 | 11 | 11 | 12 | 443 |
| scx_rdtai | 7.9 | 11 | 11 | 12 | 402 |

## 4. Local Network Throughput (iperf3)
*Higher is better (Gbps)*

| Scheduler | Throughput (Gbps) |
|-----------|-------------------| 
| scx_rlfifo | 85.06735873117064 |
| scx_rusty | 103.6015322012505 |
| scx_rustland | 85.74684450025545 |
| scx_rdtai | 71.08020345007691 |
