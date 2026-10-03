"""
Production API Gateway Backend & Simulated Microservice Mesh.

Designed for high-throughput sched_ext Linux scheduler benchmarking.
Simulates realistic asynchronous I/O fan-out, downstream latency jitter,
heterogeneous task scheduling, and CPU-intensive cryptographic validation.
"""

import asyncio
import hashlib
import math
import os
import random
import time
from collections import deque
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="Production API Gateway & Microservices Engine",
    description="High-performance async gateway simulating microservice mesh fan-out queries",
    version="1.0.0",
)

# ---------------------------------------------------------
# Latency & Metrics Instrumentation Reservoir
# ---------------------------------------------------------
class MetricsReservoir:
    def __init__(self, capacity: int = 20000):
        self.capacity = capacity
        self.samples: deque = deque(maxlen=capacity)
        self.total_requests: int = 0
        self.successful_requests: int = 0
        self.failed_requests: int = 0
        self.start_time: float = time.time()
        self.lock = asyncio.Lock()

    async def record(self, latency_ms: float, success: bool = True):
        async with self.lock:
            self.total_requests += 1
            if success:
                self.successful_requests += 1
            else:
                self.failed_requests += 1
            self.samples.append(latency_ms)

    async def get_percentiles(self) -> Dict[str, Any]:
        async with self.lock:
            n = len(self.samples)
            uptime = max(0.001, time.time() - self.start_time)
            rps = self.total_requests / uptime

            if n == 0:
                return {
                    "total_requests": self.total_requests,
                    "successful_requests": self.successful_requests,
                    "failed_requests": self.failed_requests,
                    "uptime_seconds": round(uptime, 2),
                    "throughput_rps": round(rps, 2),
                    "samples_count": 0,
                    "p50_ms": 0.0,
                    "p90_ms": 0.0,
                    "p95_ms": 0.0,
                    "p99_ms": 0.0,
                    "p999_ms": 0.0,
                    "min_ms": 0.0,
                    "max_ms": 0.0,
                    "avg_ms": 0.0,
                }

            sorted_samples = sorted(self.samples)

            def percentile(p: float) -> float:
                idx = int(math.ceil(p * (n - 1)))
                return sorted_samples[min(idx, n - 1)]

            return {
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "uptime_seconds": round(uptime, 2),
                "throughput_rps": round(rps, 2),
                "samples_count": n,
                "p50_ms": round(percentile(0.50), 3),
                "p90_ms": round(percentile(0.90), 3),
                "p95_ms": round(percentile(0.95), 3),
                "p99_ms": round(percentile(0.99), 3),
                "p999_ms": round(percentile(0.999), 3),
                "min_ms": round(sorted_samples[0], 3),
                "max_ms": round(sorted_samples[-1], 3),
                "avg_ms": round(sum(sorted_samples) / n, 3),
            }


metrics = MetricsReservoir()

@app.middleware("http")
async def track_latency_middleware(request: Request, call_next):
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        raise
    finally:
        latency_ms = (time.perf_counter() - start) * 1000.0
        # Don't pollute user-facing percentiles with internal /health or /metrics polling
        if not request.url.path.startswith(("/health", "/metrics", "/favicon")):
            await metrics.record(latency_ms, success=(status_code < 400))


# ---------------------------------------------------------
# Simulated Downstream Microservices
# ---------------------------------------------------------
async def call_auth_service(user_id: str) -> Dict[str, Any]:
    """Simulates downstream Auth/RBAC Token validation (2-5 ms latency)."""
    delay = max(0.001, random.gauss(0.0035, 0.001))
    await asyncio.sleep(delay)
    return {
        "user_id": user_id,
        "authenticated": True,
        "roles": ["customer", "subscriber"],
        "token_valid_until": "2026-12-31T23:59:59Z",
        "downstream_service": "auth-service",
        "simulated_delay_ms": round(delay * 1000, 2),
    }


async def call_user_profile_service(user_id: str) -> Dict[str, Any]:
    """Simulates downstream User Profile & Settings service (5-12 ms latency)."""
    delay = max(0.002, random.gauss(0.008, 0.002))
    await asyncio.sleep(delay)
    return {
        "user_id": user_id,
        "username": f"enterprise_user_{user_id}",
        "tier": "enterprise_platinum",
        "locale": "en_US",
        "currency": "USD",
        "downstream_service": "user-profile-service",
        "simulated_delay_ms": round(delay * 1000, 2),
    }


async def call_order_history_service(user_id: str) -> Dict[str, Any]:
    """Simulates transactional SQL/NoSQL Order History query (8-18 ms latency)."""
    delay = max(0.004, random.gauss(0.012, 0.003))
    await asyncio.sleep(delay)
    return {
        "user_id": user_id,
        "recent_orders_count": 3,
        "total_lifetime_spend": 14250.75,
        "recent_orders": [
            {"order_id": "ORD-9841", "amount": 2500.00, "status": "DELIVERED"},
            {"order_id": "ORD-9842", "amount": 6250.75, "status": "SHIPPED"},
            {"order_id": "ORD-9843", "amount": 5500.00, "status": "PROCESSING"},
        ],
        "downstream_service": "order-history-service",
        "simulated_delay_ms": round(delay * 1000, 2),
    }


async def call_recommendation_service(user_id: str) -> Dict[str, Any]:
    """
    Simulates AI/ML vector search recommendation engine.
    Incurs 12-25 ms network/compute delay + mild CPU matrix/hash scoring.
    """
    delay = max(0.005, random.gauss(0.016, 0.004))
    await asyncio.sleep(delay)

    # Mild CPU workload: cosine similarity score simulation
    token = f"{user_id}:{delay}".encode()
    for _ in range(300):
        token = hashlib.sha256(token).digest()

    return {
        "user_id": user_id,
        "recommendations": [
            {"item_id": "SKU-4091", "score": 0.984, "category": "Cloud Infrastructure"},
            {"item_id": "SKU-8192", "score": 0.941, "category": "Kernel Performance"},
            {"item_id": "SKU-1024", "score": 0.892, "category": "Distributed Cache"},
        ],
        "engine_version": "v3.8-ann-vector",
        "downstream_service": "recommendation-service",
        "simulated_delay_ms": round(delay * 1000, 2),
    }


# ---------------------------------------------------------
# Gateway API Endpoints
# ---------------------------------------------------------
@app.get("/health")
@app.get("/healthz")
async def health_check():
    """L4/L7 Load balancer health check probe."""
    return {
        "status": "UP",
        "service": "api-gateway-mesh",
        "timestamp": time.time(),
    }


@app.get("/api/v1/dashboard")
async def get_user_dashboard(user_id: str = Query(default="usr_482910")):
    """
    Primary API Gateway aggregator route.
    Concurrently fans out requests to 4 downstream microservices via asyncio.gather().
    Tests concurrent I/O wakeups, coroutine scheduling, and tail latency aggregation.
    """
    start = time.perf_counter()

    auth_task = asyncio.create_task(call_auth_service(user_id))
    profile_task = asyncio.create_task(call_user_profile_service(user_id))
    order_task = asyncio.create_task(call_order_history_service(user_id))
    rec_task = asyncio.create_task(call_recommendation_service(user_id))

    auth_res, profile_res, order_res, rec_res = await asyncio.gather(
        auth_task, profile_task, order_task, rec_task
    )

    total_latency_ms = (time.perf_counter() - start) * 1000.0

    return {
        "gateway_status": "SUCCESS",
        "user_id": user_id,
        "total_aggregated_latency_ms": round(total_latency_ms, 2),
        "data": {
            "auth": auth_res,
            "profile": profile_res,
            "orders": order_res,
            "recommendations": rec_res,
        },
    }


@app.get("/api/v1/fanout")
async def custom_fanout_benchmark(
    fanout: int = Query(default=6, ge=1, le=64, description="Number of concurrent microservices to query"),
    delay_ms: float = Query(default=10.0, ge=0.1, le=1000.0, description="Base simulated I/O delay in ms"),
    jitter_ms: float = Query(default=3.0, ge=0.0, le=500.0, description="Random jitter standard deviation in ms"),
    cpu_cycles: int = Query(default=500, ge=0, le=50000, description="SHA256 CPU computation loops per fan-out task"),
):
    """
    Configurable fan-out endpoint for fine-grained scheduler benchmarking.
    Allows testing scalability from 1 up to 64 concurrent downstream tasks per request.
    """
    start = time.perf_counter()

    async def single_downstream(sub_id: int):
        t_start = time.perf_counter()
        # Simulated async I/O delay with gaussian distribution
        jittered_delay = max(0.0005, random.gauss(delay_ms / 1000.0, jitter_ms / 1000.0))
        await asyncio.sleep(jittered_delay)

        # Simulated CPU compute work
        if cpu_cycles > 0:
            h = f"sub-{sub_id}-{time.perf_counter()}".encode()
            for _ in range(cpu_cycles):
                h = hashlib.sha256(h).digest()

        return {
            "sub_service_id": sub_id,
            "service_name": f"downstream_node_{sub_id:02d}",
            "latency_ms": round((time.perf_counter() - t_start) * 1000.0, 2),
        }

    tasks = [asyncio.create_task(single_downstream(i)) for i in range(fanout)]
    results = await asyncio.gather(*tasks)

    total_latency_ms = (time.perf_counter() - start) * 1000.0

    return {
        "fanout_count": fanout,
        "configured_base_delay_ms": delay_ms,
        "total_elapsed_ms": round(total_latency_ms, 2),
        "downstream_responses": results,
    }


@app.get("/api/v1/cpu-heavy")
async def cpu_heavy_validation(
    iterations: int = Query(default=10000, ge=100, le=500000, description="SHA256 hashing iterations")
):
    """
    Simulates CPU-heavy cryptographic operations (e.g. JWT signature verification, PBKDF2).
    Forces scheduler preemption and tests CPU time-slicing vs I/O latency sensitivity.
    """
    start = time.perf_counter()
    seed = b"production-api-gateway-auth-token-signature-verification-entropy-key"
    current = seed
    for _ in range(iterations):
        current = hashlib.sha256(current).digest()

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return {
        "operation": "cryptographic_token_validation",
        "iterations": iterations,
        "hash_prefix": current[:8].hex(),
        "cpu_time_ms": round(elapsed_ms, 3),
    }


class OrderPayload(BaseModel):
    user_id: str
    item_id: str
    quantity: int = 1
    payment_method: str = "credit_card"


@app.post("/api/v1/checkout")
async def post_checkout_transaction(payload: OrderPayload):
    """
    Simulates transactional stateful write operation:
    1. Inventory lock (async I/O)
    2. Payment gateway authorization (async network)
    3. Ledger write (async I/O)
    """
    start = time.perf_counter()
    # Step 1: Inventory lock
    await asyncio.sleep(random.uniform(0.003, 0.007))
    # Step 2: Payment authorization
    await asyncio.sleep(random.uniform(0.008, 0.018))
    # Step 3: Transaction write
    await asyncio.sleep(random.uniform(0.004, 0.009))

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return {
        "status": "ORDER_CONFIRMED",
        "order_id": f"ORD-{random.randint(100000, 999999)}",
        "user_id": payload.user_id,
        "item_id": payload.item_id,
        "quantity": payload.quantity,
        "transaction_latency_ms": round(elapsed_ms, 2),
    }


@app.get("/metrics")
async def get_gateway_metrics():
    """
    Provides real-time runtime metrics including P50, P90, P95, P99, and P99.9 latency.
    """
    percentiles = await metrics.get_percentiles()
    return {
        "gateway_node": os.uname().nodename,
        "process_id": os.getpid(),
        "metrics": percentiles,
    }
