# Phase 0 — Offline Mock Provider v2

## Goal

Provide a deterministic, configurable, zero-network provider/test harness that
can exercise the canonical `/v1` HTTP transport and ProviderRuntime without
OpenAI/Gemini/Anthropic API keys, Ollama, a local model server, or outbound
provider HTTP calls.

## v2 guarantees

- Mock-only discovery never instantiates real providers.
- `ProviderRuntime` passes `RuntimeContext.config` into discovery, routing,
  retry and execution handlers.
- `RoutingPolicy`, `ProviderExecutor` and handler timeout can be constructed in
  tests without first loading the global `ConfigurationRegistry`.
- `fail_next=N` fails exactly N matching operations and then succeeds.
- `fail_after_chunks=N` emits exactly N stream chunks before failing.
- Mock identifiers and state are deterministic from the configured seed.
- `MockProvider.send()`/`send_stream()` remain explicit network-I/O guards.
- Canonical chat transport emits `provider.chat.execute`, matching the
  ProviderRuntime command subscription.
- HTTP E2E exercises chat, streaming, embeddings, models, files, auth, agents,
  tools, multi-agent, admin, health and WebSocket events using local fakes only
  where those application subsystems are unrelated to external AI providers.

## Verification

```bash
pytest -q \
  tests/providers/test_mock_provider.py \
  tests/providers/test_mock_capabilities.py \
  tests/providers/test_mock_faults.py \
  tests/providers/test_mock_runtime_flow.py \
  tests/e2e/test_v1_offline.py
```
