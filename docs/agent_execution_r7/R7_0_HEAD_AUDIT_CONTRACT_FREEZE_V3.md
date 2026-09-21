# R7-0 — HEAD Audit + Durable Checkpoint / ResumeClaim / Resume Protocol Contract Freeze v3

**Repository:** `boxs-51/assistant`  
**Audited branch:** `main`  
**Audited HEAD:** `0e7aff938e7d560ba502d62f0690fdd4b7aea388`  
**HEAD commit message:** `cap nhat trang thai cuoi R6-D trien khai R6-E`  
**Audit date:** 2026-09-21  
**Previous contract:** `docs/R7_RESUME_CHECKPOINT_CONTRACT_V2.md` at baseline `e5f7898667556b120261663d8c6d712ddb16363d`  
**Document status:** **R7-0 PRE-IMPLEMENTATION / CONTRACT FREEZE v3**  
**Implementation status:** **NO R7 IMPLEMENTATION / NO R7 PATCH**

---

## 0. Executive decision

R7 must not be implemented by extending the existing Phase 6.9 continuation branch/merge path. HEAD is currently a hybrid of:

```text
legacy continuation JSON + in-memory continuation branches
+
AgentExecution revision CAS / WAITING budget semantics
+
R6 durable CapabilityInvocation reconciliation/idempotency
+
process-local AgentExecutionSupervisor ownership
```

The target R7 architecture is a normalized durable resume protocol with one execution lifecycle authority:

```text
RUNNING E1
    |
    | atomic safe-point commit
    v
WAITING E1@N + current checkpoint C1@N
    |
    | reconnect / trigger
    v
R6-aware reconstruction + reconciliation
    |
    v
immutable ResumePlan
    |
    v
ResumeClaim RC1(CREATED)
    |
    | one atomic authority transaction
    v
E1 WAITING@N -> RUNNING@N+1
RC1 CREATED   -> CONSUMED
    |
    | process-local supervisor accepts ownership
    v
execution.resume.accepted
    |
    v
continue the SAME execution_id E1
```

R7 v3 freezes the following corrections beyond v2:

1. **Multi-pending invocation checkpointing** replaces the v2 singular pending invocation fields.
2. **AgentToolResult commitment semantics** distinguish `PROVISIONAL` transport uncertainty from `COMMITTED` model-consumable outcomes.
3. **TaskBudget + AgentExecution + Checkpoint / ResumeClaim atomicity** uses one SQL UoW for each lifecycle boundary.
4. **Existing CapabilityInvocation continuation** is a first-class API; R7 must never recreate the same logical invocation through `execute_capability()`.
5. **Reconciliation-to-claim TOCTOU fencing** uses per-invocation revision/state snapshots in `ResumePlan` and revalidation in the claim transaction.
6. **Resume request idempotency** uses a stable `resume_request_id` so lost ACKs do not turn a successful resume into a false conflict on retry.
7. **Supervisor ownership precedes accepted ACK**; the current HEAD ordering is reversed and must be replaced.
8. **Post-claim handoff failure creates a new recovery checkpoint**, not a blind `RUNNING -> CANCELLED` rollback and not reuse of a stale checkpoint revision.

R7 implementation may start only after this v3 contract is accepted and the R6-E dependency is formally treated as green by the project gate. HEAD contains the R6-E harness/fixes, but `R6_E_EXIT_GATE.md` still carries an active/pre-implementation status, so status documentation must be reconciled before R7 is declared complete.

---

# 1. HEAD audit scope

The audit follows the actual runtime path rather than only the roadmap documents:

```text
AgentRuntime
    -> CapabilityToolExecutionAdapter / Coordinator
    -> CapabilityRuntime
    -> RemoteClientDriver
    -> RealtimeMultiplexer
    -> WebSocket
    -> ClientRuntime / CapabilityDispatcher
    -> ClientInvocationLedger

on remote uncertainty:

AgentRuntime
    -> AgentContinuationService.checkpoint_disconnect()
    -> DurableAgentStore.save_continuation_state()
    -> AgentRuntime durable RUNNING -> WAITING finish

on reconnect:

execution.resume
    -> events_router._resume_execution()
    -> AgentContinuationService.ensure_loaded()
    -> DurableAgentStore.resume_execution()
    -> AgentContinuationService.reconnect()
    -> AgentExecutionSupervisor.reserve()
    -> AgentRuntime.claim_resume()
    -> AgentContinuationService.confirm_merge()
    -> execution.resume.accepted
    -> AgentExecutionSupervisor.start_reserved()
    -> AgentRuntime.execute(durable_revision=...)
```

Audited call-site families:

```text
se/src/runtimes/agent/contracts/continuation.py
se/src/runtimes/agent/continuation.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/persistence.py
se/src/runtimes/agent/task_budget.py
se/src/runtimes/agent/supervisor.py
se/src/domain/schemas/agent_execution.py
se/src/infrastructure/storage/models/sql/agent/*
se/src/infrastructure/storage/repositories/agent.py
se/src/infrastructure/storage/core/unit_of_work.py
se/src/runtimes/capability/contracts/invocation.py
se/src/runtimes/capability/contracts/reconciliation.py
se/src/runtimes/capability/reconciliation.py
se/src/runtimes/capability/runtime.py
se/src/infrastructure/storage/models/sql/capability/invocation.py
se/src/infrastructure/storage/repositories/capability_invocations.py
se/src/transport/gateway/api/v1/events_router.py
cl/src/core/client_runtime.py
cl/src/core/realtime_client.py
cl/src/core/capability_dispatcher.py
cl/src/core/client_invocation_ledger.py
```

---

# 2. Verified HEAD facts

## 2.1 AgentExecution already has the correct canonical lifecycle vocabulary

HEAD already normalizes execution lifecycle to:

```text
CREATED
RUNNING
WAITING
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

and carries a separate `wait_reason`.

This remains canonical in R7.

## 2.2 R4 active-budget / wait-TTL semantics already exist

HEAD already persists:

```text
revision
remaining_active_budget_seconds
wait_expires_at
```

and restores active budget only after a WAITING -> RUNNING CAS wins.

R7 must preserve this behavior.

## 2.3 TaskBudget already owns atomic task-scoped execution capacity changes

`TaskBudgetService._transition_execution_with_budget()` already commits:

```text
TaskBudget CAS
+
AgentExecution CAS
+
TaskBudgetReservation
```

inside one SQL UoW.

R7 must extend this boundary rather than performing ResumeClaim or Checkpoint writes in a second transaction.

## 2.4 R6 now has durable remote outcome semantics

HEAD defines:

```text
RemoteOutcomeState:
    NOT_DISPATCHED
    IN_FLIGHT
    OUTCOME_UNKNOWN
    TERMINAL_COMMITTED
```

and persists reconciliation identity:

```text
capability_version
idempotency
request_fingerprint
owner_user_id
origin_client_id
remote_outcome_state
```

R7 must consume this authority rather than infer replay safety from transport errors.

## 2.5 R6 reconciliation protocol exists

HEAD contains:

```text
capability.reconcile
capability.reconciliation
```

with semantic fingerprint validation and same-user/same-client authorization.

## 2.6 CL has a durable invocation ledger

`CapabilityDispatcher` can distinguish durable client states and replay terminal outcomes by stable `invocation_id` + semantic fingerprint.

This is the client-side side-effect authority R7 must query before continuing ambiguous work.

## 2.7 Legacy continuation authority still exists

HEAD still contains:

```text
ContinuationBranch
READY_TO_MERGE
reconnect()
confirm_merge()
_checkpoints
_branches
_merged
context_state["continuation"]
```

These are migration inputs only after R7 becomes canonical.

## 2.8 Normalized R7 persistence does not exist yet

HEAD currently lacks:

```text
AgentExecution.current_checkpoint_id
agent_execution_checkpoints
agent_checkpoint_pending_invocations
agent_resume_claims
```

and `SqlAlchemyUnitOfWork` exposes `agents` but not one shared capability-invocation repository bound to the same SQL session.

## 2.9 Current resume ACK ordering is unsafe for R7

HEAD currently performs:

```text
reserve
claim_resume
confirm_merge
ACK accepted
start_reserved
```

R7 freezes the opposite ownership rule:

```text
reserve
claim atomically
start_reserved
ACK accepted
```

## 2.10 Current resumed-tool execution is not sufficiently R6-aware

HEAD reconstructs `resume_pending_tool_calls`, reuses an `agent_tool_results` row if present, and executes remaining requests through the ordinary tool execution path.

That is insufficient because:

```text
an AgentToolResult row
!=
a committed remote side-effect outcome
```

when the row represents transport uncertainty such as `REMOTE_OUTCOME_UNKNOWN`.

---

# 3. Scope

R7 owns:

```text
normalized durable checkpoints
WAITING safe-point atomicity
multi-pending invocation checkpoint references
ResumePlan reconstruction
R6-aware resume decisions
ResumeClaim durability and CAS ownership
resume request idempotency
WAITING -> RUNNING atomic resume
TaskBudget coupling at R7 boundaries
runtime handoff before ACK
same-client reconnect resume protocol
PendingResumeTicket in ClientRuntime
legacy continuation materialization
removal of branch/merge from canonical resume path
real-TCP reconnect/race/restart proof
```

R7 does not own:

```text
R8 TaskBranch / FORK semantics
R9 retry scheduler
R10+ workflow/DAG expansion
R11 transcript snapshot/delta optimization
R12 distributed execution leases / zombie RUNNING recovery
R13 complete wire-version migration/removal of compatibility errors
R14+ MCP integration cleanup
cross-user collaborative resume
cross-client resume policy
exactly-once guarantees for arbitrary external systems beyond R6 contracts
```

---

# 4. Canonical vocabulary

## 4.1 Execution

One durable AgentRuntime run:

```text
1 execution_id = 1 AgentExecution lifecycle
```

Resume does not create a new execution.

## 4.2 ExecutionCheckpoint

An immutable durable safe point tied to one committed WAITING execution revision.

It is reconstruction evidence, not execution lifecycle authority.

## 4.3 CheckpointPendingInvocation

One immutable checkpoint reference to one unresolved capability invocation that matters to safe continuation.

It is a watermark/snapshot, while `CapabilityInvocation` remains the mutable invocation authority.

## 4.4 ResumePlan

An immutable in-memory reconstruction product produced only after deterministic validation and required R6 reconciliation.

Building a plan never mutates `AgentExecution`.

## 4.5 ResumeClaim

A durable intent to acquire one WAITING execution revision from one checkpoint.

`CREATED` owns nothing.

`CONSUMED` means it won the execution lifecycle CAS.

## 4.6 PendingResumeTicket

ClientRuntime-owned resumable-work metadata keyed by `(execution_id, checkpoint_id)`.

It is not execution authority and does not replace server durability.

---

# 5. Authority map

The following ownership split is frozen.

| Concern | Canonical authority |
|---|---|
| AgentExecution lifecycle | `AgentRuntime` / AgentRuntime-owned durable lifecycle boundary |
| WAITING safe point | normalized checkpoint transaction invoked by AgentRuntime |
| Checkpoint persistence | durable SQL repository/UoW |
| Capability invocation lifecycle | `CapabilityInvocationLifecycle` + durable capability invocation row |
| Remote side-effect certainty | `RemoteOutcomeState` + R6 reconciliation + CL ledger |
| Task execution capacity | `TaskBudgetService` / TaskBudget transaction primitive |
| Process-local execution ownership | `AgentExecutionSupervisor` |
| Authentication | gateway authentication identity |
| Stable installation identity | `client_id` |
| WebSocket generation | `connection_id` |
| Resume orchestration | `AgentContinuationService` / ResumeService façade |
| Wire parsing / response | transport only; never lifecycle mutation |
| Client resumable ticket memory | `ClientRuntime` |

Forbidden dual authorities after R7 canonicalization:

```text
context_state["continuation"] as writable checkpoint authority
ContinuationBranch as resume authority
confirm_merge() as lifecycle authority
UI state as resumable-work authority
AgentToolResult presence as proof of remote side-effect commitment
```

---

# 6. P0 findings frozen by v3

## P0-R7-01 — WAITING and checkpoint are split across transactions

Current legacy checkpoint persistence and AgentExecution WAITING CAS can commit independently.

R7 requires one atomic WAITING safe-point transaction.

## P0-R7-02 — AgentExecution has no normalized current checkpoint pointer

R7 adds `current_checkpoint_id`.

## P0-R7-03 — ResumeClaim is not durable

Current `claim_resume()` is only an AgentExecution CAS.

R7 adds durable claim intent and consumption.

## P0-R7-04 — current claim does not fence checkpoint identity

R7 claim must validate:

```text
execution_id
execution revision
current_checkpoint_id
checkpoint.execution_revision
```

in the claim transaction.

## P0-R7-05 — ACK currently precedes supervisor ownership

R7 reverses the ordering.

## P0-R7-06 — legacy reconnect branch persists before execution claim

R7 removes `ContinuationBranch` from the canonical path.

## P0-R7-07 — resumed pending calls can be executed without an R6 disposition

Every unresolved remote invocation must receive an R6-backed resume disposition before AgentExecution becomes RUNNING.

## P0-R7-08 — remote uncertainty can be persisted as if it were a committed tool result

`REMOTE_OUTCOME_UNKNOWN`, connection loss and reconciliation-required projections may be stored in `agent_tool_results` today.

R7 introduces explicit tool-result commitment state and forbids provisional results from entering model transcript reconstruction.

## P0-R7-09 — ordinary CapabilityRuntime execution recreates invocation identity

`execute_capability()` creates a new durable `CapabilityInvocation`.

R7 needs a separate API for continuing/replaying an existing invocation using the same `invocation_id`.

## P0-R7-10 — SQL UoW cannot currently coordinate agent + capability persistence

R7 requires a shared session/UoW repository boundary.

## P0-R7-11 — TaskBudget resume/release transactions do not yet include R7 checkpoint/claim rows

R7 extends the existing transaction, not compensates afterward.

## P0-R7-12 — v2 singular pending invocation is incompatible with parallel tool batches

R7 v3 normalizes `1 checkpoint -> N pending invocation references`.

## P0-R7-13 — target K2 binding is not durably recorded by the execution claim

R7 atomically persists the new runtime routing binding.

## P0-R7-14 — reconciliation decision can become stale before claim

There is a TOCTOU window:

```text
reconcile / inspect invocation
    -> build plan
    -> invocation changes concurrently
    -> consume execution claim using stale R6 decision
```

R7 v3 includes invocation revision/state snapshots in each `ResumeInvocationAction` and revalidates them inside the same claim transaction.

## P0-R7-15 — resume ACK loss has no request-level idempotency key

A successful claim followed by a lost ACK can cause a retry to see `RUNNING` and be misreported as a failed resume.

R7 v3 requires a stable `resume_request_id` and durable request dedupe through `ResumeClaim`.

---

# 7. P1 findings

## P1-R7-01 — legacy transcript may contain synthetic remote-error tool messages

Legacy checkpoint materialization must not blindly copy a transcript where `REMOTE_OUTCOME_UNKNOWN` has already been serialized as a final `role=tool` message.

## P1-R7-02 — current client has no PendingResumeTicket authority

The client can send `execution.resume`, but production code does not maintain resumable tickets.

## P1-R7-03 — generic WebSocket error is insufficient for stable retry policy

R7 introduces canonical accepted/rejected/failed resume events and stable error codes, while compatibility generic errors may temporarily remain.

## P1-R7-04 — v2 transcript reference fields are premature for R7

R7 v3 freezes full `transcript_snapshot` persistence for correctness. R11 owns reference/delta optimization.

## P1-R7-05 — routing metadata is currently mixed into JSON context metadata

R7 introduces first-class durable execution binding fields; JSON metadata cannot be required for correctness.

---

# 8. Global invariants

## 8.1 Execution identity and lifecycle

### R7-I01

Resume preserves `execution_id`.

```text
WAITING E1 -> RUNNING E1
```

### R7-I02

Only `WAITING` is resumable.

### R7-I03

Terminal AgentExecution states never return to RUNNING.

### R7-I04

Resume does not mutate execution lineage:

```text
task_id
branch_id
parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
correlation_id
trace_id
```

### R7-I05

`connection_id` is a transport generation/routing binding, not execution identity.

### R7-I06

CapabilityInvocation hard affinity remains per invocation/attempt. AgentExecution `bound_connection_id` is the current client routing attachment, not a prohibition on server-side work.

---

## 8.2 WAITING / checkpoint

### R7-I07

A committed `WAITING` execution must have a normalized current checkpoint.

```text
execution.state == WAITING
=> execution.current_checkpoint_id IS NOT NULL
```

### R7-I08

The current checkpoint must belong to the same execution.

### R7-I09

Checkpoint revision must equal the committed WAITING execution revision.

```text
checkpoint.execution_revision == execution.revision
```

### R7-I10

Checkpoints are immutable after insertion.

### R7-I11

A failed AgentExecution CAS rolls back its checkpoint insert and pending-reference inserts.

### R7-I12

`transcript_snapshot` is reconstructable and excludes provisional/uncommitted tool outcomes.

### R7-I13

All unresolved calls from the active tool batch that can affect safe continuation are represented, not only the first remote failure.

### R7-I14

`CheckpointPendingInvocation.ordinal` is the original position in the iteration's canonical `tool_call_ids` ordering.

### R7-I15

Checkpoint metadata is observability-only. Resume correctness must not depend on arbitrary `metadata` keys.

---

## 8.3 Tool-result commitment

### R7-I16

An `AgentToolResult` row is model-consumable only when `commit_state == COMMITTED`.

### R7-I17

Remote transport uncertainty is `PROVISIONAL`, even if represented as a ToolExecutionResult object in process memory.

### R7-I18

For remote invocations, commitment requires R6 terminal authority:

```text
CapabilityInvocation.remote_outcome_state == TERMINAL_COMMITTED
```

and a matching semantic invocation identity.

### R7-I19

A `PROVISIONAL` tool result may transition to `COMMITTED` only with the exact terminal outcome authorized by R6 reconciliation or normal remote terminal delivery.

### R7-I20

A committed result is never overwritten by a conflicting later result.

---

## 8.4 ResumePlan / R6 safety

### R7-I21

ResumePlan construction is read/reconcile-only with respect to AgentExecution state.

### R7-I22

Every pending invocation has one final safe disposition before claim.

### R7-I23

Final ResumePlan may contain only actions safe to execute after claim:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

Unsafe or unresolved states reject/defer the resume before claim.

### R7-I24

Every plan action carries the invocation semantic identity and the invocation revision/state snapshot used to make the decision.

### R7-I25

Claim consumption revalidates those invocation snapshots in the same SQL transaction as the AgentExecution CAS.

### R7-I26

If a relevant invocation revision/state changes between planning and claiming, the claim does not start the execution. The request is rejected as stale and the execution remains WAITING.

### R7-I27

`server_continuation_available` is never replay-safety proof.

### R7-I28

Stable logical remote invocation means stable `invocation_id` and stable request fingerprint.

---

## 8.5 ResumeClaim

### R7-I29

ResumeClaim states are exactly:

```text
CREATED
CONSUMED
REJECTED
EXPIRED
```

### R7-I30

`CREATED` owns no execution authority.

### R7-I31

`CONSUMED` is true if and only if that claim won the corresponding WAITING -> RUNNING lifecycle transaction.

### R7-I32

One execution revision/checkpoint can have multiple competing CREATED claims, but at most one can consume execution authority because the AgentExecution CAS is the final fence.

### R7-I33

Claim expiration does not timeout the waiting execution.

### R7-I34

Claim TTL and execution wait TTL are independent clocks.

### R7-I35

Terminal claim states are immutable.

### R7-I36

A consumed claim records the resulting execution revision.

---

## 8.6 Resume request idempotency

### R7-I37

Each logical wire resume attempt has a stable `resume_request_id`.

### R7-I38

The client reuses the same `resume_request_id` when retrying because an ACK may have been lost.

### R7-I39

The server deduplicates `resume_request_id` durably.

### R7-I40

Reusing one `resume_request_id` with different execution/checkpoint/principal/client semantics is `RESUME_REQUEST_CONFLICT`.

### R7-I41

A duplicate request for an already CONSUMED claim replays the accepted outcome; it does not attempt another execution CAS.

---

## 8.7 Supervisor / ACK

### R7-I42

Supervisor reservation occurs before durable claim.

### R7-I43

Supervisor task activation occurs after durable claim but before accepted ACK.

### R7-I44

`execution.resume.accepted` means:

```text
claim CONSUMED
AgentExecution durably entered RUNNING at accepted_revision
supervisor owns the continuation task
```

### R7-I45

If activation fails after claim but before ACK, the claim remains CONSUMED and AgentRuntime creates a new durable `WAITING(RECOVERY)` checkpoint or a policy-selected terminal state.

### R7-I46

The old checkpoint cannot be reused after a post-claim recovery transition because its execution revision is stale.

---

## 8.8 Authorization and connection

### R7-I47

For `WAITING(CONNECTION)`, automatic client resume is same authenticated principal and same stable `client_id` by default.

### R7-I48

Cross-client resume is denied unless a future explicit policy allows it.

### R7-I49

The target `connection_id` must be active/usable and belong to the authenticated principal.

### R7-I50

For reconnect resume, the target connection generation must differ from the disconnected origin generation.

### R7-I51

All required client capabilities for all pending invocation actions must be registered and enabled on the target connection before claim.

### R7-I52

Initial R7 policy requires all unresolved client-side pending invocations in one CONNECTION checkpoint to be satisfiable by the same stable client installation. Multi-client fan-in is deferred.

---

# 9. Canonical state machines

## 9.1 AgentExecution

R7 does not add new lifecycle states.

```text
CREATED -> RUNNING

RUNNING -> WAITING
RUNNING -> COMPLETED
RUNNING -> FAILED
RUNNING -> CANCELLED
RUNNING -> TIMEOUT

WAITING -> RUNNING
WAITING -> CANCELLED
WAITING -> TIMEOUT
WAITING -> FAILED   (only explicit fail-closed policy)
```

Forbidden:

```text
terminal -> RUNNING
WAITING -> CREATED
resume -> new execution_id
```

`WAITING` reason remains separate:

```text
CONNECTION
HUMAN_APPROVAL
DEPENDENCY
RESOURCE
EXPLICIT_PAUSE
RECOVERY
RETRY_BACKOFF
AGENT
```

R7 auto-resume implements CONNECTION only. Other reasons may reuse the same claim substrate later but require their own trigger policy.

---

## 9.2 ResumeClaim

```text
              +---------- CONSUMED
              |
CREATED ------+---------- REJECTED
              |
              +---------- EXPIRED
```

No transitions out of terminal claim states.

---

## 9.3 AgentToolResultCommitState

New canonical projection state:

```text
PROVISIONAL -> COMMITTED
COMMITTED   -> terminal/immutable
```

`PROVISIONAL` means an audit projection exists but cannot be placed into reconstructed model context.

---

## 9.4 Resume invocation action

Final `ResumePlan` action enum:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

The following are planning outcomes, not executable final actions:

```text
DEFER_REMOTE_RUNNING
BLOCK_UNSAFE_OUTCOME
BLOCK_CONFLICT
BLOCK_FOREIGN_CLIENT
BLOCK_CAPABILITY_NOT_READY
STALE_RECONCILIATION_SNAPSHOT
```

If one pending invocation is blocked/deferred, the AgentExecution remains WAITING and no claim is consumed.

---

# 10. Persistence schema freeze

The first implementation migration should follow R6 migration `12a_r6_remote_reconciliation`; recommended revision name:

```text
13a_r7_durable_resume
```

Exact Alembic filename is implementation detail; semantic schema below is frozen.

---

## 10.1 `agent_executions` additions

Add:

```text
current_checkpoint_id    VARCHAR(255) NULL
bound_client_id          VARCHAR(255) NULL
bound_connection_id      VARCHAR(255) NULL
```

Semantics:

```text
current_checkpoint_id:
    current immutable safe point when WAITING;
    may remain populated while RUNNING/terminal for audit.

bound_client_id:
    stable client installation currently attached to the execution context.

bound_connection_id:
    current WebSocket generation attached to the execution context.
```

For `WAITING(CONNECTION)` caused by a lost K1:

```text
bound_client_id      may remain the expected stable client
bound_connection_id  = NULL
```

On successful resume to K2:

```text
bound_client_id      = client_id
bound_connection_id  = K2
```

These fields do not replace CapabilityInvocation `connection_id`/attempt affinity.

Recommended indexes:

```text
ix_agent_executions_current_checkpoint_id
ix_agent_executions_bound_client_id
ix_agent_executions_bound_connection_id
```

A physical circular FK from `current_checkpoint_id` back to the checkpoint table is optional; the semantic invariant and transaction tests are mandatory.

---

## 10.2 `agent_execution_checkpoints`

```text
checkpoint_id                       VARCHAR(255) PK
execution_id                        VARCHAR(255) NOT NULL INDEX
execution_revision                  INTEGER NOT NULL

session_id                          VARCHAR(255) NOT NULL
task_id                             VARCHAR(255) NULL
branch_id                           VARCHAR(255) NULL

parent_checkpoint_id                VARCHAR(255) NULL
iteration                           INTEGER NOT NULL
wait_reason                         VARCHAR(32) NOT NULL

remaining_active_budget_seconds     FLOAT NULL
wait_expires_at                     TIMESTAMP TZ NULL

owner_user_id                       VARCHAR(255) NULL
origin_client_id                    VARCHAR(255) NULL
origin_connection_id                VARCHAR(255) NULL

transcript_snapshot                 JSON NOT NULL

legacy_source_key                   VARCHAR(512) NULL UNIQUE
metadata                            JSON NOT NULL DEFAULT {}
created_at                          TIMESTAMP TZ NOT NULL
```

Frozen decisions relative to v2:

```text
REMOVE singular:
    pending_invocation_id
    pending_tool_call_id
    pending_capability_id

REMOVE generic:
    side_effect_watermark

DEFER to R11:
    transcript_ref
    transcript_version
```

R7 persists a full snapshot for correctness.

`owner_user_id` is required for client-triggered `WAITING(CONNECTION)` checkpoints unless another canonical execution-owner lookup is guaranteed by the implementation. It must never be hidden only in arbitrary metadata.

Recommended constraints:

```text
execution_revision >= 0
iteration >= 0
wait_reason != 'NONE'
```

---

## 10.3 `agent_checkpoint_pending_invocations`

This table is new in v3 and replaces the singular pending fields.

```text
checkpoint_id                 VARCHAR(255) NOT NULL
invocation_id                 VARCHAR(255) NOT NULL
ordinal                       INTEGER NOT NULL

invocation_revision           INTEGER NOT NULL
tool_call_id                  VARCHAR(255) NOT NULL
capability_id                 VARCHAR(255) NOT NULL
capability_version            VARCHAR(64) NULL
request_fingerprint           VARCHAR(64) NULL
idempotency                   VARCHAR(32) NOT NULL
observed_remote_outcome_state VARCHAR(32) NULL
origin_client_id              VARCHAR(255) NULL
origin_connection_id          VARCHAR(255) NULL

PRIMARY KEY (checkpoint_id, invocation_id)
UNIQUE (checkpoint_id, tool_call_id)
INDEX (invocation_id)
INDEX (checkpoint_id, ordinal)
```

`ordinal` is the position in the original iteration `tool_call_ids` ordering.

The row is a checkpoint watermark, not the mutable invocation authority.

At resume time, server loads the current `CapabilityInvocation` and validates immutable identity fields against this snapshot.

Allowed current invocation revision:

```text
current_revision >= checkpoint.invocation_revision
```

but the final ResumePlan stores the exact current revision used for the R6 decision, and the claim transaction requires that plan revision to still match.

---

## 10.4 `agent_resume_claims`

```text
claim_id                     VARCHAR(255) PK
resume_request_id            VARCHAR(255) NOT NULL UNIQUE

execution_id                 VARCHAR(255) NOT NULL INDEX
checkpoint_id                VARCHAR(255) NOT NULL INDEX
expected_execution_revision  INTEGER NOT NULL
plan_fingerprint             VARCHAR(64) NOT NULL

user_id                      VARCHAR(255) NULL
client_id                    VARCHAR(255) NULL
connection_id                VARCHAR(255) NULL

wait_reason                  VARCHAR(32) NOT NULL
trigger_type                 VARCHAR(32) NOT NULL

state                        VARCHAR(16) NOT NULL INDEX
revision                     INTEGER NOT NULL DEFAULT 0

rejection_code               VARCHAR(64) NULL
consumed_execution_revision  INTEGER NULL

metadata                     JSON NOT NULL DEFAULT {}

created_at                   TIMESTAMP TZ NOT NULL
claim_expires_at             TIMESTAMP TZ NOT NULL
consumed_at                  TIMESTAMP TZ NULL
rejected_at                  TIMESTAMP TZ NULL
expired_at                   TIMESTAMP TZ NULL
```

Frozen state CHECK:

```text
state IN ('CREATED', 'CONSUMED', 'REJECTED', 'EXPIRED')
```

Frozen trigger vocabulary for R7-capable storage:

```text
CLIENT_RECONNECT
SERVER_RECOVERY
MANUAL
DEPENDENCY_READY
RESOURCE_READY
```

R7 production implementation is required to support `CLIENT_RECONNECT`; other trigger types may be stored for future reuse.

`plan_fingerprint` hashes only immutable resume-plan semantics, never volatile timestamps.

---

## 10.5 `agent_tool_results` addition

Add:

```text
commit_state VARCHAR(16) NOT NULL
```

CHECK:

```text
commit_state IN ('PROVISIONAL', 'COMMITTED')
```

New-write rule:

```text
local/server known terminal outcome -> COMMITTED
remote TERMINAL_COMMITTED          -> COMMITTED
remote ambiguity / unknown         -> PROVISIONAL
```

Migration rule for existing rows:

```text
if linked CapabilityInvocation is remote and
remote_outcome_state != TERMINAL_COMMITTED:
    commit_state = PROVISIONAL
else:
    commit_state = COMMITTED
```

If no linked invocation exists for legacy data, migration/materialization must fail closed for ambiguous known remote error codes rather than blindly marking them model-consumable.

---

# 11. Transaction boundary freeze

## 11.1 WAITING safe-point transaction

Given:

```text
E1 RUNNING @ revision N
```

R7 computes:

```text
waiting_revision = N + 1
```

One SQL transaction must perform the lifecycle-safe boundary.

Conceptual transaction:

```text
BEGIN

load E1 and require:
    state == RUNNING
    revision == N

load/validate all relevant pending CapabilityInvocation rows
load/validate tool-call/result projections

if task-scoped:
    release TaskBudget active capacity
    create/validate TaskBudget release reservation

insert checkpoint C1:
    execution_id = E1
    execution_revision = N + 1
    transcript_snapshot = reconstructable snapshot
    ...

insert N CheckpointPendingInvocation watermarks

CAS E1:
    RUNNING@N -> WAITING@N+1
    wait_reason = reason
    current_checkpoint_id = C1
    remaining_active_budget_seconds = frozen remaining budget
    wait_expires_at = wait TTL
    bound_connection_id = NULL for connection-loss wait

COMMIT
```

Post-condition:

```text
no observer can see WAITING without C1
no C1 from a lost execution CAS survives
TaskBudget active counters agree with AgentExecution state
```

### R6 state already committed before this transaction

R7 does not need to roll back an earlier durable R6 fact such as `OUTCOME_UNKNOWN`.

However, every capability state required for a safe checkpoint must be durable before or inside this transaction, and the transaction must read/validate that durable row using the same SQL session/UoW.

No checkpoint may reference only process-local invocation state.

---

## 11.2 Resume claim creation

Claim creation is durable intent only:

```text
ResumePlan validated
    -> create RC1(CREATED)
```

It does not change execution state or TaskBudget.

Idempotency:

```text
same resume_request_id + same semantic request
    -> return existing claim

same resume_request_id + different semantics
    -> RESUME_REQUEST_CONFLICT
```

---

## 11.3 Atomic resume consumption

Given:

```text
E1 WAITING@N
C1 execution_revision=N
RC1 CREATED
```

one SQL transaction must perform:

```text
BEGIN

reload E1
reload C1
reload RC1

require:
    E1.state == WAITING
    E1.revision == N
    E1.current_checkpoint_id == C1
    C1.execution_revision == N
    RC1.state == CREATED
    RC1.expected_execution_revision == N
    RC1.checkpoint_id == C1
    claim TTL valid
    wait TTL valid

for every ResumeInvocationAction:
    reload current CapabilityInvocation
    validate semantic fingerprint
    validate expected invocation revision/state/outcome snapshot

if task-scoped:
    reacquire TaskBudget active capacity
    create/validate TaskBudget resume reservation

CAS E1:
    WAITING@N -> RUNNING@N+1
    wait_reason = NULL
    wait_expires_at = NULL
    bound_client_id = target client
    bound_connection_id = K2

CAS RC1:
    CREATED -> CONSUMED
    consumed_execution_revision = N+1
    consumed_at = now

COMMIT
```

Frozen post-condition:

```text
RC1 == CONSUMED
iff
RC1 acquired E1 RUNNING authority
```

If any invocation snapshot changed, the transaction does not consume the claim and does not reacquire execution capacity.

---

## 11.4 TaskBudget coupling

R7 must not call existing public TaskBudget transition, commit it, and then update checkpoint/claim separately.

Implementation target:

```text
TaskBudget transaction-scoped primitive
    operates inside caller-supplied R7 UoW/session
```

Public R5 APIs may remain as wrappers for existing call-sites.

R7 semantic requirement is one transaction, not a specific class name.

---

# 12. ResumePlan contract

## 12.1 Frozen immutable model

Conceptual contract:

```python
ResumePlan(
    execution_id: str,
    checkpoint_id: str,
    expected_execution_revision: int,
    plan_fingerprint: str,

    agent_id: str,
    session_id: str,
    task_id: str | None,
    branch_id: str | None,
    parent_execution_id: str | None,
    retry_of_execution_id: str | None,
    base_execution_id: str | None,
    base_checkpoint_id: str | None,

    correlation_id: str,
    trace_id: str | None,
    request_id: str | None,

    iteration: int,
    ordered_tool_call_ids: tuple[str, ...],
    transcript_snapshot: tuple[InferenceMessage, ...],

    remaining_active_budget_seconds: float,
    wait_expires_at: datetime | None,

    target_user_id: str,
    target_client_id: str | None,
    target_connection_id: str | None,

    invocation_actions: tuple[ResumeInvocationAction, ...],
)
```

## 12.2 ResumeInvocationAction

```python
ResumeInvocationAction(
    invocation_id: str,
    tool_call_id: str,
    ordinal: int,

    capability_id: str,
    capability_version: str,
    request_fingerprint: str,
    idempotency: CapabilityIdempotency,

    expected_invocation_revision: int,
    expected_invocation_state: CapabilityInvocationState,
    expected_remote_outcome_state: RemoteOutcomeState | None,

    action: ResumeInvocationActionKind,
)
```

Final actions:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

## 12.3 Plan creation phases

Plan construction is logically:

```text
1. load execution + checkpoint
2. validate authorization / TTL / current checkpoint
3. load iteration/tool-call canonical ordering
4. load every checkpoint pending invocation
5. load current CapabilityInvocation rows
6. inspect already COMMITTED AgentToolResults
7. execute required R6 reconciliation queries
8. promote exact recovered terminal results to COMMITTED projection
9. classify each unresolved call
10. validate target capability readiness
11. rebuild transcript excluding provisional outcomes
12. freeze exact invocation revisions/states used for decisions
13. compute plan_fingerprint
14. return immutable ResumePlan
```

If a safe final action cannot be produced for every unresolved call, no plan eligible for claim is returned.

---

# 13. R6 -> R7 reconciliation decision matrix

The following table is canonical.

| Durable / reconciliation condition | R7 result |
|---|---|
| CapabilityInvocation `TERMINAL_COMMITTED` + exact terminal result available | `REUSE_COMMITTED` |
| Committed AgentToolResult exists and matches a terminal committed invocation | `REUSE_COMMITTED` |
| `NOT_DISPATCHED` and semantic identity intact | `DISPATCH_NOT_DISPATCHED` using same invocation ID |
| `OUTCOME_UNKNOWN` then reconciliation `TERMINAL` | commit exact terminal authority, then `REUSE_COMMITTED` |
| reconciliation `RUNNING` | defer; execution remains WAITING |
| reconciliation semantic `CONFLICT` | reject resume |
| `OUTCOME_UNKNOWN` / reconciliation unknown + `IDEMPOTENT` | `REPLAY_SAFE` with same invocation ID |
| `OUTCOME_UNKNOWN` / reconciliation unknown + `DEDUPLICATED` | `REPLAY_SAFE` with same invocation ID |
| `OUTCOME_UNKNOWN` / unknown + `NON_IDEMPOTENT` | block; execution remains WAITING/fail-safe policy |
| `OUTCOME_UNKNOWN` / unknown + idempotency `UNKNOWN` | block |
| reconciliation `NOT_FOUND` after possible dispatch, non-idempotent/unknown | block |
| `NOT_FOUND` where durable state proves `NOT_DISPATCHED` | `DISPATCH_NOT_DISPATCHED` |
| foreign principal/client | reject |
| required capability absent on K2 | reject/defer |
| invocation revision changes before claim | reject stale plan and rebuild |

No R7 code may translate:

```text
socket disconnected
server implementation exists
cancel sent
timeout occurred
```

into proof that a remote side effect did not happen.

---

# 14. Existing CapabilityInvocation continuation API freeze

R7 must not use ordinary new-invocation creation for a pending logical call.

Canonical semantic API:

```python
CapabilityRuntime.continue_invocation(
    invocation_id: str,
    *,
    target_connection_id: str | None,
    mode: ExistingInvocationContinuationMode,
    expected_revision: int,
    expected_request_fingerprint: str,
    cancellation_event: asyncio.Event | None = None,
) -> CapabilityResult
```

Frozen continuation modes:

```text
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

Required behavior:

```text
load existing CapabilityInvocation
verify expected revision / semantic fingerprint
verify invocation belongs to same AgentExecution/tool call
verify mode is compatible with R6 state + idempotency
create a NEW attempt, not a new invocation
preserve invocation_id
preserve request_fingerprint
bind attempt to the target implementation/connection
execute through the normal driver boundary
commit terminal outcome through CapabilityInvocationLifecycle
return exact result
```

Forbidden:

```text
CapabilityRuntime.execute_capability(... invocation_id=existing_id ...)
    -> create a second invocation row
```

A later implementation may factor common internals between new and existing invocation execution, but the public semantic distinction is frozen.

---

# 15. AgentToolResult projection contract

## 15.1 Why a projection state is required

The Agent result table is consumed by transcript reconstruction, while R6 CapabilityInvocation is the side-effect certainty authority.

Therefore:

```text
ToolExecutionResult error object
```

is not sufficient to decide model-context commitment.

## 15.2 COMMITTED

A committed row can be used to create exactly one `role=tool` message.

For a remote call it requires:

```text
matching invocation_id
matching tool_call_id
matching capability_id
CapabilityInvocation.remote_outcome_state == TERMINAL_COMMITTED
```

## 15.3 PROVISIONAL

A provisional row is audit/debug evidence only.

Typical causes:

```text
REMOTE_CONNECTION_LOST
REMOTE_OUTCOME_UNKNOWN
REMOTE_RESULT_RECONCILIATION_REQUIRED
remote timeout/cancel with OUTCOME_UNKNOWN
```

It must not:

```text
satisfy _load_committed_tool_result()
enter transcript_snapshot
advance model inference as a final tool result
```

## 15.4 Promotion

When R6 reconciliation recovers a terminal outcome:

```text
PROVISIONAL -> COMMITTED
```

using the exact terminal payload.

The existing unique `(execution_id, tool_call_id)` row may be promoted atomically; conflicting committed content is rejected.

---

# 16. Parallel tool-batch resume contract

R7 supports the existing parallel tool-call model.

For iteration tool order:

```text
T1, T2, T3, T4
```

possible state at disconnect:

```text
T1 committed
T2 OUTCOME_UNKNOWN
T3 committed
T4 NOT_DISPATCHED
```

Checkpoint stores pending refs for T2/T4 with original ordinals 1 and 3.

ResumePlan loads all four positions from canonical iteration `tool_call_ids` and produces:

```text
T1 -> reuse committed
T2 -> reconcile / final safe action
T3 -> reuse committed
T4 -> dispatch same invocation
```

Before next inference, tool messages are reconstructed in original order:

```text
T1 result
T2 terminal/replayed result
T3 result
T4 result
```

R7 must never use only:

```python
remote_waiting[0]
```

as the continuation authority.

---

# 17. AgentRuntime API freeze

Canonical public semantic entry:

```python
AgentRuntime.resume(
    plan: ResumePlan,
    claim: ResumeClaim,
    ownership_token: ExecutionOwnershipToken,
) -> ResumeActivation
```

The exact Python return type name may vary, but semantics are frozen.

The entry owns:

```text
claim consumption lifecycle transaction
context instantiation from ResumePlan
restoring active budget only after claim succeeds
starting/activating already-reserved supervisor ownership
entering execution loop without a second durable begin CAS
```

The current pattern:

```text
claim_resume(context)
then
execute(context, durable_revision=...)
```

may be retained internally during migration only if it cannot perform a second WAITING -> RUNNING transition and all v3 transaction invariants are met.

Canonical internal split may be:

```text
_claim_resume_transaction(plan, claim)
_execute_already_claimed(context, revision)
```

but lifecycle authority stays in AgentRuntime.

---

# 18. ResumeService / AgentContinuationService API freeze

The service remains as orchestration façade, not lifecycle authority.

Target responsibilities:

```text
load normalized checkpoint
materialize legacy checkpoint if required
validate principal/client/connection
perform R6 reconciliation orchestration
build immutable ResumePlan
create/get idempotent ResumeClaim intent
reserve supervisor ownership
invoke AgentRuntime.resume
publish resume/waiting events
map domain rejection to wire protocol
```

It must not own:

```text
AgentExecution state mutation
TaskBudget mutation outside AgentRuntime-owned transaction
ContinuationBranch authority
merge authority
in-memory current checkpoint authority
```

Recommended semantic methods:

```python
load_current_checkpoint(execution_id) -> ExecutionCheckpoint | None

materialize_legacy_checkpoint(execution_id) -> ExecutionCheckpoint | None

build_resume_plan(
    execution_id,
    checkpoint_id,
    trigger_context,
) -> ResumePlan

get_or_create_claim(
    plan,
    resume_request_id,
    trigger_context,
) -> ResumeClaim

resume(
    plan,
    claim,
) -> ResumeProtocolOutcome
```

---

# 19. Durable store / repository API freeze

The implementation may split repository classes, but the durable semantic operations below are required.

```python
commit_waiting_checkpoint(spec: WaitingCheckpointCommitSpec)
    -> WaitingCheckpointCommitResult

load_checkpoint(checkpoint_id)
    -> ExecutionCheckpoint | None

load_current_checkpoint(execution_id)
    -> ExecutionCheckpoint | None

load_checkpoint_pending_invocations(checkpoint_id)
    -> tuple[CheckpointPendingInvocation, ...]

materialize_legacy_checkpoint(spec)
    -> ExecutionCheckpoint

get_or_create_resume_claim(intent)
    -> ResumeClaim

consume_resume_claim(spec: ResumeClaimConsumeSpec)
    -> ResumeClaimConsumeResult

reject_resume_claim(claim_id, expected_revision, code)
    -> ResumeClaim

expire_resume_claim(claim_id, expected_revision)
    -> ResumeClaim

commit_recovery_checkpoint_after_activation_failure(spec)
    -> ExecutionCheckpoint
```

Critical requirement:

```text
commit_waiting_checkpoint()
consume_resume_claim()
commit_recovery_checkpoint_after_activation_failure()
```

must be able to access Agent, TaskBudget and CapabilityInvocation rows through the same SQL UoW/session.

`SqlCapabilityInvocationStore` may remain as a façade, but R7 needs transaction-scoped repository primitives bound to an existing session instead of always opening an independent UoW.

---

# 20. Runtime handoff and failure semantics

## 20.1 Canonical happy path

```text
build ResumePlan
    ->
supervisor.reserve(E1)
    ->
create/get RC1(CREATED)
    ->
AgentRuntime atomic consume:
    E1 WAITING@N -> RUNNING@N+1
    RC1 CREATED -> CONSUMED
    TaskBudget reacquire if applicable
    bind K2
    ->
supervisor.start_reserved(...)
    ->
execution.resume.accepted
```

## 20.2 Activation failure after claim, before ACK

Do not silently convert to CANCELLED merely because process-local activation failed.

Default R7 policy when the original state remains reconstructable:

```text
E1 RUNNING@N+1
    ->
new checkpoint C2 with execution_revision=N+2
    ->
E1 WAITING(RECOVERY)@N+2
```

RC1 remains:

```text
CONSUMED
```

because it really acquired the lifecycle authority.

Wire response:

```text
execution.resume.failed
```

with a stable code such as:

```text
RUNTIME_HANDOFF_FAILED
```

and, when available:

```text
recovery_checkpoint_id=C2
state=WAITING
wait_reason=RECOVERY
```

If safe recovery checkpointing itself fails, AgentRuntime applies explicit fail-closed terminal policy and records the error; it must not report accepted.

## 20.3 Process crash after durable claim

A hard process crash after RUNNING claim is not fully solved by R7. It is the R12 execution-lease/recovery problem.

R7 must not pretend a process-local token is a distributed lease.

---

# 21. Wire protocol freeze

## 21.1 `execution.waiting`

Canonical server event for resumable work:

```json
{
  "type": "execution.waiting",
  "execution_id": "E1",
  "payload": {
    "execution_id": "E1",
    "checkpoint_id": "C1",
    "revision": 8,
    "wait_reason": "CONNECTION",
    "wait_expires_at": "...",
    "origin_client_id": "client-1",
    "pending_capability_ids": ["desktop.echo"],
    "auto_resume_allowed": true
  }
}
```

The event contains no capability arguments or secrets.

## 21.2 `execution.resume`

Canonical v3 request payload:

```json
{
  "execution_id": "E1",
  "checkpoint_id": "C1",
  "resume_request_id": "rr-uuid"
}
```

`connection_id` is authoritative from the authenticated WebSocket envelope/generation. A compatibility payload field may be accepted temporarily but must match the active connection and is not trusted independently.

## 21.3 `execution.resume.accepted`

Means the request acquired authority and the runtime task is owned by the supervisor.

```json
{
  "type": "execution.resume.accepted",
  "execution_id": "E1",
  "payload": {
    "execution_id": "E1",
    "checkpoint_id": "C1",
    "resume_request_id": "rr-uuid",
    "claim_id": "RC1",
    "accepted_revision": 9,
    "state_at_accept": "RUNNING"
  }
}
```

Duplicate delivery for the same consumed `resume_request_id` replays this semantic acceptance and does not reacquire authority.

## 21.4 `execution.resume.rejected`

Used when no execution authority was acquired.

Stable codes:

```text
STALE_CHECKPOINT
STALE_RESUME_CLAIM
STALE_RECONCILIATION_SNAPSHOT
EXECUTION_NOT_WAITING
WAIT_REASON_MISMATCH
WAIT_EXPIRED
CLAIM_EXPIRED
FOREIGN_CLIENT
FOREIGN_PRINCIPAL
CONNECTION_NOT_READY
PENDING_CAPABILITY_NOT_READY
REMOTE_RECONCILIATION_REQUIRED
REMOTE_OUTCOME_UNKNOWN
REMOTE_STILL_RUNNING
REMOTE_INVOCATION_CONFLICT
TASK_TERMINAL
RESUME_CONFLICT
RESUME_REQUEST_CONFLICT
```

## 21.5 `execution.resume.failed`

Used only after authority was acquired but runtime activation could not complete.

Stable initial code:

```text
RUNTIME_HANDOFF_FAILED
```

This event is distinct from `rejected` because the claim may already be CONSUMED.

## 21.6 Generic compatibility errors

Existing generic WebSocket errors may remain during migration but internal/domain code must first produce the stable R7 result. Compatibility formatting is a transport adapter concern.

---

# 22. Client PendingResumeTicket freeze

Canonical client model:

```python
PendingResumeTicket(
    execution_id: str,
    checkpoint_id: str,
    revision: int,
    wait_reason: str,
    pending_capability_ids: tuple[str, ...],
    origin_client_id: str | None,
    wait_expires_at: datetime | None,
    auto_resume_allowed: bool,
    active_resume_request_id: str | None,
)
```

Idempotency key:

```text
(execution_id, checkpoint_id)
```

ClientRuntime owns the ticket collection.

UI may display it but may not be the only place that remembers it.

---

# 23. Client reconnect / auto-resume state machine

```text
DISCONNECTED
    |
    v
BACKOFF
    |
    v
CONNECTING K2
    |
    v
connection.registered
    |
    v
REGISTERING_CAPABILITIES
    |
    v
capability.registered
    |
    v
READY
    |
    +--> inspect PendingResumeTickets
             |
             +-- not expired
             +-- wait_reason == CONNECTION
             +-- auto_resume_allowed
             +-- origin_client_id == this client_id
             +-- all pending capabilities registered
             |
             v
          execution.resume(E1,C1,resume_request_id)
```

Do not auto-resume:

```text
HUMAN_APPROVAL
EXPLICIT_PAUSE
RESOURCE unless explicit trigger policy
DEPENDENCY unless explicit trigger policy
RECOVERY unless server policy authorizes client action
```

Only one active wire resume attempt per ticket at a time.

On transport timeout / lost ACK:

```text
retry same resume_request_id
```

On `CLAIM_EXPIRED` or an explicitly retryable rejected attempt where E1 is still WAITING on C1:

```text
create a NEW resume_request_id
```

---

# 24. Authorization freeze

For a client reconnect resume:

```text
execution exists
state == WAITING
wait_reason == CONNECTION
current checkpoint matches request
checkpoint revision matches execution revision
wait TTL valid

checkpoint.owner_user_id == authenticated user
connection.user_id == authenticated user
connection is ACTIVE/usable

checkpoint.origin_client_id == current stable client_id
K2 != origin_connection_id

all pending client invocation origin_client_id values
    are compatible with the same client installation

all required capabilities registered/enabled on K2
Task not terminal/cancelled
R6 produces safe final actions
```

No cross-client fallback is implicit.

---

# 25. Legacy continuation migration freeze

Legacy source:

```text
AgentExecution.context_state["continuation"]
```

R7 policy:

```text
normalized-write / dual-read
```

Algorithm:

```text
if execution.current_checkpoint_id exists:
    use normalized checkpoint only

else if execution.state == WAITING and legacy continuation exists:
    materialize normalized checkpoint idempotently

else:
    not resumable through legacy path
```

## 25.1 Materialization identity

Use deterministic `legacy_source_key`, logically derived from:

```text
execution_id + legacy checkpoint identity
```

with UNIQUE enforcement.

Two workers materializing the same legacy safe point result in exactly one normalized checkpoint.

## 25.2 Revision

A materialized checkpoint uses the currently committed WAITING `AgentExecution.revision` as `execution_revision`.

Do not trust an absent or stale legacy revision field.

## 25.3 Pending invocations

Do not copy only one legacy `pending_invocation_id`.

Rebuild the unresolved set from durable:

```text
AgentIteration.tool_call_ids
AgentToolCall
AgentToolResult.commit_state
CapabilityInvocation
```

and R6 identity.

## 25.4 Transcript sanitization

Do not blindly copy a legacy transcript that contains provisional remote failure as a terminal tool result.

Materializer rebuilds/sanitizes:

```text
assistant/tool-call history
+
only COMMITTED tool outcomes
```

while unresolved calls remain represented by checkpoint pending rows.

## 25.5 End of legacy writes

Once normalized R7 persistence is enabled for an execution path:

```text
save_continuation_state()
reconnect() branch persistence
confirm_merge()
```

must not continue as a second writable authority.

Legacy data remains read-only migration input until removed in a later cleanup.

---

# 26. Multi-worker race contracts

## 26.1 Duplicate client resume

```text
A builds plan P1 for E1@N/C1
B builds plan P2 for E1@N/C1
A creates RCA
B creates RCB

one consume transaction wins E1 revision CAS
other loses

winner claim -> CONSUMED
loser claim  -> REJECTED RESUME_CONFLICT (or remains CREATED only until explicit rejection finalization)

one supervisor task
one authority acquisition
```

Wire request dedupe may collapse duplicates earlier when they share `resume_request_id`.

## 26.2 Client resume vs recovery worker

Both compete using:

```text
execution state
execution revision
current_checkpoint_id
```

and the same claim/transition contract.

Exactly one acquires RUNNING.

## 26.3 Reconciliation race

If a late remote terminal commits after plan construction but before claim:

```text
CapabilityInvocation revision changes
claim revalidation detects mismatch
no stale replay starts
plan is rebuilt
```

## 26.4 Old connection frame

Late K1 terminal frames cannot resolve K2 execution/reconciliation correlation namespaces and cannot overwrite a committed newer invocation state.

R6 remains authority here.

---

# 27. Crash semantics

## 27.1 Crash before WAITING transaction commit

No visible checkpoint/WAITING partial state.

## 27.2 Crash after WAITING commit

Restart can reconstruct entirely from normalized checkpoint + durable invocation state.

## 27.3 Crash after claim creation but before consume

Execution remains WAITING. Claim eventually expires or can be rejected. Wait TTL is unaffected.

## 27.4 Crash during consume transaction

Transaction atomicity yields either:

```text
WAITING + CREATED claim
```

or:

```text
RUNNING + CONSUMED claim
```

never a half state.

## 27.5 Crash after consume commit but before process-local handoff

This is the R12 lease/recovery gap. R7 documents it and must not mislabel the local supervisor token as a durable lease.

## 27.6 In-process handoff failure

R7 handles it immediately through a new recovery safe point before returning a failed response.

---

# 28. Resume plan fingerprint

`plan_fingerprint` is a deterministic hash over semantic plan content such as:

```text
execution_id
checkpoint_id
expected_execution_revision
iteration
ordered pending invocation actions:
    invocation_id
    tool_call_id
    capability_id/version
    request_fingerprint
    expected invocation revision/state/outcome
    final action

target client_id/connection_id
```

Exclude:

```text
created_at
volatile trace/log timestamps
non-authoritative metadata
```

The fingerprint is audit/idempotency evidence; it is not a substitute for reloading rows during claim consumption.

---

# 29. Implementation waves

## R7-A — Representation + migration

Implement only durable types/schema:

```text
AgentExecution current_checkpoint/binding columns
ExecutionCheckpoint
CheckpointPendingInvocation
ResumeClaim
AgentToolResult.commit_state
migration 13a
repository CRUD/CAS contracts
```

No wire behavior change yet.

### Exit gate

```text
migration upgrade/downgrade
constraints/indexes
model round trips
legacy data backfill for commit_state
```

---

## R7-B — Atomic WAITING checkpoint commit

Replace canonical `checkpoint_disconnect() + later execution finish` with one durable safe-point transaction.

Integrate TaskBudget release in the same UoW.

Capture all unresolved pending invocations.

### Exit gate

```text
WAITING always has normalized checkpoint
checkpoint revision == execution revision
CAS loser leaves no checkpoint
TaskBudget counts stay consistent
parallel pending set is complete
```

---

## R7-C — Tool result commitment + checkpoint-directed reconstruction

Implement:

```text
PROVISIONAL/COMMITTED semantics
committed-only transcript reconstruction
legacy transcript sanitization
multi-call deterministic ordering
```

### Exit gate

```text
OUTCOME_UNKNOWN never enters model transcript as committed
reconciled terminal promotes exact result once
```

---

## R7-D — R6-aware ResumePlan

Implement:

```text
normalized checkpoint load
R6 reconciliation matrix
invocation action classification
invocation revision snapshot
plan fingerprint
capability readiness validation
```

No execution state mutation during planning.

### Exit gate

```text
unsafe non-idempotent ambiguity never produces claimable plan
foreign client rejected
stale invocation snapshots detected
```

---

## R7-E — Existing invocation continuation

Add `CapabilityRuntime.continue_invocation()` semantics for:

```text
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

### Exit gate

```text
same invocation_id
same fingerprint
new attempt only
no duplicate invocation row
```

---

## R7-F — ResumeClaim + atomic AgentRuntime resume

Implement:

```text
resume_request_id dedupe
claim creation
claim expiry/rejection
atomic claim consumption
TaskBudget reacquire in same transaction
execution K2 binding
TOCTOU invocation revalidation
```

### Exit gate

```text
two claims -> one winner
same request retry -> same claim/outcome
stale reconciliation snapshot cannot start execution
```

---

## R7-G — Supervisor handoff + protocol

Reorder canonical path to:

```text
reserve -> consume claim -> start_reserved -> accepted ACK
```

Implement `accepted`, `rejected`, `failed` semantics.

### Exit gate

```text
no accepted ACK before owned runtime task
activation failure yields recovery checkpoint/no accepted ACK
lost ACK retry replays accepted claim
```

---

## R7-H — Client PendingResumeTicket

Implement ticket ingestion from `execution.waiting` and compatibility WAITING responses.

Auto-attempt only after READY.

### Exit gate

```text
stable client_id
new K2
capability registration complete
same ticket idempotent
same resume_request_id on ACK retry
```

---

## R7-I — Legacy materialization / authority removal

Implement normalized-read-first, legacy materialization, then stop canonical legacy writes.

Remove branch/merge from active resume flow.

### Exit gate

```text
legacy continuation is read-only input
no _branches/_merged authority
no confirm_merge on canonical resume path
```

---

## R7-J — Real network / restart / race exit gate

Run canonical real TCP WebSocket scenarios and broad regression.

R7 is not complete from architecture/unit tests alone.

---

# 30. Required test matrix

## 30.1 Schema / migration

```text
migration upgrades from 12a
migration downgrade
checkpoint indexes/uniques
resume_request_id unique
claim state constraint
tool-result commit-state backfill
```

## 30.2 WAITING atomicity

```text
checkpoint insert succeeds + execution CAS loses -> full rollback
TaskBudget release conflict -> no WAITING checkpoint
process error before commit -> RUNNING remains
successful commit -> WAITING/current_checkpoint/revision all consistent
```

## 30.3 Multi-pending

```text
2+ remote pending invocations captured
mixed committed + pending tool batch
original ordinal preserved
all required capabilities checked
```

## 30.4 Tool projection

```text
PROVISIONAL not returned by committed-result loader
PROVISIONAL excluded from transcript
TERMINAL reconciliation promotes exact result
conflicting committed result rejected
```

## 30.5 ResumePlan

```text
same-client valid plan
foreign user rejection
foreign client rejection
stale checkpoint rejection
wait TTL boundary rejection
capability-not-ready rejection
Task terminal rejection
R6 terminal reuse
R6 NOT_DISPATCHED action
R6 safe replay action
R6 non-idempotent unknown block
R6 RUNNING defer
```

## 30.6 TOCTOU

```text
build plan at invocation revision M
late terminal/reconciliation changes invocation to M+1
claim transaction rejects stale plan
no replay starts
rebuild plan reuses terminal outcome
```

## 30.7 Claim races

```text
two different claim IDs against E1@N/C1 -> one CONSUMED
same resume_request_id duplicate -> one durable claim
same resume_request_id different checkpoint -> conflict
claim expiry distinct from wait expiry
```

## 30.8 TaskBudget coupling

```text
resume claim + capacity reacquire atomic
claim conflict rolls back capacity
WAITING checkpoint + capacity release atomic
recovery checkpoint releases capacity again correctly
```

## 30.9 Supervisor / ACK

```text
start_reserved occurs before accepted ACK
start_reserved failure -> no accepted ACK
claim stays consumed after post-claim failure
new RECOVERY checkpoint revision is fresh
```

## 30.10 ACK loss

```text
claim consumed
supervisor task owned
accepted send dropped
client retries same resume_request_id
server replays accepted outcome
no second execution CAS
no second runtime task
```

## 30.11 Server restart

```text
restart while WAITING
load normalized checkpoint
build plan from DB only
same execution_id resumes
```

## 30.12 Legacy materialization

```text
legacy checkpoint -> one normalized checkpoint
concurrent materialization -> one row
legacy remote-error tool message sanitized
legacy singular pending input rebuilt to full durable set
```

## 30.13 Real TCP E2E

Canonical flow:

```text
real Uvicorn gateway
real GatewayRealtimeClient receiver
real capability registration
real remote tool
real disconnect K1
real WAITING checkpoint
new connection K2
real R6 reconciliation
real ResumeClaim
same E1 resumes
second inference/final completion
```

Mandatory assertions:

```text
execution_id unchanged
K1 != K2
client_id unchanged
checkpoint revision == WAITING revision
claim consumed exactly once
bound_connection_id == K2 after claim
side-effect count correct
invocation_id unchanged
request_fingerprint unchanged
no provisional result in transcript
one accepted ACK
no leaked pending Future/Task
```

---

# 31. R7 rejection / failure code freeze

Domain codes reserved by R7 v3:

```text
STALE_CHECKPOINT
STALE_RESUME_CLAIM
STALE_RECONCILIATION_SNAPSHOT
EXECUTION_NOT_WAITING
WAIT_REASON_MISMATCH
WAIT_EXPIRED
CLAIM_EXPIRED
FOREIGN_CLIENT
FOREIGN_PRINCIPAL
CONNECTION_NOT_READY
PENDING_CAPABILITY_NOT_READY
REMOTE_RECONCILIATION_REQUIRED
REMOTE_OUTCOME_UNKNOWN
REMOTE_STILL_RUNNING
REMOTE_INVOCATION_CONFLICT
TASK_TERMINAL
RESUME_CONFLICT
RESUME_REQUEST_CONFLICT
RUNTIME_HANDOFF_FAILED
LEGACY_CHECKPOINT_UNSAFE
CHECKPOINT_INCOMPLETE
```

Transport may map these to HTTP/WS presentation, but must not replace them with unstable free-form matching internally.

---

# 32. Files/modules expected to change during implementation

This is a blast-radius plan, not a patch.

Server domain/runtime likely:

```text
se/src/domain/schemas/agent_execution.py
se/src/runtimes/agent/contracts/continuation.py   (replace/deprecate legacy types)
se/src/runtimes/agent/contracts/resume.py         (recommended new contract module)
se/src/runtimes/agent/continuation.py
se/src/runtimes/agent/persistence.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/task_budget.py
se/src/runtimes/agent/supervisor.py
se/src/runtimes/capability/runtime.py
se/src/runtimes/capability/invocation.py
```

Persistence likely:

```text
se/src/infrastructure/storage/models/sql/agent/execution.py
se/src/infrastructure/storage/models/sql/agent/tool_result.py
new checkpoint model
new resume-claim model
se/src/infrastructure/storage/repositories/agent.py
se/src/infrastructure/storage/repositories/capability_invocations.py
se/src/infrastructure/storage/core/unit_of_work.py
se/src/infrastructure/storage/migrations/sql/versions/13a_*.py
```

Transport likely:

```text
se/src/runtimes/connection/protocol.py
se/src/transport/gateway/api/v1/events_router.py
```

Client likely:

```text
cl/src/core/realtime_client.py
cl/src/core/client_runtime.py
cl/src/core/capability_runtime.py
```

Tests likely:

```text
new R7 architecture contract tests
new R7 SQL integration tests
new R7 claim-race tests
new R7 real-TCP reconnect/resume E2E
existing R4/R5/R6 tests retained as regressions
```

---

# 33. Explicit compatibility decisions

## KEEP

```text
execution.resume wire concept
execution.resume.accepted concept
stable client_id
rotating connection_id
READY only after capability registration
R6 capability.reconcile protocol
R6 ClientInvocationLedger
AgentExecutionSupervisor process-local ownership
R4 active budget / wait TTL
R5 TaskBudget accounting
```

## MIGRATE

```text
ExecutionCheckpoint -> normalized SQL checkpoint
AgentContinuationService -> orchestration façade
DurableAgentStore.resume_execution -> checkpoint-directed plan reconstruction
resume_pending_tool_calls -> ResumeInvocationAction set
AgentToolResult -> explicit commitment projection
```

## DELETE FROM CANONICAL PATH

```text
ContinuationBranch
READY_TO_MERGE
CheckpointReason.READY_TO_MERGE
reconnect() branch creation semantics
confirm_merge()
_branches authority
_merged authority
context_state["continuation"] writes
server_continuation_available as replay-safety proof
```

Compatibility readers may remain temporarily until migration tests are green.

---

# 34. Deferred decisions

The following are intentionally not frozen as R7 implementation requirements:

```text
exact numeric default claim TTL
transcript delta/reference storage
cross-client resume
multiple client installations jointly satisfying one checkpoint
persistent distributed AgentExecution lease
automatic recovery of orphan RUNNING after process crash
TaskBranch/FORK
retry scheduler
```

They may not weaken any v3 invariant.

---

# 35. R7 implementation preconditions

Before writing R7-A production code:

```text
1. accept this v3 contract
2. reconcile R6-E status documentation with current harness/test evidence
3. preserve R4/R5/R6 exit-gate regressions
4. do not delete legacy reader support before materialization tests exist
```

R7 should be implemented in waves. Do not combine schema, transaction ownership, wire protocol and client auto-resume into one unreviewable patch.

---

# 36. Final R7 exit gate

R7 may be marked COMPLETE only when every condition below is demonstrated by tests on the implementation commit.

```text
[ ] every WAITING execution has a valid normalized current checkpoint
[ ] checkpoint.execution_revision == AgentExecution.revision for WAITING
[ ] checkpoint writes are immutable
[ ] parallel unresolved invocations are all represented
[ ] provisional tool outcomes never enter reconstructed model context
[ ] R6 terminal outcomes are reused exactly once
[ ] unsafe NON_IDEMPOTENT/UNKNOWN ambiguity is never blindly replayed
[ ] existing invocation continuation preserves invocation_id + fingerprint
[ ] R6 plan snapshot is revalidated atomically at claim consumption
[ ] ResumeClaim CONSUMED iff it won WAITING -> RUNNING
[ ] TaskBudget and execution/claim/checkpoint boundaries are atomic
[ ] same resume_request_id is idempotent across ACK loss/retry
[ ] same execution_id survives resume
[ ] target K2 binding is durable
[ ] one duplicate claimant wins
[ ] supervisor owns the runtime task before accepted ACK
[ ] post-claim activation failure creates a fresh durable recovery state/no accepted ACK
[ ] server restart while WAITING resumes from normalized DB state
[ ] legacy continuation is read-only migration input
[ ] ContinuationBranch/confirm_merge are absent from canonical resume path
[ ] real TCP K1 disconnect -> K2 reconnect -> reconciliation -> resume is green
[ ] broad repository regression is green
[ ] no unowned asyncio Future/Task diagnostics are introduced
```

---

# 37. Contract-freeze decision

With v3 accepted, the next coding phase is **R7-A — Representation + Migration**, not direct modification of `_resume_execution()`.

The required implementation order is:

```text
R7-A schema/contracts
    ->
R7-B atomic WAITING checkpoint
    ->
R7-C commitment/reconstruction
    ->
R7-D R6-aware ResumePlan
    ->
R7-E existing invocation continuation
    ->
R7-F ResumeClaim atomic consume
    ->
R7-G supervisor/wire protocol
    ->
R7-H client tickets
    ->
R7-I legacy authority removal
    ->
R7-J real TCP / restart / race exit gate
```

No R8 TaskBranch/FORK implementation should begin until this R7 exit gate is green.

**R7-0 status: CONTRACT FREEZE v3 READY FOR REVIEW — NO PRODUCTION CODE AUTHORIZED BY THIS DOCUMENT ITSELF.**
