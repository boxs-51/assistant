# AE-R9-B Implementation — Atomic Same-Branch RETRY Admission

## Scope

R9-B implements read-only retry planning, transactional revalidation and one
atomic admission operation. The operation binds Task, TaskBudget, OPEN branch,
terminal source execution, new execution, NEW_EXECUTION reservation and the
immutable RetryAdmission receipt.

## Transaction

1. Lock/revalidate Task and TaskBudget.
2. Lock/revalidate the OPEN branch and current terminal source execution.
3. Validate optional checkpoint and fail closed on unsafe capability effects.
4. CAS Task and TaskBudget counters.
5. Insert `RUNNING@1` E2 in the same branch and move the branch head E1 -> E2.
6. Insert the NEW_EXECUTION reservation and retry receipt.
7. Commit once; no activation occurs before commit.

Replay reads the receipt first and returns the same execution. A reused request
ID with a different semantic fingerprint returns `RETRY_REQUEST_CONFLICT`.

## Verification plan

- Same branch/new execution lineage and unchanged `active_branches`.
- Concurrent same-request replay charges execution capacity once.
- Semantic drift conflicts.
- Forced receipt insertion failure rolls back counters, execution and branch
  head together.
