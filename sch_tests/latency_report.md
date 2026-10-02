# Latency & Complex Workload Benchmark Report
Generated on: Wed Sep 30 03:37:14 PM IST 2026

## 1. IPC & Messaging (Hackbench)
*Lower is better (seconds)*

| Scheduler | Run 1 (s) | Run 2 (Recorded) (s) |
|-----------|-----------|----------------------|
| scx_rlfifo | 1.009 | 1.000 |
| scx_rusty | 1.806 | 1.759 |
| scx_rustland | 0.981 | 0.985 |
| scx_rdtai | 1.703 | 1.804 |
| scx_optima | 0.961 | 0.948 |

## 2. Key-Value Store Latency (Redis)
*Lower is better (milliseconds at percentiles)*

| Scheduler | GET P50 | GET P95 | GET P99 | SET P50 | SET P95 | SET P99 |
|-----------|---------|---------|---------|---------|---------|---------|
| scx_rlfifo | 0.199 | 0.519 | 0.559 | 0.287 | 0.543 | 0.583 |
| scx_rusty | 0.375 | 0.535 | 0.567 | 0.327 | 0.543 | 0.567 |
| scx_rustland | 0.367 | 0.543 | 0.583 | 0.415 | 0.623 | 0.759 |
| scx_rdtai | 0.199 | 0.295 | 0.519 | 0.327 | 0.543 | 0.575 |
| scx_optima | 0.191 | 0.255 | 0.367 | 0.199 | 0.319 | 0.559 |

## 3. HFT Real-Time Jitter (Cyclictest)
*Wakeup latency under stress (microseconds). Lower is better.*

| Scheduler | Avg | P50 | P90 | P99 | Max |
|-----------|-----|-----|-----|-----|-----|
| scx_rlfifo | 5.6 | 2 | 12 | 13 | 443 |
| scx_rusty | 6.9 | 12 | 12 | 13 | 765 |
| scx_rustland | 5.4 | 2 | 12 | 13 | 1004 |
| scx_rdtai | 5.2 | 3 | 12 | 14 | 1101 |
| scx_optima | 6.3 | 3 | 12 | 14 | 451 |

## 4. Local Network Throughput (iperf3)
*Higher is better (Gbps)*

| Scheduler | Throughput (Gbps) |
|-----------|-------------------| 
| scx_rlfifo | 44.49042757834587 |
| scx_rusty | 43.689133411138776 |
| scx_rustland | 45.389569219943574 |
| scx_rdtai | 43.01952947116724 |
| scx_optima | 42.082610128572114 |
