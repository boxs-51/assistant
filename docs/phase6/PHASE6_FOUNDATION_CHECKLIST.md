# Phase 6.0 Foundation Checklist

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

- [ ] Add catalog capable of storing multiple implementations per capability.
- [ ] Add implementation lifecycle/state transitions.
- [ ] Add ownership and authorization checks for client registrations.
- [ ] Add capability routing policy.
- [ ] Add tests for same capability with multiple implementations.
- [ ] Add tests proving AgentRuntime is location-agnostic.

## Required before remote execution

- [ ] Authenticated connection handshake.
- [ ] Heartbeat/stale eviction.
- [ ] Invocation multiplexer.
- [ ] Timeout/cancel/disconnect semantics.
- [ ] Duplicate-result idempotency.
- [ ] End-to-end client registration/invoke/result tests.

## Exit rule

Phase 6.0 is complete when the foundation contracts exist and are covered by
architecture tests, while the current Phase 5 execution path remains behaviorally
unchanged.
