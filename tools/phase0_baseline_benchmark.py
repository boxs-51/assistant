#!/usr/bin/env python3
"""Phase 0 deterministic baseline benchmark.

Measures Provider Runtime latency/throughput/error-rate using the offline MockProvider and
runs a synthetic Circuit Breaker lifecycle probe. An optional live HTTP mode measures the
running Gateway without requiring any external AI API.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitBreakerManager
from src.infrastructure.config import ConfigurationRegistry
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.discovery import ProviderDiscovery
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.registry import ProviderRegistry


@dataclass
class BenchmarkResult:
    name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate_percent: float
    throughput_requests_per_second: float
    wall_time_seconds: float
    latency_min_ms: Optional[float]
    latency_avg_ms: Optional[float]
    latency_p50_ms: Optional[float]
    latency_p95_ms: Optional[float]
    latency_p99_ms: Optional[float]
    latency_max_ms: Optional[float]


def percentile(samples: list[float], pct: float) -> Optional[float]:
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


async def benchmark(operation: Callable[[], Awaitable[object]], *, name: str, total: int, concurrency: int) -> BenchmarkResult:
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    latencies: list[float] = []
    successes = 0
    failures = 0

    async def one() -> None:
        nonlocal successes, failures
        async with semaphore:
            t0 = time.perf_counter()
            try:
                await operation()
            except Exception:
                failures += 1
            else:
                successes += 1
            finally:
                latencies.append((time.perf_counter() - t0) * 1000.0)

    await asyncio.gather(*(one() for _ in range(total)))
    wall = time.perf_counter() - started
    error_rate = (failures / total * 100.0) if total else 0.0
    
    return BenchmarkResult(
        name=name,
        total_requests=total,
        successful_requests=successes,
        failed_requests=failures,
        error_rate_percent=error_rate,
        throughput_requests_per_second=(total / wall) if wall else 0.0,
        wall_time_seconds=wall,
        latency_min_ms=min(latencies) if latencies else None,
        latency_avg_ms=statistics.fmean(latencies) if latencies else None,
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        latency_p99_ms=percentile(latencies, 99),
        latency_max_ms=max(latencies) if latencies else None,
    )


async def build_mock_runtime() -> tuple[ChatExecutionHandler, httpx.AsyncClient]:
    ConfigurationRegistry.set_config(ConfigSchema(
        provider=ProviderSettings(priority=["mock"], mock_enabled=True, timeout=5, retry=0),
    ))
    registry = ProviderRegistry()
    ProviderDiscovery(registry).run()
    providers = registry.list_all_providers()
    manager = CircuitBreakerManager()
    executor = ProviderExecutor(manager)
    runtime = ChatExecutionHandler(
        providers=providers,
        routing_policy=RoutingPolicy(providers),
        executor=executor,
        circuit_breaker_manager=manager,
    )
    return runtime, httpx.AsyncClient()


async def run_mock(total: int, concurrency: int) -> BenchmarkResult:
    handler, client = await build_mock_runtime()
    try:
        payload = {"model": "mock-chat", "provider": "mock", "messages": [{"role": "user", "content": "phase0 baseline"}]}
        async def operation() -> object:
            return await handler.execute_with_fallback(client, payload)
        return await benchmark(operation, name="mock_provider_runtime", total=total, concurrency=concurrency)
    finally:
        await client.aclose()


async def run_live(base_url: str, token: str | None, total: int, concurrency: int) -> BenchmarkResult:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {"model": "mock-chat", "provider": "mock", "messages": [{"role": "user", "content": "phase0 live baseline"}]}
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=30.0) as client:
        async def operation() -> object:
            response = await client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            return response
        return await benchmark(operation, name="live_gateway_http", total=total, concurrency=concurrency)


async def circuit_breaker_probe() -> dict[str, object]:
    breaker = CircuitBreaker(provider_name="phase0-probe", failure_threshold=3, reset_timeout=0.02, success_threshold=1)
    for _ in range(3):
        await breaker.on_failure()
    blocked = False
    try:
        await breaker.before_request()
    except CircuitBreakerOpenError:
        blocked = True
    await asyncio.sleep(0.03)
    await breaker.before_request()
    await breaker.on_success()
    return {
        "final_state": breaker.current_state.value,
        "blocked_request_observed": blocked,
        **breaker.metrics_snapshot(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--live-url", default=os.getenv("PHASE0_LIVE_BASE_URL"))
    parser.add_argument("--live-token", default=os.getenv("PHASE0_LIVE_AUTH_TOKEN"))
    parser.add_argument("--output", default="artifacts/phase0-baseline.json")
    parser.add_argument("--skip-live", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.requests <= 0 or args.concurrency <= 0:
        raise SystemExit("--requests and --concurrency must be positive")
    results = {"mock": asdict(await run_mock(args.requests, args.concurrency)), "circuit_breaker": await circuit_breaker_probe()}
    if not args.skip_live and args.live_url:
        results["live"] = asdict(await run_live(args.live_url, args.live_token, args.requests, args.concurrency))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())