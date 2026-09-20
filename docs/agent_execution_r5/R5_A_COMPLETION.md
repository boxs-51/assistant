# R5-A Async Ownership — Formal Completion

**Repository:** `boxs-51/assistant`

**Implementation baseline:** `65b9a6afcf1ee9506ac4795e880aaaeae48f6fb6`
plus the applied `R5_A4_A6_ASYNC_OWNERSHIP_WIRING_v1.patch`.

**Status:** COMPLETE

## Scope completed

R5-A closes process-local async ownership for the Agent execution graph:

```text
A1 regression tests for outer-cancellation ownership
A2 low-level child-task cancel + drain cleanup
A3 AgentExecutionSupervisor
A4 production Supervisor wiring / durable-begin ownership / resume activation
A5 coordinator and EventDispatcher background-task ownership + shutdown drain
A6 focused and repository-wide exit-gate evidence
```

TaskBudget is not part of this completion.

## Frozen completion invariants

The implementation and tests now establish:

1. Each live AgentExecution has one process-local Supervisor owner.
2. Parent and child Agent executions use distinct cancellation events.
3. Parent cancellation propagates down the local execution tree; child
   cancellation does not cancel its parent.
4. Production AgentRuntime entry points are Supervisor-owned.
5. Outer cancellation cannot abandon ProviderInferenceAdapter,
   CapabilityRuntime driver, AgentRuntime contextual child, or tool gather
   Tasks.
6. Durable Agent begin/resume-claim is owned across caller cancellation.
7. If a process-local resume activation step fails after a successful durable
   claim but before owned Agent start, the execution fails closed instead of
   remaining RUNNING without an owner.
8. Whole-process death in the resume claim/start window remains explicitly
   deferred to R7 durable claim lease / stale-RUNNING recovery.
9. MultiAgentCoordinator production cancellation cancels and drains its
   background runner and Supervisor task scope.
10. EventDispatcher owns, observes, cancels and drains dispatch workers during
    shutdown.
11. Application shutdown quiesces new Agent admission before draining live
    Agent and event-worker ownership.

## Verification evidence

Patch verification:

```text
patch_applier.py --check: PASS
patch application: 16/16 files successful
```

Focused R5-A/R3/R4 regression gate:

```text
39 passed in 10.17s
```

Repository-wide gate:

```text
555 passed, 5 warnings in 84.73s
0 failures
```

The five counted warnings are pre-existing deprecation/configuration warnings:

```text
Starlette / anyio BlockingPortal deprecation
passlib argon2 version deprecation
Starlette HTTP 422 constant deprecation
Alembic path_separator deprecation (2 occurrences)
```

After the pytest summary, Windows also emitted two
`PytestUnraisableExceptionWarning` messages from asyncio Proactor transport
destructors:

```text
BaseSubprocessTransport.__del__
_ProactorBasePipeTransport.__del__
ValueError: I/O operation on closed pipe
```

These warnings are not classified as an R5-A blocker because the captured
trace does not identify an Agent-owned Task or one of the ownership surfaces
changed by R5-A.  They remain an explicit follow-up resource-cleanup issue.

## Follow-up for the Windows Proactor warning

The repository's strongest current suspect is Playwright-backed
`tools/v1/web_tool`, because it is the only searched code path that explicitly
mentions Windows Proactor subprocess-pipe cleanup.

This remains a hypothesis, not a root-cause conclusion.

Suggested isolation commands:

```powershell
py -X tracemalloc=25 -m pytest -q tools/v1/test/test_web_tool.py `
  -W error::pytest.PytestUnraisableExceptionWarning

py -X tracemalloc=25 -m pytest -q tools `
  -W error::pytest.PytestUnraisableExceptionWarning
```

If neither reproduces it, bisect the full suite by top-level group before
changing production cleanup code.

## R5 status after closure

```text
R5-A Async Ownership      COMPLETE
R5-B TaskBudget           NOT STARTED
R5-C Runtime accounting   NOT STARTED
R5-D Delegation policy    NOT STARTED
R5-E Task cancellation    NOT STARTED

R5 overall                NOT COMPLETE
```

The next implementation phase is R5-B TaskBudget representation, migration,
repository CAS and reservation-service contract work.  R5-B must retain the
P0 corrections already frozen in the implementation-plan review, especially
legacy-task handling, transactional reservation with AgentExecution creation,
idempotency identity and atomic active-slot state transitions.
