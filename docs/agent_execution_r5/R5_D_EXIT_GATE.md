# R5-D Delegation Policy Exit Gate

**Baseline:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`

**Status:** COMPLETE

## Scope

```text
D1 durable delegation admission representation
D2 parent_execution_id ancestry traversal
D3 max_delegation_depth live enforcement
D4 recursive Agent cycle policy
D5 nested Agent durable task-scope requirement
D6 stable TaskBudget error propagation
```

## Frozen invariants

1. Delegation depth comes only from durable `parent_execution_id` edges.
2. Root execution depth is zero.
3. Retry/fork lineage never contributes to delegation depth.
4. Durable parent must exist and belong to the same Task.
5. Execution-id ancestry loops fail closed as `TASK_BUDGET_CONFLICT`.
6. Repeated Agent ID fails as `AGENT_DELEGATION_CYCLE` when policy denies
   recursion.
7. Policy allowing recursion does not disable the depth cap.
8. Agent -> Agent requires real Task scope.
9. Direct taskless root Agent remains valid.
10. Rejected child admission creates no execution row and charges no budget.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r5_d_agent_delegation_boundary.py `
  se/tests/integration/test_r5_d_delegation_policy.py `
  se/tests/integration/test_r5_c2_c4_atomic_task_budget.py `
  se/tests/integration/test_r5_c5_c6_runtime_accounting.py `
  se/tests/architecture/test_r5_b_task_budget_service.py `
  se/tests/architecture/test_r5_a4_a6_ownership_integration.py `
  se/tests/architecture/test_r4_c3_nested_agent_budget.py `
  se/tests/architecture/test_r3_execution_lineage.py `
  se/tests/architecture/test_unified_capability_drivers.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore e:\assistant\cl\tests\test_mcp_client_ownership.py
```

## Completion evidence

```text
Initial focused run:
    42 passed, 1 failed

Observed failure:
    test_r5_d_capability_runtime_preserves_task_budget_error_code

Root cause:
    TASK_BUDGET_REQUIRED was raised by AgentCapabilityDriver before the
    TaskBudgetError -> CapabilityError conversion boundary, so
    CapabilityRuntime normalized it to CAPABILITY_EXECUTION_FAILED.

Regression fix:
    R5_D_STABLE_ERROR_BOUNDARY_FIX_v1.patch

Targeted boundary gate after fix:
    4 passed in 1.02s

Focused R5-D/R5-C/R5-B/R5-A/R4/R3 gate:
    43 passed in 11.28s

Broad repository regression:
    592 passed, 7 warnings in 65.12s
    0 failures
    explicit out-of-scope exclusion:
        cl/tests/test_mcp_client_ownership.py

Warnings:
    Starlette anyio BlockingPortal deprecation
    passlib argon2 version deprecation
    Starlette HTTP_422 constant deprecation
    Alembic path_separator deprecation
```

## Phase status

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C COMPLETE
R5-D COMPLETE
R5-E PENDING
R5 overall NOT COMPLETE
```
