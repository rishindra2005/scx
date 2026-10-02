#!/usr/bin/env python3
import subprocess
import time
import os
import sys

SCHED_DIR = "/home/rishi/Desktop/OS/project/scx/target/release"
LINUX_DIR = "/home/rishi/Desktop/OS/project/scx/sch_tests/linux"

SCHEDULERS = [
    ("Linux CFS/EEVDF", None),
    ("scx_rlfifo", f"{SCHED_DIR}/scx_rlfifo"),
    ("scx_rusty", f"{SCHED_DIR}/scx_rusty"),
    ("scx_rdtai", f"{SCHED_DIR}/scx_rdtai"),
    ("scx_optima", f"{SCHED_DIR}/scx_optima")
]

def run_cmd(cmd):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.stdout

def get_current_sched():
    try:
        with open("/sys/kernel/sched_ext/root/ops", "r") as f:
            return f.read().strip()
    except Exception:
        return "None"

def stop_sched(name):
    run_cmd(f"pkexec pkill -SIGINT -f {name} 2>/dev/null")
    for _ in range(20):
        time.sleep(0.3)
        if get_current_sched() == "None":
            return
    run_cmd(f"pkexec pkill -9 -f {name} 2>/dev/null")
    time.sleep(1)

def compile_kernel():
    # Clean object files
    run_cmd(f"make -C {LINUX_DIR} clean > /dev/null 2>&1")
    start = time.time()
    res = subprocess.run(f"make -C {LINUX_DIR} -j24 bzImage", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    end = time.time()
    if res.returncode != 0:
        return None, res.stderr.decode("utf-8", errors="replace")
    return round(end - start, 2), None

def main():
    clean_path = ":".join([p for p in os.environ.get("PATH", "").split(":") if not p.startswith("/root")])
    os.environ["PATH"] = clean_path

    # Ensure no lingering scheduler
    if get_current_sched() != "None":
        run_cmd("pkexec pkill -SIGINT -f scx_ 2>/dev/null")
        time.sleep(2)

    results = {}

    print("=================================================================")
    print("      FULL LINUX KERNEL COMPILATION BENCHMARK (make -j24 bzImage)")
    print("      Host: AMD Ryzen AI 9 HX 370 (24 Cores) | Linux 6.18.0-rc7")
    print("=================================================================")

    for name, bin_path in SCHEDULERS:
        print(f"\n--> Benchmarking: {name}...")
        
        proc = None
        if bin_path:
            proc = subprocess.Popen(f"pkexec env PATH=\"{clean_path}\" {bin_path} > /dev/null 2>&1", shell=True)
            # Wait for scheduler to attach
            attached = False
            for _ in range(25):
                time.sleep(0.4)
                curr = get_current_sched()
                if curr != "None":
                    attached = True
                    break
            if not attached:
                print(f"  [ERROR] {name} failed to attach! Current ops: {get_current_sched()}")
                results[name] = "ATTACH_FAILED"
                continue
            print(f"  Attached: {get_current_sched()}")

        print("  Starting compilation (make -j24 bzImage)...")
        elapsed, err = compile_kernel()
        
        if elapsed is not None:
            print(f"  Finished: {elapsed} seconds")
            results[name] = elapsed
        else:
            print(f"  [FAILED] Build error: {err[:200]}")
            results[name] = "FAILED"

        if bin_path:
            print(f"  Unmounting {name}...")
            stop_sched(name)
            time.sleep(1)

    print("\n" + "="*60)
    print("       FINAL FULL KERNEL COMPILATION BENCHMARK REPORT")
    print("="*60)
    print(f"{'Scheduler Name':<25} | {'Compilation Time (s)':<20} | {'Delta vs CFS':<15}")
    print("-" * 65)
    cfs_time = results.get("Linux CFS/EEVDF", None)
    for name, _ in SCHEDULERS:
        t = results.get(name, "N/A")
        delta_str = "-"
        if isinstance(t, (int, float)) and isinstance(cfs_time, (int, float)):
            diff = t - cfs_time
            sign = "+" if diff > 0 else ""
            delta_str = f"{sign}{diff:.2f}s"
        val_str = f"{t}s" if isinstance(t, (int, float)) else str(t)
        print(f"{name:<25} | {val_str:<20} | {delta_str:<15}")
    print("="*60)

if __name__ == "__main__":
    main()
