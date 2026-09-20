# Phase 5.11 Exit Gate

This is the canonical release gate for Context Integration.

The executable gate is `se/tests/architecture/test_phase5_11_exit_gate.py`.

## P0

- **E1:** the ContextBuilder boundary loads session context through ContextRuntime/ContextEngine.
- **E2:** snapshots are immutable and carry the correct execution/iteration identity.
- **E3:** history, agent instructions, and tool results are composed deterministically.
- **E4:** tool visibility and authorization are enforced before tools enter the snapshot.
- **E5:** invalid identity, unavailable context runtime, cancellation, and deadline conditions fail closed.

## P1

- **E6:** application wiring exposes `context_builder_port`, and AgentRuntime consumes that port.
- **E7:** snapshot metadata preserves agent/session/trace correlation.
- **E8:** CI runs the full suite and this gate as blocking checks.
- **E9:** legacy Phase 5.11 documentation points to this canonical gate.

Phase 5.11 closes only when E1-E9 and the full test suite pass.
