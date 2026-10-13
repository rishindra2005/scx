# `scx_optima` Quickstart Operations Guide

This guide contains the exact operational commands to start, inspect, tune, debug, and stop the **`scx_optima`** scheduler and baseline `sched_ext` schedulers.

---

## 1. Quick Start Commands

All commands require root privileges (`sudo`) to attach eBPF schedulers to the Linux kernel.

### A. Start `scx_optima` (Foreground)

```bash
# Maximum performance mode (silent, 0ns userspace polling overhead)
sudo ./target/release/scx_optima

# With live telemetry dashboard updating every 1 second
sudo ./target/release/scx_optima --stats 1
```

### B. Start `scx_optima` (Background / Daemon)

```bash
# Start in background, write output and telemetry to log file
sudo nohup ./target/release/scx_optima --stats 1 > /tmp/scx_optima.log 2>&1 &

# Inspect live telemetry stream in background
tail -f /tmp/scx_optima.log
```

---

## 2. Verbosity Modes (`-v`, `-vv`, `-vvv`)

| Flag | Mode | What It Does |
| :--- | :--- | :--- |
| *(none)* | **Silent High-Throughput** | Pure kernel eBPF execution. Zero userspace query overhead. |
| `-v` | **Telemetry Breakdown** | Displays per-queue dispatch counts (Direct dispatch, DSQ_PERF, DSQ_EFF, Steals, Fallbacks). |
| `-vv` | **In-Kernel Decision Trace** | Enables BPF `bpf_trace_printk` inside the kernel and streams real-time scheduling decisions from `/sys/kernel/tracing/trace_pipe`. |
| `-vvv` | **libbpf Verifier Debug** | Enables low-level BPF verifier logging and object loader debugging output. |

### Command Examples:

```bash
# 1. Verbose Telemetry Breakdown (requires --stats N)
sudo ./target/release/scx_optima --stats 1 -v

# 2. Live Kernel Decision Tracing (-vv streams kernel decisions to console)
sudo ./target/release/scx_optima -vv

# 3. Both live stats and in-kernel decision streaming
sudo ./target/release/scx_optima --stats 1 -vv

# 4. Low-level verifier debugging (for developer diagnosis)
sudo ./target/release/scx_optima -vvv
```

---

## 3. Stopping the Scheduler

When `scx_optima` receives a termination signal, it cleanly unregisters its `struct_ops` from the Linux kernel. The kernel immediately and safely falls back to CFS / EEVDF with **zero downtime, zero lockups, and zero dropped tasks**.

### A. In Foreground
Press `Ctrl+C` (`SIGINT`).

### B. In Background / From Another Terminal
```bash
# Send SIGINT (graceful exit handler)
sudo killall -SIGINT scx_optima
# or
sudo pkill -2 scx_optima
```

### C. Verify Scheduler Termination & CFS Fallback
```bash
# Check kernel log for clean unregistration
dmesg | grep -E "sched_ext|scx_optima" | tail -n 5

# Verify process is dead
pgrep -a scx_optima
```

---

## 4. Fine-Tuning Parameters

| Parameter | Default | Description | Example |
| :--- | :--- | :--- | :--- |
| `-s, --slice-us <US>` | `3000` | Scheduling quantum (in microseconds). Lower values favor interactive responsiveness; higher values favor batch compute throughput. | `sudo ./target/release/scx_optima -s 1000` (1ms low latency) |
| `--stats <SECS>` | `0` | Telemetry refresh interval in seconds (`0` disables userspace polling). | `sudo ./target/release/scx_optima --stats 2` |
| `--dp-threshold <VAL>` | `500000` | Task density cutoff for P-core priority queueing. | `sudo ./target/release/scx_optima --dp-threshold 400000` |
| `--dp-capacity <CAP>` | `8` | P-core runqueue depth limit before compute tasks spill over to E-cores. | `sudo ./target/release/scx_optima --dp-capacity 12` |

---

## 5. Status & Telemetry Output Reference

When running with `--stats 1 -v`, the output appears as:

```text
[scx_optima] WSPT: 84920    | DP (P: 61042 S: 19412 E: 4466 ) | B&B (Pruned: 14210 Preempt: 82 ) | Steal: 310
[scx_optima:detail] Direct: 12040  | DSQ_PERF: 61042 | DSQ_EFF: 4466  | Steal: 310   | Fallback: 0
```

- **`WSPT`**: Number of tasks sorted and dispatched using WSPT density ordering.
- **`DP (P / S / E)`**: Tasks partitioned into P-Cores (Zen 5), Shared Queue, and E-Cores (Zen 5c).
- **`B&B Pruned / Preempt`**: Candidate preemption cores pruned vs. actual urgent preemptions made.
- **`Steal`**: Idle cores stealing tasks from another domain (work-conserving balance).

---

## 6. Running Baseline Schedulers for Comparison

All baseline schedulers are compiled in `target/release/`:

```bash
# 1. scx_rusty (Rust userspace multi-domain scheduler)
sudo ./target/release/scx_rusty

# 2. scx_lavd (Latency-aware critical path scheduler)
sudo ./target/release/scx_lavd

# 3. scx_bpfland (Pure BPF vtime scheduler)
sudo ./target/release/scx_bpfland

# 4. scx_rustland (User-space scheduling model)
sudo ./target/release/scx_rustland

# 5. scx_flash (Simple low-overhead scheduler)
sudo ./target/release/scx_flash
```

To stop any active scheduler and return to CFS / EEVDF:
```bash
sudo killall -SIGINT scx_rusty scx_lavd scx_bpfland scx_rustland scx_flash scx_optima 2>/dev/null
```

---

## 7. Rebuilding from Source

If you make modifications to `scheds/rust/scx_optima/src/main.rs` or `main.bpf.c`:

```bash
# Rebuild scx_optima with maximum optimizations
cargo build --release -p scx_optima
```
