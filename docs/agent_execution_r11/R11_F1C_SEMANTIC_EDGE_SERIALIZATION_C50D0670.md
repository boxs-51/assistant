# R11-F1-C semantic inbound-edge serialization hardening

Status: refreshed Repair-B candidate for `P1-R11-F1C-SEMANTIC-EDGE-RACE-1`.

Exact baseline:

```text
canonical main = 4e1e5cb3a90412e74a82d6248e53e57dfae30a90
Repair A / PR #77 = MERGED
post-PR#77 Architecture #1220 / run 36104672013 = GREEN/GREEN
CTX-F5-0 / PR #80 = MERGED; contract/evidence-only drift is path-disjoint from Repair B
historical Repair-B Architecture #1227 = GREEN/GREEN on pre-refresh HEAD 5c18020e
repair branch = work/ae-r11-f1c-semantic-edge-race-200aa3dc
branch name is historical; current candidate is re-anchored to exact main@4e1e5cb3
fresh exact-head Architecture after this re-anchor is REQUIRED
```

## Problem

F1-B now has the static inbound closure required to detect retained semantic
references into a candidate Task graph. Static reverse scans alone are not a
serialization authority. A writer that can create a new semantic inbound edge
after F1-C's in-transaction revalidation can still leave a dangling reference
unless the writer and collector share a deterministic fence or the writer
contract makes cross-lineage edges impossible.

The owner/auditor writer audit confirmed three concrete Repair-B classes:

1. `AgentTaskRecord.parent_task_id`;
2. `AgentExecutionCheckpointRecord.parent_checkpoint_id`;
3. fresh `AgentToolCallRecord.invocation_id` before R6 invocation creation.

## 1. Task parent serialization

A shared Task-scoped GC serialization fence is provided by
`AgentRepository.lock_task_gc_serialization_fence(task_id)`.

Row-locking dialects use the canonical Task `FOR UPDATE` row lock. SQLite
ignores `FOR UPDATE`, so the fence performs a semantic no-op UPDATE:

```text
revision = revision
updated_at = updated_at
```

Child Task creation with non-null `parent_task_id` must acquire that parent
fence and re-prove the parent exists inside the same UoW before inserting the
child Task and TaskBudget.

Future F1-C must acquire the same Task fence before fresh F1-B revalidation.

## 2. Checkpoint parent lineage closure

A new checkpoint parent is accepted only when the referenced durable
checkpoint is proven to belong to the exact same:

```text
execution_id
session_id
task_id
branch_id
```

The parent revision must precede the child revision and self-reference is
rejected.

Normalized `stage_waiting_checkpoint()` additionally requires the supplied
parent pointer to equal the execution's current checkpoint pointer. Therefore a
caller cannot replace the canonical current checkpoint with an arbitrary
same-execution ancestor.

Legacy materialization invokes the same lineage validator before transcript
structural sharing and before normalized checkpoint insertion. A legacy
cross-task/cross-branch parent is rejected with
`LEGACY_CHECKPOINT_UNSAFE`; an unknown parent is also fail-closed rather than
silently dropping lineage.

This removes the legitimate production path that could create:

```text
external checkpoint.parent_checkpoint_id -> candidate checkpoint
```

after F1-B scanned the candidate graph.

## 3. CapabilityInvocation semantic-ID serialization

Fresh AgentToolCall persistence historically committed the Agent-side
`invocation_id` before `CapabilityRuntime` created the R6
CapabilityInvocation row.

UUID collision probability is not GC authority.

`CapabilityInvocationRepository.lock_invocation_gc_serialization_fence()`
provides the shared fence for an already-existing invocation id:

- row-locking dialects use `SELECT ... FOR UPDATE`;
- SQLite performs a semantic no-op UPDATE of `revision` and `updated_at`
  to obtain a real writer lock;
- the operation does not transition R6 state or revision and therefore does not
  take R6 lifecycle ownership.

For a **fresh** AgentToolCall, `DurableAgentStore.save_tool_call()` now:

1. preserves existing idempotent tool-call replay behavior;
2. acquires the invocation GC fence before a new AgentToolCall insert;
3. rejects the write if the invocation id is already bound to a durable R6 row;
4. only inserts the AgentToolCall when no existing invocation owns that id.

Future F1-C must acquire the same invocation fence for every candidate
CapabilityInvocation before destructive revalidation/deletion.

If GC wins first and deletes the old invocation, a later fresh writer may
proceed and normal R6 creation may establish a new invocation with that id.
The writer cannot commit a new semantic inbound edge while GC still owns the
old candidate fence.

## Regression evidence

The Repair-B integration matrix requires:

- missing parent Task -> child creation fails with no Task/TaskBudget row;
- SQLite Task GC fence -> concurrent child writer blocks, then fails closed
  after the parent is deleted;
- cross-task checkpoint parent -> normalized checkpoint creation fails before
  transcript/checkpoint write;
- existing invocation id -> fresh AgentToolCall is rejected and no tool-call row
  is committed;
- SQLite invocation GC fence -> fresh tool-call writer cannot pass while GC
  owns the existing invocation fence; after old authority is removed, the
  writer may proceed and a new matching R6 authority can be established.

Architecture evidence freezes both fence implementations, checkpoint lineage
proof ordering, invocation rejection ordering, exact baseline, and authority
boundaries.

## Other semantic edge families

Repair A / PR #77 already closes the static F1-B reverse scans for external:

- `AgentExecution.branch_id`;
- `AgentExecution.current_checkpoint_id`;
- `AgentExecutionCheckpoint.parent_checkpoint_id`.

TaskBudget-enabled task-scoped execution/branch writers remain gated by
nonterminal Task + OPEN TaskBudget authority. Current-checkpoint binding is
transactionally tied to the same normalized WAITING execution/checkpoint.
Tool-result and checkpoint-pending invocation writers continue to validate the
existing R6 CapabilityInvocation execution/tool/capability identity.

No additional writer race is claimed in those paths by this Repair-B scope.

## Authority boundaries

- **R11-F1-C destructive implementation remains HOLD** until Repair B receives
  exact-head CI, independent audit, explicit merge authorization, lands, and
  post-merge canonical-main Architecture is GREEN/GREEN.
- This repair contains no F1-C DELETE sequence.
- No schema migration is introduced.
- The invocation fence is serialization-only; ClientInvocationLedger and
  CapabilityInvocation lifecycle/state remain R6-owned.
- No R12 lease/recovery/stale-RUNNING semantics.
- **CAS hard-deny** remains unchanged: no FileAsset/FileBlob/FileReference/
  FileProviderBinding lifecycle, READY release, ObjectStorage deletion or
  reconciliation, provider hydration/F5, or CAS physical GC.
- R11-G/H remain CLOSED.
- Merge authorization for PR #76 = NONE.
