# R5-E Durable Task Cancellation / CAS Exit Gate

**Dependency:** R5-D COMPLETE

**Status:** COMPLETE

## Scope

```text
E1 AgentTask production CAS transitions
E2 atomic terminal Task + TaskBudget CLOSED
E3 durable cancellation-before-local-drain ordering
E4 cancel/completion race no-resurrection semantics
E5 CLOSED budget invalidates future growth/resume admission
```

## Frozen invariants

1. Correctness-sensitive R5 coordinator paths no longer use unconditional
   `update_task()` when TaskBudgetService is present.
2. ASSIGNED -> RUNNING is CAS-protected.
3. RUNNING -> WAITING is CAS-protected and leaves TaskBudget OPEN.
4. RUNNING -> COMPLETED/FAILED/CANCELLED atomically closes TaskBudget.
5. ASSIGNED/WAITING -> CANCELLED atomically closes TaskBudget.
6. Existing terminal Task is authoritative; late completion/cancel cannot
   rewrite it.
7. Task cancellation closes the budget before process-local cancel/drain.
8. CLOSED budget rejects future new execution/resume/inference/tool/branch
   reservations.
9. Already-active AgentRuntime owners may still release active slots while
   budget is CLOSED.
10. WAITING Task cannot start a new AgentExecution; it must resume the same
    execution through the continuation path.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/integration/test_r5_e_task_cancellation_cas.py `
  se/tests/architecture/test_r5_d_agent_delegation_boundary.py `
  se/tests/integration/test_r5_d_delegation_policy.py `
  se/tests/integration/test_r5_c2_c4_atomic_task_budget.py `
  se/tests/integration/test_r5_c5_c6_runtime_accounting.py `
  se/tests/architecture/test_r5_b_task_budget_service.py `
  se/tests/architecture/test_r5_a4_a6_ownership_integration.py `
  se/tests/architecture/test_roadmap_r0_r2.py `
  se/tests/architecture/test_phase4_multi_agent.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore e:\assistant\cl\tests\test_mcp_client_ownership.py
```

## Completion evidence

```text
Focused R5-E plus R5-D/R5-C/R5-B/R5-A/R0-R4 regressions:
    49 passed in 13.84s

Broad repository regression:
    598 passed, 7 warnings in 61.74s
    0 failures
    explicit out-of-scope exclusion:
        cl/tests/test_mcp_client_ownership.py

Warnings:
    Starlette anyio BlockingPortal deprecation
    passlib argon2 version deprecation
    Starlette HTTP_422 constant deprecation
    Alembic path_separator deprecation
```

## Phase status after completion

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C COMPLETE
R5-D COMPLETE
R5-E COMPLETE
R5 overall COMPLETE
```
