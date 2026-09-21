# R7-A — Representation + Migration Exact Call-Site Audit and Implementation Plan

**Repository:** `boxs-51/assistant`  
**HEAD audited:** `0e7aff938e7d560ba502d62f0690fdd4b7aea388`  
**Contract baseline:** `R7-0 HEAD Audit + Contract Freeze v3`  
**Scope:** R7-A only — representation, SQL schema, transaction-scoped repository primitives, migration and tests.  
**Explicitly out of scope:** canonical WAITING transaction, ResumePlan construction, R6 reconciliation decisions, claim consumption, supervisor handoff, WebSocket protocol switch, client PendingResumeTicket, legacy continuation deletion.

## 1. Exact call-site findings

### `se/src/domain/schemas/agent_execution.py`
`AgentExecution` is the public durable execution representation. It currently has R3 lineage and R4 budget/TTL fields but no normalized R7 pointer or durable routing binding. R7-A must add nullable `current_checkpoint_id`, `bound_client_id`, and `bound_connection_id`. They stay nullable so migration does not invent checkpoint/routing certainty for historical rows.

### `se/src/infrastructure/storage/models/sql/agent/execution.py`
`AgentExecutionRecord` mirrors the durable execution authority and currently lacks the three R7 fields. R7-A adds them as nullable indexed columns. No FK is added from `current_checkpoint_id` to the checkpoint table in R7-A because it creates a circular migration dependency and because R7-B, not R7-A, owns the semantic WAITING/current-checkpoint invariant.

### `se/src/runtimes/agent/contracts/continuation.py`
This file is still the Phase 6.9 legacy branch/merge representation (`ContinuationBranch`, `READY_TO_MERGE`, singular pending invocation). R7-A must not mutate it because current runtime/tests still import it. A new normalized contract module is introduced beside it. Legacy deletion is deferred to R7-H.

### `se/src/infrastructure/storage/models/sql/agent/tool_result.py`
The table currently has no distinction between a true terminal tool outcome and a remote ambiguity projection. `AgentRuntime._persist_tool_result()` persists all `ToolExecutionResult` objects before checking the `remote_waiting` set. Therefore historical rows with `REMOTE_CONNECTION_LOST`, `REMOTE_OUTCOME_UNKNOWN`, `REMOTE_RESULT_RECONCILIATION_REQUIRED`, or normalized `CAPABILITY_EXECUTION_FAILED` carrying `metadata.original_error_code=REMOTE_CONNECTION_LOST` cannot safely be treated as committed. R7-A adds `commit_state = PROVISIONAL|COMMITTED` with fail-safe default `PROVISIONAL`.

### `se/src/runtimes/agent/runtime.py` lines around `_persist_tool_result`, `_load_committed_tool_result`, and the `remote_waiting` branch
R7-A deliberately does not change these call-sites. They establish why the new column and migration backfill are required. R7-C will change reads so only `COMMITTED` rows are reusable and will upgrade/downgrade projections based on R6 authority.

### `se/src/runtimes/agent/persistence.py` `save_tool_result()` / `load_tool_result()`
The store currently deduplicates solely by `(execution_id, tool_call_id)` and returns any row as if committed. R7-A does not switch semantics here; doing so before ResumePlan/R6 reconciliation is wired would break the current resume path. The new SQL field is intentionally passive until R7-C.

### `se/src/infrastructure/storage/repositories/agent.py`
This is already the transaction-scoped repository used by `SqlAlchemyUnitOfWork.agents`. It is the correct location for normalized checkpoint/pending-invocation/claim primitives because R7-B and R7-D must compose them inside the same UoW as execution and TaskBudget CAS. R7-A adds CRUD and claim-CAS primitives here rather than adding high-level `DurableAgentStore` methods that would each open separate transactions.

### `se/src/infrastructure/storage/core/unit_of_work.py`
No code change is required in R7-A. `SqlAlchemyUnitOfWork` already exposes `AgentRepository` as `uow.agents`; adding primitives to `AgentRepository` immediately makes them available inside the existing transaction boundary. Capability invocation participation remains an R7-B/R7-C concern.

### `se/src/runtimes/agent/task_budget.py` `_transition_execution_with_budget()`
This is the existing R5 atomic execution+TaskBudget boundary. R7-A does not modify it. R7-B will factor/extend this boundary so checkpoint insertion joins RUNNING→WAITING and claim consumption joins WAITING→RUNNING without split commits.

### `se/src/infrastructure/storage/repositories/capability_invocations.py`
The R6 store owns its own UoW. R7-A does not refactor it yet. R7-C must introduce transaction-scoped capability-invocation access or equivalent primitives before the ResumePlan/claim transaction can fence invocation revisions without a TOCTOU gap.

### `se/src/transport/gateway/api/v1/events_router.py::_resume_execution`
The current ordering remains legacy (`reconnect -> claim_resume -> confirm_merge -> ACK -> start_reserved`). R7-A must not touch it. R7-F will replace the protocol after R7-B through R7-E are available.

## 2. Frozen R7-A schema

### `agent_executions`
Add nullable/indexed:

- `current_checkpoint_id VARCHAR(255)`
- `bound_client_id VARCHAR(255)`
- `bound_connection_id VARCHAR(255)`

Migration must not backfill these fields.

### `agent_tool_results`
Add:

- `commit_state VARCHAR(16) NOT NULL DEFAULT 'PROVISIONAL'`
- check constraint: `PROVISIONAL|COMMITTED`
- index on `commit_state`

Historical backfill is fail-safe:

- successful rows -> `COMMITTED`
- failed rows with a concrete non-ambiguity error code -> `COMMITTED`
- `REMOTE_CONNECTION_LOST`, `REMOTE_OUTCOME_UNKNOWN`, `REMOTE_RESULT_RECONCILIATION_REQUIRED` -> `PROVISIONAL`
- rows whose JSON metadata contains `REMOTE_CONNECTION_LOST` -> `PROVISIONAL`
- rows without enough certainty remain `PROVISIONAL`

The server default stays `PROVISIONAL`; R7-C will explicitly commit rows when durable outcome authority exists.

### `agent_execution_checkpoints`
Immutable normalized checkpoint representation:

- `checkpoint_id` PK
- `execution_id` FK -> `agent_executions.id`, indexed
- `execution_revision >= 0`
- session/task/branch lineage snapshot
- parent checkpoint id
- iteration >= 0
- wait reason
- remaining active budget and wait expiry
- origin stable client and connection generation
- transcript snapshot/ref/version
- side-effect watermark reference
- metadata JSON
- nullable unique `legacy_source_key`
- created timestamp

At least one transcript representation (`transcript_snapshot` or `transcript_ref`) is required.

### `agent_checkpoint_pending_invocations`
One checkpoint may own N pending invocations:

- `(checkpoint_id, ordinal)` PK
- `invocation_id`
- `invocation_revision >= 0`
- `tool_call_id`
- `capability_id`
- unique `(checkpoint_id, invocation_id)`
- unique `(checkpoint_id, tool_call_id)`

`ordinal` preserves original tool-call ordering. No FK to `capability_invocations` is added in R7-A so legacy materialization can fail safely instead of making migration dependent on complete R6 history.

### `agent_resume_claims`
Durable claim intent/idempotency representation:

- `claim_id` PK
- `execution_id` FK
- `checkpoint_id` FK
- globally unique `resume_request_id`
- `expected_execution_revision >= 0`
- user/client/connection identity
- wait reason + trigger type
- `state = CREATED|CONSUMED|REJECTED|EXPIRED`
- `revision >= 0`
- non-null `plan_fingerprint`
- optional rejection code + metadata
- `claim_expires_at`
- terminal timestamps
- `consumed_execution_revision`

There is no durable `ACCEPTED` state.

## 3. File-by-file patch plan

1. `se/src/domain/schemas/agent_execution.py` — add three nullable R7 execution fields.
2. `se/src/runtimes/agent/contracts/resume.py` — add immutable normalized checkpoint, pending invocation, ResumeClaim, `ResumeClaimState`, and `ToolResultCommitState` representations.
3. `se/src/runtimes/agent/contracts/__init__.py` — export R7-A contracts without deleting legacy continuation exports.
4. `se/src/infrastructure/storage/models/sql/agent/execution.py` — add execution pointer/binding columns.
5. `se/src/infrastructure/storage/models/sql/agent/tool_result.py` — add `commit_state`, check constraint, fail-safe model/server default.
6. `se/src/infrastructure/storage/models/sql/agent/checkpoint.py` — add normalized checkpoint and 1:N pending invocation models.
7. `se/src/infrastructure/storage/models/sql/agent/resume_claim.py` — add ResumeClaim model and state/timestamp invariants.
8. `se/src/infrastructure/storage/models/sql/agent/__init__.py` — register/export new models so Alembic `env.py` and `Base.metadata.create_all()` see them.
9. `se/src/infrastructure/storage/repositories/agent.py` — add transaction-scoped checkpoint/pending/claim primitives plus ResumeClaim revision/state CAS.
10. `se/src/infrastructure/storage/migrations/sql/versions/13a_r7_durable_resume.py` — migrate from `12a_r6_remote_reconciliation`, add columns/tables/constraints/indexes, perform conservative tool-result backfill, and support downgrade.
11. `se/tests/architecture/test_r7_a_representation.py` — freeze enum/dataclass/domain/SQL/repository representation and claim CAS behavior.
12. `se/tests/integration/test_r7_a_migration.py` — prove upgrade from 12a, no fake checkpoint/routing backfill, conservative tool-result classification, new indexes/tables, and DB constraints.

## 4. R7-A non-goals

The patch must not modify `AgentRuntime`, `AgentContinuationService`, `_resume_execution`, `TaskBudgetService`, `CapabilityRuntime`, `RemoteInvocationReconciliationService`, `ClientRuntime`, or `GatewayRealtimeClient`. It must not emit `execution.waiting`, consume a ResumeClaim, bind K2 during resume, reconcile/replay a capability invocation, or stop legacy `context_state["continuation"]` writes. Those require later atomic/runtime phases.

## 5. Exit criteria before R7-B

R7-A is complete only when:

- Alembic head is `13a_r7_durable_resume`.
- Existing legacy execution rows gain no fabricated checkpoint/client/connection binding.
- Existing ambiguous remote result projections are not backfilled as `COMMITTED`.
- New tool-result rows default fail-safe to `PROVISIONAL` until R7-C explicitly commits them.
- SQLAlchemy metadata sees all three new R7 tables.
- one checkpoint can persist multiple ordered pending invocations.
- `resume_request_id` is a durable uniqueness fence.
- ResumeClaim has no `ACCEPTED` state.
- transaction-scoped repository primitives are available through `uow.agents`.
- no runtime/protocol authority has changed yet.

## 6. Targeted verification after applying the patch

```powershell
py -m pytest -q `
  se/tests/architecture/test_r7_a_representation.py `
  se/tests/integration/test_r7_a_migration.py

py -m pytest -q `
  se/tests/architecture/test_r6_a_domain_persistence_contracts.py `
  se/tests/integration/test_r6_a_remote_reconciliation_migration.py `
  se/tests/architecture/test_r5_b_task_budget_service.py `
  se/tests/integration/test_r5_c2_c4_atomic_task_budget.py `
  se/tests/architecture/test_phase5_9_exit_gate.py
```

Then run the normal broad suite before starting R7-B.
