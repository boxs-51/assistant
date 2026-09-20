# R5-D Delegation Policy Exit Gate

**Baseline:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`

**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

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
Patch check: <pending>
Focused:     <pending>
Broad:       <pending>
```

## Phase status

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C COMPLETE
R5-D IMPLEMENTED / PENDING TEST EVIDENCE
R5-E PENDING
R5 overall NOT COMPLETE
```
