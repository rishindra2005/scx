# Latency & Complex Workload Benchmark Report
Generated on: Wed Sep 30 12:01:46 PM IST 2026

## 1. IPC & Messaging (Hackbench)
*Lower is better (seconds)*

| Scheduler | Run 1 (s) | Run 2 (Recorded) (s) |
|-----------|-----------|----------------------|
| scx_rlfifo | 0.690 | 0.688 |
| scx_rusty | 1.117 | 1.003 |
| scx_rustland | 0.667 | 0.698 |
| scx_rdtai | 1.094 | 1.235 |

## 2. Key-Value Store Latency (Redis)
*Lower is better (milliseconds at percentiles)*

| Scheduler | GET P50 | GET P95 | GET P99 | SET P50 | SET P95 | SET P99 |
|-----------|---------|---------|---------|---------|---------|---------|
| scx_rlfifo | 0.127 | 0.223 | 0.239 | 0.135 | 0.223 | 0.247 |
| scx_rusty | 0.159 | 0.223 | 0.247 | 0.159 | 0.215 | 0.239 |
| scx_rustland | 0.119 | 0.247 | 0.335 | 0.159 | 0.215 | 0.247 |
| scx_rdtai | 0.079 | 0.151 | 0.223 | 0.239 | 0.335 | 0.367 |

## 3. HFT Real-Time Jitter (Cyclictest)
*Wakeup latency under stress (microseconds). Lower is better.*

| Scheduler | Avg | P50 | P90 | P99 | Max |
|-----------|-----|-----|-----|-----|-----|
| scx_rlfifo | 8.0 | 11 | 11 | 12 | 291 |
| scx_rusty | 8.6 | 11 | 11 | 12 | 374 |
| scx_rustland | 7.8 | 11 | 11 | 12 | 269 |
| scx_rdtai | 8.4 | 11 | 11 | 12 | 214 |

## 4. Local Network Throughput (iperf3)
*Higher is better (Gbps)*

| Scheduler | Throughput (Gbps) |
|-----------|-------------------| 
| scx_rlfifo | 82.90647992249899 |
| scx_rusty | 70.8051923791252 |
| scx_rustland | 85.78656230378971 |
| scx_rdtai | 107.47175806203241 |
