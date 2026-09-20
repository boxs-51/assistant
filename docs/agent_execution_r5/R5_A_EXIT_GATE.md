# R5-A Async Ownership Exit Gate

**Baseline:** `65b9a6afcf1ee9506ac4795e880aaaeae48f6fb6`  
**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

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

Fill after execution:

```text
Focused: <pending>
Full:    <pending>
Failures:<pending>
Warnings:<pending>
```

The known Windows `BaseSubprocessTransport` /
`_ProactorBasePipeTransport.__del__` warnings must remain recorded separately
unless tracemalloc proves they originate from an Agent-owned async task.

## Status after implementation

```text
R5-A1 COMPLETE
R5-A2 COMPLETE
R5-A3 COMPLETE
R5-A4 IMPLEMENTED
R5-A5 IMPLEMENTED
R5-A6 PENDING TEST EVIDENCE

R5-B TaskBudget NOT STARTED
R5 overall NOT COMPLETE
```
