# AE-R9-E Implementation — ADOPT and Task Completion

## Scope

ADOPT is the only initial R9 operation that chooses Task result authority. The
selected branch must be OPEN and its current execution must be COMPLETED with a
durable result.

## Winner transaction

1. Lock Task and TaskBudget.
2. Lock all branches in deterministic `branch_id` order.
3. Lock and validate the selected current execution.
4. Lock all task-scoped CREATED ResumeClaims.
5. CAS Task to COMPLETED with the selected execution result.
6. Close TaskBudget and set `active_branches` to zero.
7. Resolve selected OPEN branch as ADOPTED and every other OPEN branch as
   SUPERSEDED.
8. Reject every locked CREATED ResumeClaim with `TASK_RESOLVED`.
9. Commit once.

Any losing CAS rolls the entire transaction back. A late loser execution may
finish its own history but cannot rewrite the terminal Task output.

## Verification plan

- Selected output is frozen on Task.
- Other OPEN branches become SUPERSEDED.
- TaskBudget closes with zero active branches.
- CREATED claims become REJECTED atomically.
- Two simultaneous ADOPT attempts produce exactly one winner.
