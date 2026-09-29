# AE-R12-E — Atomic Recovery Ownership + WAITING(RECOVERY)

Baseline: `main@92afb23270705c8fc1afbf58599b9d70182ac607`  
Primary workspace: Issue #107  
Policy: Issue #85 v2.5

## Scope

R12-E turns one exact expired owned RUNNING execution into a normalized
`WAITING(RECOVERY)` safe point. It does not reconcile or replay invocations,
does not activate AgentRuntime/Supervisor work, does not schedule a background
scanner, and does not mutate UBQ/UserResourceBudget authority.

Production scope is limited to:

- `se/src/infrastructure/storage/repositories/agent.py`
- `se/src/runtimes/agent/persistence.py`
- `se/src/runtimes/agent/task_budget.py`
- `se/src/runtimes/agent/safe_point_reconstruction.py`

No SQL model or migration change is required.

## Frozen receipt

```text
execution_id
observed_owner_instance_id
observed_lease_generation
observed_lease_expires_at
takeover_now_utc
```

Both timestamps must be timezone-aware UTC with offset zero.

## Atomic winner

The repository CAS requires, in one SQL UPDATE predicate:

```text
id == execution_id
revision == source_revision
state == RUNNING
owner_instance_id == observed_owner_instance_id
lease_generation == observed_lease_generation
lease_expires_at == observed_lease_expires_at
lease_expires_at <= takeover_now_utc
```

A winner establishes:

```text
revision = source_revision + 1
state = WAITING
wait_reason = RECOVERY
owner_instance_id = NULL
lease_expires_at = NULL
lease_generation = observed_lease_generation + 1
current_checkpoint_id = <execution_id>:checkpoint:<target_revision>
```

The remaining active-time budget and execution/task/branch lineage are
preserved.

## Shared R7-C safe-point reconstruction

`reconstruct_r7c_safe_point_in_uow(...)` is caller-UoW, read-only authority.
It never commits/rolls back, never mutates CapabilityInvocation, and never
dispatches, reconciles or activates runtime work.

Rules:

1. durable iteration numbers must be unambiguous;
2. the current checkpoint, when present, is materialized through canonical
   checkpoint transcript authority;
3. an older checkpoint on stale RUNNING is an immutable prefix; the latest
   durable iteration is the current batch only when the later durable transcript
   proves it extends that prefix;
4. tool projections are model-safe only when AgentToolResult is COMMITTED;
5. active-batch tool messages are removed from the prefix and may be
   rematerialized later at most once;
6. `AgentIteration.tool_call_ids` is the only active-batch ordinal authority;
7. AgentToolCall and CapabilityInvocation identities are validated exactly;
8. unresolved/non-COMMITTED active slots are snapshotted only as evidence for
   later R12-F reconciliation.

Ordinals are never inferred from SQL row order, timestamps, row ids or
CapabilityInvocation query order.

## Crash-window safe-point closure

Two durable write-order crash windows are part of the R12-E recovery boundary:

1. **Before the first iteration is durable.** A valid recovery may publish
   `iteration=0` when there are no `AgentIteration` rows in the recovery
   snapshot. On subsequent resume, that safe point is accepted only when the
   execution is the exact current `WAITING(RECOVERY)` revision, the checkpoint
   is `RECOVERY@0`, and the checkpoint carries the R12-E recovery fingerprint.
   A stale expired worker may later publish an `AgentIteration` row; such a
   post-snapshot row cannot retroactively redefine the already-published
   recovery safe point and is ignored while this exact checkpoint remains the
   current recovery authority. No iteration row is synthesized. Recovery
   activation beyond this safe point remains R12-F authority.

2. **Inference transcript persisted before active tool order.** Runtime persists
   the inference response into the durable execution transcript before the
   following `AgentIteration.tool_call_ids` update. If recovery observes an
   unresolved assistant tool-call declaration while the authoritative active
   iteration has no `tool_call_ids`, recovery fails closed before checkpoint,
   lease, or TaskBudget mutation. Resolution is proved only against the
   sanitized COMMITTED tool-result projection; a raw/provisional tool transcript
   message never clears a pending assistant declaration. Transcript-declared
   tool IDs are evidence of an incomplete write only; they never become ordering
   authority.

These rules preserve the core R7-C invariant that
`AgentIteration.tool_call_ids` is the only live active-batch ordering authority
before a recovery cut. At the R12-E cut itself, the checkpoint freezes that
authority into immutable checkpoint metadata:

- `r12_recovery_iteration_id`
- `r12_recovery_active_tool_call_ids`

A current R12-E `WAITING(RECOVERY)` checkpoint must reconstruct its active batch
from that frozen metadata rather than rereading a subsequently mutable
`AgentIteration.tool_call_ids` row. This prevents an expired worker's late
iteration update from adding abandoned calls to an already-published safe point.

For any current normalized checkpoint, if the sanitized/COMMITTED transcript
still declares an unresolved assistant tool batch while the authoritative active
ID set is empty, reconstruction fails closed with
`SAFE_POINT_ACTIVE_BATCH_AUTHORITY_MISSING`. The only special recovery case is
the exact R12-E iteration-zero safe point, whose empty batch is still validated
against the same sanitized transcript rule.

These rules prevent a recovery or ordinary R7-C checkpoint from orphaning
assistant tool requests.


At an R12-E recovery cut with a non-empty authoritative active batch, the
sanitized/COMMITTED transcript must prove the exact same unresolved tool-call
membership as the authoritative ID set. Equality is set-based proof only:
`AgentIteration.tool_call_ids` (or its frozen recovery snapshot) remains the
sole ordering authority. Missing or extra transcript IDs fail closed with
`SAFE_POINT_ACTIVE_BATCH_MEMBERSHIP_MISMATCH` before any recovery mutation.


After a recovery checkpoint is later consumed and the execution semantic
revision advances, that checkpoint may remain the durable prefix named by
`current_checkpoint_id`. While it remains an R12-E recovery checkpoint:

- a durable iteration whose number is equal to the frozen checkpoint iteration
  is not post-resume progress and must not replace the frozen batch snapshot;
- same-iteration late transcript/tool-call/invocation writes from the expired
  worker are ignored in favor of the immutable checkpoint transcript and frozen
  active-batch metadata;
- only a durable iteration with a number strictly greater than the frozen
  checkpoint iteration may become the newer current batch candidate;
- until such strictly-later progress exists, repeated R12-E recovery republishes
  the frozen recovery safe point rather than promoting stale same-iteration
  writes.

This is a provenance rule, not R12-F activation authority.

## Checkpoint publication

R12-E reuses canonical `stage_waiting_checkpoint(...)` inside the same winning
UoW. The recovery checkpoint is deterministic and its metadata binds a SHA-256
fingerprint of the exact recovery receipt and target safe-point semantics.

If the final fenced execution CAS loses, checkpoint/transcript/pending-snapshot
writes roll back with the transaction.

## TaskBudget atomicity

For task-scoped execution, the same UoW owns:

```text
receipt revalidation
+ R7-C safe-point reconstruction
+ active_executions decrement
+ active_parallel_agents decrement iff delegated
+ stage_waiting_checkpoint
+ fenced execution recovery CAS
+ RELEASE_EXECUTION reservation
+ commit
```

Canonical reservation identity remains:

```text
kind = RELEASE_EXECUTION
key  = execution_id:target_revision
```

Recovery-specific receipt semantics are bound in the existing
`payload_fingerprint`. Cumulative counters such as used executions, tool
calls, inference calls, tokens and cost are not reset or refunded.

## Uncertain-commit replay

A retry returns an already committed winner only when durable state proves the
same frozen receipt:

- WAITING + RECOVERY;
- owner/expiry cleared;
- generation equals observed generation + 1;
- deterministic checkpoint at current execution revision;
- checkpoint recovery fingerprint matches;
- task-scoped canonical RELEASE_EXECUTION reservation exists and its
  fingerprint matches.

Replay performs zero second budget decrement, zero checkpoint insertion and
zero lease mutation.

## UBQ boundary

R12-E does not:

- create or roll UserBudgetWindow;
- reset/refill/mint renewable user quota;
- refund committed user-resource usage;
- reinterpret an existing budget epoch under mutable defaults;
- mutate UBQ reservations/windows;
- double-charge a logical invocation.

Unknown remote outcomes remain R6 reconciliation authority. UBQ-1 remains
path-disjoint from this implementation unless a later bilateral audit changes
that classification.

## Evidence matrix

The R12-E architecture/integration tests cover the released E-I01..E-I22
contract, including:

- exact lease receipt and expiry boundary;
- release/reacquire/renew/terminal/race rejection;
- one durable recovery winner;
- one generation/revision advance;
- WAITING revision equals checkpoint revision;
- pending ordinal from `AgentIteration.tool_call_ids`;
- non-COMMITTED projection exclusion;
- canonical RELEASE_EXECUTION identity;
- exact uncertain-commit replay;
- task active-slot release exactly once;
- cumulative usage preservation;
- zero mutation on stale receipt.

R12-F/G/H remain closed. Merge authority remains NONE.
