# R7-B Completion — Atomic WAITING Transaction

## Baseline

- Repository: `boxs-51/assistant`
- Closed implementation commit: `24f3c5fc808729cbe714e13650c37262bccfef48`
- Phase: `R7-B Atomic WAITING Transaction`
- Status: **CLOSED / IMPLEMENTATION EXIT GATE SATISFIED**
- Depends on: R7-A CLOSED

## Closure summary

R7-B establishes one normalized durable WAITING transaction. A successful RUNNING -> WAITING transition now commits the checkpoint, every pending invocation semantic snapshot, the AgentExecution revision/state/current_checkpoint_id update, and the task-scoped active-slot release inside one SQL UnitOfWork.

The implementation keeps R6 `CapabilityInvocation` as remote-side-effect authority. The runtime supplies only ordered logical pending references; the transaction reloads the authoritative invocation rows before freezing the checkpoint watermark.

## Applied scope

R7-B added or activated:

- transaction-scoped `CapabilityInvocationRepository` on `SqlAlchemyUnitOfWork`;
- forward-only migration `13b_r7_pending_snapshot` extending the already-applied 13a representation;
- complete pending-invocation semantic watermark:
  - invocation revision;
  - capability version;
  - request fingerprint;
  - idempotency;
  - observed remote outcome state;
  - origin client and connection identity;
- `stage_waiting_checkpoint()` and committed-checkpoint verification;
- atomic task-scoped WAITING through the existing TaskBudget transaction coordinator;
- atomic non-task WAITING through `DurableAgentStore.commit_waiting_checkpoint()`;
- deterministic checkpoint identity `<execution_id>:checkpoint:<target_revision>`;
- runtime staging of all remote-waiting invocations with their original batch ordinal;
- normalized checkpoint transcript captured before uncertain tool outcomes are appended.

## Frozen invariants satisfied by the implementation

1. `execution.state == WAITING` implies a normalized `current_checkpoint_id` for the canonical R7 writer.
2. `checkpoint.execution_revision == execution.revision`.
3. A lost AgentExecution CAS rolls back checkpoint rows and task-budget release.
4. Pending invocation snapshots are loaded from R6 authority in the same SQL transaction.
5. Multi-pending snapshots retain the original batch ordinal.
6. CONNECTION waits clear the active connection binding while retaining the durable client binding.
7. A retry after an uncertain commit verifies the already-committed checkpoint instead of minting another safe point.

## Exit-gate evidence in HEAD

The repository contains dedicated proof for the transaction boundary:

- `se/tests/architecture/test_r7_b_atomic_waiting.py`
  - task-scoped atomic success;
  - execution-update failure rollback;
  - non-task atomic WAITING;
- `se/tests/integration/test_r7_b_migration.py`
  - 13a -> 13b forward upgrade and downgrade;
- prior R5/R6/R7-A tests remain the regression boundary for TaskBudget and CapabilityInvocation authority.

GitHub exposes no workflow run or combined status for commit `24f3c5f`, and this review environment cannot execute the repository test suite. This completion document therefore records the audited implementation gate and does not invent pass counts. Before merging a downstream R7-C branch, run the focused and broad gates below.

## Verification commands

```powershell
py -m pytest -q `
  se/tests/architecture/test_r7_b_atomic_waiting.py `
  se/tests/integration/test_r7_b_migration.py `
  se/tests/architecture/test_r7_a_representation.py `
  se/tests/integration/test_r7_a_migration.py

py -m pytest -q `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py `
  se/tests/architecture/test_r6_c_server_reconciliation.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py `
  se/tests/integration/test_r5_c2_c4_atomic_task_budget.py

py -m pytest -q se/tests tools cl/tests
```

## Authority after R7-B

R7-B closes only the WAITING safe-point transaction. The following remain intentionally outside its authority:

- tool-result promotion and model-consumable commitment;
- checkpoint-directed transcript reconstruction;
- R6-aware ResumePlan decisions;
- existing-invocation continuation/replay;
- ResumeClaim consume/TOCTOU fencing;
- resume supervisor handoff/ACK ordering;
- ClientRuntime PendingResumeTicket;
- canonical removal of legacy branch/merge continuation.

## Next phase gate

R7-C may begin with these non-negotiable invariants:

> An AgentToolResult is model-consumable only when `commit_state == COMMITTED`. A `PROVISIONAL` row must never enter reconstructed model context.

> Reconstruction must use the checkpoint safe prefix plus the original `AgentIteration.tool_call_ids` ordering. Parallel completion timing or SQL row creation order must never reorder tool messages.
