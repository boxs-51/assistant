# Phase 5.10 Exit Gate

This is the canonical release gate for agent lifecycle event publication and correlation.

The executable gate is `se/tests/architecture/test_phase5_10_exit_gate.py`.

## P0

- **E1:** runtime publishes execution and iteration lifecycle events.
- **E2:** runtime publishes inference and tool lifecycle events.
- **E3:** every event preserves the required correlation chain.
- **E4:** publisher failures do not change the execution outcome.

## P1

- **E5:** the agent publisher adapts to the existing gateway EventBus.
- **E6:** application wiring exposes the event-enabled `AgentRuntime`.
- **E7:** CI runs the full suite and this gate as blocking checks.
- **E8:** legacy Phase 5.10 documentation points to this canonical gate.

Phase 5.10 closes only when E1-E8 and the full test suite pass.
