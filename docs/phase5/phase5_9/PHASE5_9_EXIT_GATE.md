# Phase 5.9 Exit Gate

This is the canonical release gate for Phase 5.9 persistence and resume.

The executable gate is `tests/architecture/test_phase5_9_exit_gate.py`.

## P0

- **E1:** execution, iteration, tool-call, and tool-result records persist with ownership links.
- **E2:** `load_execution()`, `load_iteration()`, and `resume_execution()` restore the latest checkpoint and pending calls.
- **E3:** committed tool results are idempotent by `tool_call_id`.

## P1

- **E4:** runtime checkpoints iterations, tool calls, and tool results through the durable store.
- **E5:** CI runs the full suite and this gate as blocking checks.
- **E6:** legacy Phase 5.9 status documentation points to this gate.

Phase 5.9 is closed only when all six criteria and the full test suite pass.