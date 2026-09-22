# R7-G Last-Mile Audit + Exact Implementation Plan

- Audit baseline: `edf3489239d2c66f6ebe85955b8bdc3f425ec43f`
- Branch: `r7-g-supervisor-handoff`
- Scope: R7-G only
- Explicitly out of scope: R7-H PendingResumeTicket / client auto-resume state machine
- Frozen dependency: R7-F4 `db260c64434fd9e99dccbf3eec0c34ba9cf75756`

## 1. Exit contract

R7-G closes exactly this authority ordering:

```text
build/validate ResumePlan
    ->
prepare read-only resume context
    ->
AgentExecutionSupervisor.reserve(execution_id)
    ->
get/create ResumeClaim(resume_request_id)
    ->
atomic consume:
    AgentExecution WAITING@N -> RUNNING@N+1
    ResumeClaim CREATED -> CONSUMED
    TaskBudget reacquire (when task-scoped)
    K2 binding
    R6 invocation snapshot revalidation
    ->
AgentExecutionSupervisor.start_reserved(...)
    ->
R7-F4 activation barrier
    ->
durably record handoff ACCEPTED
    ->
execution.resume.accepted
    ->
continue same AgentRuntime execution
```

Required failure path:

```text
claim consumed
    +
process-local activation/handoff fails before ACK
        ->
cancel/drain owned task when necessary
        ->
fresh WAITING(RECOVERY) checkpoint
        OR explicit fail-closed terminal transition
        ->
claim remains CONSUMED
        ->
durably record handoff FAILED
        ->
execution.resume.failed
```

Required lost-ACK path:

```text
claim CONSUMED
supervisor ownership established
handoff ACCEPTED persisted
wire ACK lost
    ->
same resume_request_id retried
    ->
load durable claim BEFORE rebuilding ResumePlan
    ->
replay execution.resume.accepted
    ->
no second claim consume
no second WAITING -> RUNNING CAS
no second runtime task
```

## 2. Baseline audit findings

### P0-G1 — canonical production path stops at R7-D preflight

At `edf34892`, `events_router._resume_execution()` calls
`resume_planning_service.build_resume_plan()`, emits
`execution.resume.preflight / PLAN_READY`, then returns.

Therefore the production WebSocket path never reaches the R7-F claim authority,
R7-F4 continuation actions, or the R7-G primitives already present in runtime
and persistence.

### P0-G2 — compatibility path still ACKs before supervisor ownership

The compatibility path remains:

```text
reserve
claim_resume
confirm_merge
execution.resume.accepted
start_reserved
```

This violates frozen R7-I43/R7-I44. Canonical R7-G must not reuse this ordering.

### P0-G3 — durable handoff primitive is not wired

`DurableAgentStore.record_resume_claim_handoff()` exists at the baseline and
provides the durable ACK-loss authority, but no production call-site records
`ACCEPTED` or `FAILED`.

### P0-G4 — activation/recovery primitives are not wired

The baseline already contains:

- `AgentRuntime.prepare_claimed_resume_activation()`
- `AgentRuntime.recover_claimed_resume()`
- `AgentRuntime.fail_claimed_resume()`

but `events_router` does not invoke them.

### P0-G5 — lost ACK replay cannot depend on ResumePlan reconstruction

A consumed/accepted execution may already be RUNNING or terminal when the same
`resume_request_id` is retried. Rebuilding a WAITING ResumePlan first can
produce a false rejection.

The replay fence must therefore read the durable ResumeClaim/handoff before
planning.

### P0-G6 — post-claim request cancellation can strand RUNNING authority

Socket/request cancellation after claim consumption but before accepted ACK
must not abandon the supervisor-owned activation task or leave AgentExecution
RUNNING without a handoff outcome.

Cancellation must cancel/drain the owned task and run recovery under shielding
before propagating cancellation.

### P1-G1 — no dedicated R7-G regression matrix

At `edf34892`, the R7-G diff contains production primitives but no dedicated
test proving ACK ordering, recovery, lost-ACK replay, or post-claim
cancellation.

## 3. Exact patch plan

### G-LM0 — transport/domain imports only

Update `events_router.py` to consume existing R7 contracts:

- `ResumeClaimIntent`
- `ResumeClaimConsumeSpec`
- `ResumeClaimState`
- `ResumeTriggerType`
- `ResumeClaimError`
- `AgentExecutionOwnershipError`

No new durable lifecycle model is introduced.

### G-LM1 — stable wire helpers

Add transport helpers for:

- `execution.resume.rejected`
- `execution.resume.failed`
- stable domain-code extraction
- durable consumed-claim replay

Transport maps domain state; it does not mutate AgentExecution authority.

### G-LM2 — replay-before-planning fence

When `resume_request_id` is present:

1. load claim by request id;
2. validate principal/client/execution/checkpoint semantics;
3. if durable handoff is ACCEPTED, replay accepted immediately;
4. if durable handoff is FAILED, replay failed immediately;
5. if rejected/expired, return stable rejected outcome;
6. only unresolved CREATED/no-claim requests proceed to plan reconstruction.

This is the lost-ACK idempotency boundary.

### G-LM3 — retain preflight-only compatibility without pulling R7-H forward

R7-H owns PendingResumeTicket creation and client retry state.

Until R7-H lands, an `execution.resume` request without stable
`resume_request_id` remains R7-D `PLAN_READY` preflight-only and acquires no
execution authority.

### G-LM4 — canonical ownership sequence

For a stable resume request:

1. build R7-D ResumePlan;
2. reconstruct read-only context using
   `DurableAgentStore.prepare_resume_plan_context()`;
3. reserve supervisor ownership;
4. create/get idempotent ResumeClaim;
5. atomically consume with `consume_resume_claim()`;
6. start the reserved supervisor task;
7. inside that task run `prepare_claimed_resume_activation()`;
8. wait for activation barrier;
9. persist durable `ACCEPTED` handoff;
10. send `execution.resume.accepted`;
11. release the task past the ACK barrier into `AgentRuntime.execute()`.

No second WAITING->RUNNING transition is allowed.

### G-LM5 — post-claim recovery

Any failure after consume but before durable ACCEPTED:

- never converts the claim back to CREATED;
- never sends accepted;
- creates fresh `WAITING(RECOVERY)` through
  `recover_claimed_resume()`;
- if recovery itself cannot be established, applies
  `fail_claimed_resume()`;
- records durable `FAILED` handoff;
- emits `execution.resume.failed` when the socket is still usable.

### G-LM6 — cancellation hardening

If the request coroutine is cancelled after consume:

- cancel/drain supervisor-owned runtime task;
- run recovery in a shielded task;
- record FAILED handoff;
- re-raise cancellation;
- do not attempt a wire response on the cancelled socket path.

### G-LM7 — focused regression tests

Add `se/tests/architecture/test_r7_g_supervisor_handoff.py` proving:

1. accepted ACK observes supervisor ownership and completed activation barrier;
2. post-claim activation failure emits no accepted ACK and creates fresh
   RECOVERY state;
3. lost accepted ACK retry replays durable accepted outcome without a second
   planner call, claim consume, handoff record, or runtime task;
4. request cancellation after claim recovers and leaves no process-local owned
   task.

## 4. Invariants intentionally unchanged

R7-G does not modify:

- R7-D ResumePlan classification;
- R7-E same-invocation continuation;
- R7-F atomic SQL claim consumption;
- R7-F4 committed-result ordering/continuation execution;
- R7-H client PendingResumeTicket state;
- legacy branch/merge compatibility path removal (R7-I);
- distributed execution leases/process-crash recovery (R12).

## 5. Exit gate

R7-G can be closed only when all are true:

```text
[ ] no accepted ACK before supervisor ownership
[ ] activation barrier completes before accepted ACK
[ ] post-claim activation failure -> fresh RECOVERY/no accepted ACK
[ ] claim remains CONSUMED after post-claim failure
[ ] lost ACK retry same resume_request_id -> durable accepted replay
[ ] replay does not rebuild ResumePlan after accepted authority
[ ] replay does not perform a second execution CAS
[ ] replay does not create a second runtime task
[ ] post-claim request cancellation leaves no unowned task
[ ] broad repository regression is green
[ ] cl/tests remains green
```
