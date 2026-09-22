# R7-G Completion — Supervisor Handoff + ACK/Recovery Semantics

- Verified code HEAD: `fcbeddd0715a9113fca1f3d2b39c273d58b89bf5`
- Branch: `r7-g-supervisor-handoff`
- Baseline audited: `edf3489239d2c66f6ebe85955b8bdc3f425ec43f`
- Depends on: R7-F/F4 atomic ResumeClaim + claimed continuation
- Status: **CLOSED / VERIFIED**
- Explicitly not implemented: R7-H PendingResumeTicket / client auto-resume

## 1. Closure summary

R7-G closes the process-local handoff boundary between the durable R7-F claim
transaction and the realtime resume ACK.

The canonical R7 path is now:

```text
ResumePlan
    -> prepare read-only resume context
    -> AgentExecutionSupervisor.reserve()
    -> get/create ResumeClaim(resume_request_id)
    -> atomic consume
       WAITING@N -> RUNNING@N+1
       CREATED -> CONSUMED
       TaskBudget reacquire
       K2 bind
       R6 snapshot revalidation
    -> AgentExecutionSupervisor.start_reserved()
    -> R7-F4 activation barrier
    -> durable handoff ACCEPTED
    -> execution.resume.accepted
    -> continue the SAME AgentExecution
```

No accepted ACK is emitted before the supervisor owns the runtime task and the
claimed resume activation barrier has completed.

## 2. Baseline findings closed

The audit at `edf34892` found the following last-mile gaps:

1. production `_resume_execution()` stopped after R7-D `PLAN_READY`;
2. the compatibility path still ACKed before `start_reserved()`;
3. durable `record_resume_claim_handoff()` had no production call-site;
4. R7-G activation/recovery runtime primitives had no production call-site;
5. lost-ACK retry could incorrectly rebuild a WAITING ResumePlan after the
   execution had already left WAITING;
6. request cancellation after claim consumption could strand RUNNING
   authority;
7. cancellation during the durable ACCEPTED write created an uncertain-commit
   window;
8. no dedicated R7-G regression matrix existed.

The canonical R7 path now closes all eight findings. The legacy
branch/merge compatibility path remains migration-only and is intentionally
left for R7-I authority removal.

## 3. Canonical wire behavior

### 3.1 Request without stable resume_request_id

R7-H owns PendingResumeTicket creation and client retry state.

Until R7-H lands, a request without `resume_request_id` retains the R7-D
preflight-only behavior:

```text
execution.resume
    -> build/validate ResumePlan
    -> execution.resume.preflight / PLAN_READY
    -> no durable authority acquisition
```

This keeps R7-G from silently implementing client ticket policy.

### 3.2 Request with stable resume_request_id

The request enters the canonical R7-G authority path.

Stable outcomes are:

- `execution.resume.accepted`
- `execution.resume.rejected`
- `execution.resume.failed`

`accepted` contains:

```text
execution_id
checkpoint_id
resume_request_id
claim_id
accepted_revision
state_at_accept=RUNNING
```

## 4. Supervisor-before-ACK invariant

The new ordering is:

```text
reserve
-> create/get claim
-> consume claim
-> start_reserved
-> prepare_claimed_resume_activation
-> persist ACCEPTED handoff
-> accepted ACK
```

The supervisor-owned task pauses on an ACK barrier after activation. Only after
the durable ACCEPTED outcome is established does transport attempt to send the
wire ACK; the task is then released into `AgentRuntime.execute()`.

A dropped ACK therefore cannot abandon the already-owned execution.

## 5. Post-claim failure semantics

If activation fails after claim consumption and before accepted authority is
recorded:

```text
ResumeClaim stays CONSUMED
RUNNING@N+1
    -> fresh WAITING(RECOVERY)@N+2
       OR explicit fail-closed terminal state
durable handoff = FAILED
execution.resume.failed
```

The old checkpoint is not reused as new lifecycle authority.

The failed wire payload includes the recovery checkpoint/revision when recovery
succeeds.

## 6. Lost-ACK idempotency

Lost-ACK replay is resolved before ResumePlan reconstruction.

For the same durable `resume_request_id`:

```text
load ResumeClaim
-> validate execution/checkpoint/principal/stable-client semantics
-> read metadata.r7_g_handoff
-> replay ACCEPTED or FAILED
```

An already accepted retry therefore performs:

- no second ResumePlan build;
- no second ResumeClaim consume;
- no second WAITING -> RUNNING CAS;
- no second TaskBudget reacquire;
- no second supervisor runtime task;
- no second handoff mutation.

A consumed accepted request may replay on a replacement WebSocket generation
for the same authenticated principal/stable client. The durable claim remains
the request-level authority.

## 7. Cancellation hardening

Two cancellation windows are covered.

### 7.1 Cancellation during activation

If the request/socket coroutine is cancelled after claim consumption but while
the activation barrier is still running:

1. the supervisor-owned task is cancelled and drained;
2. the activation Future exception is consumed;
3. recovery runs in a shielded task;
4. the claim remains CONSUMED;
5. FAILED handoff is persisted;
6. no accepted ACK is emitted;
7. no owned task is leaked.

### 7.2 Cancellation during durable ACCEPTED handoff

The ACCEPTED metadata write runs in its own task and is shielded from caller
cancellation.

The transport drains that write before deciding the outcome:

- write failed -> cancel owned task + RECOVERY/FAILED;
- write succeeded -> ACCEPTED authority is known, release runtime past ACK
  barrier, propagate request cancellation, and let a later retry replay the
  durable ACK.

This removes the uncertain-commit case where durable metadata could say
ACCEPTED while the execution was incorrectly rolled back to RECOVERY.

## 8. Durable handoff immutability

`DurableAgentStore.record_resume_claim_handoff()` is now covered by SQLite
integration regression.

The test proves:

- handoff may only be written on a CONSUMED claim;
- same ACCEPTED payload is idempotent;
- outcome survives a durable reload;
- an existing ACCEPTED outcome cannot be replaced by FAILED or a conflicting
  payload;
- conflict returns `RESUME_REQUEST_CONFLICT`.

## 9. Regression coverage

Added:

```text
se/tests/architecture/test_r7_g_supervisor_handoff.py
```

It proves:

1. accepted ACK observes active supervisor ownership;
2. activation barrier completes before accepted ACK;
3. post-claim activation failure creates recovery/no accepted ACK;
4. lost accepted ACK replays durable outcome without a second planner/claim/task;
5. request cancellation during activation recovers and leaves no owned task;
6. cancellation during ACCEPTED commit drains the commit and replays later.

Extended:

```text
se/tests/integration/test_r7_f_resume_claim_atomicity.py
```

with durable R7-G handoff idempotency/immutability coverage.

## 10. Final CI evidence

Verified code HEAD:

```text
fcbeddd0715a9113fca1f3d2b39c273d58b89bf5
```

All workflows present on the branch completed successfully:

```text
Architecture Baseline   SUCCESS
Phase 5.6 Exit Gate     SUCCESS
Phase 5.7 Exit Gate     SUCCESS
Phase 5.8 Exit Gate     SUCCESS
Phase 5.9 Exit Gate     SUCCESS
Phase 5.10 Exit Gate    SUCCESS
Phase 5.11 Exit Gate    SUCCESS
```

Architecture Baseline includes the Linux full repository suite and Windows
client contracts.

Relevant run IDs:

```text
Architecture Baseline   35686924680
Phase 5.6               35686924510
Phase 5.7               35686924494
Phase 5.8               35686924574
Phase 5.9               35686924554
Phase 5.10              35686924512
Phase 5.11              35686924657
```

## 11. Blast radius

Compared with audited baseline `edf34892`, the implementation touches only:

```text
se/src/transport/gateway/api/v1/events_router.py
se/tests/architecture/test_r7_g_supervisor_handoff.py
se/tests/integration/test_r7_f_resume_claim_atomicity.py
docs/agent_execution_r7/R7_G_LAST_MILE_AUDIT_IMPLEMENTATION_PLAN.md
```

No CL runtime/PendingResumeTicket implementation was introduced.

## 12. Exit decision

The R7-G frozen exit conditions are satisfied:

```text
[x] no accepted ACK before supervisor-owned runtime task
[x] activation barrier completes before accepted ACK
[x] activation failure -> fresh recovery/no accepted ACK
[x] claim remains CONSUMED after post-claim failure
[x] lost ACK retry replays durable accepted outcome
[x] replay occurs before ResumePlan reconstruction
[x] no second execution CAS on accepted replay
[x] no second runtime task on accepted replay
[x] cancellation after claim leaves no unowned task
[x] durable ACCEPTED write has no uncertain-cancellation rollback window
[x] durable handoff is idempotent/immutable
[x] broad repository regression is green
[x] Windows client contracts are green
```

**R7-G is CLOSED / VERIFIED.**

## 13. Frozen R7-G -> R7-H boundary

R7-H may now implement client-side PendingResumeTicket and automatic reconnect
triggering, but it must consume the R7-G wire contract rather than creating a
second resume authority.

R7-H owns:

```text
execution.waiting ticket ingestion
stable ticket key (execution_id, checkpoint_id)
active_resume_request_id
READY-after-capability-registration
same resume_request_id on ACK timeout/loss
new resume_request_id only for a new eligible attempt
auto-resume trigger policy
```

R7-H must not own:

```text
AgentExecution WAITING -> RUNNING mutation
ResumeClaim consume
TaskBudget reacquire
R6 reconciliation authority
supervisor ownership
post-claim recovery
handoff ACCEPTED/FAILED durability
```
