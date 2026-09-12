# Phase 6.1 / 6.2 Exit Checklist

## Contract

- [x] Separate `CapabilityDefinition` from `CapabilityImplementation`.
- [x] Define `SERVER`, `CLIENT`, `MCP`, `DECLARATIVE` execution locations.
- [x] Define `TOOL`, `SKILL`, `AGENT` semantic kinds.
- [x] Define ownership boundary.
- [x] Define connection-to-implementation binding.
- [x] Define realtime envelope and correlation IDs.
- [x] Define cancellation/progress/result message families.

## Repository safety

- [x] No modification to existing Phase 5 execution flow.
- [x] No modification to `CapabilityRegistry` in foundation slice.
- [x] No modification to `ConnectionRuntime` in foundation slice.
- [x] No modification to `AgentRuntime`.
- [x] No replacement of compatibility APIs.

## Required before Phase 6.1

- [x] Add catalog capable of storing multiple implementations per capability.
- [x] Add implementation lifecycle/state transitions.
- [x] Add ownership and authorization checks for client registrations.
- [x] Add capability routing policy.
- [x] Add tests for same capability with multiple implementations.
- [x] Add tests proving AgentRuntime is location-agnostic.

## Phase 6.1

- [x] Logical capability catalog.
- [x] Multiple concrete implementations per capability.
- [x] Implementation lifecycle.
- [x] Client owner/connection authorization.
- [x] Required-scope authorization.
- [x] Deterministic implementation routing.
- [x] Location-agnostic routing boundary.

Phase 6.1 remains control-plane only. It does not introduce WebSocket
transport, remote invocation, connection heartbeat, client execution,
or changes to the existing Phase 5 execution path.

## Phase 6.2

- [x] Connection identity separate from session identity.
- [x] Explicit lifecycle state machine.
- [x] Persistent state transitions.
- [x] Heartbeat liveness tracking.
- [x] Stale eviction.
- [x] Disconnect persistence.
- [x] Immutable connection snapshots.
- [x] Connection ID reuse prevention.
- [x] ConnectionRegistry uses lifecycle registry as source of truth.

## 6.1 / 6.2 integration gate

- [x] Client implementation requires owner type CLIENT.
- [x] Client implementation requires owner ID.
- [x] Client implementation requires connection ID.
- [x] Implementation version matches capability definition version.
- [x] Client routing requires ACTIVE connection.
- [x] STALE connection is not routable.
- [x] DISCONNECTED connection is not routable.
- [x] Disconnect does not delete logical capability definition.
- [x] AgentRuntime remains unchanged.

## Required before remote execution

- [ ] Authenticated connection handshake.
- [ ] Heartbeat/stale eviction.
- [ ] Invocation multiplexer.
- [ ] Timeout/cancel/disconnect semantics.
- [ ] Duplicate-result idempotency.
- [ ] End-to-end client registration/invoke/result tests.

## Exit rule

Phase 6.1/6.2 may advance to Phase 6.3 only when the control-plane catalog,
connection lifecycle, and their integration tests pass.

Phase 6.3 remains responsible for realtime multiplexing and invocation
correlation. No WebSocket invocation protocol is introduced by this gate.