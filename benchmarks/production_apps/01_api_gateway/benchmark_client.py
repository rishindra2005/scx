#!/usr/bin/env python3
"""
Lightweight Asynchronous Load Generator for API Gateway Benchmarks.
Generates concurrent HTTP client requests, aggregates response timings,
and computes P50, P90, P95, P99, and P99.9 latency distributions.
"""

import argparse
import asyncio
import math
import sys
import time
from typing import List
import httpx


async def run_worker(
    client: httpx.AsyncClient,
    target_url: str,
    requests_per_worker: int,
    latencies: List[float],
    errors: List[str],
):
    for _ in range(requests_per_worker):
        t0 = time.perf_counter()
        try:
            resp = await client.get(target_url, timeout=30.0)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                latencies.append(latency_ms)
            else:
                errors.append(f"HTTP {resp.status_code}")
        except Exception as e:
            errors.append(str(type(e).__name__))


async def main():
    parser = argparse.ArgumentParser(description="API Gateway Async Benchmark Load Client")
    parser.add_argument("--url", default="http://127.0.0.1/api/v1/dashboard", help="Target URL endpoint")
    parser.add_argument("--concurrency", type=int, default=50, help="Number of concurrent client coroutines")
    parser.add_argument("--requests", type=int, default=2000, help="Total number of HTTP requests to execute")
    parser.add_argument("--json", help="Path to write JSON telemetry output")
    args = parser.parse_args()

    reqs_per_worker = max(1, args.requests // args.concurrency)
    actual_total = reqs_per_worker * args.concurrency

    print(f"================================================================")
    print(f" API Gateway Load Benchmark (sched_ext evaluation)              ")
    print(f" Target: {args.url}")
    print(f" Concurrency: {args.concurrency} | Planned Requests: {actual_total}")
    print(f"================================================================")

    latencies: List[float] = []
    errors: List[str] = []

    limits = httpx.Limits(max_keepalive_connections=args.concurrency, max_connections=args.concurrency * 2)
    start_time = time.perf_counter()

    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [
            run_worker(client, args.url, reqs_per_worker, latencies, errors)
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*tasks)

    total_time = time.perf_counter() - start_time
    throughput = len(latencies) / total_time if total_time > 0 else 0

    if not latencies:
        print(f"[Error] All requests failed! Total errors: {len(errors)}")
        sys.exit(1)

    latencies.sort()
    n = len(latencies)

    def p(pct: float) -> float:
        idx = int(math.ceil(pct * (n - 1)))
        return latencies[min(idx, n - 1)]

    res_json = {
        "duration_s": round(total_time, 3),
        "successful_reqs": n,
        "total_reqs": actual_total,
        "failed_reqs": len(errors),
        "throughput_req_s": round(throughput, 2),
        "p50_ms": round(p(0.50), 2),
        "p90_ms": round(p(0.90), 2),
        "p95_ms": round(p(0.95), 2),
        "p99_ms": round(p(0.99), 2),
        "p999_ms": round(p(0.999), 2),
        "min_ms": round(latencies[0], 2),
        "max_ms": round(latencies[-1], 2),
        "avg_ms": round(sum(latencies) / n, 2),
    }

    if args.json:
        import json
        with open(args.json, "w") as f:
            json.dump(res_json, f, indent=2)

    print(f"\nBenchmark Results:")
    print(f"  Total Duration:     {total_time:.3f} s")
    print(f"  Successful Reqs:    {n} / {actual_total} ({100.0 * n / actual_total:.1f}%)")
    print(f"  Failed Reqs:        {len(errors)}")
    print(f"  Throughput:         {throughput:.2f} req/s")
    print(f"\nLatency Distribution (Round-Trip):")
    print(f"  P50 (Median):       {p(0.50):.2f} ms")
    print(f"  P90:                {p(0.90):.2f} ms")
    print(f"  P95:                {p(0.95):.2f} ms")
    print(f"  P99:                {p(0.99):.2f} ms")
    print(f"  P99.9 (Tail):       {p(0.999):.2f} ms")
    print(f"  Min / Max / Avg:    {latencies[0]:.2f} / {latencies[-1]:.2f} / {sum(latencies)/n:.2f} ms")
    print(f"================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
