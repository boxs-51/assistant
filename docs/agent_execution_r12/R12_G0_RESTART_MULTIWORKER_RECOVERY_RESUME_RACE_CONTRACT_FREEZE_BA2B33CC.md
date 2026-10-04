# AE-R12-G0 — Restart / Multi-worker / Recovery-vs-Resume Race Contract Freeze

Release authority: Issue #107 independent PRE-CLAIM re-anchor comment `#5976246049`  
Release baseline: `main@ba2b33ccb2bb1aed167f7ee6eff8f70f7d8db1bc`  
Owner CLAIM: Issue #107 comment `#5976242897`  
Policy: Issue #85 v2.5

## Status

```text
stage = AE-R12-G0
class = CONTRACT / EVIDENCE ONLY
production/runtime delta = ZERO
schema/migration delta = ZERO
API/transport delta = ZERO
R12-G production repair authority = NONE
R12-H authority = NONE
merge authority = NONE
```

This freeze consumes the independently re-anchored release on exact
`main@ba2b33cc...` after CTX PR #215 landed. The CTX movement is
promotion-reservation recovery only and remains NON_MATERIAL to the durable
ResumeClaim / recovery-activation arbitration owned by G0. Exact-main
Architecture #1963 is GREEN/GREEN.

## Purpose

R12-G0 proves whether already-landed R7/R12 primitives are sufficient for
restart, multi-worker, and recovery-vs-user-resume races before any new
production authority is requested.

The proof MUST reuse the existing durable seams:

```text
R7 CLIENT_RECONNECT
  -> ResumeClaim
  -> DurableAgentStore.consume_resume_claim(...)
  -> final WAITING -> RUNNING durable CAS

R12 SERVER_RECOVERY
  -> ResumeClaim
  -> AgentRecoveryActivationService.activate(...)
  -> DurableAgentStore.consume_recovery_claim(...)
  -> final WAITING(RECOVERY) -> RUNNING durable CAS + fresh lease
```

There is no second recovery-specific claim lifecycle, no new MANUAL API, and no
test-only arbitration transaction.

## Frozen invariants

### G0-I01 — one durable winner

Two independent service/store instances racing one exact durable recovery cut
MUST converge to at most one RUNNING activation. A losing worker cannot mint a
second active lease owner, second lease generation, or second TaskBudget active
execution slot.

### G0-I02 — process restart is not authority loss

A CREATED ResumeClaim, its plan identity, the execution revision/checkpoint,
and the later CONSUMED result are durable. A fresh service/store instance after
process replacement can reconstruct and consume the same authority without a
process-local Supervisor token.

### G0-I03 — CLIENT_RECONNECT and SERVER_RECOVERY cannot both own one cut

The current public user resume vocabulary remains
`ResumeTriggerType.CLIENT_RECONNECT`. `MANUAL` is not introduced as a new
public R12 path.

A reconnect claim frozen on an older `WAITING(CONNECTION)` revision MUST fail
closed after that execution has resumed, acquired an execution lease, and
published a newer `WAITING(RECOVERY)` cut.

A recovery claim frozen on an older `WAITING(RECOVERY)` revision MUST fail
closed after later durable authority has advanced through a new
`WAITING(CONNECTION)` cut and a valid CLIENT_RECONNECT consume.

No durable revision is simultaneously both CONNECTION and RECOVERY.

### G0-I04 — one canonical arbitration seam

R7 and R12 continue to converge through the same durable ResumeClaim substrate
and final execution CAS. G0 evidence may build independent process/store/service
objects, but MUST NOT invent a second claim table, lock, in-memory winner token,
or recovery-only activation transaction.

### G0-I05 — TaskBudget and lineage preservation

A task-scoped race preserves `execution_id`, `task_id`, `branch_id`,
remaining active budget, TaskBudget incarnation, and branch lineage. The
single winning activation reacquires exactly one active execution slot; losers
do not reset budget or duplicate capacity.

### G0-I06 — terminal state and external-side-effect fences remain inherited

A terminal execution cannot be resurrected by a stale recovery claim.
R6 invocation reconciliation and R10 no-blind-replay/provider-visible-output
rules remain inherited. G0 introduces no provider/tool dispatch and no replay
rule.

## Evidence matrix

The released integration evidence proves:

1. two independent recovery services race one task-scoped RECOVERY cut;
2. exactly one activation becomes the durable RUNNING/lease owner;
3. TaskBudget active execution accounting remains exactly one;
4. a claim created by one process is consumed by a fresh service/store process;
5. an older reconnect claim is stale after a later RECOVERY cut;
6. an older recovery claim is stale after later CONNECTION + valid reconnect;
7. terminal truth rejects stale recovery activation.

The architecture evidence freezes source-level ownership:

- CLIENT_RECONNECT remains the existing R7 path;
- SERVER_RECOVERY remains the R12-F2 path;
- both use durable ResumeClaim records;
- recovery activation delegates to `consume_recovery_claim`;
- no G0 production source file is changed.

## Explicitly closed

G0 does not authorize edits to Agent production/runtime/persistence/TaskBudget,
lease/recovery planning, transport/API, schema/migrations, provider retry or
deadline behavior, CAS, CTX, R11 retention/GC, #156 routing/sandbox, or R12-H.

If the matrix exposes a production defect, the owner MUST stop and request a
separate bounded R12-G production PRE-CLAIM naming the exact invariant, source
paths, and focused evidence.
