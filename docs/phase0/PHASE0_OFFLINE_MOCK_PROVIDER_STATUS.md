# Phase 0 — Offline Mock Provider

## Goal

A deterministic, configurable, zero-network provider for tests and local E2E.
No OpenAI/Gemini/Anthropic API key, Ollama server, or outbound provider HTTP is
required for the mocked execution path.

## Scope

- Chat + streaming chat.
- Embeddings.
- Model catalog.
- File lifecycle.
- Audio STT/TTS/translation.
- Vision/OCR.
- Image generation/edit/variation.
- Video generation/understanding.
- Batch operations.
- Token counting.
- Reranking.
- Tool/web-search/code-execution stubs.
- Moderation and computer-use deterministic stubs.
- Provider metadata and health.
- Capability matrix.
- Deterministic IDs/state with reset/snapshot.
- Fault injection and stream-failure injection.
- Latency injection.
- Explicit network-I/O guard.
- Opt-in discovery.
- ProviderRuntime handler integration tests.
- Canonical `/v1` HTTP/WebSocket E2E tests.

## Invariants

1. `mock_enabled=false` does not register the mock provider.
2. `MockProvider.send()` and `send_stream()` always fail with
   `mock_network_forbidden`; accidental provider HTTP I/O therefore cannot be
   silently introduced into an offline test.
3. Identical request + seed produces identical response identifiers/data.
4. `reset()` clears mutable provider state.

## Verification

```bash
pytest -q \
  tests/providers/test_mock_provider.py \
  tests/providers/test_mock_capabilities.py \
  tests/providers/test_mock_faults.py \
  tests/providers/test_mock_runtime_flow.py \
  tests/e2e/test_v1_offline.py
```
