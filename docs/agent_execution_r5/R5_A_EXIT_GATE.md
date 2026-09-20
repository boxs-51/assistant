# R5-A Async Ownership Exit Gate

**Baseline:** `65b9a6afcf1ee9506ac4795e880aaaeae48f6fb6`  
**Status:** COMPLETE

## Scope

R5-A closes process-local async ownership only:

- outer cancellation cleanup
- AgentExecutionSupervisor
- singleton production wiring
- distinct nested Agent cancellation scope
- owned durable begin/claim
- process-local resume activation fail-closed cleanup
- coordinator runner cancel/drain
- EventDispatcher worker tracking/drain
- application quiesce/drain shutdown order

R5-A does **not** implement TaskBudget or distributed resume/execution leases.

## Frozen invariants

1. One live/reserved process-local owner per `execution_id`.
2. Parent and child Agent executions never share one mutable cancellation event.
3. Every production `AgentRuntime.execute()` is Supervisor-owned.
4. `_begin_durable_execution()` is never abandoned on outer cancellation.
5. A process-local failure after successful resume claim but before owned start
   fails the durable execution closed instead of leaving it RUNNING.
6. Full-process death after claim is explicitly deferred to R7 stale-RUNNING /
   claim-lease recovery.
7. MultiAgent background runners are cancelled and drained before production
   cancellation returns.
8. EventDispatcher owns and drains every dispatch worker.
9. Shutdown quiesces new Agent admissions before draining live execution.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r5_a_async_ownership.py `
  se/tests/architecture/test_r5_a_agent_execution_supervisor.py `
  se/tests/architecture/test_r5_a4_a6_ownership_integration.py `
  se/tests/architecture/test_r3_c1_c3_execution_id_authority.py `
  se/tests/architecture/test_r4_b2_resume.py `
  se/tests/architecture/test_r4_c3_nested_agent_budget.py `
  se/tests/architecture/test_r4_exit_gate.py
```

## Full gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Completion evidence

```text
Patch check: PASS
Patch apply: 16/16 files
Focused: 39 passed in 10.17s
Full:    555 passed, 5 warnings in 84.73s
Failures:0
Warnings:5 counted deprecation warnings
```

Two post-suite Windows asyncio unraisable transport warnings were also emitted:

```text
BaseSubprocessTransport.__del__
_ProactorBasePipeTransport.__del__
ValueError: I/O operation on closed pipe
```

These are tracked as an unresolved Windows subprocess/transport resource
cleanup issue.  They are not evidence of an R5-A Agent ownership invariant
failure:

- no focused/full test failed;
- no `Task exception was never retrieved` was emitted;
- Supervisor ownership tests drained to an empty registry;
- coordinator cancellation/drain tests passed;
- EventDispatcher worker drain tests passed.

The repository contains Playwright-backed `tools/v1/web_tool` code and an
existing comment about Windows Proactor subprocess-pipe cleanup, making that
tooling path a plausible follow-up target.  The current traceback only points
to asyncio transport destructors, however, so the root cause remains
unattributed until a targeted tracemalloc/reproduction run identifies the
creator.

## Status after implementation

```text
R5-A1 COMPLETE
R5-A2 COMPLETE
R5-A3 COMPLETE
R5-A4 COMPLETE
R5-A5 COMPLETE
R5-A6 COMPLETE

R5-A Async Ownership COMPLETE

R5-B TaskBudget NOT STARTED
R5 overall NOT COMPLETE
```
