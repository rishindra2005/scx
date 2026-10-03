# Automated Stress-to-Failure & Breaking Point Profiler Report

**Execution Timestamp:** 2026-10-02 21:14:47
**Hardware Testbed:** AMD Ryzen AI 9 HX 370 (Strix Point: 12 Cores / 24 Threads)
- **Isolated Execution Domain:** 12 Logical CPUs (`2-7, 14-19`: 2 Zen 5 P-cores + 4 Zen 5c E-cores)
- **CPU Frequencies:** Uncapped (P-cores 5.16 GHz, E-cores 3.29 GHz)
- **Memory Isolation:** Strict 4.0 GB RAM constraint (`benchmark.slice`)
- **L3 Cache Partition:** AMD CAT `resctrl` dedicated upper 8 ways (`ff00`)

---

## 1. Executive Breaking Point & Overload Cliff Matrix

| Application Workload | Metric | Default CFS / EEVDF | `scx_optima` | `scx_rdtai` | `scx_rusty` | `scx_rustland` |
|:---|:---|:---|:---|:---|:---|:---|
| **App 1: API Gateway** | Max Stable Throughput | **479.1 req/s** | **544.1 req/s** | **411.3 req/s** | **519.3 req/s** | **500.7 req/s** | 
| | Breaking / Cliff Point | `Concurrency 100` | `Concurrency 100` | `Concurrency 100` | `Concurrency 100` | `Concurrency 100` | 
| | Failure Reason | `Latency Cliff: P99 3021.7ms > 2000ms` | `Latency Cliff: P99 4324.95ms > 2000ms` | `Latency Cliff: P99 4005.48ms > 2000ms` | `Latency Cliff: P99 3397.1ms > 2000ms` | `Latency Cliff: P99 3546.55ms > 2000ms` | 
| | Latency (P50 / P90 / P99) | 323.8 / 1457.1 / 3021.7 ms | 558.7 / 2193.9 / 4324.9 ms | 490.5 / 1803.9 / 4005.5 ms | 504.3 / 1956.9 / 3397.1 ms | 375.2 / 1562.3 / 3546.6 ms | 
| | Peak CPU % | 8.2% | 8.4% | 11.5% | 11.6% | 10.9% | 
| | Peak RAM (MB) | 266.3 MB | 268.2 MB | 273.0 MB | 274.8 MB | 284.1 MB | 
| **App 2: Redis Cache** | Max Stable Throughput | **0 ops/s** | **0 ops/s** | **2,379,679.1 ops/s** | **2,175,925.9 ops/s** | **1,160,037.8 ops/s** | 
| | Breaking / Cliff Point | `Clients 50` | `Clients 50` | `Clients 350` | `Clients 100` | `Clients 200` | 
| | Failure Reason | `P99 Latency Breach: 39.519ms > 5.0ms` | `P99 Latency Breach: 8.247ms > 5.0ms` | `P99 Latency Breach: 8.143ms > 5.0ms` | `P99 Latency Breach: 7.775ms > 5.0ms` | `P99 Latency Breach: 6.575ms > 5.0ms` | 
| | Latency (P50 / P90 / P99) | 23.98 / 26.21 / 39.52 ms | 0.40 / 0.64 / 8.25 ms | 3.79 / 4.71 / 8.14 ms | 0.74 / 1.12 / 7.78 ms | 1.91 / 2.84 / 6.58 ms | 
| | Peak CPU % | 8.3% | 29.0% | 29.3% | 29.4% | 25.5% | 
| | Peak RAM (MB) | 17.1 MB | 22.0 MB | 32.9 MB | 33.8 MB | 43.0 MB | 
| **App 3: Pro-Audio DSP** | Max Stable Throughput | **3,072 DSP filters @ 1.33ms** | **3,072 DSP filters @ 1.33ms** | **128 DSP filters @ 1.33ms** | **3,072 DSP filters @ 1.33ms** | **0 DSP filters @ 1.33ms** | 
| | Breaking / Cliff Point | `Survived Max (3072 filters)` | `Survived Max (3072 filters)` | `16ch / 24stg (384 filters)` | `Survived Max (3072 filters)` | `8ch / 8stg (64 filters)` | 
| | Failure Reason | `None (Zero Xruns)` | `None (Zero Xruns)` | `DEADLINE BREACH: 1 Xruns! (Max: 1401.2us > 1333.3us)` | `None (Zero Xruns)` | `DEADLINE BREACH: 2 Xruns! (Max: 2394.0us > 1333.3us)` | 
| | Latency (P50 / P90 / P99) | 380.4 / 387.6 / 395.6 us | 383.2 / 521.1 / 585.2 us | 177.7 / 213.7 / 530.1 us | 382.4 / 385.1 / 548.5 us | 87.3 / 149.9 / 459.8 us | 
| | Peak CPU % | 2.0% | 2.2% | 0.7% | 2.0% | 0.2% | 
| | Peak RAM (MB) | 11.6 MB | 16.8 MB | 17.5 MB | 28.4 MB | 32.1 MB | 
| **App 4: HFT Matching** | Max Stable Throughput | **0 orders/sec** | **0 orders/sec** | **7,235,674.0 orders/sec** | **13,105,957.0 orders/sec** | **0 orders/sec** | 
| | Breaking / Cliff Point | `50,000 orders` | `50,000 orders` | `150,000 orders` | `150,000 orders` | `50,000 orders` | 
| | Failure Reason | `HFT Latency Squeeze: P99 1688.3us > 1000us` | `HFT Latency Squeeze: P99 1113.7us > 1000us` | `HFT Latency Squeeze: P99 1336.5us > 1000us` | `HFT Latency Squeeze: P99 1496.2us > 1000us` | `HFT Latency Squeeze: P99 1109.6us > 1000us` | 
| | Latency (P50 / P90 / P99) | 1536.2 / 1660.9 / 1688.3 us | 979.3 / 1099.1 / 1113.7 us | 793.2 / 1276.1 / 1336.5 us | 909.8 / 1408.7 / 1496.2 us | 501.1 / 980.3 / 1109.6 us | 
| | Peak CPU % | 0.7% | 0.8% | 1.3% | 1.7% | 1.2% | 
| | Peak RAM (MB) | 11.2 MB | 16.6 MB | 16.8 MB | 29.1 MB | 33.5 MB | 
| **App 5: 120 FPS Game Server** | Max Stable Throughput | **2,000 players @ 120 FPS** | **500 players @ 120 FPS** | **1,600 players @ 120 FPS** | **1,300 players @ 120 FPS** | **1,600 players @ 120 FPS** | 
| | Breaking / Cliff Point | `Survived Max (2000 players)` | `750 players` | `2000 players` | `1600 players` | `2000 players` | 
| | Failure Reason | `None` | `DESYNC COLLAPSE: 3.33% frame drops (24/720 ticks > 8.33ms)` | `DESYNC COLLAPSE: 3.47% frame drops (25/720 ticks > 8.33ms)` | `DESYNC COLLAPSE: 1.11% frame drops (8/720 ticks > 8.33ms)` | `DESYNC COLLAPSE: 3.33% frame drops (24/720 ticks > 8.33ms)` | 
| | Latency (P50 / P90 / P99) | 3051.7 / 3421.3 / 3823.1 us | 1483.6 / 5879.7 / 8582.2 us | 4839.7 / 5076.8 / 7383.1 us | 4125.5 / 4412.3 / 4931.3 us | 4936.9 / 5472.0 / 7183.3 us | 
| | Peak CPU % | 5.9% | 3.0% | 7.8% | 6.7% | 7.6% | 
| | Peak RAM (MB) | 12.2 MB | 17.6 MB | 19.0 MB | 28.6 MB | 36.4 MB | 

---

## App 1: Cloud-Native API Gateway (Incremental Concurrency Ramp)

### Scheduler: `default_cfs_eevdf`
* **Max Sustained Capacity:** 479.11 req/s
* **Overload Cliff Point:** `Concurrency 100` (Latency Cliff: P99 3021.7ms > 2000ms)

| Concurrency | Throughput | P50 | P90 | P95 | P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|
| 25 | 479.11 | 28.65 | 81.08 | 113.36 | 196.36 | 7.50 | 261.70 | HEALTHY |
| 50 | 254.56 | 75.83 | 393.74 | 535.23 | 965.91 | 8.00 | 263.60 | HEALTHY |
| 100 | 130.81 | 323.83 | 1457.06 | 1883.14 | 3021.70 | 8.20 | 266.30 | Latency Cliff: P99 3021.7ms > 2000ms |

### Scheduler: `scx_optima`
* **Max Sustained Capacity:** 544.15 req/s
* **Overload Cliff Point:** `Concurrency 100` (Latency Cliff: P99 4324.95ms > 2000ms)

| Concurrency | Throughput | P50 | P90 | P95 | P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|
| 25 | 544.15 | 24.56 | 51.57 | 61.82 | 88.14 | 7.80 | 262.40 | HEALTHY |
| 50 | 246.24 | 105.49 | 393.78 | 507.60 | 961.96 | 8.40 | 265.80 | HEALTHY |
| 100 | 87.68 | 558.72 | 2193.88 | 2855.47 | 4324.95 | 6.00 | 268.20 | Latency Cliff: P99 4324.95ms > 2000ms |

### Scheduler: `scx_rdtai`
* **Max Sustained Capacity:** 411.32 req/s
* **Overload Cliff Point:** `Concurrency 100` (Latency Cliff: P99 4005.48ms > 2000ms)

| Concurrency | Throughput | P50 | P90 | P95 | P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|
| 25 | 411.32 | 30.05 | 91.60 | 135.54 | 256.80 | 11.50 | 268.10 | HEALTHY |
| 50 | 289.36 | 52.68 | 348.29 | 503.30 | 787.33 | 11.10 | 269.50 | HEALTHY |
| 100 | 96.56 | 490.47 | 1803.92 | 2467.56 | 4005.48 | 9.50 | 273.00 | Latency Cliff: P99 4005.48ms > 2000ms |

### Scheduler: `scx_rusty`
* **Max Sustained Capacity:** 519.31 req/s
* **Overload Cliff Point:** `Concurrency 100` (Latency Cliff: P99 3397.1ms > 2000ms)

| Concurrency | Throughput | P50 | P90 | P95 | P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|
| 25 | 519.31 | 27.21 | 72.09 | 100.31 | 196.08 | 11.60 | 268.70 | HEALTHY |
| 50 | 208.66 | 100.15 | 518.62 | 681.17 | 950.09 | 10.70 | 271.10 | HEALTHY |
| 100 | 98.97 | 504.28 | 1956.86 | 2481.59 | 3397.10 | 9.60 | 274.80 | Latency Cliff: P99 3397.1ms > 2000ms |

### Scheduler: `scx_rustland`
* **Max Sustained Capacity:** 500.69 req/s
* **Overload Cliff Point:** `Concurrency 100` (Latency Cliff: P99 3546.55ms > 2000ms)

| Concurrency | Throughput | P50 | P90 | P95 | P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|
| 25 | 500.69 | 27.26 | 81.31 | 109.78 | 209.14 | 10.90 | 278.80 | HEALTHY |
| 50 | 220.94 | 107.81 | 460.36 | 619.64 | 991.47 | 9.90 | 280.60 | HEALTHY |
| 100 | 119.22 | 375.17 | 1562.31 | 2136.41 | 3546.55 | 9.40 | 284.10 | Latency Cliff: P99 3546.55ms > 2000ms |

---

## App 2: Production In-Memory Cache (Incremental Client Ramp)

### Scheduler: `default_cfs_eevdf`
* **Max Sustained Capacity:** 0 ops/s
* **Overload Cliff Point:** `Clients 50` (P99 Latency Breach: 39.519ms > 5.0ms)

| Clients | Throughput | P50 | P90 | P95 | P99 | Get P50 | Get P90 | Get P95 | Get P99 | Set P50 | Set P90 | Set P95 | Set P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 50 | 32589.30 | 23.98 | 26.21 | 26.77 | 39.52 | 23.98 | 26.26 | 26.83 | 34.11 | 23.98 | 26.16 | 26.70 | 39.52 | 8.30 | 17.10 | P99 Latency Breach: 39.519ms > 5.0ms |

### Scheduler: `scx_optima`
* **Max Sustained Capacity:** 0 ops/s
* **Overload Cliff Point:** `Clients 50` (P99 Latency Breach: 8.247ms > 5.0ms)

| Clients | Throughput | P50 | P90 | P95 | P99 | Get P50 | Get P90 | Get P95 | Get P99 | Set P50 | Set P90 | Set P95 | Set P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 50 | 1263736.20 | 0.40 | 0.64 | 0.70 | 8.25 | 0.26 | 0.47 | 0.52 | 8.25 | 0.54 | 0.81 | 0.87 | 5.02 | 29.00 | 22.00 | P99 Latency Breach: 8.247ms > 5.0ms |

### Scheduler: `scx_rdtai`
* **Max Sustained Capacity:** 2379679.1 ops/s
* **Overload Cliff Point:** `Clients 350` (P99 Latency Breach: 8.143ms > 5.0ms)

| Clients | Throughput | P50 | P90 | P95 | P99 | Get P50 | Get P90 | Get P95 | Get P99 | Set P50 | Set P90 | Set P95 | Set P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 50 | 2290260.20 | 0.28 | 0.40 | 0.43 | 0.85 | 0.17 | 0.22 | 0.23 | 0.27 | 0.39 | 0.58 | 0.63 | 0.85 | 29.30 | 27.60 | HEALTHY |
| 100 | 2379679.10 | 0.54 | 0.60 | 0.62 | 0.97 | 0.29 | 0.38 | 0.40 | 0.70 | 0.78 | 0.83 | 0.85 | 0.97 | 16.80 | 32.90 | HEALTHY |
| 200 | 1877406.90 | 1.40 | 1.60 | 1.64 | 3.37 | 0.80 | 0.97 | 1.01 | 1.46 | 2.01 | 2.22 | 2.28 | 3.37 | 10.50 | 29.90 | HEALTHY |
| 350 | 1363564.30 | 3.79 | 4.71 | 4.94 | 8.14 | 1.43 | 2.95 | 3.33 | 6.48 | 6.16 | 6.47 | 6.55 | 8.14 | 8.90 | 29.70 | P99 Latency Breach: 8.143ms > 5.0ms |

### Scheduler: `scx_rusty`
* **Max Sustained Capacity:** 2175925.9 ops/s
* **Overload Cliff Point:** `Clients 100` (P99 Latency Breach: 7.775ms > 5.0ms)

| Clients | Throughput | P50 | P90 | P95 | P99 | Get P50 | Get P90 | Get P95 | Get P99 | Set P50 | Set P90 | Set P95 | Set P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 50 | 2175925.90 | 0.25 | 0.31 | 0.33 | 0.53 | 0.14 | 0.21 | 0.23 | 0.41 | 0.35 | 0.41 | 0.42 | 0.53 | 29.40 | 28.60 | HEALTHY |
| 100 | 1939586.70 | 0.74 | 1.12 | 1.22 | 7.78 | 0.45 | 0.56 | 0.59 | 1.09 | 1.04 | 1.69 | 1.85 | 7.78 | 19.30 | 33.80 | P99 Latency Breach: 7.775ms > 5.0ms |

### Scheduler: `scx_rustland`
* **Max Sustained Capacity:** 1160037.8 ops/s
* **Overload Cliff Point:** `Clients 200` (P99 Latency Breach: 6.575ms > 5.0ms)

| Clients | Throughput | P50 | P90 | P95 | P99 | Get P50 | Get P90 | Get P95 | Get P99 | Set P50 | Set P90 | Set P95 | Set P99 | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 50 | 484400.70 | 1.83 | 1.92 | 1.94 | 2.94 | 1.84 | 1.93 | 1.95 | 2.94 | 1.83 | 1.91 | 1.94 | 2.85 | 25.50 | 37.30 | HEALTHY |
| 100 | 1160037.80 | 1.13 | 1.98 | 2.19 | 3.70 | 0.69 | 1.34 | 1.50 | 2.06 | 1.57 | 2.61 | 2.87 | 3.70 | 18.80 | 43.00 | HEALTHY |
| 200 | 1253675.90 | 1.91 | 2.84 | 3.08 | 6.58 | 1.68 | 2.15 | 2.26 | 3.36 | 2.14 | 3.54 | 3.89 | 6.58 | 10.60 | 40.70 | P99 Latency Breach: 6.575ms > 5.0ms |

---

## App 3: Real-Time Pro-Audio DSP Engine (Incremental Filter Complexity)

### Scheduler: `default_cfs_eevdf`
* **Max Sustained Capacity:** 3072 DSP filters @ 1.33ms
* **Overload Cliff Point:** `Survived Max (3072 filters)` (None (Zero Xruns))

| Complexity | Total Filters | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Xruns | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Step 1:  8ch /  8 stages (64 filters) | 64 | 89.30 | 94.30 | 95.58 | 119.47 | 948.38 | 0 | 0.20 | 11.10 | PERFECT (0 xruns) |
| Step 2:  8ch / 16 stages (128 filters) | 128 | 109.80 | 114.30 | 115.38 | 127.29 | 401.08 | 0 | 0.30 | 11.20 | PERFECT (0 xruns) |
| Step 3: 16ch / 24 stages (384 filters) | 384 | 191.61 | 200.60 | 202.81 | 217.02 | 1001.60 | 0 | 0.70 | 11.40 | PERFECT (0 xruns) |
| Step 4: 24ch / 32 stages (768 filters) | 768 | 152.24 | 286.80 | 320.38 | 341.12 | 357.02 | 0 | 0.90 | 11.40 | PERFECT (0 xruns) |
| Step 5: 32ch / 48 stages (1536 filters) | 1,536 | 217.06 | 441.70 | 497.80 | 567.06 | 595.87 | 0 | 1.20 | 11.60 | PERFECT (0 xruns) |
| Step 6: 48ch / 64 stages (3072 filters) | 3,072 | 380.43 | 387.60 | 389.39 | 395.55 | 892.50 | 0 | 2.00 | 11.50 | PERFECT (0 xruns) |

### Scheduler: `scx_optima`
* **Max Sustained Capacity:** 3072 DSP filters @ 1.33ms
* **Overload Cliff Point:** `Survived Max (3072 filters)` (None (Zero Xruns))

| Complexity | Total Filters | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Xruns | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Step 1:  8ch /  8 stages (64 filters) | 64 | 89.82 | 93.50 | 94.39 | 97.84 | 578.71 | 0 | 0.20 | 16.00 | PERFECT (0 xruns) |
| Step 2:  8ch / 16 stages (128 filters) | 128 | 104.53 | 125.30 | 130.53 | 416.76 | 802.38 | 0 | 0.30 | 16.50 | PERFECT (0 xruns) |
| Step 3: 16ch / 24 stages (384 filters) | 384 | 95.41 | 173.20 | 192.63 | 200.14 | 224.13 | 0 | 0.40 | 16.50 | PERFECT (0 xruns) |
| Step 4: 24ch / 32 stages (768 filters) | 768 | 134.65 | 280.80 | 317.37 | 320.91 | 472.13 | 0 | 0.70 | 16.50 | PERFECT (0 xruns) |
| Step 5: 32ch / 48 stages (1536 filters) | 1,536 | 217.06 | 221.90 | 223.16 | 401.47 | 547.64 | 0 | 1.00 | 16.30 | PERFECT (0 xruns) |
| Step 6: 48ch / 64 stages (3072 filters) | 3,072 | 383.24 | 521.10 | 555.57 | 585.23 | 1126.42 | 0 | 2.20 | 16.80 | PERFECT (0 xruns) |

### Scheduler: `scx_rdtai`
* **Max Sustained Capacity:** 128 DSP filters @ 1.33ms
* **Overload Cliff Point:** `16ch / 24stg (384 filters)` (DEADLINE BREACH: 1 Xruns! (Max: 1401.2us > 1333.3us))

| Complexity | Total Filters | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Xruns | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Step 1:  8ch /  8 stages (64 filters) | 64 | 90.24 | 94.50 | 95.54 | 98.83 | 410.70 | 0 | 0.20 | 17.10 | PERFECT (0 xruns) |
| Step 2:  8ch / 16 stages (128 filters) | 128 | 111.00 | 115.60 | 116.76 | 145.35 | 722.66 | 0 | 0.30 | 17.30 | PERFECT (0 xruns) |
| Step 3: 16ch / 24 stages (384 filters) | 384 | 177.69 | 213.70 | 222.70 | 530.09 | 1401.20 | 1 | 0.70 | 17.50 | DEADLINE BREACH: 1 Xruns! (Max: 1401.2us > 1333.3us) |

### Scheduler: `scx_rusty`
* **Max Sustained Capacity:** 3072 DSP filters @ 1.33ms
* **Overload Cliff Point:** `Survived Max (3072 filters)` (None (Zero Xruns))

| Complexity | Total Filters | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Xruns | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Step 1:  8ch /  8 stages (64 filters) | 64 | 90.06 | 94.60 | 95.71 | 123.23 | 868.55 | 0 | 0.20 | 27.90 | PERFECT (0 xruns) |
| Step 2:  8ch / 16 stages (128 filters) | 128 | 106.17 | 137.60 | 145.45 | 456.35 | 1319.74 | 0 | 0.30 | 27.50 | PERFECT (0 xruns) |
| Step 3: 16ch / 24 stages (384 filters) | 384 | 175.77 | 182.20 | 183.81 | 204.66 | 1108.54 | 0 | 0.70 | 28.20 | PERFECT (0 xruns) |
| Step 4: 24ch / 32 stages (768 filters) | 768 | 290.88 | 313.20 | 318.76 | 342.87 | 1215.54 | 0 | 1.30 | 28.30 | PERFECT (0 xruns) |
| Step 5: 32ch / 48 stages (1536 filters) | 1,536 | 217.16 | 289.00 | 306.92 | 364.35 | 550.66 | 0 | 1.10 | 28.00 | PERFECT (0 xruns) |
| Step 6: 48ch / 64 stages (3072 filters) | 3,072 | 382.35 | 385.10 | 385.78 | 548.52 | 901.98 | 0 | 2.00 | 28.40 | PERFECT (0 xruns) |

### Scheduler: `scx_rustland`
* **Max Sustained Capacity:** 0 DSP filters @ 1.33ms
* **Overload Cliff Point:** `8ch / 8stg (64 filters)` (DEADLINE BREACH: 2 Xruns! (Max: 2394.0us > 1333.3us))

| Complexity | Total Filters | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Xruns | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Step 1:  8ch /  8 stages (64 filters) | 64 | 87.32 | 149.90 | 165.52 | 459.84 | 2394.00 | 2 | 0.20 | 32.10 | DEADLINE BREACH: 2 Xruns! (Max: 2394.0us > 1333.3us) |

---

## App 4: Ultra-Low-Latency HFT Matching Engine (Volume Influx Ramp)

### Scheduler: `default_cfs_eevdf`
* **Max Sustained Capacity:** 0 orders/sec
* **Overload Cliff Point:** `50,000 orders` (HFT Latency Squeeze: P99 1688.3us > 1000us)

| Orders | Throughput | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|
| 50,000 | 10545215.03 | 1536.25 | 1660.93 | 1676.53 | 1688.29 | 1730.12 | 0.70 | 11.20 | HFT Latency Squeeze: P99 1688.3us > 1000us |

### Scheduler: `scx_optima`
* **Max Sustained Capacity:** 0 orders/sec
* **Overload Cliff Point:** `50,000 orders` (HFT Latency Squeeze: P99 1113.7us > 1000us)

| Orders | Throughput | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|
| 50,000 | 14605644.67 | 979.29 | 1099.10 | 1107.25 | 1113.65 | 1114.94 | 0.80 | 16.60 | HFT Latency Squeeze: P99 1113.7us > 1000us |

### Scheduler: `scx_rdtai`
* **Max Sustained Capacity:** 7235674.0 orders/sec
* **Overload Cliff Point:** `150,000 orders` (HFT Latency Squeeze: P99 1336.5us > 1000us)

| Orders | Throughput | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|
| 50,000 | 7235674.31 | 272.63 | 470.69 | 499.30 | 526.47 | 531.31 | 1.00 | 16.50 | HEALTHY |
| 150,000 | 18859443.71 | 793.19 | 1276.09 | 1310.53 | 1336.49 | 1344.10 | 1.30 | 16.80 | HFT Latency Squeeze: P99 1336.5us > 1000us |

### Scheduler: `scx_rusty`
* **Max Sustained Capacity:** 13105957.0 orders/sec
* **Overload Cliff Point:** `150,000 orders` (HFT Latency Squeeze: P99 1496.2us > 1000us)

| Orders | Throughput | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|
| 50,000 | 13105957.21 | 250.94 | 433.78 | 451.61 | 465.28 | 470.67 | 1.20 | 29.10 | HEALTHY |
| 150,000 | 13785919.56 | 909.81 | 1408.69 | 1455.19 | 1496.21 | 1513.79 | 1.70 | 28.40 | HFT Latency Squeeze: P99 1496.2us > 1000us |

### Scheduler: `scx_rustland`
* **Max Sustained Capacity:** 0 orders/sec
* **Overload Cliff Point:** `50,000 orders` (HFT Latency Squeeze: P99 1109.6us > 1000us)

| Orders | Throughput | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|
| 50,000 | 7412418.20 | 501.14 | 980.33 | 1042.58 | 1109.61 | 1127.06 | 1.20 | 33.50 | HFT Latency Squeeze: P99 1109.6us > 1000us |

---

## App 5: 120 FPS Game Server (Player Population Scaling)

### Scheduler: `default_cfs_eevdf`
* **Max Sustained Capacity:** 2000 players @ 120 FPS
* **Overload Cliff Point:** `Survived Max (2000 players)` (None)

| Players | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Drops | Drop Rate | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 340.29 | 492.78 | 536.54 | 649.71 | 895.36 | 0 | 0.00 | 1.60 | 11.80 | PERFECT (0 drops) |
| 250 | 440.18 | 734.64 | 824.49 | 938.35 | 1305.36 | 0 | 0.00 | 2.00 | 12.00 | PERFECT (0 drops) |
| 500 | 777.28 | 1133.89 | 1271.29 | 1556.65 | 1855.52 | 0 | 0.00 | 2.60 | 11.80 | PERFECT (0 drops) |
| 750 | 1072.63 | 1529.42 | 1644.22 | 1920.88 | 2263.83 | 0 | 0.00 | 3.20 | 11.90 | PERFECT (0 drops) |
| 1,000 | 1412.65 | 1855.97 | 1966.30 | 2248.42 | 2510.03 | 0 | 0.00 | 3.70 | 12.00 | PERFECT (0 drops) |
| 1,300 | 1837.03 | 2434.95 | 2566.80 | 2781.45 | 3052.66 | 1 | 0.14 | 4.60 | 12.20 | Transient Hitching: 1 drops (0.14%) |
| 1,600 | 2303.29 | 2764.62 | 2887.56 | 3351.49 | 3675.94 | 0 | 0.00 | 5.00 | 12.10 | PERFECT (0 drops) |
| 2,000 | 3051.73 | 3421.26 | 3441.63 | 3823.07 | 4501.45 | 0 | 0.00 | 5.90 | 12.20 | PERFECT (0 drops) |

### Scheduler: `scx_optima`
* **Max Sustained Capacity:** 500 players @ 120 FPS
* **Overload Cliff Point:** `750 players` (DESYNC COLLAPSE: 3.33% frame drops (24/720 ticks > 8.33ms))

| Players | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Drops | Drop Rate | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 382.10 | 494.68 | 502.38 | 645.17 | 742.18 | 0 | 0.00 | 1.10 | 16.80 | PERFECT (0 drops) |
| 250 | 557.08 | 910.08 | 937.66 | 1141.80 | 7163.07 | 0 | 0.00 | 1.80 | 17.30 | PERFECT (0 drops) |
| 500 | 898.58 | 1425.94 | 1696.86 | 5630.58 | 6523.24 | 0 | 0.00 | 2.20 | 17.50 | PERFECT (0 drops) |
| 750 | 1483.56 | 5879.70 | 6875.35 | 8582.22 | 10698.13 | 24 | 3.33 | 3.00 | 17.60 | DESYNC COLLAPSE: 3.33% frame drops (24/720 ticks > 8.33ms) |

### Scheduler: `scx_rdtai`
* **Max Sustained Capacity:** 1600 players @ 120 FPS
* **Overload Cliff Point:** `2000 players` (DESYNC COLLAPSE: 3.47% frame drops (25/720 ticks > 8.33ms))

| Players | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Drops | Drop Rate | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 281.88 | 520.44 | 570.03 | 693.28 | 858.84 | 0 | 0.00 | 1.60 | 18.00 | PERFECT (0 drops) |
| 250 | 827.94 | 1028.89 | 1069.29 | 1191.05 | 1551.50 | 0 | 0.00 | 2.50 | 18.40 | PERFECT (0 drops) |
| 500 | 1348.41 | 1586.06 | 1693.45 | 1833.41 | 2573.80 | 0 | 0.00 | 3.50 | 18.60 | PERFECT (0 drops) |
| 750 | 2026.84 | 2342.88 | 2416.83 | 2767.46 | 3378.16 | 0 | 0.00 | 4.30 | 19.00 | PERFECT (0 drops) |
| 1,000 | 1713.62 | 2126.79 | 2267.48 | 2672.07 | 3044.47 | 0 | 0.00 | 4.20 | 19.00 | PERFECT (0 drops) |
| 1,300 | 3083.37 | 3396.78 | 3464.08 | 3970.04 | 5025.77 | 2 | 0.28 | 6.00 | 18.40 | Transient Hitching: 2 drops (0.28%) |
| 1,600 | 2516.44 | 3121.92 | 3216.64 | 3550.43 | 4229.11 | 0 | 0.00 | 5.10 | 18.00 | PERFECT (0 drops) |
| 2,000 | 4839.68 | 5076.77 | 5160.90 | 7383.11 | 11387.71 | 25 | 3.47 | 7.80 | 18.20 | DESYNC COLLAPSE: 3.47% frame drops (25/720 ticks > 8.33ms) |

### Scheduler: `scx_rusty`
* **Max Sustained Capacity:** 1300 players @ 120 FPS
* **Overload Cliff Point:** `1600 players` (DESYNC COLLAPSE: 1.11% frame drops (8/720 ticks > 8.33ms))

| Players | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Drops | Drop Rate | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 449.24 | 557.62 | 591.38 | 626.14 | 658.62 | 0 | 0.00 | 1.90 | 28.10 | PERFECT (0 drops) |
| 250 | 681.13 | 951.42 | 1021.44 | 1182.13 | 1381.74 | 0 | 0.00 | 2.30 | 28.40 | PERFECT (0 drops) |
| 500 | 1053.44 | 1451.85 | 1541.66 | 1698.02 | 1950.85 | 0 | 0.00 | 3.00 | 28.60 | PERFECT (0 drops) |
| 750 | 1939.52 | 2309.34 | 2381.10 | 2557.57 | 3624.55 | 0 | 0.00 | 4.20 | 28.30 | PERFECT (0 drops) |
| 1,000 | 2401.89 | 2776.84 | 2881.47 | 3205.47 | 4077.89 | 0 | 0.00 | 5.00 | 28.50 | PERFECT (0 drops) |
| 1,300 | 2251.98 | 2757.20 | 2873.92 | 3247.64 | 3815.32 | 0 | 0.00 | 4.90 | 28.00 | PERFECT (0 drops) |
| 1,600 | 4125.52 | 4412.31 | 4479.39 | 4931.29 | 8398.57 | 8 | 1.11 | 6.70 | 27.60 | DESYNC COLLAPSE: 1.11% frame drops (8/720 ticks > 8.33ms) |

### Scheduler: `scx_rustland`
* **Max Sustained Capacity:** 1600 players @ 120 FPS
* **Overload Cliff Point:** `2000 players` (DESYNC COLLAPSE: 3.33% frame drops (24/720 ticks > 8.33ms))

| Players | P50 Us | P90 Us | P95 Us | P99 Us | Max Us | Drops | Drop Rate | Cpu Percent | Ram Mb | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 412.60 | 523.41 | 567.39 | 651.74 | 666.17 | 0 | 0.00 | 1.80 | 33.70 | PERFECT (0 drops) |
| 250 | 695.54 | 919.02 | 996.79 | 1146.73 | 1328.71 | 0 | 0.00 | 2.40 | 34.10 | PERFECT (0 drops) |
| 500 | 1308.60 | 1592.07 | 1683.51 | 1831.41 | 2247.37 | 0 | 0.00 | 3.30 | 34.70 | PERFECT (0 drops) |
| 750 | 1944.19 | 2244.58 | 2317.47 | 2475.22 | 3211.96 | 0 | 0.00 | 4.30 | 35.60 | PERFECT (0 drops) |
| 1,000 | 2078.94 | 2553.32 | 2668.03 | 2963.50 | 3519.83 | 1 | 0.14 | 4.70 | 36.40 | Transient Hitching: 1 drops (0.14%) |
| 1,300 | 2457.69 | 2932.63 | 3042.29 | 3411.27 | 4219.64 | 2 | 0.28 | 5.40 | 36.00 | Transient Hitching: 2 drops (0.28%) |
| 1,600 | 3937.19 | 4393.48 | 4451.12 | 4608.62 | 4948.10 | 2 | 0.28 | 6.40 | 35.80 | Transient Hitching: 2 drops (0.28%) |
| 2,000 | 4936.95 | 5471.96 | 5616.15 | 7183.32 | 20890.58 | 24 | 3.33 | 7.60 | 34.40 | DESYNC COLLAPSE: 3.33% frame drops (24/720 ticks > 8.33ms) |

---

