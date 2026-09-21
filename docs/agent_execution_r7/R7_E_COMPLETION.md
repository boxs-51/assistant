# R7-E Completion — Existing CapabilityInvocation Continuation

## Closure

- Repository: `boxs-51/assistant`
- R7-D baseline: `8fd48c19d2a3e2d66b807fc981dd3cff0abb0490`
- Verified R7-E code HEAD: `9dec364d7497fd4df5e19041128b04a7c71d70b7`
- Branch: `r7-e-existing-invocation-continuation`
- PR: #3
- Status: **CLOSED / VERIFIED**
- Next phase boundary: **R7-F**
- R7-F/G/H implementation in this phase: **none**

## Delivered E0 → E5

### E0 — contracts

Added:

```text
ExistingInvocationContinuationMode
    DISPATCH_NOT_DISPATCHED
    REPLAY_SAFE

CAPABILITY_CONTINUATION_NOT_FOUND
CAPABILITY_CONTINUATION_STALE
CAPABILITY_CONTINUATION_INVALID_STATE
CAPABILITY_CONTINUATION_UNSAFE
CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE
CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT
```

### E1 — atomic persistence

Added atomic same-invocation continuation primitives for both in-memory and SQL
stores:

```text
begin_continuation_attempt()
start_continuation_attempt()
```

The begin transaction combines invocation CAS and attempt N+1 insertion. The
start transaction combines invocation RUNNING transition and attempt RUNNING
transition.

No database schema migration was required; the existing
`CapabilityInvocationAttempt` table and
`UNIQUE(invocation_id, attempt_number)` constraint are sufficient.

### E2 — runtime continuation

Added:

```text
CapabilityRuntime.continue_invocation()
```

It:

- loads the existing invocation;
- enforces exact revision and request fingerprint;
- recomputes fingerprint from durable arguments;
- requires Agent execution/tool-call lineage;
- requires WAITING(CONNECTION);
- validates continuation mode against R6 remote outcome/idempotency;
- validates complete terminal attempt history;
- validates K2 same-principal/stable-client authority;
- validates current capability contract and exactly one client implementation;
- atomically creates attempt N+1 on the same invocation;
- executes only that one attempt through RemoteClientDriver;
- returns CapabilityResult with the same invocation_id.

It never calls logical invocation creation.

### E3 — shared execution path

Ordinary new invocation execution and existing-invocation continuation share the
same attempt error/outcome/terminal machinery.

Ordinary execution retains internal retry behavior.

Continuation always uses:

```text
allow_internal_retry = False
```

so one ResumePlan action cannot silently fan out into multiple attempts.

### E4 — real TCP continuation

Added real uvicorn/WebSocket tests proving:

1. `DISPATCH_NOT_DISPATCHED` uses the same invocation and creates attempt 2.
2. `REPLAY_SAFE + IDEMPOTENT` replays through K2 with the same invocation.
3. A durable DEDUPLICATED terminal ledger returns the prior terminal and the
   external effect count remains one.
4. A durable RUNNING IDEMPOTENT ledger after process restart can safely re-enter
   execution and complete the same ledger/invocation identity.

No FakeSocket/manual inbound shortcut is used for these R7-E E2E cases.

### E5 — hardening and regression

Final audit added:

- atomic DISPATCHING -> RUNNING invocation+attempt transition;
- client safe replay of durable RUNNING without a duplicate ledger
  `mark_running`;
- capability-definition idempotency/kind/execution-mode/version drift rejection;
- target implementation owner_type/driver_kind validation;
- wrong mode/state/wait reason/lineage rejection;
- durable argument/fingerprint corruption rejection;
- same-generation/inactive/foreign K2 rejection;
- UNKNOWN idempotency replay rejection;
- prior attempt history must be contiguous and terminal;
- SQL rollback proof when continuation start cannot update its attempt row;
- SQL two-worker single-winner/no-orphan proof;
- no-internal-retry proof even when `max_attempts` is higher.

## Identity invariant

Across every successful R7-E continuation:

```text
CapabilityInvocation.invocation_id = SAME
request_fingerprint = SAME
arguments = SAME
execution_id = SAME
tool_call_id = SAME

attempt:
    N -> N+1
```

No second logical invocation row is created.

## Regression inventory

Focused R7-E coverage at the verified code HEAD:

```text
13 architecture continuation tests
5 SQL atomicity/integration tests
4 real-TCP R7-E continuation tests
client ledger restart/reconciliation suite including
  IDEMPOTENT and DEDUPLICATED RUNNING restart replay
```

The full repository regression suite also exercises all prior R6/R7 phases.

## CI evidence

Verified on code HEAD `9dec364d7497fd4df5e19041128b04a7c71d70b7`.

Architecture Baseline:

```text
Linux:
722 passed, 1 skipped, 14 warnings in 60.21s

Windows cl/tests:
40 passed in 8.61s
```

Workflow gates:

```text
Architecture Baseline   SUCCESS
Phase 5.6 Exit Gate     SUCCESS
Phase 5.7 Exit Gate     SUCCESS
Phase 5.8 Exit Gate     SUCCESS
Phase 5.9 Exit Gate     SUCCESS
Phase 5.10 Exit Gate    SUCCESS
Phase 5.11 Exit Gate    SUCCESS
```

## Explicit R7-F boundary

R7-E does not make Agent resume executable.

Production `execution.resume` remains the R7-D read-only plan preflight.

Still deferred to R7-F/G:

```text
resume_request_id
ResumeClaim creation / expiry / consumption
transactional ResumePlan revalidation
AgentExecution WAITING -> RUNNING
TaskBudget reacquire
AgentExecution K2 binding
invocation snapshot TOCTOU revalidation at claim time
ordered execution of ResumeInvocationAction list
AgentToolResult projection after continuation
execution.resume.accepted ordering
post-claim recovery
```

Until R7-F lands, `CapabilityRuntime.continue_invocation()` is a tested
capability primitive and is not called by canonical Agent resume.

## Exit decision

R7-E exit conditions are mechanically proven:

```text
same invocation_id
same request_fingerprint
one new attempt per continuation call
no second CapabilityInvocation row
atomic invocation CAS + attempt insertion
atomic invocation/attempt RUNNING transition
single winner under concurrent continuation
no unsafe ambiguous non-idempotent replay
same stable client / new connection generation
real TCP same-ID dispatch/replay works
no execution.resume wiring
```

**R7-E is CLOSED / VERIFIED.**
