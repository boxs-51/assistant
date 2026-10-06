# TBO-0 — Task Orchestration exact-head contract re-freeze

**Canonical workspace:** Issue #150  
**Policy:** Issue #85 v2.5  
**Opening-gate audit:** #6022242619 / PASS  
**Owner opening CLAIM:** #6022371229  
**Development baseline:** `main@1cd05ad3e5c390dfcc1612a49ad34474a2c894c8`  
**Current-main Architecture at opening:** #2198 / GREEN-GREEN  
**Class:** CONTRACT + ARCHITECTURE EVIDENCE ONLY  
**Production/runtime/schema/migration/API/client delta:** ZERO  
**Production CLAIM:** NONE

## 1. Purpose

TBO-0 opens the reserved TBO namespace as **Task Orchestration** only.

This stage freezes the exact-head boundary between:

```text
Task lifecycle/orchestration policy      -> TBO
renewable user resource quota           -> UBQ
Execution state/resume/recovery          -> AE
timer/event delivery                     -> AAT
provider retry/fallback/deadline         -> AE-R10 / timeout contract
```

TBO-0 does not implement any of those future capabilities. It records the
current repository facts and the authority boundary that later TBO stages must
consume.

## 2. Exact-head Task inventory

At `main@1cd05ad3e5c390dfcc1612a49ad34474a2c894c8`, the canonical Task-facing
domain surface still exposes:

```text
AgentTaskStatus:
  CREATED
  ASSIGNED
  RUNNING
  WAITING
  COMPLETED
  FAILED
  CANCELLED
```

`WAITING_FOR_CONNECTION` remains a source-compatible alias normalized to the
canonical `WAITING` value.

The current `AgentTask` / durable `AgentTaskRecord` representation carries,
among other existing fields:

```text
task/session/creator/assigned-agent identity
revision
parent_task_id
connection_id / client_id
status
wait_reasons
input / output / error
created_at / updated_at
```

TBO-0 adds no new Task representation and performs no migration.

The current source does not yet need to be reinterpreted as a durable
finite/recurring Task policy object. Future representation belongs to TBO-1 and
requires its own released authority.

## 3. Task status is not Execution-state ownership transfer

TBO may coordinate Task lifecycle, but legal Execution state semantics remain
owned by AE.

Current execution state continues to use the canonical AE state machine,
including `WAITING` plus an explicit `AgentExecutionWaitReason` such as
`CONNECTION`, `HUMAN_APPROVAL`, `DEPENDENCY`, `RESOURCE`,
`EXPLICIT_PAUSE`, `RECOVERY`, or `RETRY_BACKOFF`.

TBO therefore MUST NOT:

- redefine AE `WAITING -> RUNNING` resume authority;
- invent a second recovery or execution lease authority;
- replay an unknown external side effect;
- terminalize/resurrect an Execution merely from Task orchestration policy;
- bypass AE revision/CAS, ResumeClaim, reconciliation, or recovery contracts.

A Task-level `WAITING` projection may describe orchestration state, but it does
not manufacture Execution activation authority.

## 4. Task horizon and timeout boundary

The current timeout contract classifies:

```text
agent_limits.task_timeout_seconds
    = optional Task wall-clock horizon
    = Task lifecycle deadline at the TBO/AE boundary

remaining_active_budget_seconds
    = durable AE compatibility/execution-time state

ProviderCallBudget
    = provider logical-call deadline/retry compatibility
```

TBO-0 does not rename or remove any of these fields.

`remaining_active_budget_seconds` remains an AE compatibility surface until a
separately released terminology/migration stage changes all durable consumers.

`ProviderCallBudget` remains AE-R10/provider deadline and retry authority.
TBO-0 does not absorb UBQ-5F/T-5 terminology cleanup.

The canonical lifetime rule is:

```text
Task lifetime != synchronous response lifetime
```

A response may close while a durable Task later resumes through a legal
continuation path.

## 5. Renewable resource quota remains UBQ-owned

TBO-0 consumes the established UBQ ownership split.

A Task does not own renewable user quota.

Opening or creating another:

```text
client
connection
session
Task
branch
Execution
retry
fork
resume
```

MUST NOT mint or reset an independent user quota window.

Renewable token/compute/inference/tool/cost accounting remains UBQ authority.
Historical TaskBudget rows, counters, reservations, migrations, field names and
completion evidence remain compatibility/history unless a separately released
UBQ stage changes them.

TBO may surface resource denial/exhaustion into Task continuation policy, but it
may not refill, reset, roll over, or manufacture UBQ authority.

## 6. Frozen future TBO policy boundary

Future TBO stages may define Task-level orchestration policy such as:

```text
task_mode = finite | recurring
task_horizon
review_horizon
activation eligibility/cadence references
completion/renewal authorization state
Task-level structural guard references
```

These are lifecycle/orchestration semantics, not renewable resource quota.

TBO-0 freezes this ordering for any future activation:

```text
AAT event / user request / other legal trigger
        ->
TBO Task eligibility decision
        ->
UBQ resource admission
        ->
AE execution admission / activation
```

A due timer/event is only a trigger request. AAT does not grant Task
eligibility, UBQ quota, or AE execution authority.

Likewise, TBO eligibility by itself does not prove UBQ admission, and UBQ
admission by itself does not bypass AE execution-state gates.

## 7. Horizon invariants

```text
TBO0-I01  Task horizon is lifecycle policy, not token/compute/tool/inference/cost quota.
TBO0-I02  UBQ usage-window rollover does not silently extend a Task horizon.
TBO0-I03  Extending a Task horizon does not mint a new UBQ window.
TBO0-I04  Response expiry/close does not by itself cancel a durable Task.
TBO0-I05  Resource exhaustion may cause continuation/wait policy but cannot mint/reset UBQ.
TBO0-I06  Retry/fork/resume preserve existing identity/lineage authority and create no quota.
TBO0-I07  Execution WAITING/resume/recovery semantics remain AE-owned.
TBO0-I08  AAT delivery requests activation; it does not grant admission.
TBO0-I09  Unknown external outcomes still reconcile through AE-R6 before replay.
TBO0-I10  TBO-0 grants no production, schema, migration, scheduler, API, or client authority.
```

## 8. Exact migration/disposition boundary

```text
Task lifecycle/horizon concepts       KEEP / REHOME IN TBO
finite/recurring Task policy          FUTURE TBO-1+
activation eligibility                FUTURE TBO-2/TBO-4+
AAT wakeup handoff                    KEEP / FUTURE TBO-5
UBQ exhaustion continuation handoff   FUTURE TBO-3
periodic Task resource allowance      SUPERSEDED BY UBQ USER WINDOW
Task-owned renewable resource epoch   SUPERSEDED BY UBQ USER WINDOW
Task resource quota minting           PROHIBITED
AE WAITING/recovery semantics          KEEP / AE-OWNED
Provider retry/fallback/deadline       KEEP / AE-R10-OWNED
timeout terminology cleanup           EXCLUDED / UBQ-5F/T-5
```

Historical AE-R4/R5/R8/R9 evidence is not rewritten by TBO-0.

## 9. Cross-track boundary at CLAIM

- AE-R12 / Issue #107 is COMPLETE/CLOSED/CANONICAL. TBO-0 consumes its
  lifecycle/recovery boundary and transfers none of that authority.
- UBQ owns renewable user quota and current compatibility disposition.
- AAT remains generic timer/event delivery authority.
- CTX, CAS and Capability retain their own lifecycle/authority.
- PR #277 currently owns `docs/ROADMAP_NAMESPACE_REGISTRY.md`.

Therefore:

```text
docs/ROADMAP_NAMESPACE_REGISTRY.md = EXCLUDED FROM TBO-0
UBQ-5F/T-5 terminology cleanup     = EXCLUDED FROM TBO-0
TBO-1+ implementation              = EXCLUDED FROM TBO-0
```

## 10. Exact owned scope

TBO-0 owns exactly two NEW files:

1. `docs/task_budget_orchestration/TBO_0_TASK_ORCHESTRATION_CONTRACT_FREEZE_1CD05AD3.md`
2. `se/tests/architecture/test_tbo0_task_orchestration_contract_freeze.py`

No third path is authorized.

TBO-0 must not modify `se/src/**`, `cl/src/**`, migrations, SQL schema,
configuration, runtime/API paths, scheduler code, provider code, or the roadmap
namespace registry.

## 11. Exit gate

TBO-0 reaches FINAL GREEN only when:

1. the exact two-file zero-production scope is preserved;
2. the document and architecture evidence bind the current Task/timeout/UBQ/AE
   boundaries above;
3. fresh exact-head Architecture Linux + Windows is GREEN;
4. independent FINAL finds no blocking TBO-0 P0/P1;
5. current-main drift is classified and no material ownership drift invalidates
   this freeze;
6. Policy #85 integration governance is satisfied.

Only after TBO-0 is canonical may a fresh independent audit consider releasing
TBO-1 representation authority.

No TBO-1 production/schema/migration authority is implied by this document.
