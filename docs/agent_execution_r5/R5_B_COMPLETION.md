# R5-B TaskBudget Core — Formal Completion

**Repository:** `boxs-51/assistant`

**Committed implementation:** `d7dcbde5177fad17274b6049d80052bbb2e17b17`

**Status:** COMPLETE

## Completed scope

R5-B establishes the durable TaskBudget core:

```text
B1 domain representation
B2 SQL model + migration
B3 AgentTask / TaskBudget CAS
B4 TaskBudgetService + durable reservation ledger
```

## Frozen invariants now implemented

1. A legacy Task with durable AgentExecution history and no TaskBudget fails
   closed instead of receiving zeroed historical usage.
2. Alembic never invents TaskBudget rows for existing Tasks.
3. AgentTask and TaskBudget both expose revision/CAS primitives.
4. TaskBudget limits/policy are immutable and fingerprinted.
5. Durable cost accounting uses Decimal + NUMERIC(20,8).
6. `max_parallel_agents` counts active delegated child Agent executions.
7. Logical reservation identity is durable; revision CAS alone is not treated
   as idempotency.
8. New-execution reservation, AgentExecution insertion, and reservation-ledger
   insertion can be committed in one durable transaction.
9. CLOSED budget rejects new growth while idempotent release remains possible.

## Verification

```text
patch_applier --check    PASS
patch apply              15/15 files

focused gate:
27 passed, 2 warnings in 101.85s

full gate:
573 passed, 7 warnings in 401.55s

failures:
0
```

The warnings are known dependency/configuration deprecations and do not
invalidate R5-B contracts.

## Explicitly not claimed by R5-B

R5-B does not yet wire TaskBudget into live AgentRuntime transitions.

These remain R5-C:

```text
new execution admission through TaskBudget
WAITING -> RUNNING active-slot reacquire
RUNNING -> WAITING/terminal slot release
inference reservation
actual token/cost accounting
tool-call reservation
production DI/bootstrap
```

## Phase status

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C PENDING
R5-D PENDING
R5-E PENDING

R5 overall NOT COMPLETE
```
