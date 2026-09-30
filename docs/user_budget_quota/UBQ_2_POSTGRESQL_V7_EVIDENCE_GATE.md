# UBQ-2 PostgreSQL V7 Evidence Gate

Status: **OPEN / REQUIRED BEFORE POSTGRESQL UBQ-2 PRODUCTION ENABLEMENT**

UBQ-2 V7 freezes equivalent logical TaskBudget-incarnation behavior for SQLite
and PostgreSQL. The repository Architecture Baseline currently runs Python
tests on Linux and Windows without a PostgreSQL service, and `se/tests` has
no PostgreSQL pytest fixture. Therefore SQLite execution is **not** accepted as
cross-dialect proof.

The safe repository default remains:

```text
user_budget.dual_accounting.enabled = false
```

PostgreSQL UBQ-2 production support MUST remain disabled/unclaimed until an
executable PostgreSQL evidence run proves all of the following on the released
migration/runtime candidate:

1. monotonic allocator serialization under concurrent TaskBudget creators;
2. `MAX_INT64-1` commits as the last valid generation and atomically moves the
   allocator to the `MAX_INT64` exhausted sentinel;
3. rollback of an uncommitted final allocation restores the last-valid
   generation as available;
4. exact `(task_id, incarnation_generation)` TaskBudget/reservation relation;
5. TaskBudget and reservation incarnation identity is immutable;
6. parent TaskBudget delete is RESTRICTED while an incarnation-bound
   reservation exists, then succeeds after reservation deletion;
7. allocator singleton anti-delete/anti-reset/monotonicity rules hold;
8. a committed historical generation is never reused after source GC/recreate.

Acceptance evidence must identify the exact PostgreSQL version, exact commit
SHA, migration head, test commands/results, and concurrent/final-boundary
cases. Until that evidence is recorded, no release note, deployment runbook,
or operator may claim PostgreSQL UBQ-2 runtime parity merely from SQLite CI.

This is a deployment evidence gate, not authority to weaken V7 and not a
request to introduce PostgreSQL CI infrastructure inside UBQ-2.
