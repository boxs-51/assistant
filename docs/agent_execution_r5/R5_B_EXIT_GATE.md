# R5-B TaskBudget Core Exit Gate

**Baseline:** `1f6fd54a15872005a971927e8597f55c5a343f40`

**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

## Scope

R5-B implements:

```text
B1 TaskBudget domain representation
B2 SQL model + Alembic migration
B3 AgentTask / TaskBudget repository and DurableAgentStore CAS
B4 TaskBudgetService + durable reservation idempotency ledger
```

R5-B does not wire TaskBudget into AgentRuntime dispatch/state transitions.
That integration belongs to R5-C.

## Corrected invariants

1. Legacy Task with durable execution history and no budget fails closed.
2. Alembic never invents policy limits or backfills zero-use budget rows.
3. Existing budget limits/policy are immutable and fingerprinted.
4. Cost uses `Decimal` + `NUMERIC(20,8)`.
5. `max_parallel_agents` counts active delegated child Agents, not the root.
6. Revision CAS protects Task and TaskBudget mutations.
7. Logical reservation identity is durable; CAS alone is not treated as
   idempotency.
8. New execution budget reservation + AgentExecution insert + reservation
   ledger insert are one transaction.
9. R5-B resume/release primitives are not production-wired until R5-C can
   atomically couple them to AgentExecution state CAS.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r5_b_task_budget_domain.py `
  se/tests/integration/test_r5_b_task_budget_migration.py `
  se/tests/architecture/test_r5_b_task_budget_cas.py `
  se/tests/architecture/test_r5_b_task_budget_service.py `
  se/tests/architecture/test_r5_a4_a6_ownership_integration.py `
  se/tests/architecture/test_r4_exit_gate.py
```

## Full gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Completion evidence

```text
Patch check: <pending>
Focused:     <pending>
Full:        <pending>
```

## Phase status

```text
R5-A COMPLETE
R5-B1 IMPLEMENTED
R5-B2 IMPLEMENTED
R5-B3 IMPLEMENTED
R5-B4 IMPLEMENTED
R5-B PENDING TEST EVIDENCE

R5-C PENDING
R5-D PENDING
R5-E PENDING
R5 overall NOT COMPLETE
```
