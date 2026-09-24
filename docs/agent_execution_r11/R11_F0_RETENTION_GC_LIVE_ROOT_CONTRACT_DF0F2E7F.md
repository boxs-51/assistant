# AE-R11-F0 — Retention / GC Live-Root Contract Freeze

Status: **F0 CONTRACT / EVIDENCE CANDIDATE — NO PRODUCTION DELETE/GC**

Authority:
- Issue #31
- landed parent PR #60 audited HEAD: `389b727edad426fdee959a17f99221473a89a8df`
- parent merge commit / canonical main: `170dcf31811bc9cdc2822de4bb324ad293af0d2f`
- parent stage: R11-E2 MERGED / CLOSED / FINAL GREEN
- integration model: direct child of canonical main
- PR #60 is landed; F0 remains non-destructive and separately gated

## 1. Objective

R11-F must define liveness before it authorizes deletion.

F0 freezes:
1. the minimum Agent persistence live-root set;
2. the reachability closure required by those roots;
3. safe eligibility and deletion-order rules;
4. the separate R6 ClientInvocationLedger retention rule;
5. TaskBudget reservation replay/idempotency authority;
6. strict R11/R12 and R11/CAS/CTX ownership boundaries.

F0 does **not** implement a GC executor, repository DELETE API, TTL scanner, lease, stale-RUNNING interpretation, or destructive migration.

## 2. Fundamental rule

```text
AGE != COLLECTIBILITY

collectible(row)
    requires
        policy says row class may be retained/expired
    AND no live root reaches row
    AND no replay/reconstruction/provenance authority still needs row
    AND all ownership boundaries permit deletion
```

Wall-clock age alone never authorizes Agent history deletion.

## 3. Minimum Agent live roots

The following are live roots or create live-root edges.

### 3.1 Execution/checkpoint authority

- `AgentExecution.current_checkpoint_id`
- `AgentExecution.base_checkpoint_id` when the execution lineage still participates in active/replayable work
- any WAITING execution and its current checkpoint
- checkpoint pending invocation rows attached to a live checkpoint
- the checkpoint transcript representation referenced by `transcript_ref + transcript_version`, when present
- inline `transcript_snapshot` remains checkpoint-owned compatibility evidence

A WAITING execution is never collectible under R11-F.

### 3.2 Branch lineage

- `TaskBranch.base_checkpoint_id`
- `TaskBranch.current_execution_id`
- OPEN branch lineage
- terminal branch history (ADOPTED / SUPERSEDED / DISCARDED / CANCELLED) remains retained until a later policy proves it no longer participates in replay, audit, accepted-result provenance, or admission receipts

R11-F0 does not define a wall-clock TTL for terminal branches.

### 3.3 ResumeClaim

A ResumeClaim is live while it is usable or required as replay/history evidence.

At minimum:
- CREATED claims root `checkpoint_id` until consumed/rejected/expired under the existing claim state machine;
- claim state/history must not disappear merely because the checkpoint has an old timestamp;
- deletion of a checkpoint must never silently erase an active/usable claim via FK cascade.

Consumed/rejected/expired claim retention duration is a later policy decision; F0 only freezes the safety boundary.

### 3.4 Fork admission

Every immutable ForkAdmission roots:
- `source_branch_id`
- `source_execution_id`
- `source_checkpoint_id`
- resulting `branch_id`
- resulting `execution_id`
- runtime seed/fingerprint evidence needed to validate the committed fork result

ForkAdmission checkpoint/execution references use RESTRICT semantics and must remain meaningful until the receipt itself is policy-collectible.

### 3.5 Retry admission

Every immutable RetryAdmission roots:
- `branch_id`
- `source_execution_id`
- optional `source_checkpoint_id`
- resulting `execution_id`
- plan fingerprint / committed retry provenance

A NULL source checkpoint is valid for retry shapes that do not originate from a checkpoint; it does not authorize collection of the source execution while other replay/provenance roots remain.

### 3.6 Aggregate admission

AggregateAdmission contains immutable provenance rather than one direct checkpoint FK.

It roots, at minimum:
- `target_branch_id`
- resulting `execution_id`
- source branch snapshots
- source execution snapshots
- result fingerprints
- runtime seed fingerprint

F0 treats receipt provenance as durable history. It does not invent checkpoint IDs from JSON snapshots and does not reinterpret those snapshots as CAS/CTX identities.

### 3.7 TaskBudget and reservation replay authority

`TaskBudgetRecord` is Task-scoped accounting/CAS authority. `TaskBudgetReservationRecord` is durable idempotency and replay evidence, not generic TTL history.

Production mutation first resolves `(task_id, kind, reservation_key)`. An existing reservation with the same payload fingerprint resolves the logical mutation idempotently; reuse of the same key with a different fingerprint fails closed. Removing the reservation while the Task/logical request is still replayable would remove duplicate/conflict protection and could make an already-committed logical request look new.

Reservation kinds include:
- `NEW_EXECUTION`
- `RESUME_EXECUTION`
- `RELEASE_EXECUTION`
- `TOOL_CALL`
- `INFERENCE`
- `USAGE`
- `BRANCH`
- `RELEASE_BRANCH`

Frozen retention rules:
- a reservation must not be independently collected while its Task/logical request remains within replay/idempotency authority;
- reservation age alone never makes it collectible;
- if a future policy permits whole-Task collection, TaskBudget and reservations may disappear only as part of a proven whole-Task unreachable/collectible decision;
- `TaskBudgetRecord.task_id -> AgentTask.id ON DELETE CASCADE` and `TaskBudgetReservationRecord.task_id -> AgentTask.id ON DELETE CASCADE` are physical ownership edges only; CASCADE does not authorize Task collection or independent reservation collection.

R11-F preserves the existing TaskBudget CAS/accounting and reservation semantics; it does not redefine them.

### 3.8 Execution-side continuation and capability replay authority

A live or replayable AgentExecution roots the durable execution-side evidence required by FORK/retry/continuation correctness:

- `AgentIterationRecord` rows for that execution, including the checkpoint-matching iteration and its ordered `tool_call_ids`;
- `AgentToolCallRecord` rows that bind execution/iteration/tool-call/invocation/capability identity;
- `AgentToolResultRecord` rows whose COMMITTED state and identity are required to reconstruct model-visible tool results safely;
- `CapabilityInvocationRecord` rows associated semantically by `execution_id`, including remote-outcome and side-effect authority;
- `CapabilityInvocationAttemptRecord` rows under a retained invocation when attempt history remains required by retry/continuation/provenance semantics.

The Agent-side SQL children use physical CASCADE ownership:

```text
AgentExecution
  -> AgentIteration.execution_id          ON DELETE CASCADE
  -> AgentToolCall.execution_id           ON DELETE CASCADE
  -> AgentToolResult.execution_id         ON DELETE CASCADE

AgentIteration
  -> AgentToolCall.iteration_id            ON DELETE CASCADE
  -> AgentToolResult.iteration_id          ON DELETE CASCADE
```

These CASCADE edges do not authorize collection of a replayable execution.

`CapabilityInvocationRecord.execution_id` is intentionally **not** a foreign key to `agent_executions`. It is a semantic cross-authority GC edge. A future collector must therefore join/resolve it explicitly; FK traversal alone is incomplete.

`CapabilityInvocationAttemptRecord.invocation_id -> CapabilityInvocationRecord.invocation_id ON DELETE CASCADE` is physical ownership. Deleting an invocation may erase attempt history, so invocation collectibility must already have been proven under the existing R6 capability semantics.

Frozen replay rule:

```text
live/replayable execution
  -> checkpoint iteration exists
  -> required tool result exists and is COMMITTED
  -> tool-result execution/tool-call/capability identity matches
  -> matching CapabilityInvocation authority exists for the same execution
  -> retained invocation keeps any attempt history still required by policy
```

The real FORK-safe reader fails closed when any required iteration, COMMITTED result, or matching invocation authority is missing/inconsistent. R11-F preserves that authority but does not redefine R6 invocation lifecycle/retry semantics.

### 3.9 Execution semantic lineage authority

Several `AgentExecutionRecord` lineage fields are correctness authority but are intentionally stored as semantic identifiers rather than SQL foreign keys:

- `parent_execution_id` — delegation ancestry;
- `retry_of_execution_id` — retry source lineage;
- `base_execution_id` — fork/aggregate origin execution;
- `base_checkpoint_id` — fork/retry/aggregate origin checkpoint.

A future collector must resolve these edges explicitly. FK traversal alone is incomplete.

Frozen rules:

- a retained execution with `parent_execution_id` roots the ancestor execution chain needed to validate delegation ancestry/depth;
- a retained retry execution/receipt roots the execution referenced by `retry_of_execution_id` while retry replay/bootstrap/activation authority remains valid;
- retained fork/aggregate execution provenance roots the relevant `base_execution_id/base_checkpoint_id` source authority together with the existing Branch/admission receipt fences;
- an execution/checkpoint cannot be collected while another retained execution or durable receipt still needs it through one of these semantic lineage identifiers;
- these semantic edges complement RESTRICT/CASCADE FK behavior; they are not replaced by it.
- F1 lineage expansion must be deterministic and cycle-safe: traverse semantic lineage fields in a fixed order, track visited execution/checkpoint identities, and fail closed/retain on a detected cycle or inconsistent cross-Task ancestor rather than continuing deletion.

Concrete production readers already fail closed when the semantic lineage is broken:

```text
FORK delegation revalidation
  -> parent_execution_id
  -> get_execution(parent)
  -> missing/cross-task ancestor => FORK_EXECUTION_LINEAGE_CONFLICT

RETRY bootstrap
  -> retry_of_execution_id
  -> get_execution(source)
  -> missing source => RETRY_ADMISSION_CORRUPT
```

R11-F preserves these frozen R5/R8/R9 lineage semantics. It does not redefine delegation, retry, fork, aggregate, or R12 recovery behavior.

## 4. Transcript reachability closure

A live checkpoint with `transcript_ref + transcript_version` roots the exact `AgentTranscriptRepresentation`.

Reachability expands through:

```text
Checkpoint(transcript_ref, transcript_version)
  -> TranscriptRepresentation
       -> parent TranscriptRepresentation (for DELTA)
       -> payload_root_ref
            -> PayloadNode
                 -> parent_payload_root_ref
                 -> chunk_id
                      -> TranscriptChunk
```

Rules:
- checkpoint `parent_checkpoint_id` is **not** transcript ancestry;
- transcript ancestry comes only from transcript representation parent fields;
- payload ancestry comes only from payload-node parent fields;
- all representation/payload/chunk nodes needed to reconstruct a live checkpoint are live;
- a shared chunk/node is collectible only when no live representation closure reaches it.

R11-F must never infer liveness solely from checkpoint age or checkpoint-parent lineage.

## 5. Safe deletion-order contract

F0 freezes the order conceptually; it does not execute it.

### Phase A — snapshot roots

Build a deterministic root snapshot in one consistent persistence view:
- active/WAITING execution roots;
- branch roots;
- claim roots;
- admission receipt roots;
- TaskBudget/reservation replay-idempotency roots;
- execution-side iteration/tool-call/tool-result replay evidence;
- CapabilityInvocation semantic roots linked by execution_id, plus retained attempt history;
- AgentExecution semantic lineage roots through parent_execution_id / retry_of_execution_id / base_execution_id / base_checkpoint_id;
- explicit policy-retained terminal history;
- transcript roots referenced by rooted checkpoints.

R12 may later add recovery-specific roots. The R11 root model must remain extensible.

### Phase B — expand reachability

Expand:
1. Task -> TaskBudget/reservation physical ownership while preserving replay/idempotency authority;
2. execution/branch/claim/admission checkpoint edges;
3. live/replayable execution -> iteration/tool-call/tool-result evidence;
4. execution -> CapabilityInvocation semantic association by execution_id (not FK traversal);
5. retained CapabilityInvocation -> attempt children where attempt history remains required;
6. retained execution -> semantic delegation/retry/fork/aggregate source lineage, including ancestor chains where required;
7. checkpoint pending-invocation ownership;
8. checkpoint transcript representation roots;
9. transcript representation parent closure;
10. payload-node parent closure;
11. chunk reachability.

### Phase C — classify candidates

A row can become a candidate only when:
- its row class is explicitly policy-collectible;
- it is not a root;
- it is not reachable from any root;
- removing it does not destroy replay/reconstruction/provenance/idempotency evidence.

### Phase D — child/provenance authority before parent physical deletion

Before a checkpoint/execution/Task can be deleted, any collectible dependent claim/admission/branch/history/reservation authority must already have become safely collectible under its own policy or be part of a proven whole-owner collection decision.

Foreign-key CASCADE is a physical mechanism, not liveness authority.

In particular:
- checkpoint -> pending invocation CASCADE does not mean pending rows are disposable;
- checkpoint -> ResumeClaim CASCADE does not authorize deleting an active claim;
- execution -> checkpoints CASCADE does not authorize deleting a terminal execution whose checkpoints remain replay/audit roots;
- execution/iteration -> AgentIteration/AgentToolCall/AgentToolResult CASCADE does not authorize deleting FORK/replay evidence;
- CapabilityInvocation.execution_id has no AgentExecution FK, so whole-execution collection must explicitly prove the semantic invocation edge before either side is removed;
- CapabilityInvocation -> CapabilityInvocationAttempt CASCADE does not authorize deleting retained attempt/retry evidence;
- AgentExecution semantic lineage columns have no FK protection, so parent/retry/base execution/checkpoint sources must be checked explicitly before deletion;
- Task -> TaskBudget/TaskBudgetReservation CASCADE does not authorize deleting replay/idempotency evidence;
- RESTRICT edges from branch/fork/retry lineage are safety fences, not the complete GC algorithm.

### Phase E — transcript leaves to parents

For unreachable transcript storage:
1. remove collectible leaf representations before their referenced parents;
2. remove unreferenced payload-node leaves before parent payload nodes;
3. remove chunks only after no retained payload node references them.

No R11-F implementation may bypass RESTRICT constraints or disable FK safety to make GC pass.

## 6. R6 ClientInvocationLedger rule

ClientInvocationLedger is a separate R6 client SQLite authority.

Frozen behavior:

```text
TERMINAL + expires_at <= now
    -> collectible by the existing terminal TTL policy

RUNNING
    -> never generic-TTL collected
    -> preserved as crash/replay-safety evidence
```

R11-F may preserve/test this rule, but may not reinterpret RUNNING crash evidence as permission to retry or recover. Recovery interpretation remains outside R11-F.

## 7. R11 / R12 boundary

R11-F owns:
- retention/reachability policy for already-terminal/unreachable Agent history;
- safe deletion graph/order;
- future GC performance and correctness evidence.

R12 owns:
- owner_instance_id;
- execution leases;
- stale RUNNING detection;
- recovery scanner/coordinator;
- WAITING(RECOVERY);
- crash ownership races;
- deciding whether a RUNNING execution is abandoned/recoverable.

Therefore R11-F must not classify RUNNING execution history as collectible merely because it appears old.

## 8. CAS / CTX boundary

R11-F does not own:
- FileAsset / FileBlob / FileReference / FileProviderBinding lifecycle;
- object-storage physical GC;
- CAS reconciliation;
- CTX source lifecycle;
- Memory / Personalization / CompactContext retention.

Agent transcript refs remain Agent persistence identities.

CTX projections/digests are rebuildable projections unless a later explicit contract grants durable-root semantics; F0 grants no such ownership.

## 9. F0 deterministic evidence obligations

F0 regressions must prove:
1. every minimum live-root edge has a concrete schema field;
2. fork/retry checkpoint provenance uses FK RESTRICT where currently frozen;
3. checkpoint pending rows remain checkpoint-owned;
4. transcript representation/payload parent edges exist and use RESTRICT;
5. Task -> TaskBudget and Task -> TaskBudgetReservation physical edges exist and CASCADE is non-authorizing;
6. real TaskBudgetService behavior proves reservation replay authority: first logical reservation mutates once, same key+fingerprint is idempotent without a second mutation, and same key+different fingerprint fails closed;
7. execution-scoped iteration/tool-call/tool-result physical edges are explicit and CASCADE is non-authorizing;
8. CapabilityInvocation.execution_id is recorded as a semantic GC edge with no AgentExecution FK, while invocation-attempt CASCADE remains physical ownership only;
9. the real FORK-safe transcript reader fails closed for missing checkpoint iteration, missing/non-COMMITTED tool result, and missing/inconsistent CapabilityInvocation authority;
10. AgentExecution parent/retry/base lineage fields are proven semantic (no SQL FK), production delegation/retry readers fail closed when required source lineage is missing, and semantic ancestry expansion is deterministic/cycle-safe;
11. existing R6 ledger GC removes expired TERMINAL rows while preserving RUNNING rows;
12. no F0 production DELETE/GC API is introduced.

## 10. F0 exit gate

F0 may close when:
- contract + schema evidence are GREEN;
- TaskBudget reservation replay/idempotency regression is GREEN;
- R6 ledger retention regression is GREEN;
- exact-head Linux/Windows Architecture CI is GREEN;
- independent audit finds no P0/P1;
- no production deletion code is present;
- exact parent remains `df0f2e7f` or any stacked-parent drift is explicitly re-audited.

Only after F0 closure may an R11-F1 production-GC design be separately claimed.


## M2 post-E2 landing refresh

R11-E2 / PR #60 landed after exact-head Architecture #1185 GREEN/GREEN,
independent REFRESH FINAL GREEN, and explicit user confirmation.

Canonical M2 base is now:

```text
170dcf31811bc9cdc2822de4bb324ad293af0d2f
```

This F0 refresh follows the SERIAL LAND-THEN-REFRESH integration rule and
replays only the historical F0 semantic delta directly onto landed E2 main.

The client-ledger and architecture-test blobs are replayed byte-for-byte from
historical FINAL-GREEN F0 `db33f64d...`. Only this contract updates integration
history and parent authority.

No production DELETE/GC executor, repository DELETE API, TTL scanner, R12
recovery semantics, CAS physical lifecycle/GC, CTX lifecycle, R11-F1-C, or
R11-G/H authority is opened by this refresh.

Fresh exact-head Linux + Windows Architecture and refreshed independent Issue
#31 audit are required before any merge decision.
