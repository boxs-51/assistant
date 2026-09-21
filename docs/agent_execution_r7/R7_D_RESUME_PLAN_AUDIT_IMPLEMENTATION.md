# R7-D — R6-Aware ResumePlan + Read-Only Resume Preflight

## Baseline

- Repository: `boxs-51/assistant`
- Baseline: `45f7a6353b46fdb9054ef41c457fbee424849429`
- Branch: `r7-d-resume-plan`
- Depends on: R7-C CLOSED / VERIFIED
- Status: **IMPLEMENTED / VERIFICATION PENDING**

## Scope

R7-D creates immutable planning authority only.

It intentionally does **not**:

- create or consume ResumeClaim;
- mutate AgentExecution `WAITING -> RUNNING`;
- reacquire TaskBudget;
- bind the execution to K2;
- call `CapabilityRuntime.continue_invocation()`;
- execute/replay pending tool calls;
- emit `execution.resume.accepted`.

## Canonical R7-D flow

```text
execution.resume
    -> validate active principal/connection
    -> load AgentExecution.current_checkpoint_id
    -> load normalized AgentExecutionCheckpoint
    -> load ordered checkpoint pending invocation snapshots
    -> load current R6 CapabilityInvocation rows
    -> reuse already terminal authority OR perform R6 reconciliation
    -> classify every checkpointed unresolved invocation
    -> validate K2 capability readiness for future dispatch/replay actions
    -> rebuild R7-C committed-only transcript prefix
    -> freeze current invocation revisions/states/outcome
    -> compute deterministic plan_fingerprint
    -> execution.resume.preflight
```

No AgentExecution lifecycle authority is acquired.

## ResumeInvocationAction matrix

Final executable dispositions:

```text
TERMINAL_COMMITTED
    -> REUSE_COMMITTED

NOT_DISPATCHED
    -> DISPATCH_NOT_DISPATCHED

OUTCOME_UNKNOWN / IN_FLIGHT
    -> R6 reconciliation

reconciliation TERMINAL
    -> promote exact R7-C projection
    -> REUSE_COMMITTED

reconciliation RUNNING
    -> DEFER

UNKNOWN / NOT_FOUND + IDEMPOTENT
    -> REPLAY_SAFE

UNKNOWN / NOT_FOUND + DEDUPLICATED
    -> REPLAY_SAFE

UNKNOWN / NOT_FOUND + NON_IDEMPOTENT
    -> DEFER

UNKNOWN / NOT_FOUND + UNKNOWN idempotency
    -> DEFER

CONFLICT / foreign principal / foreign client / semantic fingerprint drift
    -> REJECT
```

No socket disconnect, timeout, cancellation request, or server implementation
availability is treated as proof that a remote side effect did not happen.

## New contracts

`se/src/runtimes/agent/contracts/resume.py` adds:

```text
ResumeInvocationActionKind
ResumeInvocationAction
ResumePlan
```

Each action freezes:

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

The only action kinds are:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

Intermediate/unsafe states do not become plan actions.

## Checkpoint authority migration

Production `AgentRuntime` no longer writes a Phase 6.9
`AgentContinuationService.checkpoint_disconnect()` checkpoint when a durable
AgentExecution lifecycle store is available.

The canonical writer remains the R7-B atomic WAITING transition:

```text
_finish_durable_execution()
    -> stage normalized checkpoint
    -> snapshot every unresolved pending invocation
    -> TaskBudget release if task-scoped
    -> RUNNING@N -> WAITING@N+1
    -> AgentExecution.current_checkpoint_id = C1
```

The old continuation checkpoint remains only for compatibility stores that do
not expose the durable lifecycle primitives.

This removes the dual-checkpoint identity problem where the client received the
normalized checkpoint but `execution.resume` validated a different legacy
`checkpoint-<uuid>`.

## New normalized durable reads

`DurableAgentStore` adds:

```text
load_current_checkpoint(execution_id)
load_checkpoint_pending_invocations(checkpoint_id)
load_committed_checkpoint_transcript(
    execution_id,
    checkpoint_id,
    active_tool_call_ids=...
)
```

The checkpoint loader requires:

```text
AgentExecution.current_checkpoint_id == checkpoint.checkpoint_id
AgentExecution.revision == checkpoint.execution_revision
checkpoint.execution_id == AgentExecution.id
```

The transcript loader preserves R7-C:

- active-batch tool messages are always stripped;
- provisional historical messages are dropped;
- committed historical tool messages are re-materialized from durable
  `AgentToolResult`.

## R6 reconciliation authority

R7-D does not implement a second reconciliation engine.

It calls the existing:

```text
CapabilityRuntime.reconcile_remote_invocation()
    -> RemoteInvocationReconciliationService
```

This preserves R6 authorization, stable-client enforcement, semantic
fingerprint validation, client-ledger reconciliation, and terminal commitment.

After reconciliation R7-D reloads the durable CapabilityInvocation and freezes
the **post-reconciliation** revision/state/outcome in ResumePlan.

## Plan fingerprint

`plan_fingerprint` is SHA-256 over the semantic plan, including:

- execution/checkpoint/revision;
- agent/session/task and execution lineage;
- correlation/request/trace identity;
- iteration and canonical tool-call ordering;
- committed-only transcript snapshot;
- active budget and wait TTL;
- target user/client/connection;
- every action and its R6 revision/state/outcome/fingerprint snapshot.

The hash is planning identity only. It grants no execution authority.

## Production execution.resume boundary

Production bootstrap now binds `AgentResumePlanningService`.

Therefore `events_router._resume_execution()` takes the R7-D path and returns
before all legacy activation operations:

```text
AgentContinuationService.reconnect()
AgentExecutionSupervisor.reserve()
AgentRuntime.claim_resume()
AgentContinuationService.confirm_merge()
AgentExecutionSupervisor.start_reserved()
AgentRuntime.execute()
```

Those calls remain reachable only through the compatibility branch for tests or
embedders that deliberately construct a container without
`resume_planning_service`.

## Temporary wire response

`execution.resume.accepted` is forbidden in R7-D because no execution
authority has been acquired.

R7-D introduces the temporary connection-bound response:

```text
execution.resume.preflight
status = PLAN_READY | DEFERRED | REJECTED
```

`PLAN_READY` means only that a safe immutable plan was constructed.

The client `GatewayRealtimeClient.resume_execution()` accepts this preflight
response so it does not falsely time out during the R7-D migration window.

R7-F/G will replace preflight-only behavior with durable ResumeClaim + atomic
activation + accepted ACK semantics.

## Explicit non-scope

Not implemented in this patch:

### R7-E
```text
CapabilityRuntime.continue_invocation()
DISPATCH_NOT_DISPATCHED execution
REPLAY_SAFE execution
new attempt on same invocation_id
```

### R7-F
```text
resume_request_id
ResumeClaim creation/consume
WAITING -> RUNNING CAS
TaskBudget reacquire
K2 binding
TOCTOU action snapshot revalidation
```

### R7-G/H
```text
start_reserved before accepted ACK
post-claim recovery checkpoint
PendingResumeTicket
legacy branch/merge deletion
```

## Regression proof required

Focused R7-D tests cover:

1. `OUTCOME_UNKNOWN + IDEMPOTENT -> REPLAY_SAFE`;
2. `NOT_DISPATCHED -> DISPATCH_NOT_DISPATCHED` without reconciliation;
3. reconciled terminal -> `REUSE_COMMITTED` with post-reconcile revision;
4. non-idempotent ambiguity produces no claimable plan;
5. foreign client rejection before reconciliation;
6. semantic fingerprint drift rejection;
7. missing target capability defers a future executable action;
8. normalized checkpoint read follows `AgentExecution.current_checkpoint_id`;
9. production `execution.resume` emits preflight without claim/runtime start.

## Verification gate

```powershell
py -m pytest -q se/tests/architecture/test_r7_d_resume_plan.py

py -m pytest -q `
  se/tests/architecture/test_r7_c_tool_result_commitment.py `
  se/tests/architecture/test_r7_b_atomic_waiting.py `
  se/tests/architecture/test_r6_c_server_reconciliation.py `
  se/tests/architecture/test_r4_b2_resume.py `
  se/tests/architecture/test_r5_a4_a6_ownership_integration.py

py -m pytest -q
```

## Final senior-architect hardening

Post-green review found and hardened four correctness gaps before closure:

1. **P0 remote-outcome watermark regression** — a checkpoint that observed
   `OUTCOME_UNKNOWN` must never later classify a regressed
   `NOT_DISPATCHED` row as dispatch-safe. Planner now validates monotonic R6
   outcome progress from the checkpoint watermark.
2. **P1 invocation revision regression** — current invocation revision must be
   `>= checkpoint.invocation_revision`.
3. **P1 Task terminal / K2 authority validation** — planner now rejects
   terminal/cancelled tasks and independently revalidates target connection
   usability, principal and stable client identity.
4. **P1 committed-result semantic authority** — `REUSE_COMMITTED` now
   requires exact AgentToolResult identity and exact terminal R6 projection,
   not merely the presence of a COMMITTED row.

R7-D remains **IMPLEMENTED / VERIFICATION PENDING** until CI on the hardening
commit is green.
