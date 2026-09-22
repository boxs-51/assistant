# R7 FINAL EXIT GATE — Durable Resume / Reconnect / Continuation

**Repository:** `boxs-51/assistant`  
**Final branch:** `r7-j-real-network-exit-gate`  
**Frozen R7 code HEAD:** `08f15a3768bff134889d399e65ad57ee987dbdca`  
**Frozen R7 code HEAD message:** `R7-J1: preserve canonical prior_messages in test context builder`  
**R7-I compatibility-removal base:** `333ca6695147a846651046daac0dcd6c9004b7ca`  
**Original R7-B/main baseline:** `24f3c5fc808729cbe714e13650c37262bccfef48`  
**Exit-gate date:** 2026-09-22  
**Status:** **R7 CLOSED / FINAL EXIT GATE GREEN**

> This document freezes the final R7 implementation state before integration.
> It is documentation-only and does not change runtime semantics.

---

## 1. Final decision

R7-A through R7-J are closed as one coherent durable execution-resume system.

The final implementation proves the intended lifecycle:

```text
RUNNING E1
    |
    | remote execution becomes unavailable / outcome requires waiting
    v
atomic durable WAITING safe point
    |
    | normalized checkpoint + pending invocation watermarks
    v
WAITING E1
    |
    | replacement connection generation K2 / K3
    | capability registration ACK
    | stable resume_request_id
    v
ResumePlan
    |
    v
ResumeClaim
    |
    | atomic authority consume
    v
RUNNING E1
    |
    | same execution_id
    | same logical invocation identity
    | safe same-invocation continuation/replay only
    v
COMPLETED / FAILED / CANCELLED
```

There is no second execution created by resume.

Legacy Phase 6.9 branch/merge continuation is no longer canonical authority after R7-I.

---

## 2. Final frozen authority model

### 2.1 AgentExecution

`AgentExecution` remains the single durable lifecycle authority.

Allowed resumed transition:

```text
WAITING E1 -> RUNNING E1
```

Resume must not create `E2` for the same logical execution.

### 2.2 ExecutionCheckpoint

A normalized checkpoint is an immutable durable safe point.

For canonical WAITING state:

```text
execution.state == WAITING
=> execution.current_checkpoint_id != null
=> checkpoint.execution_revision == execution.revision
```

The checkpoint contains enough logical state to reconstruct execution without trusting transient process memory.

### 2.3 CapabilityInvocation

R6 `CapabilityInvocation` remains the authority for remote side effects.

R7 never treats a serialized checkpoint string, transport error, or provider/client guess as stronger authority than the durable invocation state and reconciliation result.

### 2.4 AgentToolResult

Model-visible tool output is governed by:

```text
PROVISIONAL
    -> never enters model context

COMMITTED
    -> may enter canonical reconstructed model context
```

### 2.5 ResumeClaim

`ResumeClaim` is the durable single-winner authority for a resume request.

The claim transaction owns the critical transition:

```text
WAITING@N
    -> RUNNING@N+1

ResumeClaim:
    CREATED
    -> CONSUMED
```

together with required routing/task-budget/revalidation fences.

### 2.6 PendingResumeTicket

The client-side ticket is retry/reconnect state, not execution authority.

It carries stable correlation across WebSocket generations and is settled only by canonical resume outcomes.

---

## 3. R7-A -> R7-J closure summary

| Phase | Final state | Closed responsibility |
|---|---|---|
| R7-A | CLOSED | Normalized durable representation, migrations, checkpoint/pending-invocation/resume-claim schema |
| R7-B | CLOSED | Atomic RUNNING -> WAITING transaction and complete pending invocation watermark |
| R7-C | CLOSED | Tool-result commitment boundary and deterministic checkpoint-directed reconstruction |
| R7-D | CLOSED | R6-aware read-only ResumePlan and safe action classification |
| R7-E | CLOSED | Continue the same CapabilityInvocation with attempt N+1; no logical invocation recreation |
| R7-F | CLOSED | Atomic ResumeClaim authority consume and claimed resume action execution |
| R7-G | CLOSED | Supervisor handoff before ACK, durable ACCEPTED/FAILED handoff, recovery and lost-ACK idempotency |
| R7-H | CLOSED | PendingResumeTicket lifecycle, capability-ACK readiness, retry/tombstone/watermark semantics |
| R7-I | CLOSED | Legacy continuation authority removal/materialization compatibility boundary |
| R7-J | CLOSED | Real-network final exit gate: reconnect, restart, lost ACK, concurrent resume race |

---

## 4. R7-A — Representation + Migration

R7-A introduced the durable normalized representation without switching execution authority prematurely.

Key durable surfaces include:

```text
AgentExecution.current_checkpoint_id
AgentExecution.bound_client_id
AgentExecution.bound_connection_id

agent_execution_checkpoints
agent_checkpoint_pending_invocations
agent_resume_claims

AgentToolResult.commit_state
```

Migration history is forward-only. Applied historical migrations are not rewritten.

R7-A intentionally left runtime authority migration to later phases.

Historical completion evidence recorded by `R7_A_COMPLETION.md`:

```text
R7-A focused gate:       5 passed
R6/R5 regression gate:  26 passed
full repository suite:   665 passed
```

---

## 5. R7-B — Atomic WAITING Safe Point

R7-B made WAITING one atomic SQL authority boundary.

A successful canonical transition commits together:

```text
checkpoint
ordered pending invocation snapshots
AgentExecution state/revision/current_checkpoint_id
task-scoped active-slot release when applicable
```

Critical invariant:

> A failed AgentExecution CAS must not leave a committed checkpoint, pending snapshot, or released task-budget slot that represents a transition which never became authoritative.

Pending invocation snapshots are reloaded from R6 authority inside the transaction and retain the complete semantic watermark required by later resume planning.

---

## 6. R7-C — Tool Result Commitment + Deterministic Reconstruction

R7-C closes the model-context safety boundary.

Hard invariants:

1. `PROVISIONAL` tool results never enter model context.
2. R6 terminal authority is required before remote results become `COMMITTED`.
3. Conflicting committed result identity/payload fails closed.
4. Reconstruction uses checkpoint-safe transcript state.
5. Parallel tool results are reconstructed by canonical `AgentIteration.tool_call_ids` ordering.
6. SQL completion/insertion timing never controls model message order.
7. Historical tool messages are rebuilt from durable committed projections rather than trusted serialized payload.

Historical verified CI at R7-C:

```text
Linux full suite:       678 passed, 1 skipped
Windows cl/tests:        38 passed
Phase 5.6 -> 5.11:       SUCCESS
```

---

## 7. R7-D — R6-Aware ResumePlan

R7-D makes resume planning read-only and reconciliation-aware.

Final executable action kinds:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

Unsafe or ambiguous outcomes do not become executable actions.

Planning validates:

```text
checkpoint revision
invocation revision monotonicity
remote outcome watermark monotonicity
request fingerprint
capability version
idempotency
principal/client affinity
task/session authority
parallel ordinal consistency
```

`plan_fingerprint` is semantic identity only; it is never execution authority.

---

## 8. R7-E — Same-Invocation Continuation

R7-E adds continuation of an existing `CapabilityInvocation`.

Successful continuation preserves:

```text
invocation_id = SAME
request_fingerprint = SAME
arguments = SAME
execution_id = SAME
tool_call_id = SAME

attempt_number:
    N -> N+1
```

It does not create another logical invocation.

Continuation is limited to the safe R7-D modes and executes one continuation attempt without silently introducing ordinary internal retries.

Historical verified CI at R7-E:

```text
Linux full suite:       722 passed, 1 skipped
Windows cl/tests:        40 passed
Phase 5.6 -> 5.11:       SUCCESS
```

---

## 9. R7-F — Atomic ResumeClaim Authority

R7-F closes the TOCTOU boundary between a read-only ResumePlan and execution authority.

The canonical consume transaction revalidates current durable state before granting resume ownership.

Conceptually:

```text
load plan assumptions
    |
reserve process-local execution slot
    |
create/get ResumeClaim(resume_request_id)
    |
atomic consume transaction
    |- verify execution still WAITING at expected revision
    |- verify checkpoint still current
    |- verify target principal/client/connection authority
    |- verify R6 invocation watermarks are still valid
    |- reacquire TaskBudget when required
    |- bind replacement connection
    |- WAITING@N -> RUNNING@N+1
    '- ResumeClaim CREATED -> CONSUMED
```

Only one concurrent claimant can win authority.

Claimed actions then compose R7-C/R7-E:

```text
REUSE_COMMITTED
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

Regression authority is covered by:

```text
se/tests/architecture/test_r7_f_resume_claim_contracts.py
se/tests/integration/test_r7_f_resume_claim_atomicity.py
se/tests/architecture/test_r7_f4_claimed_resume_actions.py
```

---

## 10. R7-G — Supervisor Handoff + ACK/Recovery

R7-G establishes the final ordering between durable authority, process-local ownership, and wire acknowledgement.

Canonical accepted path:

```text
ResumePlan
-> supervisor.reserve()
-> ResumeClaim create/get
-> atomic claim consume
-> supervisor.start_reserved()
-> claimed activation barrier
-> durable handoff ACCEPTED
-> execution.resume.accepted
-> continue same AgentExecution
```

The ACK never grants authority.

A lost ACK after durable ACCEPTED cannot cause a second claim, second lifecycle CAS, second task-budget acquisition, or second supervisor task.

Post-claim activation/handoff failure is converted into durable recovery semantics rather than abandoning `RUNNING` ownership ambiguously.

---

## 11. R7-H — PendingResumeTicket + Client Recovery State

R7-H closes reconnect behavior on the client side.

Key invariants:

1. WAITING tickets are principal/stable-client scoped.
2. Automatic resume begins only after capability registration ACK establishes readiness.
3. A stable `resume_request_id` survives reconnect.
4. Lost-ACK retry reuses the same request identity.
5. Terminal ACK watermarks are monotonic.
6. Non-retryable rejection/conflict is tombstoned rather than retried forever.
7. Malformed resume outcomes fail closed.
8. Resume worker processing is bounded and fair across tickets.
9. Realtime correlation queues are bounded.
10. Duplicate WAITING publication does not destroy an in-flight ticket.

Primary proof surfaces:

```text
cl/src/core/resume_ticket.py
cl/tests/test_r7_h_pending_resume_ticket.py
se/tests/architecture/test_r7_h_waiting_ticket.py
se/tests/integration/test_r7_h_waiting_ticket_query.py
```

---

## 12. R7-I — Legacy Authority Removal

R7-I removes legacy Phase 6.9 continuation state as canonical write/runtime authority.

The final compatibility model is:

```text
historical legacy continuation state
    -> may be read/materialized through fenced compatibility logic

new canonical authority
    -> normalized R7 checkpoint + ResumePlan + ResumeClaim
```

R7-I specifically hardens:

- materialization of legacy WAITING state into normalized checkpoint authority;
- idempotent/concurrent materialization;
- checkpoint collision fail-closed behavior;
- canonical preflight without legacy authority;
- legacy continuation JSON as read-only compatibility data;
- removal of new legacy continuation writes;
- removal of legacy runtime/bootstrap/container authority;
- pending connection watermark sourced from R6;
- preservation of late-committed pending invocation identity.

Frozen R7-I base:

```text
333ca6695147a846651046daac0dcd6c9004b7ca
```

---

## 13. R7-J — Real-Network Final Exit Gate

R7-J is the final system-level proof over real TCP WebSocket transport.

Production change after R7-I is deliberately narrow:

```text
se/src/transport/gateway/api/v1/events_router.py
```

Resume planning is detached from the WebSocket receive loop so reconciliation responses can be ingested on the same socket.

Disconnect cleanup order is:

```text
disconnect_connection()
-> fail connection-bound invocation/reconciliation futures
-> cancel/drain detached resume tasks
-> unregister websocket
```

### Final R7-J E2E cases

`se/tests/e2e/test_r7_j_real_network_exit_gate.py` proves four canonical cases:

#### J1 — K1 disconnect -> K2 automatic resume

```text
same execution_id
replacement connection generation
same logical pending invocation
single external effect
no leaked multiplexer waiters
```

#### J2 — server restart while WAITING

```text
process-local state lost
durable SQL retained
replacement server reconstructs WAITING execution
replacement client resumes E1
same invocation identity
same checkpoint authority
single external effect
```

The restarted inference path receives the reconstructed canonical prior messages rather than relying on in-memory K1 history.

#### J3 — lost accepted ACK -> K3 same-request retry

The first `execution.resume.accepted` wire delivery is intentionally dropped after durable ACCEPTED.

The replacement connection retries with the same:

```text
resume_request_id
```

and proves:

```text
one ResumeClaim
one supervisor start
one WAITING -> RUNNING authority transition
one external effect
semantic ACCEPTED replay on K3
```

#### J4 — concurrent real resume authority race

Two real WebSocket generations race using different resume request IDs against the same WAITING checkpoint.

Exactly:

```text
1 x execution.resume.accepted
1 x execution.resume.rejected(code=RESUME_CONFLICT)
1 x ResumeClaim winner
1 x external effect
```

Both planners may observe the same safe point, but only one consumer wins the durable authority CAS.

---

## 14. Final R7 invariants

The following are frozen for downstream work.

### R7-FINAL-I01 — one execution lifecycle

Resume preserves the same `execution_id`.

### R7-FINAL-I02 — WAITING requires a durable safe point

Canonical WAITING state must be reconstructable from durable state.

### R7-FINAL-I03 — checkpoint is immutable

A resume/recovery transition never mutates an older safe point into a different logical safe point.

### R7-FINAL-I04 — R6 remains remote side-effect authority

No transport symptom overrides durable invocation/reconciliation state.

### R7-FINAL-I05 — provisional tool output is invisible

`PROVISIONAL` cannot enter model context or canonical resumed transcript.

### R7-FINAL-I06 — deterministic reconstruction

Parallel tool results are ordered by logical tool-call order, not completion timing.

### R7-FINAL-I07 — no logical invocation recreation

Safe continuation creates attempt N+1 under the same `CapabilityInvocation`.

### R7-FINAL-I08 — ResumePlan is not authority

Planning may race. Durable claim consumption decides the winner.

### R7-FINAL-I09 — one resume authority winner

Concurrent consumers of the same WAITING safe point cannot both enter RUNNING authority.

### R7-FINAL-I10 — supervisor ownership precedes accepted ACK

No accepted ACK may advertise an execution that has no owned runtime handoff.

### R7-FINAL-I11 — ACK delivery is not authority

Lost ACK is resolved by durable handoff replay using the same request identity.

### R7-FINAL-I12 — stable client, replaceable connection generation

`client_id` is durable/stable affinity; `connection_id` identifies a replaceable realtime generation.

### R7-FINAL-I13 — READY follows capability ACK

Client auto-resume cannot race ahead of capability registration readiness.

### R7-FINAL-I14 — legacy continuation cannot regain write authority

Legacy continuation state remains migration/read compatibility only.

### R7-FINAL-I15 — process restart must be survivable

Required continuation semantics must be recoverable without relying on server or client process memory.

---

## 15. Final CI evidence at frozen R7 code HEAD

Frozen code HEAD:

```text
08f15a3768bff134889d399e65ad57ee987dbdca
```

GitHub Actions `Architecture Baseline`:

```text
linux-full-suite:
    791 passed
    1 skipped
    14 warnings
    SUCCESS

windows-client-contracts:
    SUCCESS
```

The Linux job executes:

```text
python -m pytest -q
```

so the final R7-J real-network exit-gate tests are part of the repository-wide collection.

All legacy phase workflow gates at this same HEAD are green:

```text
Phase 5.6 Exit Gate   SUCCESS
Phase 5.7 Exit Gate   SUCCESS
Phase 5.8 Exit Gate   SUCCESS
Phase 5.9 Exit Gate   SUCCESS
Phase 5.10 Exit Gate  SUCCESS
Phase 5.11 Exit Gate  SUCCESS
Architecture Baseline SUCCESS
```

Warnings are deprecation warnings and do not represent an R7 correctness blocker.

---

## 16. R7-J branch delta from R7-I

R7-J is exactly:

```text
R7-I @ 333ca669
    + 11 commits
    + 0 commits behind
```

The R7-J-only production/test blast radius is:

```text
se/src/transport/gateway/api/v1/events_router.py
se/tests/e2e/test_r7_j_real_network_exit_gate.py
```

This narrow delta is intentional: R7-J is an exit-gate/hardening phase, not another redesign.

---

## 17. Final frozen baseline

### Code baseline

```text
branch:
r7-j-real-network-exit-gate

code HEAD:
08f15a3768bff134889d399e65ad57ee987dbdca
```

The documentation commit that adds this file may move the branch HEAD, but it must not be treated as a new R7 runtime baseline.

The immutable code baseline remains:

```text
08f15a3768bff134889d399e65ad57ee987dbdca
```

### Merge rule

Before R7 is considered integrated into `main`:

1. merge/rebase must preserve the R7-J code baseline semantics;
2. no legacy continuation authority may be reintroduced during conflict resolution;
3. full CI must remain green after integration;
4. downstream branches based on R7-I must first absorb final R7 before their own merge.

---

## 18. Explicit non-goals after closure

R7 closure does not imply implementation of unrelated future systems.

In particular R7 does not require:

```text
Central Asset Storage F5 provider hydration
F6 client/UI asset flow
F7 provider-generated media ingestion
F8 legacy asset cutover
new TaskBranch semantics
new tool metadata architecture
new SE/CL capability redesign beyond frozen R7 contracts
```

Those features must consume R7 as a stable baseline rather than mutate R7 authority casually.

---

## 19. Final closure statement

```text
R7-A  CLOSED
R7-B  CLOSED
R7-C  CLOSED
R7-D  CLOSED
R7-E  CLOSED
R7-F  CLOSED
R7-G  CLOSED
R7-H  CLOSED
R7-I  CLOSED
R7-J  CLOSED

FINAL CODE BASELINE:
08f15a3768bff134889d399e65ad57ee987dbdca

FINAL EXIT GATE:
GREEN

BLOCKING P0:
NONE FOUND

BLOCKING P1:
NONE FOUND
```

R7 durable resume/reconnect/continuation is frozen at this boundary.
