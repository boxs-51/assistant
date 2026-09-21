# R7 — Durable Resume / Normalized Checkpoint Contract v2

**Repository:** `boxs-51/assistant`  
**Baseline commit:** `e5f7898667556b120261663d8c6d712ddb16363d`  
**Prerequisites:** R2.1 PASS, R3 PASS, R4/R5 contracts available, R6 reconciliation/idempotency PASS  
**Document status:** FINAL-REVIEW CANDIDATE / CONTRACT v2  
**Implementation status:** **NO R7 CODE / NO R7 PATCH**

---

## 1. Purpose

R7 replaces legacy Phase 6.9 continuation branch/merge semantics with two normalized durable concepts:

```text
ExecutionCheckpoint
ResumeClaim
```

R7 does not create a new execution when resuming:

```text
WAITING E1 -> RUNNING E1
```

R7 must preserve one lifecycle authority, eliminate ACK-before-CAS races, prevent replay of unsafe remote side effects, and make server restart/multi-worker resume deterministic.

---

## 2. Legacy migration decisions

### KEEP

```text
execution.resume
execution.resume.accepted

stable client_id
new connection_id for each WS generation
READY only after capability registration ACK

RealtimeMultiplexer invocation correlation
RemoteClientDriver hard connection affinity
CapabilityDispatcher running dedupe
CapabilityDispatcher terminal replay concept
DurableAgentStore rehydration responsibility
committed tool-result reuse
```

### MIGRATE

```text
ExecutionCheckpoint concept
AgentContinuationService responsibility
RemoteConnectionLost -> R6 outcome classification
TerminalOutcome cache -> durable invocation ledger + hot cache
resume_pending_tool_calls -> R6 reconciliation-aware pending state
WS _resume_execution handler -> ResumePlan/ResumeClaim/AgentRuntime resume entry
context_state["continuation"] -> legacy read-only migration source
```

### DELETE / REPLACE

Before R8 TaskBranch becomes active:

```text
ContinuationBranch
ContinuationState.READY_TO_MERGE
CheckpointReason.READY_TO_MERGE
reconnect() branch creation semantics
confirm_merge()
_branches in-memory authority
_merged in-memory authority
legacy branch/merge vocabulary
server_continuation_available as sufficient replay-safety proof
```

---

## 3. Single lifecycle authority

This v2 resolves the R2/R7 ownership conflict.

### R7-I01 — AgentRuntime owns AgentExecution lifecycle transitions

Transport and ResumeService may validate and orchestrate claims, but they are not a second execution-state authority.

Canonical ownership:

```text
Transport / ResumeService
    -> authentication
    -> connection/client/checkpoint validation
    -> build immutable ResumePlan
    -> create ResumeClaim intent

AgentRuntime resume entry / AgentRuntime-owned lifecycle component
    -> atomically consumes claim
    -> CAS WAITING -> RUNNING
    -> continues E1
```

The canonical R7 runtime entry is conceptually:

```text
AgentRuntime.resume(resume_plan, resume_claim)
```

Exact class/method naming is implementation detail, but the authority split is frozen.

### Compatibility rule

Legacy code may temporarily call adapters that delegate to this canonical resume entry. Transport must not directly mutate `AgentExecution.state`.

---

## 4. Resume identity invariants

### R7-I02 — resume keeps the same execution ID

```text
WAITING E1 -> RUNNING E1
```

### R7-I03 — only WAITING is resumable

Terminal states never return to RUNNING.

### R7-I04 — resume preserves execution lineage

Resume does not change:

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

Only routing/runtime binding may change.

### R7-I05 — connection identity is routing affinity

A resume may bind E1 to a new connection generation K2 while preserving E1.

---

## 5. WAITING/checkpoint invariant

### R7-I06 — WAITING requires a durable safe point

A runtime may release active ownership only when enough durable state exists to reconstruct execution safely.

### R7-I07 — checkpoint is immutable

Checkpoint rows are append-only. Newer checkpoints reference earlier ones where useful; they do not mutate old checkpoint content.

### R7-I08 — AgentExecution remains lifecycle authority

Checkpoint records observed/committed facts about a safe point. It does not own mutable execution lifecycle state.

---

## 6. `AgentExecution.current_checkpoint_id`

R7 freezes a first-class nullable durable pointer on `AgentExecution`:

```text
current_checkpoint_id
```

For a WAITING execution:

```text
state == WAITING
=> current_checkpoint_id != null
=> referenced checkpoint.execution_id == execution_id
=> referenced checkpoint.execution_revision == execution.revision
```

For RUNNING/terminal state, retaining the last checkpoint for audit is allowed, but resume authority is determined by state + revision + current checkpoint contract.

---

## 7. Normalized `ExecutionCheckpoint`

Suggested table:

```text
agent_execution_checkpoints
```

Target fields:

```text
checkpoint_id                 PK
execution_id                  indexed
execution_revision            committed WAITING revision

session_id
task_id                       nullable
branch_id                     nullable until R8

parent_checkpoint_id          nullable
iteration

wait_reason                   required for WAITING checkpoint

remaining_active_budget_seconds    supplied by R4
wait_expires_at                    supplied by R4

pending_invocation_id         nullable
pending_tool_call_id          nullable
pending_capability_id         nullable

origin_client_id              nullable
origin_connection_id          nullable

transcript_snapshot           nullable JSON
transcript_ref                nullable
transcript_version            nullable/int

side_effect_watermark         nullable/reference
metadata                      small JSON-safe metadata

legacy_source_key             nullable/unique for legacy materialization

created_at
```

### Transcript contract

R7 correctness requires a reconstructable transcript state, not an R11 storage optimization.

First R7 implementation may use:

```text
transcript_snapshot
```

Future R11 may migrate to:

```text
transcript_ref + transcript_version + snapshot/delta strategy
```

Invariant:

```text
checkpoint must have at least one reconstructable transcript representation
```

R7 does not require the R11 storage engine.

### Side-effect contract

`side_effect_watermark` is a compact reference to the durable capability/tool outcome authority. It must not become an unbounded copied list.

---

## 8. Correct checkpoint revision semantics

This v2 resolves the stale-at-creation conflict.

Given:

```text
E1 RUNNING revision=N
```

the WAITING transaction computes:

```text
waiting_revision = N + 1
```

and commits:

```text
checkpoint C1.execution_revision = N + 1

E1:
    state = WAITING
    revision = N + 1
    current_checkpoint_id = C1
```

Frozen invariant:

```text
checkpoint.execution_revision
==
execution.revision of the WAITING state that owns the checkpoint
```

A checkpoint insert whose associated execution CAS loses must roll back.

---

## 9. Mandatory atomic WAITING transaction

The former “where feasible / preferred” wording is removed.

For a transactional SQL store, the following **MUST be atomic**:

```text
required pending invocation/tool durability state
+ immutable checkpoint insert
+ AgentExecution RUNNING@N -> WAITING@N+1 CAS
+ wait_reason
+ current_checkpoint_id binding
+ remaining_active_budget/wait expiration state
```

If capability-invocation and Agent persistence currently use separate repository objects, R7 implementation must coordinate them through one UoW/transactional continuation repository for this boundary.

A non-transactional durable store may implement an equivalent CAS/recovery protocol only if it preserves the same externally visible invariant. It may not expose WAITING without a valid checkpoint.

---

## 10. `ResumePlan`

R7 introduces an immutable pre-CAS reconstruction/validation product:

```text
ResumePlan
```

Conceptual contents:

```text
execution_id
checkpoint_id
expected_execution_revision

agent identity/definition reference
session/task/branch/lineage
limits and remaining active budget
transcript reconstruction state
iteration position

pending invocation/tool identity
reconciled committed outcomes
R6 replay/reconciliation decision

target user/client/connection binding
request/correlation/trace context
```

### ResumePlan rule

All deterministic reconstruction prerequisites must be validated **before** claiming RUNNING authority.

Building a ResumePlan must not change execution state.

---

## 11. Normalized `ResumeClaim`

Suggested table:

```text
agent_resume_claims
```

Target fields:

```text
claim_id                      PK
execution_id                  indexed
checkpoint_id                 indexed
expected_execution_revision

user_id
client_id                     nullable for non-client triggers
connection_id                 nullable

wait_reason
trigger_type

state
revision

rejection_code                nullable
metadata                      small JSON-safe metadata

created_at
claim_expires_at
consumed_at                   nullable
```

### Frozen claim states

v2 removes the ambiguous durable `ACCEPTED` state.

```text
CREATED
CONSUMED
REJECTED
EXPIRED
```

Semantics:

- `CREATED`: durable intent exists; it owns nothing.
- `CONSUMED`: claim won the execution lifecycle CAS.
- `REJECTED`: validation/CAS rejected it.
- `EXPIRED`: claim attempt TTL elapsed before consumption.

Wire `execution.resume.accepted` is never inferred from `CREATED`.

---

## 12. Separate wait and claim clocks

R4 execution waiting TTL and R7 claim TTL are different contracts:

```text
wait_expires_at
    = how long E1 may remain WAITING

claim_expires_at
    = short lifetime of one unconsumed ResumeClaim attempt
```

Claim expiration does not itself timeout E1.

---

## 13. CONNECTION resume authorization

For `WAITING(CONNECTION)`, default policy requires:

```text
execution exists
execution.state == WAITING
execution.wait_reason == CONNECTION

current checkpoint exists
checkpoint belongs to execution
checkpoint.execution_revision == execution.revision
checkpoint_id == execution.current_checkpoint_id

wait_expires_at not exhausted

authenticated user authorized for execution
new connection ACTIVE/usable
connection user == authenticated user

same client_id as origin_client_id by default
new connection generation valid
required pending capability registered/ready

Task not terminal/cancelled

R6 reconciliation says continuation/replay is safe
```

Cross-client resume requires an explicit future policy; it is not implicit.

---

## 14. R6 reconciliation prerequisite

R7 may not blindly redispatch pending tool calls.

For each pending invocation:

```text
durable CapabilityInvocation / ToolResult lookup
    |
    +-- terminal committed outcome
    |      -> reuse exact outcome
    |
    +-- known NOT_DISPATCHED
    |      -> R6 may permit dispatch
    |
    +-- OUTCOME_UNKNOWN
           -> ClientInvocationLedger reconciliation
                |
                +-- terminal found -> reuse/replay exact terminal result
                +-- IDEMPOTENT/DEDUPLICATED -> controlled same-invocation policy
                +-- NON_IDEMPOTENT/UNKNOWN -> remain WAITING / HITL / fail-safe
```

Stable `invocation_id` is mandatory.

`server_continuation_available` alone is never proof of replay safety.

---

## 15. Mandatory atomic resume transaction

Once a valid ResumePlan and `ResumeClaim(CREATED)` exist, the AgentRuntime-owned lifecycle boundary performs one atomic transaction:

```text
re-check execution state/revision/current checkpoint
re-check claim state/expiry

CAS E1:
    WAITING@N -> RUNNING@N+1
    wait_reason -> null
    bind current client/connection routing metadata

ResumeClaim:
    CREATED -> CONSUMED
    consumed_at = now
```

Exactly one concurrent claimant may win.

Frozen post-condition:

```text
claim == CONSUMED
iff
that claim acquired the corresponding WAITING -> RUNNING authority
```

Transport cannot perform this transaction directly.

---

## 16. Safe runtime handoff before ACK

This v2 resolves the RUNNING-but-not-runnable ACK window.

### Pre-claim

Before lifecycle CAS:

```text
build and validate ResumePlan
reserve runtime supervisor capacity/handoff token
validate Agent definition/context reconstruction
validate R6 reconciliation decision
```

No execution state changes yet.

### Claim

AgentRuntime-owned atomic claim changes E1 to RUNNING and consumes the claim.

### Post-claim

Using the already validated plan:

```text
instantiate runtime context
register/activate continuation task with runtime supervisor
```

### ACK rule

Only after supervisor ownership is accepted:

```text
send execution.resume.accepted
```

If deterministic handoff/activation fails before ACK, AgentRuntime lifecycle handling must transition to a durable safe state (for example WAITING/RECOVERY or FAILED according to policy) before returning an error.

A process crash after durable claim/handoff is the R12 lease/recovery problem.

---

## 17. Canonical resume flow

```text
CLIENT K2
  |
  | execution.resume(E1, C1)
  v
WS transport
  |
  v
ResumeService
  |
  +-- auth/user/client/connection validation
  +-- load E1 + C1
  +-- validate WAITING/revision/current checkpoint/TTL
  +-- validate capability READY
  +-- R6 reconciliation
  +-- build immutable ResumePlan
  +-- reserve runtime supervisor handoff capacity
  |
  v
create ResumeClaim RC1(CREATED)
  |
  v
AgentRuntime.resume(plan, RC1)
  |
  +-- AgentRuntime-owned transaction
  |      E1 WAITING@N -> RUNNING@N+1
  |      RC1 CREATED -> CONSUMED
  |      bind K2/client routing state
  |
  +-- instantiate validated context
  +-- activate runtime supervisor task
  |
  v
execution.resume.accepted
  |
  v
continue E1
```

No second call may perform another `_begin_durable_execution()` CAS for the same resume. The canonical resume path enters execution after the AgentRuntime-owned claim has already established its revision.

Implementation may use a dedicated internal “execute already-claimed execution” function to avoid double-start logic.

---

## 18. Accepted/rejected wire semantics

### KEEP

```text
execution.resume
execution.resume.accepted
```

### `execution.resume.accepted`

Means all of:

```text
claim CONSUMED
execution durably RUNNING
resume authority acquired
runtime supervisor accepted continuation ownership
```

Recommended payload:

```text
execution_id
checkpoint_id
claim_id
state=RUNNING
revision
```

### Rejection

Recommended future event:

```text
execution.resume.rejected
```

Stable codes:

```text
STALE_CHECKPOINT
STALE_RESUME_CLAIM
EXECUTION_NOT_WAITING
WAIT_REASON_MISMATCH
WAIT_EXPIRED
CLAIM_EXPIRED
FOREIGN_CLIENT
CONNECTION_NOT_READY
PENDING_CAPABILITY_NOT_READY
REMOTE_RECONCILIATION_REQUIRED
REMOTE_OUTCOME_UNKNOWN
TASK_TERMINAL
RESUME_CONFLICT
RUNTIME_HANDOFF_FAILED
```

Compatibility generic WS errors may remain until R13, but internal codes must stay stable.

---

## 19. Client `PendingResumeTicket`

Target:

```text
PendingResumeTicket:
    execution_id
    checkpoint_id
    wait_reason
    pending_capability_id
    origin_client_id
    wait_expires_at
    auto_resume_allowed
```

Idempotency key:

```text
(execution_id, checkpoint_id)
```

### Canonical ingestion

When possible, server emits:

```text
execution.waiting
```

to the originating client/runtime with the ticket payload.

### Compatibility ingestion

HTTP/SSE/chat WAITING payload parsing may register the same ticket into ClientRuntime.

Ticket insertion is idempotent. UI code is not the authority for remembering resumable work.

### Reconnect action

After:

```text
new connection generation
-> connection.registered
-> capability.registered
-> ClientRuntime READY
```

ClientRuntime may auto-attempt tickets where:

```text
wait_reason == CONNECTION
auto_resume_allowed == true
origin_client_id == this installation
wait_expires_at not exhausted
```

Do not auto-resume HUMAN_APPROVAL or EXPLICIT_PAUSE.

---

## 20. Legacy continuation materialization

Legacy source:

```text
AgentExecution.context_state["continuation"]
```

### R7 migration rule

Normalized-write / dual-read only.

```text
if normalized current checkpoint exists:
    use normalized source
else if legacy continuation exists:
    materialize one normalized checkpoint idempotently
else:
    not resumable
```

### Race-safe materialization

Two workers may discover the same legacy safe point. Materialization must be idempotent by one of:

```text
deterministic checkpoint_id from legacy identity
or
unique legacy_source_key
or
unique transaction key + reload-on-conflict
```

v2 freezes `legacy_source_key` as an allowed nullable unique field.

Only one normalized checkpoint may represent one legacy safe point.

### Dual-write guarantee

R2.1 only guarantees sequential preservation of an observed legacy continuation member.

R7 is the phase that eliminates concurrent dual-writer authority by stopping legacy continuation writes after normalized persistence is authoritative.

---

## 21. Transcript migration boundary

R7 correctness-first implementation may persist full `transcript_snapshot`.

R11 owns optimization to snapshot/delta/reference storage.

During transition:

```text
transcript_snapshot != null
OR
transcript_ref != null
```

must make reconstruction deterministic.

R7 must not block on R11.

---

## 22. `AgentContinuationService` migration target

KEEP the service concept only as orchestration/compatibility façade.

Target responsibilities:

```text
load/validate checkpoint
materialize legacy checkpoint idempotently
build/validate ResumePlan
create ResumeClaim intent
coordinate trigger authorization
call AgentRuntime canonical resume entry
publish waiting/resume/rejection events
```

It must not own:

```text
AgentExecution lifecycle mutation
in-memory branch authority
merge authority
TaskBranch semantics
```

---

## 23. `DurableAgentStore` target responsibilities

R7 extends durable APIs to support:

```text
create WAITING checkpoint + execution transition atomically
load checkpoint/current checkpoint
legacy materialization
create ResumeClaim
atomically consume claim + start execution through AgentRuntime lifecycle boundary
checkpoint-directed reconstruction
```

`resume_execution()` becomes checkpoint/ResumePlan-directed reconstruction rather than “load latest and retry pending tools blindly.”

Rehydration itself does not decide replay safety; R6 does.

---

## 24. Multi-worker race contract

### Duplicate resume

```text
A and B build claims against E1@N/C1
one AgentRuntime-owned CAS wins
one loses
one runtime continuation activates
one accepted ACK
```

### Recovery worker vs client resume

Both compete on state/revision/current checkpoint. One wins; loser reloads and exits/rejects.

### Stale connection

Old generation K1 cannot resume after validated K2 ownership conditions are established.

### Duplicate legacy materialization

Unique legacy source identity yields one normalized checkpoint.

---

## 25. Required persistence constraints/indexes

Recommended:

```text
agent_executions.current_checkpoint_id          nullable/indexed as needed

agent_execution_checkpoints.checkpoint_id       PK
agent_execution_checkpoints.execution_id        index
agent_execution_checkpoints.legacy_source_key   nullable UNIQUE

agent_resume_claims.claim_id                    PK
agent_resume_claims.execution_id                index
agent_resume_claims.checkpoint_id               index
agent_resume_claims.state                       index
```

Foreign-key decisions must be consistent with current repository migration policy; semantic ownership cannot be weakened even if DB FKs remain intentionally minimal.

---

## 26. Required test matrix

R7 cannot pass without:

```text
same-client reconnect resume
foreign-client rejection
stale checkpoint rejection
checkpoint revision equals WAITING committed revision
duplicate resume race: one winner/one ACK
server restart while WAITING
legacy materialization race/idempotency
claim expiry distinct from wait expiry
capability-not-ready rejection
committed tool result not replayed
OUTCOME_UNKNOWN non-idempotent not replayed
new connection generation binding
runtime handoff failure produces no accepted ACK
real TCP WebSocket reconnect E2E
```

Atomicity tests must use the real transactional store, not only in-memory fakes.

---

## 27. R7 exit gate

R7 is complete only when all are true:

```text
WAITING always has a valid current normalized checkpoint
checkpoint revision equals WAITING execution revision
resume lifecycle state has one authority
ResumeClaim consumption + WAITING->RUNNING is atomic
only one duplicate claimant wins
accepted ACK follows runtime ownership handoff
same execution_id survives resume
R6 prevents unsafe side-effect replay
legacy continuation is read-only migration input, not writable authority
ContinuationBranch/confirm_merge semantics are removed from canonical path
```

---

## 28. v2 change log

This v2 resolves all four P0 findings:

1. Lifecycle CAS authority is explicitly AgentRuntime-owned.
2. Checkpoint revision is the committed WAITING revision.
3. WAITING/checkpoint and resume/claim boundaries are mandatory atomic units.
4. ResumePlan + supervisor handoff must succeed before accepted ACK.

It also resolves R7-related P1 findings:

5. Transcript storage no longer prematurely requires R11 reference/delta infrastructure.
6. Legacy materialization is race-safe/idempotent.
7. `PendingResumeTicket` ingestion is explicitly defined.
8. `wait_expires_at` and `claim_expires_at` are separate.
9. R2.1 legacy continuation preservation is explicitly narrow; R7 owns elimination of dual-write races.

**Status:** ready for final architecture review; no R7 implementation has been performed.
