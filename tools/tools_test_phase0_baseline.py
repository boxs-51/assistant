import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.phase0_baseline_benchmark import percentile
from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


def test_percentile_interpolation():
    samples = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(samples, 50) == 3.0
    assert percentile(samples, 95) == 4.8
    assert percentile(samples, 99) == 4.96


def test_percentile_empty_is_nan():
    assert percentile([], 50) != percentile([], 50)


def test_circuit_breaker_metrics_capture_lifecycle():
    async def run():
        breaker = CircuitBreaker("test", failure_threshold=2, reset_timeout=0.001, success_threshold=1)
        await breaker.on_failure()
        await breaker.on_failure()
        try:
            await breaker.before_request()
        except CircuitBreakerOpenError:
            pass
        await asyncio.sleep(0.003)
        await breaker.before_request()
        await breaker.on_success()
        metrics = breaker.metrics_snapshot()
        assert metrics["open_transitions"] == 1
        assert metrics["half_open_transitions"] == 1
        assert metrics["half_open_trials"] == 1
        assert metrics["blocked_requests"] == 1
        assert metrics["close_transitions"] == 1
        assert breaker.current_state.value == "closed"
    asyncio.run(run())