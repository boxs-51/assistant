# Phase 5.10 Task Checklist

## Objective

Close Phase 5.10 by making agent lifecycle events a controlled runtime side effect with stable correlation metadata.

## P0: Event publication

- [x] Inject an `AgentEventPublisher` into `AgentRuntime`.
- [x] Publish execution started, completed, failed, cancelled, and timeout events.
- [x] Publish iteration started and completed events.
- [x] Publish inference requested and completed events.
- [x] Publish tool requested, started, completed, and failed events.
- [x] Preserve `correlation_id`, `session_id`, `execution_id`, `iteration_id`, `tool_call_id`, and `invocation_id`.
- [x] Event publication failures do not corrupt the execution result.

## P1: Integration and release safety

- [x] Adapt agent envelopes to the existing gateway EventBus.
- [x] Wire the event-enabled `AgentRuntime` into the application container.
- [x] Add behavioral lifecycle and correlation tests.
- [x] Add a canonical exit gate and blocking CI workflow.
- [x] Point legacy Phase 5.10 documentation to the canonical gate.

## Exit criteria

Phase 5.10 closes only when the dedicated gate and full suite pass, and every P0/P1 item above is verified by code or an executable test.
