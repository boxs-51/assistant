# AE-R12-H1-A — Stale RUNNING Evacuation Contract Freeze

Primary authority: Issue #107
Policy: Issue #85 v2.5
Parent independent HOLD: comment #5981136394
Owner precursor request: comment #5980429564
Canonical baseline: `main@683c614f20c7ce1dbf0e9736008eeac33f1c97b1`
Baseline health: Architecture #2053 / run `37207794150` GREEN/GREEN
Stage: AE-R12-H1-A
Class: CONTRACT + ARCHITECTURE EVIDENCE ONLY
Status: CANDIDATE / ZERO-PRODUCTION / PRODUCTION PRE-CLAIM REQUIRED

## 1. Purpose and authority

H1-A is the smallest repair contract for the H0-20 liveness gap:

~~~text
expired owned RUNNING
  -> bounded production observation
  -> exact stale-owner fenced evacuation
  -> WAITING(RECOVERY)
     OR FAILED when a recovery-safe durable cut is deterministically unprovable
~~~

The H1-A goal is only to guarantee that an execution whose durable owner lease
has expired cannot remain durably RUNNING forever while the canonical server is
healthy and the recovery control plane is operating.

H1-A does not resume Agent work. It does not create or consume a ResumeClaim,
does not reconstruct an authenticated Identity, does not activate F2, and does
not enter F3 or AgentRuntime recovered execution.

~~~text
production/runtime delta in this candidate = ZERO
schema/migration delta = ZERO
config delta = ZERO
provider/capability delta = ZERO
Identity/authentication delta = ZERO
automatic SERVER_RECOVERY activation = CLOSED
H1-A production CLAIM = NONE
merge authority = NONE
~~~

Only these two NEW files belong to this zero-production candidate:

1. `docs/agent_execution_r12/R12_H1A_STALE_RUNNING_EVACUATION_CONTRACT_683C614F.md`
2. `se/tests/architecture/test_r12_h1a_stale_running_evacuation_contract.py`

Any production implementation requires a fresh independent production PRE-CLAIM.

## 2. Canonical source facts consumed, not redefined

H1-A consumes these already-landed authorities:

- D1/D2B observation: `StaleLeaseScanCoordinator` observes only exact owned,
  expired `RUNNING` leases, in `(lease_expires_at, execution_id)` order, with
  page/row/duration bounds and no mutation authority.
- R12-E non-task takeover:
  `DurableAgentStore.commit_recovery_waiting_checkpoint(...)` revalidates
  state + revision + owner + generation + exact expiry and atomically publishes
  one deterministic `WAITING(RECOVERY)` checkpoint.
- R12-E task takeover:
  `TaskBudgetService.recover_task_scoped_execution(...)` performs the same
  stale-owner recovery cut while releasing TaskBudget active capacity exactly
  once under durable reservation/CAS authority.
- Repository stale-owner CAS:
  `AgentRepository.compare_and_set_recovery_waiting_execution(...)` already
  proves the required exact stale receipt shape.
- R6 remains owner of CapabilityInvocation/remote-outcome truth. H1-A never
  deletes, rewrites, retries or replays those rows.
- R9/R5 remain owner of TaskBudget and multi-branch accounting. H1-A may only
  request the exact stale-execution release mutation later released by PRE-CLAIM.

Current D2B `scan_once()` begins every independent sweep from:

~~~text
after_expiry = None
after_execution_id = None
~~~

That is sufficient for one bounded snapshot traversal, but not sufficient to
prove cross-sweep fairness under a persistent poison prefix. H1-A freezes the
missing fairness contract below; this candidate does not implement it.

## 3. P1-H1-AUTHENTICATED-IDENTITY-RECONSTRUCTION-1 disposition

### CLOSED FOR H1-A / REMAINS OPEN FOR H1-B

Authenticated Identity reconstruction is removed from H1-A by construction.

H1-A MUST stop after durable evacuation:

~~~text
RUNNING -> WAITING(RECOVERY)
RUNNING -> FAILED
~~~

H1-A MUST NOT perform any of:

- `AgentRecoveryPlanningService.build_recovery_plan(...)`;
- `get_or_create_resume_claim(...)` for SERVER_RECOVERY;
- `AgentRecoveryActivationService.activate(...)`;
- `DurableAgentStore.prepare_recovery_plan_context(...)`;
- `AgentExecutionSupervisor.reserve(...)` or `start_reserved(...)` for recovery;
- `AgentRecoveryExecutionService.execute_recovered_next_iteration(...)`;
- `AgentRuntime.execute_recovered_next_iteration(...)`;
- construction or fabrication of `Identity` from only a durable user/principal id;
- provider inference, capability dispatch, continuation dispatch, retry or replay.

`AgentExecution.context_state` does not durably preserve the complete
authenticated Identity authority (`auth_type`, scopes, permissions, tenant/org,
roles and related authentication attributes). H1-A therefore treats all automatic
reactivation as H1-B and CLOSED.

## 4. Exact stale observation / evacuation receipt

Every H1-A mutation must bind the immutable D1 observation plus source revision:

~~~text
execution_id
source_revision
observed_owner_instance_id
observed_lease_generation
observed_lease_expires_at
takeover_now_utc
~~~

Before either WAITING or FAILED mutation, the same transaction must revalidate:

~~~text
id == execution_id
revision == source_revision
state == RUNNING
owner_instance_id == observed_owner_instance_id
lease_generation == observed_lease_generation
lease_expires_at == observed_lease_expires_at
lease_expires_at <= takeover_now_utc
~~~

A stale receipt never mutates anything. A winner must advance revision exactly
once, clear live owner/expiry authority, and advance the lease generation exactly
once so the dead owner remains fenced.

## 5. Preferred evacuation path — WAITING(RECOVERY)

For every exact stale observation, the future H1-A control plane must attempt
the existing R12-E safe-point path first.

Non-task:

~~~text
DurableAgentStore.commit_recovery_waiting_checkpoint(...)
~~~

Task-scoped:

~~~text
TaskBudgetService.recover_task_scoped_execution(...)
~~~

Successful R12-E takeover is final for H1-A. H1-A MUST NOT continue into F1/F2/F3.

A stale/racing receipt rejection is not an error requiring terminalization.
It means another durable authority moved first. The control plane records the row
as no-longer-owned-by-this-observation and continues the bounded sweep.

## 6. P1-H1-UNRECOVERABLE-STALE-DISPOSITION-2 disposition

### CONTRACT-CLOSED / PRODUCTION SEAM STILL UNIMPLEMENTED

If R12-E proves that the exact stale receipt still owns the same RUNNING row but
safe-point reconstruction fails deterministically from durable semantic data,
H1-A requires a second, mutually exclusive evacuation outcome:

~~~text
RUNNING@N exact stale receipt
  -> FAILED@N+1
~~~

The terminal state is exactly `FAILED`. H1-A does not use COMPLETED, CANCELLED
or TIMEOUT for crash-recovery corruption.

Future production terminalization must use one specialized stale-owner CAS seam,
not generic `compare_and_set_execution(...)` by itself. The terminal mutation
must atomically require the receipt predicates in section 4 and publish:

~~~text
revision = source_revision + 1
state = FAILED
wait_reason = null
wait_expires_at = null
owner_instance_id = null
lease_expires_at = null
lease_generation = observed_lease_generation + 1
completed_at = takeover_now_utc
error = R12_STALE_RECOVERY_UNRECOVERABLE:<reason_code>
~~~

All other durable evidence is preserved unless a later separately-audited
contract says otherwise:

- checkpoint/current_checkpoint_id;
- transcript and inference request/response evidence;
- CapabilityInvocation and ClientInvocationLedger rows;
- committed tool results;
- task/branch lineage;
- bound client/connection historical fields;
- cumulative TaskBudget usage and UBQ evidence.

Terminalization is allowed only for a deterministic durable reconstruction
reason from the explicit allowlist below. Initial H1-A allowlist:

~~~text
SAFE_POINT_TRANSCRIPT_CORRUPT
SAFE_POINT_ACTIVE_BATCH_AMBIGUOUS
SAFE_POINT_TOOL_RESULT_CONFLICT
SAFE_POINT_EXECUTION_INVALID
SAFE_POINT_ITERATION_LINEAGE_CONFLICT
SAFE_POINT_ITERATION_AMBIGUOUS
SAFE_POINT_CHECKPOINT_MISSING
SAFE_POINT_CHECKPOINT_LINEAGE_CONFLICT
SAFE_POINT_CHECKPOINT_ITERATION_MISSING
SAFE_POINT_POST_RECOVERY_PROGRESS_UNPROVEN
SAFE_POINT_RECOVERY_BATCH_SNAPSHOT_CORRUPT
SAFE_POINT_RECOVERY_BATCH_SNAPSHOT_CONFLICT
SAFE_POINT_RECOVERY_BATCH_SNAPSHOT_MISSING
SAFE_POINT_TOOL_CALL_AMBIGUOUS
SAFE_POINT_TOOL_CALL_MISSING
SAFE_POINT_TOOL_CALL_CONFLICT
SAFE_POINT_COMMITTED_RESULT_CONFLICT
SAFE_POINT_INVOCATION_MISSING
SAFE_POINT_INVOCATION_CONFLICT
SAFE_POINT_ACTIVE_BATCH_MEMBERSHIP_MISMATCH
SAFE_POINT_ACTIVE_BATCH_AUTHORITY_MISSING
SAFE_POINT_TRANSCRIPT_DIVERGENCE
INVALID_CHECKPOINT_REPRESENTATION_STATE
MISSING_TRANSCRIPT_REPRESENTATION
TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH
TRANSCRIPT_REPRESENTATION_ANCESTRY_INVALID
TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED
TRANSCRIPT_REPRESENTATION_CORRUPT
DUAL_TRANSCRIPT_MISMATCH
~~~

`SAFE_POINT_INVOCATION_REPOSITORY_MISSING` is explicitly NOT terminalizable.
It is a service/container authority failure. A healthy H1-A deployment must have
the canonical shared invocation repository; absence makes the control plane
unhealthy and prevents H0-20 final certification rather than destroying an
execution.

Unknown/new reconstruction reason codes are also NOT terminalizable until a
fresh contract audit classifies them.

### Task-scoped terminalization

For `task_id != null`, the terminal FAILED mutation must share the existing
TaskBudget transaction/reservation authority and release capacity exactly once:

~~~text
active_executions -= 1
active_parallel_agents -= 1 only when parent_execution_id != null
used/cumulative counters unchanged
TaskBudget incarnation unchanged
TaskBudget state not implicitly closed
AgentTask not implicitly terminalized
~~~

The durable idempotency identity must reuse the existing release family:

~~~text
kind = RELEASE_EXECUTION
reservation_key = execution_id + ':' + target_revision
reservation payload binds target_state = FAILED plus the stale receipt
~~~

This intentionally races with task-scoped R12-E WAITING recovery at the same
source revision. Exactly one fingerprint/CAS winner may release the active slot.

After a task-scoped terminal winner, existing multi-branch Task activity
reconciliation may run under its canonical R8/R9 rules; H1-A does not resolve,
adopt, discard, close or otherwise redefine Task/branch result authority.

### Non-task terminalization

Non-task terminalization must be a specialized repository/store primitive with
the exact predicates from section 4. WAITING recovery and FAILED terminalization
race on the same source revision + stale receipt; only one may win.

## 7. P1-H1-SCAN-CURSOR-STARVATION-3 disposition

### CONTRACT-CLOSED / PRODUCTION CURSOR STILL UNIMPLEMENTED

H1-A freezes a process-local cross-sweep cursor. No schema/migration or durable
cursor table is required for the first production slice.

Cursor identity is exactly:

~~~text
(after_expiry, after_execution_id)
~~~

using the existing D1 ordering `(lease_expires_at ASC, execution_id ASC)`.

The future scanner/control plane contract is:

1. one process owns at most one H1-A sweep loop;
2. a sweep starts from the process-local cursor instead of always from null;
3. the cursor advances only to the last observation actually returned to the
   control plane; a DB timeout before a row is returned cannot skip that row;
4. `MAX_PAGES`, `MAX_ROWS` or `MAX_DURATION` with at least one returned row
   stores that last row as the next process-local cursor;
5. `EXHAUSTED` after a non-null cursor completes the current ordered pass and
   resets the next cursor to null, creating an explicit wrap;
6. an empty sweep from a non-null cursor also wraps to null on the next tick;
7. an empty sweep from null remains null;
8. process restart initializes cursor to null. This is safe because cursor is
   traversal state, never recovery authority;
9. every observation is independently revalidated by R12-E or terminal CAS, so
   cursor movement never authorizes mutation;
10. a row that loses its stale receipt is skipped for that pass; if it is still
    expired RUNNING later, a future wrap observes its new durable identity;
11. one per-row deterministic reconstruction failure is terminalized under
    section 6, so a poison row cannot permanently occupy the prefix;
12. one unexpected/transient row failure is isolated, recorded, cursor advances
    past that observation for the current pass, and the row is revisited after
    wrap; it cannot abort processing of later observations.

Fairness claim:

> Under a healthy finite-capacity database/control-plane service and repeated
> bounded sweeps, every continuously eligible expired owned RUNNING row is
> eventually observed after finitely many cursor advances and then either leaves
> RUNNING or loses the exact stale receipt to another valid durable winner.

Continuous adversarial insertion with earlier sort keys, database unavailability,
or a permanently unhealthy control-plane service is not silently declared PASS;
those conditions make H1-A health unavailable and therefore cannot certify H0-20.

## 8. Lifecycle / shutdown contract

The future H1-A production worker must be lifecycle-owned by the application:

Startup:

1. durable storage/UoW and canonical Agent services are constructed;
2. exactly one H1-A control-plane worker is constructed;
3. the worker performs one immediate bounded sweep;
4. subsequent sweeps run at one positive finite cadence;
5. no overlapping sweep tasks or per-row unbounded `create_task` fan-out.

Shutdown:

1. quiesce H1-A first: stop scheduling new sweeps;
2. signal the one worker to finish/drain its current bounded operation;
3. await worker drain before disposing persistence/storage dependencies;
4. cancellation during a transaction must roll back or be safe under the exact
   uncertain-commit replay/CAS rules on the next startup;
5. shutdown never starts F1/F2/F3 activation.

The first production slice should use bounded internal defaults and does not need
config schema/default-yaml authority. Configurability is a separate later concern
unless an independent production PRE-CLAIM finds it necessary.

## 9. Per-observation decision table

| Observation result | H1-A action | Mutation authority | Cursor action |
| --- | --- | --- | --- |
| receipt already stale / another winner moved state | NOOP | none | advance |
| exact stale receipt + safe point reconstructs | publish WAITING(RECOVERY) | existing R12-E | advance |
| exact stale receipt + allowlisted deterministic reconstruction failure | publish FAILED | future specialized stale-terminal CAS | advance |
| invocation repository/service authority missing | mark control-plane unhealthy; no terminalization | none | may advance for pass; no H0 final certification |
| unknown reconstruction reason | HOLD row / no terminalization | none | advance for pass, revisit after wrap |
| transient DB/lock/cancellation before known commit | retry by canonical uncertain-commit/CAS rules | no new authority | do not fabricate success |

## 10. External authority fences

H1-A does NOT authorize or redefine:

- F1/F2/F3 automatic SERVER_RECOVERY activation;
- authenticated Identity persistence/reconstruction;
- provider retry/fallback/deadline or inference replay;
- R6 invocation reconciliation, OUTCOME_UNKNOWN replay or terminal truth;
- Capability routing, implementation selection, #156 or sandbox authority;
- CAS generated-media lifecycle or #74 enrollment/trigger authority;
- CTX Memory/promotion/Personalization authority;
- UBQ refund/reset/recharge/accounting policy;
- Task result selection, branch ADOPT/DISCARD/AGGREGATE/RETRY/FORK authority;
- checkpoint retention/GC or R11 transcript representation rules;
- client reconnect/UI behavior;
- schema or migration changes.

## 11. Expected future production PRE-CLAIM boundary

This section is planning evidence only; it grants no production authority.

A later independent production PRE-CLAIM should attempt to keep the first H1-A
implementation within the minimum surfaces needed for:

~~~text
stale scan cursor/fairness
+ per-row evacuation coordinator
+ exact stale FAILED CAS
+ task-scoped atomic FAILED + capacity release
+ application lifecycle wiring
+ focused integration/architecture evidence
~~~

The independent auditor must determine the exact file maximum after this contract
is GREEN/GREEN. No path is pre-authorized by this planning section.

## 12. H1-A contract exit gate

H1-A zero-production contract/evidence is ready for independent production
PRE-CLAIM only when:

1. exact changed files are these two NEW paths only;
2. Architecture Linux + Windows is GREEN/GREEN on the exact candidate HEAD;
3. P1-H1-AUTHENTICATED-IDENTITY-RECONSTRUCTION-1 is CLOSED FOR H1-A by the
   explicit no-reactivation boundary, while H1-B remains CLOSED;
4. P1-H1-UNRECOVERABLE-STALE-DISPOSITION-2 has the exact FAILED receipt/CAS,
   reason allowlist and TaskBudget accounting contract above;
5. P1-H1-SCAN-CURSOR-STARVATION-3 has the explicit cursor/wrap/revisit fairness
   contract above;
6. startup/quiesce/shutdown ordering is explicit;
7. production/schema/config/migration authority remains ZERO;
8. current-main/cross-track drift is independently classified;
9. a fresh independent production PRE-CLAIM names exact production/test paths
   before any implementation begins.

Passing this contract gate does NOT close H0-20. H0-20 can become PASS only after
the later H1-A production implementation lands on canonical main, post-merge
Architecture is healthy, and executable evidence proves every stale RUNNING row
leaves RUNNING through WAITING(RECOVERY), FAILED, or a valid competing winner.
