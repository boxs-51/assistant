# AE-R12-A — HEAD Audit / Crash-Recovery & Execution-Lease Contract Freeze

**Primary authority:** Issue #107  
**Canonical predecessor:** AE-R11 / Issue #31 CLOSED / COMPLETED  
**Claim baseline:** `main@6228734ae7a380719bb14fa520e3307c5330aa31`  
**Entry health:** Architecture #1383 GREEN/GREEN  
**Policy:** Issue #85 v2.5  
**Stage class:** CONTRACT / EVIDENCE / DOCS / ARCHITECTURE-TEST ONLY  
**Production/runtime/schema/migration delta:** ZERO

## 1. Objective

AE-R12 owns crash recovery and distributed execution lease semantics for Agent Execution.

Final AE-R12 exit gate:

> Server crash cannot leave a zombie RUNNING execution indefinitely.

R12-A is pre-implementation. It freezes the current implementation gap, inherited authority boundaries, correctness invariants, staged implementation order, and stop conditions before any durable lease/schema/runtime change is allowed.

No production implementation is authorized by this file.

## 2. Canonical entry lock

```text
predecessor            = AE-R11 COMPLETE
R11-H merge            = 879e84b698100936f66719ecb0571569561e0df1
current main           = 6228734ae7a380719bb14fa520e3307c5330aa31
post-wave Architecture = #1383 GREEN/GREEN
blocking R11 P0/P1/P2  = NONE
Issue                  = #107
Policy                 = #85 v2.5
```

R12-A may remain on this stable development baseline while unrelated drift is NON_MATERIAL under Policy #85 v2.5. Drift that changes Agent execution identity, state-machine semantics, checkpoint/persistence authority, pending invocation reconciliation, TaskBudget authority, or lease/recovery ownership is MATERIAL and requires reclassification before integration.

## 3. Current implementation facts

### 3.1 RECOVERY vocabulary already exists

`se/src/domain/schemas/agent_execution.py` already contains `AgentExecutionWaitReason.RECOVERY`.

RECOVERY is a wait_reason. It is not a separate execution state. Canonical durable execution states remain CREATED / RUNNING / WAITING / terminal.

### 3.2 R7 already owns post-claim in-process recovery

`se/src/runtimes/agent/runtime.py` already contains `AgentRuntime.recover_claimed_resume(...)`.

That path handles a resume that already consumed durable authority but fails activation while the process is still executing the recovery path. It may return the same execution to a fresh `WAITING(RECOVERY)` safe point.

This is inherited R7 behavior. It is not a distributed crash lease, stale-RUNNING scanner, or orphan-worker takeover mechanism.

### 3.3 Supervisor ownership is process-local only

`se/src/runtimes/agent/supervisor.py` explicitly defines its reservation token as process-local and says it must never be confused with a distributed execution lease.

```text
AgentExecutionSupervisor ownership
!=
durable R12 execution lease
```

A worker inspecting the database cannot infer stale ownership merely because its local supervisor has no matching task.

### 3.4 Durable AgentExecution has no distributed owner/lease

`se/src/infrastructure/storage/models/sql/agent/execution.py` currently stores durable state, wait reason, revision, checkpoint, budget, wait-expiry, and lineage fields.

It does not currently store `owner_instance_id`, `lease_expires_at`, or an equivalent durable distributed execution-owner lease surface.

### 3.5 No canonical stale-RUNNING scanner/coordinator exists

The current Agent production runtime has no canonical recovery scanner/coordinator that finds expired/orphan RUNNING executions, proves distributed lease expiry, wins recovery ownership atomically, reconciles pending invocation state, and transitions to a recovery-safe durable state or terminal failure.

That is the primary R12 ownership gap.

## 4. Frozen initial findings

### P0-R12-A-OWNERSHIP-1 — durable RUNNING owner identity missing

A durable RUNNING execution does not identify the server/worker instance that owns its execution authority. Process-local duplicate exclusion cannot solve distributed ownership.

### P0-R12-A-LEASE-2 — no durable execution lease

There is no durable lease expiry authority for a RUNNING execution. A RUNNING row alone does not distinguish a live-but-slow owner from a dead owner / zombie RUNNING execution.

### P0-R12-A-RECOVERY-3 — stale RUNNING coordinator absent

No canonical scanner/coordinator currently owns stale-RUNNING recovery. R12 must define scan eligibility, lease-expiry evidence, atomic recovery claim, winner/loser behavior, safe transition target, and scanner retry/idempotency behavior.

### P0-R12-A-RECONCILIATION-4 — crash recovery cannot blindly replay side effects

Lease expiry says nothing about whether an external provider/remote-client side effect already happened. If pending invocation authority is IN_FLIGHT or OUTCOME_UNKNOWN, R12 must reuse R6/R7 reconciliation before runtime/model continuation.

R12 must not invent a second remote invocation truth source.

### P1-R12-A-RACE-5 — competing recovery/resume ownership needs one durable winner

The following races must converge by durable CAS/lease authority:

```text
recovery worker A vs recovery worker B
recovery worker vs user RESUME
recovery worker vs terminalization
recovery worker vs retry/fork authority where semantically applicable
```

Process-local locks are insufficient.

### P1-R12-A-BUDGET-6 — recovery must preserve budget accounting

Recovery must preserve remaining active execution budget, TaskBudget active execution accounting, retry/fork branch accounting, and wait TTL semantics.

Lease expiry alone does not authorize budget reset, new execution allocation, or TaskBudget slot duplication.

### P1-R12-A-LEASE-LOSS-FENCING-2 — expired owner must be fenced before recovered activation

Revision/CAS protects durable writes, but it is not sufficient to prevent an old process from issuing a new external provider/tool side effect after its lease expired and another worker acquired recovery authority.

R12 therefore requires a durable fencing authority associated with execution ownership. The concrete representation may be an epoch, generation, token, or another independently audited mechanism with equivalent monotonic stale-owner rejection semantics.

A runtime that owns a RUNNING execution must validate current lease/fence authority:
- immediately before every externally visible provider/tool dispatch;
- immediately before every durable state/checkpoint/terminal commit that depends on active ownership.

If lease renewal fails or the current fencing authority is lost, that local runtime must immediately stop issuing new external dispatches and relinquish/park local execution ownership safely. A paused or partitioned old owner that resumes after a newer recovery owner wins must fail closed before any new external side effect.

Fencing does not replace R6/R7 reconciliation or R10 no-replay authority. Unknown external outcomes still reconcile through the inherited invocation lifecycle before continuation.

## 5. Frozen authority inheritance

R12 extends the landed Agent architecture; it does not replace it.

- **AE-R6** remains authoritative for CapabilityInvocation / ClientInvocationLedger lifecycle, remote result reconciliation, terminal replay/idempotency, and IN_FLIGHT / OUTCOME_UNKNOWN interpretation.
- **AE-R7** remains authoritative for WAITING, ResumeClaim, checkpoint-directed resume, WAITING -> RUNNING durable claim semantics, reconnect protocol, and post-claim in-process RECOVERY safe-point handling.
- **AE-R8** remains authoritative for TaskBranch identity, FORK lineage, and branch context isolation.
- **AE-R9** remains authoritative for RETRY lineage, branch resolution, final Task resolution, and TaskBudget.
- **AE-R10** remains authoritative for provider retry/fallback, logical deadline/budget, and post-visible-output no-replay constraints.
- **AE-R11** remains authoritative for checkpoint/transcript physical representation, reconstruction, transaction/query behavior, persistence performance, retention, and GC.

R12 must not introduce a second checkpoint, transcript, GC, or invocation persistence lifecycle.

## 6. Cross-track hard boundaries

R12 does not own CAS FileAsset/FileBlob/FileReference/FileProviderBinding lifecycle, provider hydration, ObjectStorage reconciliation, READY deletion/release, physical CAS GC, or provider remote cleanup/reclamation.

R12 does not own CTX Memory admission/promotion, Personalization, Context source lifecycle, source-proof reservation authority, or Context retention.

A CAS or CTX lifecycle state cannot become proof of stale Agent execution ownership.

## 7. R12-A invariants

```text
R12A-I01
Process-local supervisor ownership != durable distributed execution lease.

R12A-I02
A RUNNING execution is not stale merely because the inspecting worker has no local asyncio task for it.

R12A-I03
Lease expiry is necessary evidence for stale ownership but is not sufficient authority to replay an unknown external side effect.

R12A-I04
Recovery of the same logical execution preserves execution_id unless an independently authorized RETRY/FORK rule creates a new execution.

R12A-I05
Terminal executions are never recovered or resurrected.

R12A-I06
WAITING remains the only resumable durable execution state; RECOVERY remains a wait_reason.

R12A-I07
RUNNING -> WAITING(RECOVERY) and recovery terminalization require revision/CAS-protected durable ownership.

R12A-I08
Competing recovery workers converge to one durable winner.

R12A-I09
Automatic recovery and user RESUME cannot both activate the same execution.

R12A-I10
IN_FLIGHT / OUTCOME_UNKNOWN remote invocation state must reconcile through R6 before runtime/model continuation.

R12A-I11
R12 preserves R11 checkpoint/transcript/retention/GC authority.

R12A-I12
R12 acquires no CAS asset lifecycle/GC or CTX Memory lifecycle authority.

R12A-I13
Durable execution ownership includes a fencing authority capable of rejecting an expired/stale owner after a newer recovery owner wins.

R12A-I14
Current lease/fence authority must be validated immediately before every externally visible provider/tool dispatch.

R12A-I15
Current lease/fence authority must be validated immediately before durable active-owner commits; revision CAS alone is not the external-side-effect fence.

R12A-I16
Lease-renewal loss or fence-authority loss requires the local runtime to stop new external dispatch immediately and relinquish/park ownership safely.
```

## 8. Proposed staged implementation

Only R12-A is claimed by this freeze.

```text
R12-A  HEAD audit + crash-recovery / lease contract freeze
R12-B  durable owner/lease/fence representation + migration
R12-C  lease acquire / renew / release authority
R12-D  stale-RUNNING classification + scanner
R12-E  atomic recovery ownership + WAITING(RECOVERY) transition
R12-F  pending invocation reconciliation + recovery activation
R12-G  restart / multi-worker / recovery-vs-resume race matrix
R12-H  full fault/exit matrix + final CI/freeze
```

No later stage gains production authority merely because it appears in this plan.

Each production stage requires its own exact CLAIM/release, scoped owned files, targeted tests, full Architecture, independent audit, and integration/merge authority.

## 9. Required final R12 evidence

Before AE-R12 can close, the complete roadmap must prove at least:

- kill worker during provider call;
- kill worker during remote invocation;
- restart with durable WAITING execution;
- restart with orphan durable RUNNING execution;
- lease acquire/renew/expiry boundaries;
- clock/expiry boundary fail-closed behavior;
- competing recovery workers;
- paused/partitioned old owner resumes after lease loss and newer recovery ownership;
- stale old owner cannot dispatch provider/tool side effects after fencing authority changes;
- recovery vs user RESUME;
- recovery vs terminalization;
- pending IN_FLIGHT reconciliation;
- OUTCOME_UNKNOWN no-blind-replay behavior;
- provider visible-output no-replay inheritance;
- TaskBudget active-slot preservation;
- terminal execution cannot resurrect;
- no duplicate AgentRuntime activation;
- recovery through R11 ref-backed checkpoint reconstruction;
- Linux + Windows full Architecture.

## 10. R12-A exact owned scope

Exactly two files:

```text
docs/agent_execution_r12/R12_A_HEAD_AUDIT_CRASH_RECOVERY_LEASE_CONTRACT_FREEZE_6228734A.md
se/tests/architecture/test_r12_a_crash_recovery_contract_freeze.py
```

R12-A must not modify production/runtime/schema/migration/client/provider/CAS/CTX files.

## 11. Stop conditions

Stop and return to contract review if an implementation would:

- treat local supervisor absence as proof of stale distributed ownership;
- recover a terminal execution;
- replay IN_FLIGHT or OUTCOME_UNKNOWN without R6 reconciliation;
- create a new execution_id merely because a lease expired;
- reset active budget or TaskBudget merely because a lease expired;
- let two workers own the same execution lease concurrently;
- allow a stale/expired owner to dispatch externally visible provider/tool work after a newer fence authority wins;
- continue new external dispatch after lease renewal or fencing-authority loss;
- rely on revision CAS alone as protection against stale-owner external side effects;
- transition RUNNING -> WAITING(RECOVERY) without durable revision/ownership CAS;
- reinterpret R11 retention/GC as R12 recovery authority;
- absorb CAS lifecycle/GC or CTX Memory lifecycle into R12;
- change provider retry/fallback semantics to implement recovery.

## 12. R12-A exit gate

R12-A becomes FINAL GREEN only if:

1. exact scope remains the two files above;
2. production/runtime/schema/migration delta remains zero;
3. the architecture test binds baseline facts and authority boundaries;
4. fresh exact-head Linux + Windows Architecture is GREEN;
5. review threads are resolved;
6. independent audit finds no blocking R12-A P0/P1 in the freeze itself;
7. no MATERIAL current-main/cross-track drift invalidates the candidate.

Only after R12-A FINAL GREEN may independent audit release R12-B production representation/migration CLAIM.

## 13. Cross-track state at CLAIM

### CTX / Issue #15

PR #105 is an open zero-production trusted-reservation authority contract candidate. Its current scope transfers no R12 lease/recovery authority. If it lands before R12-A integration with the same scope, classify the drift under Policy #85 v2 rather than automatically re-anchoring.

### CAS / Issue #74

CAS-F5-D-P1 is landed. The next CAS gate is a zero-production activation/surface-convergence contract and explicitly keeps R11/R12 authority closed. No current R12 ownership collision is known.

## 14. Current disposition

```text
AE-R12 status              = ACTIVE
R12-A status               = CONTRACT FREEZE CANDIDATE
claim baseline             = main@6228734ae7a380719bb14fa520e3307c5330aa31
production implementation  = CLOSED / NOT STARTED
durable lease              = NOT IMPLEMENTED
stale-RUNNING coordinator  = NOT IMPLEMENTED
merge authority            = governed separately by Issue #85 / Issue #107
```
