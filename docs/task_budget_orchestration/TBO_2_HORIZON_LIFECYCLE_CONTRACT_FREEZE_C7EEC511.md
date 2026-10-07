# TBO-2 — Task horizon / review-horizon lifecycle contract freeze

**Canonical workspace:** Issue #321  
**Repository policy:** Issue #85 v2.5  
**Independent zero-production PRE-CLAIM:** #6031354064 / PASS / RELEASED  
**Owner zero-production CLAIM:** #6031356478  
**Development baseline:** `main@c7eec511a07cf08019ba602f5d271edf8d1f98f5`  
**Baseline Architecture:** #2249 / 37573509767 / GREEN-GREEN  
**Class:** CONTRACT + ARCHITECTURE EVIDENCE ONLY  
**Production/runtime/schema/migration/API/client delta:** ZERO  
**Production PRE-CLAIM:** HOLD / NOT RELEASED

## 1. Purpose

TBO-2 freezes the lifecycle semantics required before any production implementation
of Task-horizon or review-horizon enforcement.

The roadmap deliverable remains:

```text
TBO-2
= Task horizon/review-horizon enforcement
+ legal Task lifecycle transitions
```

This document implements none of that behavior. It defines the bounded contract
that a later production PRE-CLAIM must consume.

Canonical predecessor state:

```text
TBO-0 / #150 = COMPLETE / LANDED / CANONICAL / HEALTHY
TBO-1 / #315 = COMPLETE / LANDED / CANONICAL / HEALTHY
TBO-1 merge = c7eec511a07cf08019ba602f5d271edf8d1f98f5
canonical Alembic head = 28a_tbo1_task_policy_representation
```

## 2. Current durable representation

TBO-1 made these fields canonical representation:

```text
task_mode = FINITE | RECURRING
task_horizon_at = optional finite non-negative absolute UNIX epoch seconds
review_horizon_at = optional finite non-negative absolute UNIX epoch seconds
```

Current Task status vocabulary remains:

```text
CREATED
ASSIGNED
RUNNING
WAITING
COMPLETED
FAILED
CANCELLED
```

No new Task status is introduced by this freeze.

## 3. Activation-attempt definition

For TBO-2, an **activation attempt** means an operation that would cause Agent
work for an existing durable Task to become RUNNING after previously not being
RUNNING.

Two existing families matter:

1. **new-Execution activation** from a durable Task such as `ASSIGNED -> RUNNING`;
2. **existing-Execution resume activation** such as AE-owned
   `WAITING -> RUNNING`.

TBO owns Task eligibility. AE owns legal Execution state transitions,
ResumeClaim, recovery, lease/fencing, safe-points, reconciliation, and the
actual `WAITING -> RUNNING` transition.

Therefore this freeze may define the Task eligibility result for either
activation family, but a later implementation MUST NOT edit AE resume/recovery
surfaces until a fresh bilateral AE/TBO production release explicitly permits
that path.

## 4. Clock contract

All TBO-2 horizon decisions use wall-clock UNIX epoch seconds.

Production implementation must use one injected/fakeable clock and sample
`now` exactly once per durable activation decision.

For a race-safe new-Execution activation, the authoritative sample is taken
**after durable Task mutation authority has been acquired** and **before the
transition that makes the Task/work RUNNING is committed**.

A coordinator-only precheck is insufficient.

The equality boundary is closed:

```text
due := now >= horizon_at
```

Thus exactly-at the horizon is already due.

## 5. Task-horizon semantics

`task_horizon_at` is the canonical durable absolute Task lifecycle fence
introduced by TBO-1.

Rules:

1. NULL means no durable TBO Task-horizon activation fence.
2. When `now < task_horizon_at`, this fence alone permits activation to
   continue to the next eligibility/admission authority.
3. When `now >= task_horizon_at`, activation fails closed with the
   deterministic internal disposition:

```text
TASK_HORIZON_EXPIRED
```

4. Horizon expiry blocks activation; it does not by itself:
   - cancel or terminalize an already RUNNING Execution;
   - create Task WAITING;
   - create or mutate an AE wait_reason;
   - close, mint, reset, refund, roll, or settle UBQ quota/history;
   - create a retry, fork, replacement Execution, or AAT event.
5. A terminal Task remains terminal and is never resurrected because of a
   horizon decision.

The roadmap acceptance invariant remains:

> Task horizon expiry blocks new Task activation but does not rewrite UBQ history.

## 6. Review-horizon semantics

`review_horizon_at` is an **activation-review fence**.

It is not an AE timer and is not a Task/Execution state-transition instruction.

Rules:

1. NULL means no review-horizon activation fence.
2. When `now < review_horizon_at`, this fence alone permits activation to
   continue.
3. When `now >= review_horizon_at`, activation fails closed with:

```text
REVIEW_REQUIRED
```

4. `REVIEW_REQUIRED` is an internal TBO eligibility disposition only.
   TBO-2 does not add a new AgentTaskStatus, AgentExecutionState, wait_reason,
   HTTP status, Gateway payload, or UI state.
5. Reaching the review horizon does not change Task status or revision and
   does not mutate output/error/wait_reasons.
6. An already RUNNING Execution is not cancelled, failed, timed out, or moved
   to WAITING merely because review time is reached.
7. TBO-2 does not create review approval, review completion, horizon renewal,
   or Task-policy mutation authority.
8. Once the review fence is due, later activation remains ineligible until a
   separately released authority lawfully changes the durable policy or the
   Task becomes terminal through an existing legal path.

This avoids manufacturing an AE-resumable WAITING state without an existing
resumable Execution.

## 7. Deterministic multiple-fence ordering

If both durable fences are due on the same activation decision, disposition is
deterministic:

```text
TASK_HORIZON_EXPIRED
    dominates
REVIEW_REQUIRED
```

Rationale: Task horizon is the outer lifecycle deadline. Review is meaningful
only while the Task remains inside its Task lifecycle horizon.

A denial result is side-effect free with respect to Task persistence, AE state,
and UBQ accounting.

## 8. Canonical Task lifecycle transition boundary

TBO-2 introduces **no new Task status and no new legal transition by this
contract freeze**.

Existing boundaries remain:

```text
ASSIGNED -> RUNNING
    existing durable new-Execution activation transition

RUNNING -> WAITING
    only as a Task projection of a legal AE WAITING result

RUNNING -> COMPLETED | FAILED | CANCELLED
    existing terminalization/cancellation authority

WAITING -> RUNNING
    AE-owned existing-Execution resume authority
    NOT redefined by TBO-2

COMPLETED | FAILED | CANCELLED
    terminal / no resurrection
```

Direct Task start from WAITING remains prohibited by the current coordinator;
the existing Execution must resume through canonical AE authority.

TBO-2 MUST NOT use Task `WAITING` as a generic "horizon blocked" state.
Horizon/review denial preserves the current durable Task state.

## 9. Atomic new-Execution enforcement seam

The current durable `ASSIGNED -> RUNNING` transition is protected by the
locked/CAS Task transition path in `TaskBudgetService.transition_task(...)`.

A later production implementation of new-Execution horizon enforcement must
place the authoritative eligibility decision at that durable mutation seam, or
an independently audited equivalent with the same race guarantees.

Required ordering:

```text
load/lock durable Task
  -> reject already-terminal / illegal source
  -> sample injected wall clock exactly once
  -> evaluate task_horizon_at
  -> evaluate review_horizon_at
  -> if denied: no Task mutation, no UBQ reservation, no Execution creation
  -> if eligible: continue existing locked/CAS transition
```

Sampling before the Task lock and then using that stale result as authority is
not sufficient.

This freeze does not authorize editing `task_budget.py`; it only identifies
the required future seam. Because that file is a shared historical
AE/TaskBudget/UBQ compatibility surface, any future edit requires a fresh
production PRE-CLAIM and applicable bilateral/no-impact review.

## 10. Existing-Execution resume boundary

A WAITING Execution may only become RUNNING through AE-owned resume authority.

If a future TBO-2 production slice enforces Task horizons on resume activation,
the check must be integrated into the existing durable ResumeClaim/state-machine
admission boundary so that:

- one activation authority wins;
- Task eligibility cannot race after an AE resume claim;
- TBO does not invent a second ResumeClaim, lease, revision, or safe-point;
- unknown external outcomes still reconcile through AE authority before replay.

No AE runtime/resume path is authorized by this zero-production claim.

## 11. Durable task_horizon_at vs compatibility task_timeout_seconds

The repository currently has two differently represented constraints:

```text
AgentTask.task_horizon_at
    durable absolute UNIX epoch Task lifecycle fence

AgentExecutionLimits.task_timeout_seconds
    existing compatibility timeout surface consumed by current runtime paths
```

TBO-2 freezes coexistence without translation:

1. TBO-2 activation eligibility reads durable `task_horizon_at`.
2. TBO-2 does not derive `task_horizon_at` from
   `task_timeout_seconds`.
3. TBO-2 does not derive `task_timeout_seconds` from
   `task_horizon_at`.
4. TBO-2 does not rename, remove, serialize differently, write back, or mutate
   `task_timeout_seconds`.
5. Existing consumers of `task_timeout_seconds` continue unchanged under
   their current timeout compatibility authority.
6. Both constraints may independently apply to the same logical Task/work.
   TBO-2 introduces no implicit numeric precedence conversion between absolute
   epoch policy and relative/runtime compatibility timeout.
7. Any future terminology cleanup, alias removal, conversion, or source-of-truth
   migration requires a separate AE/TBO/timeout compatibility release.

This contract therefore adds an activation fence without claiming ownership of
legacy timeout runtime behavior.

## 12. UBQ / AAT / AE ordering

The existing orchestration authority order remains:

```text
trigger / request
  -> TBO Task eligibility
  -> UBQ resource admission
  -> AE execution admission / activation
```

A TBO horizon denial occurs before new UBQ resource reservation for that
activation attempt.

TBO-2 cannot mint, reset, refund, extend, or roll a UBQ usage window.

AAT may deliver a trigger, but a due AAT event does not override
`TASK_HORIZON_EXPIRED` or `REVIEW_REQUIRED`.

AE remains authoritative for Execution state, WAITING/resume/recovery, external
side-effect reconciliation, and lease/fencing.

## 13. Fake-clock / state-machine acceptance matrix

A later TBO-2 production candidate must provide fake-clock evidence for at
least:

| Case | Expected TBO eligibility |
|---|---|
| both horizons NULL | no TBO-2 horizon denial |
| task horizon just before | eligible on task horizon |
| task horizon exactly at | `TASK_HORIZON_EXPIRED` |
| task horizon just after | `TASK_HORIZON_EXPIRED` |
| review horizon just before | eligible on review horizon |
| review horizon exactly at | `REVIEW_REQUIRED` |
| review horizon just after | `REVIEW_REQUIRED` |
| both due | `TASK_HORIZON_EXPIRED` |
| ASSIGNED activation denied | Task/status/revision unchanged |
| already RUNNING crosses either horizon | no forced TBO-2 transition |
| terminal Task | remains terminal |
| horizon denial | no UBQ mint/reset/refund/reservation |
| review denial | no AE WAITING/resume mutation |
| concurrent activation near boundary | one race-safe durable decision seam |

For existing-Execution resume, production evidence is mandatory only after a
separate AE/TBO bilateral release authorizes the relevant resume path.

## 14. Explicit non-scope

This freeze grants no authority for:

- production/runtime/schema/migration/repository/API/client edits;
- new Task or Execution states;
- generic Task WAITING for horizon/review denial;
- review approval or policy mutation endpoints;
- recurrence scheduling/cadence;
- UBQ resource-exhaustion handoff (TBO-3);
- activation eligibility service / multi-worker orchestration decision (TBO-4);
- AAT timer/event handoff implementation (TBO-5);
- Gateway/UI projection (TBO-6);
- RETRY/FORK/RESUME/R12/AAT/AIC full integration matrix (TBO-7);
- AE lease/recovery/state-machine redesign;
- timeout terminology cleanup or compatibility-field deletion.

## 15. Exact owned scope

This zero-production claim owns exactly two NEW files:

1. `docs/task_budget_orchestration/TBO_2_HORIZON_LIFECYCLE_CONTRACT_FREEZE_C7EEC511.md`
2. `se/tests/architecture/test_tbo2_horizon_lifecycle_contract_freeze.py`

No third path is authorized.

## 16. Contract-freeze exit gate

This zero-production slice reaches FINAL GREEN only when:

1. exact changed scope remains two NEW files;
2. production/runtime/schema/migration/API/client delta remains ZERO;
3. the four Issue #321 P1 findings are explicitly resolved by this contract;
4. architecture evidence binds current source and authority boundaries without
   implementing production behavior;
5. exact-head Linux + Windows Architecture is GREEN;
6. independent contract FINAL finds no blocking P0/P1/P2;
7. current-main drift is classified under Policy #85 v2.5.

Only after that may a fresh independent audit consider releasing a **separate
production PRE-CLAIM** with exact runtime paths and any required AE/UBQ bilateral
authority.

No production authority is implied by a GREEN contract freeze.
