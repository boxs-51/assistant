# Phase 5.11 Task Checklist

## Objective

Close Phase 5.11 by making context construction a typed, immutable per-iteration boundary:

```text
AgentRuntime -> ContextBuilderPort -> ContextRuntime/ContextEngine -> AgentContextSnapshot
```

## P0: Context integration boundary

- [x] `ContextBuilderAdapter` implements `ContextBuilderPort`.
- [x] Context loading is delegated to `ContextRuntime`'s `ContextEngine`.
- [x] `AgentContextSnapshot` is immutable and scoped to one execution/iteration.
- [x] Session history and the current agent instruction are composed into the snapshot.
- [x] Tool results are converted into inference tool messages.
- [x] Available tools are filtered by visibility, authorization, registry presence, and executability.
- [x] Execution identity and iteration mismatches fail closed.
- [x] Context cancellation/deadline is checked before loading context.
- [x] Snapshot metadata preserves agent, session, and trace correlation.

## P1: Integration and release safety

- [x] `ContextBuilderPort` is wired into the application container.
- [x] `AgentRuntime` consumes the port rather than accessing `ContextEngine` directly.
- [x] Behavioral adapter and runtime tests cover the production boundary.
- [x] Canonical exit gate and blocking CI workflow exist.
- [x] Legacy Phase 5.11 documentation points to the canonical gate.

## Exit criteria

Phase 5.11 closes only when the dedicated gate and full suite pass, and every P0/P1 item above is verified by implementation or an executable test.
