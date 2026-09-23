# AE-R9-D Implementation — DISCARD

## Scope and transaction

`TaskBudgetService.discard_branch()` locks Task, TaskBudget and every TaskBranch
in deterministic `branch_id` order. It permits only `OPEN -> DISCARDED`, rejects
the final OPEN branch, decrements `active_branches` exactly once, and leaves the
Task state/output untouched.

Replaying an already-DISCARDED branch is idempotent. Every other resolved state
is monotonic and returns `BRANCH_NOT_OPEN`.

## Verification plan

- Two OPEN branches become one OPEN plus one DISCARDED.
- Task remains nonterminal and has no output.
- Replay does not decrement counters again.
- Attempting to discard the final OPEN branch fails closed.
