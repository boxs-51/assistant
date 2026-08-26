# Phase 0 Baseline Benchmark

## Metrics

The benchmark records:

- Latency: min / average / **p50 / p95 / p99 / max**.
- Throughput: requests per second measured using wall-clock batch duration.
- Error rate: failed requests divided by total requests.
- Circuit Breaker: open, reopen, half-open, close transitions, blocked requests, half-open trials, and final state.

## Offline benchmark

```bash
GATEWAY_PROVIDER__MOCK_ENABLED=true \
PYTHONPATH=. \
python tools/phase0_baseline_benchmark.py \
  --requests 200 \
  --concurrency 20 \
  --skip-live \
  --output artifacts/phase0-baseline.json
```

## Live benchmark

Start the real Gateway with the mock provider enabled, then run:

```bash
PHASE0_LIVE_BASE_URL=http://127.0.0.1:8000 \
PHASE0_LIVE_AUTH_TOKEN='<token>' \
PYTHONPATH=. \
python tools/phase0_baseline_benchmark.py \
  --requests 200 \
  --concurrency 20
```

The live mode targets:

```text
POST /v1/chat/completions
model=mock-chat
provider=mock
```

This isolates Gateway HTTP/runtime overhead while keeping provider behavior deterministic and API-free.

## Regression gate recommendation

Use the first verified live run as the golden baseline. Future Phase 0/1/2 changes should compare:

- p50/p95/p99 latency.
- throughput.
- error rate.
- Circuit Breaker transition behavior.

Recommended initial alert thresholds should be relative to the verified golden baseline rather than the offline numbers. A practical first gate is a warning at >20% p95 regression and a failure gate at >50% p95 regression, with no increase in unexpected error rate.
