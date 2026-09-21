# R7-D Completion — R6-Aware ResumePlan + Read-Only Resume Preflight

## Closure

- Repository: `boxs-51/assistant`
- R7-C baseline: `45f7a6353b46fdb9054ef41c457fbee424849429`
- Verified R7-D implementation HEAD: `d8ceacab07fca9474eff62ad2c6ec91a5fdf1138`
- Branch: `r7-d-resume-plan`
- PR: #2
- Status: **CLOSED / VERIFIED**
- Next phase boundary: R7-E only
- Explicitly not implemented: R7-E/F/G/H

## What R7-D closes

R7-D closes the planning boundary between a normalized R7 WAITING checkpoint
and future resume execution authority.

The phase establishes these invariants:

1. Production resume reads only `AgentExecution.current_checkpoint_id` and the
   normalized R7 checkpoint representation.
2. Phase 6.9 continuation checkpoints are no longer a second production
   checkpoint authority when the durable AgentExecution lifecycle is present.
3. Resume planning is R6-aware and produces only safe final actions:
   `REUSE_COMMITTED`, `DISPATCH_NOT_DISPATCHED`, or `REPLAY_SAFE`.
4. Unsafe/ambiguous remote outcomes never become a claimable ResumePlan.
5. Current R6 invocation revision/state/outcome is frozen into each plan action
   after any required reconciliation.
6. Parallel tool-call ordering is inherited from the canonical iteration
   `tool_call_ids`; pending invocation ordinals must agree with that order.
7. Resume transcript reconstruction remains behind the R7-C commitment
   boundary: active-batch/provisional tool results never enter the model prefix.
8. `plan_fingerprint` deterministically hashes semantic plan identity but
   grants no execution authority.
9. Production `execution.resume` is read-only preflight in R7-D and returns
   before claim, branch merge, supervisor activation, or AgentRuntime execution.
10. `execution.resume.accepted` is not emitted because WAITING -> RUNNING
    authority is intentionally deferred to R7-F/G.

## Normalized checkpoint authority

Production AgentRuntime now relies on the R7-B atomic WAITING writer:

```text
RUNNING@N
    -> checkpoint + ordered R6 snapshots staged
    -> TaskBudget release when task-scoped
    -> AgentExecution WAITING@N+1
    -> current_checkpoint_id = C1
```

R7-D reads that exact checkpoint with:

```text
load_current_checkpoint(execution_id)
load_checkpoint_pending_invocations(checkpoint_id)
load_committed_checkpoint_transcript(...)
```

The legacy `AgentContinuationService.checkpoint_disconnect()` path remains only
for compatibility stores without the durable lifecycle surface.

## ResumePlan contract

R7-D adds immutable:

```text
ResumePlan
ResumeInvocationAction
ResumeInvocationActionKind
```

Each invocation action freezes:

```text
invocation_id
tool_call_id
ordinal
capability_id
capability_version
request_fingerprint
idempotency
expected_invocation_revision
expected_invocation_state
expected_remote_outcome_state
action
```

Final action enum:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

No intermediate or unsafe R6 state is represented as an executable action.

## R6 reconciliation matrix

Implemented classification:

```text
TERMINAL_COMMITTED
    -> verify exact committed AgentToolResult projection
    -> REUSE_COMMITTED

NOT_DISPATCHED
    -> DISPATCH_NOT_DISPATCHED

IN_FLIGHT / OUTCOME_UNKNOWN
    -> CapabilityRuntime.reconcile_remote_invocation()

reconcile TERMINAL
    -> exact R7-C commitment
    -> reload current invocation
    -> REUSE_COMMITTED

reconcile RUNNING
    -> DEFER

reconcile UNKNOWN / NOT_FOUND
    + IDEMPOTENT or DEDUPLICATED
    -> REPLAY_SAFE

reconcile UNKNOWN / NOT_FOUND
    + NON_IDEMPOTENT or UNKNOWN idempotency
    -> DEFER

semantic conflict / foreign principal / foreign client
    -> REJECT
```

R7-D does not implement another reconciliation engine; it composes the existing
R6 reconciliation authority.

## Final senior-architect hardening

The first green R7-D implementation was audited again before closure. The audit
found one P0 and three P1 correctness gaps, all fixed before this completion.

### P0 — remote-outcome watermark regression

A checkpoint that observed `OUTCOME_UNKNOWN` could previously load a corrupted
or regressed current invocation marked `NOT_DISPATCHED` and classify it as
safe to dispatch.

R7-D now validates monotonic R6 outcome progress from the checkpoint watermark:

```text
OUTCOME_UNKNOWN
    -> OUTCOME_UNKNOWN | TERMINAL_COMMITTED only

TERMINAL_COMMITTED
    -> TERMINAL_COMMITTED only
```

The existing R6 `IN_FLIGHT -> NOT_DISPATCHED` correction remains allowed.

### P1 — invocation revision regression

Current invocation revision must satisfy:

```text
current_revision >= checkpoint.invocation_revision
```

A revision below the checkpoint watermark rejects planning.

### P1 — task and K2 authority

For task-scoped executions, planning now rejects:

- missing task;
- task/session semantic mismatch;
- foreign task owner;
- terminal `COMPLETED / FAILED / CANCELLED` task.

The planning service itself also revalidates target K2:

- connection exists;
- connection is ACTIVE/usable;
- principal matches;
- stable client identity matches.

The service therefore does not rely solely on router pre-validation.

### P1 — committed result semantic authority

`REUSE_COMMITTED` no longer means merely “a COMMITTED row exists”.

It requires:

```text
CapabilityInvocation.remote_outcome_state == TERMINAL_COMMITTED
CapabilityInvocation lifecycle is terminal

AgentToolResult.execution_id   == execution_id
AgentToolResult.tool_call_id   == checkpoint tool_call_id
AgentToolResult.invocation_id  == invocation_id
AgentToolResult.capability_id  == capability_id

success/output/error_code/error_message/retryable
    == exact terminal R6 projection
```

A conflicting durable row rejects the plan.

## execution.resume behavior in R7-D

Production bootstrap binds `AgentResumePlanningService`.

The canonical handler is therefore:

```text
execution.resume
    -> active connection/principal check
    -> normalized checkpoint validation
    -> task/K2 validation
    -> R6 reconciliation
    -> immutable ResumePlan
    -> execution.resume.preflight
    -> return
```

It does not reach:

```text
AgentContinuationService.reconnect()
AgentExecutionSupervisor.reserve()
AgentRuntime.claim_resume()
AgentContinuationService.confirm_merge()
AgentExecutionSupervisor.start_reserved()
AgentRuntime.execute()
```

Temporary R7-D preflight statuses:

```text
PLAN_READY
DEFERRED
REJECTED
```

`PLAN_READY` is planning success only; it is not resume acceptance.

## Regression proof

The R7-D architecture suite proves, among other cases:

- safe replay for idempotent unknown outcome;
- NOT_DISPATCHED classification without reconciliation;
- terminal reconciliation reloads the post-R6 revision;
- unsafe non-idempotent ambiguity never produces a plan;
- foreign client and semantic fingerprint drift rejection;
- capability-not-ready defer;
- normalized checkpoint pointer authority;
- preflight path never claims or executes;
- invocation revision below checkpoint watermark is rejected;
- OUTCOME_UNKNOWN -> NOT_DISPATCHED regression is rejected;
- terminal Task cannot produce PLAN_READY;
- stale/unusable K2 cannot produce PLAN_READY;
- mismatched COMMITTED result cannot be reused.

The NOT_DISPATCHED fixtures were also corrected so their checkpoint watermark
truthfully records `NOT_DISPATCHED`; a checkpoint that recorded
`OUTCOME_UNKNOWN` is intentionally rejected if current state regresses.

## Last-mile hardening after closure re-audit

A final branch-wide audit after the first R7-D completion found one additional
P0 and three P1 contract gaps. All four were fixed before entering R7-E.

### P0 — normalized WAITING authority is stronger than generic execution lifecycle

Previously `AgentRuntime._has_execution_lifecycle_store()` required only:

```text
load_execution
save_execution
compare_and_set_execution
```

That was enough to skip the legacy Phase 6.9 checkpoint path, but a partial
durable store without `commit_waiting_checkpoint()` could later fall back to
plain AgentExecution CAS and expose:

```text
AgentExecution.state == WAITING
AgentExecution.current_checkpoint_id == NULL
```

R7-D now distinguishes generic execution lifecycle from canonical normalized
WAITING authority.

For non-task execution:

```text
checkpoint_values != None
AND commit_waiting_checkpoint unavailable
    -> fail closed
    -> plain WAITING CAS is forbidden
```

For task-scoped execution,
`TaskBudgetService.finish_task_scoped_execution()` remains the canonical
atomic writer because it commits TaskBudget release + checkpoint + pending
snapshots + RUNNING -> WAITING in one UoW.

The remote-wait path also checks normalized WAITING authority before returning
a resumable WAITING result.

### P1 — execution/checkpoint wait-reason parity

Resume planning now requires:

```text
AgentExecution.state == WAITING
AgentExecution.wait_reason == checkpoint.wait_reason
AgentExecution.wait_reason == CONNECTION
```

A drifted execution/checkpoint pair rejects with `WAIT_REASON_CONFLICT`;
checkpoint state alone is no longer sufficient.

### P1 — stable client affinity is proof, not optional metadata

Connection resume now fails closed unless a stable `client_id` exists at every
authority layer:

```text
target connection client_id
checkpoint.origin_client_id
CheckpointPendingInvocation.origin_client_id
CapabilityInvocation.origin_client_id
```

All must match the resume `target_client_id`.

Missing origin-client authority rejects with
`RESUME_ORIGIN_CLIENT_MISSING`; a missing target/connection client identity
rejects with `RESUME_CLIENT_ID_REQUIRED`.

Nullable schema fields remain available for legacy/migration representation,
but R7-D connection preflight no longer converts absent identity proof into
execution-safe planning authority.

### P1 — complete active parallel-batch coverage

`CheckpointPendingInvocation.ordinal` ordering alone did not prove that every
canonical active-batch tool call was accounted for.

R7-D now validates the full canonical `iteration.tool_call_ids` batch:

```text
for every active tool_call_id:
    pending snapshot exists
    OR durable COMMITTED AgentToolResult exists
```

If a canonical call is in neither set, planning rejects with
`CHECKPOINT_ACTIVE_BATCH_INCOMPLETE`.

Overlap is intentionally allowed after the checkpoint: a call that was pending
at checkpoint time may later become COMMITTED through normal delivery or R6
reconciliation. The invariant is complete coverage, not permanent mutual
exclusivity.

### Compatibility regression caught by CI

The first last-mile hardening run correctly caused two old R4 architecture
tests to fail because their in-memory fake exposed generic execution CAS but did
not implement the normalized checkpoint transaction.

The production fail-closed behavior was retained. The fake was upgraded with a
minimal `commit_waiting_checkpoint()` implementation and assertions that
`current_checkpoint_id` is populated, so the older budget-freeze tests now
exercise the current R7 durability contract instead of weakening it.

## CI evidence

Verified on implementation HEAD `d8ceacab07fca9474eff62ad2c6ec91a5fdf1138`.

Architecture Baseline:

```text
python -m pytest -q
698 passed, 1 skipped, 14 warnings in 55.80s

python -m pytest -q cl/tests
38 passed in 6.52s
```

All workflow gates succeeded:

```text
Architecture Baseline
Phase 5.6 Exit Gate
Phase 5.7 Exit Gate
Phase 5.8 Exit Gate
Phase 5.9 Exit Gate
Phase 5.10 Exit Gate
Phase 5.11 Exit Gate
```

## Deferred boundary

R7-D intentionally does not implement:

### R7-E

- `CapabilityRuntime.continue_invocation()`;
- execution of `DISPATCH_NOT_DISPATCHED`;
- execution of `REPLAY_SAFE`;
- same-invocation new attempt semantics.

### R7-F

- `resume_request_id`;
- ResumeClaim create/expire/reject/consume;
- atomic WAITING -> RUNNING;
- TaskBudget reacquire;
- K2 execution binding;
- transactional revalidation of plan invocation snapshots.

### R7-G/H

- supervisor reserve/start ordering around accepted ACK;
- recovery checkpoint after post-claim activation failure;
- PendingResumeTicket;
- final legacy continuation branch/merge removal.

Until R7-E/F land, a `PLAN_READY` preflight must never be interpreted as
permission to execute or replay a capability.

## Exit decision

R7-D satisfies its frozen exit gate:

```text
unsafe non-idempotent ambiguity never produces claimable plan
foreign client is rejected
stale invocation snapshots are detected
normalized checkpoint is the production authority
R6 terminal authority is reused exactly
planning grants no execution authority
```

**R7-D is CLOSED / VERIFIED.**
