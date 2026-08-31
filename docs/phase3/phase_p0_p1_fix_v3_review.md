# Senior Architect Review — commit cedd8c03

Base commit:
`cedd8c03fe2e5bb95ad33c66056d3a819f33b75b`

## Verdict

**Reject production readiness; accept the architecture direction.**

The commit correctly moves CapabilityRegistry toward the canonical registry and injects the shared registry/authorization into CapabilityRuntime, but the runtime lifecycle and deployment contracts still contain P0/P1 correctness problems.

## P0 fixes included

1. **RuntimeKernel recovery awaited initialization incorrectly.**
   `runtime.initialize(...)` was called without `await`. Recovery now performs an awaited restart sequence.

2. **Recovery incorrectly called terminal disposal.**
   Recovery now stops and re-initializes the runtime without calling `dispose()`. Disposal remains in the final shutdown path.

3. **ProviderRuntime closed the container-owned shared HTTP client.**
   Runtime stop no longer closes the shared `httpx.AsyncClient`.

4. **Docker entrypoint/port contract was invalid.**
   The Docker entrypoint now uses Uvicorn on `0.0.0.0:8000`, and Compose maps `8080:8000`.

## P1 fixes included

1. Normalize the `BaseRuntime.initialize()` contract to async and update the runtime implementations that call `super()`.
2. Unsubscribe runtime event handlers during stop so runtime recovery cannot duplicate subscriptions.
3. Await and clear the ConnectionRuntime heartbeat task during stop.
4. Introduce explicit CapabilityState transition validation.
5. Prevent definition-only capabilities from entering executable states.
6. Do not let a disabled/removed capability get re-enabled by health polling.
7. Treat MCP-only outages as `HealthStatus.DEGRADED` while marking the affected capability `UNAVAILABLE`; this avoids restarting the whole CapabilityRuntime for a remote MCP outage.
8. Remove the direct Capability→Infrastructure dependency by using an `McpSessionProvider` protocol.
9. Make MCP eager-connect timeout explicit instead of silently returning while disconnected.
10. Use the `uow_factory` passed to `bootstrap_runtime_kernel()` rather than silently replacing it with `eventing_manager.uow_factory`.

## Tests added/updated

- MCP health failure now asserts `DEGRADED`.
- Capability state transition validation.
- Disabled MCP capability remains disabled during healthy polling.
- RuntimeKernel recovery awaits async initialization and does not dispose.
- ProviderRuntime stop does not close the application-owned HTTP client.

## Scope boundary

No new HTTP endpoint, provider capability, credential implementation, versioning mechanism, or feature behavior is added by this patch.

## Apply

This artifact uses the repository's `apply_patch` format rather than a Git `format-patch` email patch.

```bash
apply_patch < phase_p0_p1_fix_v3.patch
```

Recommended focused regression run:

```bash
pytest -q \
  tests/architecture/test_phase3.py \
  tests/architecture/test_runtime_recovery.py \
  tests/architecture/test_provider_runtime_lifecycle.py
```

Then run the complete suite before merge:

```bash
pytest -q
```
