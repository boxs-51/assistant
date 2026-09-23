# AE-R9-A Completion — RETRY Representation & Durable Admission Identity

**Canonical implementation baseline:** `ae63a25a7d077dc0de1322525e70f2e74107895f`  
**AE-R9-A code HEAD:** `59042a7da922b8017d21f1e66fc78e0ec1331828`  
**Branch:** `work/ae-r9-a-ae63a25`  
**Issue:** #10  
**Draft PR:** #11  
**Status:** **COMPLETE / GREEN**

## Delivered

AE-R9-A introduces representation only. It does not activate RETRY runtime behavior.

### Immutable RETRY contracts

`se/src/runtimes/agent/contracts/retry.py`

- `RetryPlan`
- `RetryAdmission`
- `retry_plan_fingerprint()`

The logical `retry_request_id` is deliberately excluded from the semantic
fingerprint. Reusing the same request identity with semantic drift can
therefore be detected by comparing the committed receipt fingerprint to the
new plan fingerprint.

### Durable RETRY receipt

`agent_task_retry_admissions`

Canonical identity:

~~~text
(task_id, retry_request_id)
~~~

Stored immutable authority:

~~~text
plan_fingerprint
branch_id
source_execution_id
source_checkpoint_id nullable
execution_id unique
created_by
created_at
~~~

Migration chain:

~~~text
14d_r8_fork_runtime_seed
-> 15a_r9_retry_admission
~~~

### Repository primitives

Added:

~~~text
save_task_retry_admission(...)
get_task_retry_admission(...)
get_task_retry_admission_for_update(...)
get_task_retry_admission_by_execution(...)
~~~

No high-level RETRY mutation is exposed by these representation primitives.

### Task-scoped ResumeClaim locking

Added:

~~~text
list_created_resume_claims_for_task_for_update(task_id)
~~~

It joins ResumeClaim -> AgentExecution and returns only `CREATED` claims for
the exact Task in deterministic claim_id order.

AE-R9-A does not reject claims. AE-R9-E owns the terminal Task transaction
that will consume this primitive.

## Regression coverage

Added:

- `se/tests/architecture/test_r9_a_retry_contracts.py`
- `se/tests/integration/test_r9_a_retry_repository.py`
- `se/tests/integration/test_r9_a_retry_admission_migration.py`

Advanced R8 migration-head expectations while preserving the R8 ancestry
checks.

## CI evidence on AE-R9-A code HEAD

GitHub Actions on draft PR #11:

~~~text
Architecture Baseline / linux-full-suite
953 passed, 1 skipped, 47 warnings

Architecture Baseline / windows-client-contracts
68 passed

R8 Exit Gates
141 passed, 31 warnings

Phase 5 Exit Gates
40 passed
~~~

All jobs concluded SUCCESS.

Warnings are pre-existing/deprecation-class warnings; no test failure or new
R9 correctness warning was observed.

## Frozen invariants proved by A

~~~text
same logical retry has durable request identity
retry receipt cannot silently point at two executions
semantic plan fingerprint is deterministic
source/branch/checkpoint/execution lineage has normalized durable columns
R8 migration history remains one linear chain
R8 behavior is unchanged
Phase 5 behavior is unchanged
Task-scoped CREATED ResumeClaims can be locked for future atomic resolution
~~~

## Not implemented in A

- RETRY planning authority
- atomic same-branch RETRY admission
- execution activation/handoff
- DISCARD
- ADOPT/SUPERSEDE
- AGGREGATE
- public RETRY/resolution API

Those remain AE-R9-B onward.

## Exit decision

**AE-R9-A is CLOSED / GREEN.**

AE-R9-B may begin from the completion branch after this document commit.
