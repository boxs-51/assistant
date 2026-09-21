# R7-B — Atomic WAITING Transaction Audit + Implementation Plan

## Baseline

- Repository: `boxs-51/assistant`
- Audited HEAD: `5fe15ee5c7490d005f58e482ba5919645995dfd0`
- Depends on: R7-A CLOSED
- Target: canonical atomic normalized WAITING writer

## Exact call-site audit

### `AgentRuntime._finish_durable_execution`

Current runtime freezes active budget and computes WAIT TTL, then calls `_transition_running_durable`. It does not construct or commit a normalized R7 checkpoint.

### `AgentRuntime._transition_running_durable`

Task-scoped transitions call `TaskBudgetService.finish_task_scoped_execution`; non-task transitions call `DurableAgentStore.compare_and_set_execution`. Neither path currently includes the normalized checkpoint transaction.

### `TaskBudgetService.finish_task_scoped_execution`

R5 already owns the correct transaction boundary for task-scoped RUNNING -> WAITING/terminal transitions. It decrements active capacity and delegates to `_transition_execution_with_budget`.

### `TaskBudgetService._transition_execution_with_budget`

This is the critical existing transaction coordinator. Inside one UoW it currently performs:

1. reservation lookup/idempotency check;
2. AgentExecution state/revision validation;
3. TaskBudget load/mutation/CAS;
4. AgentExecution CAS;
5. TaskBudget reservation insert;
6. commit.

R7-B extends this transaction instead of creating a second coordinator.

### `DurableAgentStore`

Non-task execution CAS opens its own UoW. R7-B adds a dedicated `commit_waiting_checkpoint` transaction for non-task executions.

### `SqlAlchemyUnitOfWork`

Current UoW exposes `.agents` but not a transaction-scoped capability invocation repository. R7-B adds `.capability_invocations` so checkpoint snapshots are read from the same SQL session as AgentExecution and TaskBudget.

### `CapabilityInvocation`

R6 already stores the fields required for a safe checkpoint watermark: invocation revision, capability version, idempotency, request fingerprint, remote outcome, origin client, current connection, execution_id and tool_call_id.

## R7-A.1 forward repair required before writer activation

`13a_r7_durable_resume` has already been applied. It must remain immutable. Revision `13b_r7_pending_snapshot` adds the missing pending snapshot columns and validation constraints. This is a forward-only contract completion, not a rewrite of R7-A history.

## Frozen R7-B transaction

For execution `E@N` transitioning `RUNNING -> WAITING@N+1`:

```text
BEGIN SQL UoW
  load E and require RUNNING@N
  [task-scoped] load/open TaskBudget
  [task-scoped] CAS TaskBudget active counters down

  build checkpoint C for revision N+1
  load each referenced CapabilityInvocation in this SAME UoW
  validate invocation execution/tool/capability identity
  insert C
  insert every pending invocation semantic snapshot

  CAS E RUNNING@N -> WAITING@N+1
      current_checkpoint_id = C.id
      wait_reason/budget/wait TTL = frozen values
      bound_connection_id = NULL for CONNECTION wait

  [task-scoped] insert RELEASE_EXECUTION reservation
COMMIT
```

Any insert/validation/CAS/constraint failure rolls the whole UoW back.

## Normalized checkpoint ID

Canonical writer uses deterministic identity:

```text
<execution_id>:checkpoint:<target_revision>
```

This gives one stable safe-point identity for an uncertain commit retry and avoids minting a second checkpoint for the same durable transition.

## Pending invocation snapshot

The runtime supplies only ordered logical references. The SQL transaction reloads R6 authority and freezes:

- invocation_id
- invocation_revision
- tool_call_id
- capability_id
- capability_version
- request_fingerprint
- idempotency
- observed_remote_outcome_state
- origin_client_id
- origin_connection_id

Caller-supplied R6 revisions/states are not trusted.

## Transcript rule for R7-B

The normalized checkpoint captures the model transcript immediately before provisional remote tool outcomes are appended. Provisional transport-error tool messages are therefore not copied into the normalized safe point. Full committed-result reconstruction remains R7-C/R7-D work.

## Multi-pending rule

The legacy continuation API still receives the first disconnected invocation for compatibility, but the normalized R7 staging context records **all** remote-waiting results with their original batch ordinal. R7-B therefore removes `remote_waiting[0]` as the normalized persistence authority.

## Files changed by implementation

1. `se/src/runtimes/agent/contracts/resume.py`
2. `se/src/runtimes/agent/contracts/context.py`
3. `se/src/infrastructure/storage/models/sql/agent/checkpoint.py`
4. `se/src/infrastructure/storage/migrations/sql/versions/13b_r7_pending_snapshot.py`
5. `se/src/infrastructure/storage/repositories/capability_invocations.py`
6. `se/src/infrastructure/storage/core/unit_of_work.py`
7. `se/src/runtimes/agent/waiting_checkpoint.py`
8. `se/src/runtimes/agent/persistence.py`
9. `se/src/runtimes/agent/task_budget.py`
10. `se/src/runtimes/agent/runtime.py`
11. `se/tests/architecture/test_r7_b_atomic_waiting.py`
12. `se/tests/integration/test_r7_b_migration.py`
13. `docs/agent_execution_r7/R7_A_COMPLETION.md`
14. `docs/agent_execution_r7/R7_B_ATOMIC_WAITING_AUDIT_IMPLEMENTATION_PLAN.md`

## Explicitly out of scope

R7-B does not implement:

- committed/provisional projection promotion (R7-C);
- ResumePlan/reconciliation matrix (R7-D);
- existing-invocation continuation API (R7-E);
- ResumeClaim consume/TOCTOU fencing (R7-F);
- ACK/supervisor handoff protocol (R7-G);
- client PendingResumeTicket (R7-H);
- removal of legacy branch/merge authority.

## Exit gate

R7-B is complete when tests prove:

1. successful task-scoped WAITING commits Budget + checkpoint + all snapshots + execution CAS together;
2. a failure at execution CAS rolls back checkpoint inserts and TaskBudget release;
3. non-task WAITING commits checkpoint + snapshots + execution CAS together;
4. every pending invocation supplied by the runtime is snapshotted from R6 authority inside the same UoW;
5. generic/legacy WAITING with no pending invocation remains compatibility-valid during the migration window;
6. execution.current_checkpoint_id equals the committed checkpoint;
7. checkpoint.execution_revision equals AgentExecution.revision;
8. multiple pending invocations preserve original batch ordinal;
9. migration 13b upgrades from applied 13a and downgrades cleanly;
10. R5/R6/R7-A regression and full suite remain green.
