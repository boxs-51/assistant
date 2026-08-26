# AI Gateway — Phase 0 Verification Runbook

## Purpose

Phase 0 establishes a safe baseline before traffic is moved to the new runtime path. The repository now contains a deterministic `mock` provider so provider-facing tests do not require OpenAI, Gemini, or another external API.

## 1. Enable the mock provider

The mock provider can be enabled either through the typed config field or an environment variable:

```bash
GATEWAY_PROVIDER__MOCK_ENABLED=true
```

When enabled, `mock` is appended to the default provider chain unless it is already present. Requests can force it explicitly:

```json
{
  "model": "mock-chat",
  "provider": "mock",
  "messages": [{"role": "user", "content": "hello"}]
}
```

The mock provider implements:

- deterministic chat completion
- deterministic streaming chat
- deterministic embeddings
- model listing/details
- in-memory file upload/metadata/download/delete
- health/provider-info contract methods

It performs **zero network I/O**.

## 2. Offline test suite

From the repository root:

```bash
python -m compileall -q src tests tools
pytest -q tests/providers tests/architecture/test_phase0_provider.py
```

The runtime-flow test verifies ProviderDiscovery → Registry → RoutingPolicy → Executor → Chat/Embedding/Model/File handlers with the mock provider.

Expected result for the Phase 0 mock suite:

```text
7 passed
```

If the environment is missing `structlog`, `redis`, or other project dependencies, dependency installation must be performed in the real project environment. The verification environment used for analysis was dependency-incomplete, so a passing result there is limited to the 7 mock-focused tests executed with a minimal logging stub.

## 3. Start the real Gateway

Start the Gateway normally, with the mock provider enabled and the usual project dependencies/storage available. Example environment:

```bash
set GATEWAY_PROVIDER__MOCK_ENABLED=true
```

Linux/macOS:

```bash
export GATEWAY_PROVIDER__MOCK_ENABLED=true
```

Do not add external API keys for Phase 0 provider verification.

## 4. Live E2E suite

Point the test suite at the running Gateway:

```bash
export PHASE0_LIVE_BASE_URL=http://127.0.0.1:8000
export PHASE0_LIVE_AUTH_TOKEN='<valid gateway token>'
pytest -q tests/e2e/test_phase0_live.py
```

Or:

```bash
PHASE0_LIVE_BASE_URL=http://127.0.0.1:8000 \
PHASE0_LIVE_AUTH_TOKEN='<valid gateway token>' \
python tools/phase0_live_e2e.py
```

The live suite covers:

1. `/health`
2. `/ready`
3. missing-credential rejection (`401`)
4. non-streaming mock chat
5. streaming mock chat + `[DONE]`
6. mock embeddings
7. mock model listing
8. mock file upload → metadata → download → delete

This is a **live HTTP E2E** test against the real Gateway process. The only provider dependency is the in-process mock provider, so no external AI API is contacted.

## 5. Phase 0 acceptance criteria

Phase 0 is considered verified when all of the following are true:

- Gateway starts successfully with the normal production dependency set.
- `/health` returns `200`.
- `/ready` returns `200`.
- Protected endpoint without credentials returns `401`.
- Mock chat non-streaming returns `provider=mock`.
- Mock chat streaming emits response chunks and `[DONE]`.
- Mock embeddings return stable vectors.
- Mock model listing returns `mock-chat` and `mock-embedding`.
- Mock file round-trip succeeds.
- No external provider API request is observed during the suite.
- Baseline latency/error metrics can be collected before enabling new-runtime traffic.

## 6. Important limitation

The live suite requires the Gateway's normal storage/auth runtime to be available. The mock provider removes **AI provider network dependencies**, but it intentionally does not bypass the Gateway's own authentication, Redis, database, or application startup dependencies.
