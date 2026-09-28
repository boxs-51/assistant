# TBO — Task Orchestration roadmap

**Namespace:** `TBO-*`  
**Baseline for this superseding planning document:** `main@206334044308a532f70384a1eeb4860cc9439f86` (2026-09-28)  
**State:** `RESERVED / NOT OPEN`  
**Authority now:** contract and coordination only; no production, schema, migration, scheduler, API or user-budget implementation claim.  
**Superseding dependency:** `docs/user_budget_quota/USER_RESOURCE_BUDGET_TIMEOUT_REFREEZE.md`

## 1. Superseding scope decision

TBO no longer means **Task Budget & Orchestration** for future work.

Future meaning:

```text
TBO = Task Orchestration
UBQ = User Budget & Quota
```

Historical AE-R4/R5/R8/R9 TaskBudget evidence remains unchanged. This document does not retroactively rename historical schema/classes or migrations.

TBO MUST NOT mint renewable user resource quota. It consumes UBQ admission when orchestration activates work.

## 2. TBO objective

TBO gives a logical Task a durable lifecycle across many Agent executions and periods of inactivity without requiring one HTTP/SSE response or one RUNNING Execution to remain alive.

TBO may define:

- finite vs recurring Task policy;
- Task horizon/review horizon;
- activation eligibility and lifecycle coordination;
- durable WAITING reasons owned by existing AE contracts;
- handoff to AAT timers/events;
- user-visible Task continuation state;
- Task-level structural policy that is not renewable resource quota.

TBO does not own:

- user token/compute/tool/inference/cost quota;
- per-capability tool allowance;
- user budget reset windows;
- provider retry/fallback;
- capability registration;
- CTX/CAS lifecycle;
- AE execution lease/recovery.

## 3. Canonical separation

```text
UserResourceBudget (UBQ)
        |
        v
Task orchestration admission (TBO)
        |
        v
Agent execution (AE)
        |
        +--> provider inference
        +--> capability/tool invocation
```

A Task may outlive one response and one Execution.

A new Task, retry, fork, resume, client, session or connection does not create fresh user quota.

## 4. Task policy vs user resource budget

TBO may own Task policy such as:

```text
task_mode = finite | recurring
task_horizon / review_horizon
activation cadence/eligibility references
completion/renewal authorization state
Task-level execution/branch/delegation guard references
```

UBQ separately owns renewable user resource dimensions such as:

```text
compute units
logical inference calls
input/output/total tokens
logical tool calls total
logical tool calls by capability_id
optional cost
```

TBO may surface UBQ denial/exhaustion to the Task state machine but may not reset or refill UBQ itself.

## 5. Task horizon is not resource budget

A Task horizon is a lifecycle/deadline policy.

It may determine whether new activations are permitted, but it is not:

- token quota;
- compute quota;
- tool quota;
- inference quota;
- response timeout;
- provider timeout.

Renewal of an UBQ usage window MUST NOT silently extend a Task horizon. Extending a Task horizon MUST NOT mint a new UBQ window.

## 6. WAITING and activation

A durable Task may wait for:

- connection;
- human approval;
- dependency;
- resource eligibility;
- explicit pause;
- recovery;
- retry backoff;
- future event/timer through AAT.

TBO coordinates lifecycle only through legal AE state-machine transitions. `WAITING` remains AE-owned execution state semantics.

When a future activation is due:

```text
AAT/event/user request
  -> TBO Task eligibility
  -> UBQ resource admission
  -> AE execution admission/activation
```

No layer may infer budget authority merely because another layer says the Task is due.

## 7. Timeout boundary

TBO does not define provider/tool/response timeout taxonomy.

Canonical timeout contract:

```text
docs/agent_timeout_contract.md
```

Task lifetime may exceed synchronous response lifetime.

```text
Task lifetime != Response lifetime
```

A Task may checkpoint/WAIT and later continue after the original response closes.

## 8. Existing authority relationships

| Existing authority | TBO relationship after re-freeze |
|---|---|
| UBQ | user resource quota/reset/reservation authority; TBO consumes admission |
| AE-R4/R5 | historical active-time and TaskBudget implementation evidence; future terminology/migration coordinated through UBQ |
| AE-R6/R7 | invocation reconciliation, checkpoint, ResumeClaim and legal WAITING transitions |
| AE-R8/R9 | Task/Branch/Execution identity, fork/retry and aggregation |
| AE-R10 | provider retry/fallback/deadline |
| AE-R11/R12 | persistence, lease/fencing/recovery |
| AAT | generic timer/event delivery; requests TBO/UBQ/AE activation |
| AIC | Agent communication; cannot bypass TBO/UBQ/AE admission |
| CTX/CAS/Capability | retain their own authority; TBO consumes their contracts only |

Issue #85 v2.5 controls CLAIM, stable baseline, independent audit and Integration Wave governance.

## 9. Revised reserved stages

| Stage | Deliverable | Gate |
|---|---|---|
| `TBO-0` | exact-head Task lifecycle/orchestration audit; remove old budget ownership assumptions; freeze Task horizon/mode/activation contract | docs/tests-only independent review |
| `TBO-1` | durable finite/recurring Task policy representation | migration/API/permission tests |
| `TBO-2` | Task horizon/review-horizon enforcement and legal lifecycle transitions | fake-clock/state-machine tests |
| `TBO-3` | resource-exhaustion handoff from UBQ into Task continuation policy | UBQ compatibility + no-mint tests |
| `TBO-4` | activation eligibility service and idempotent orchestration decision | multi-worker/CAS/restart tests |
| `TBO-5` | AAT timer/event handoff | duplicate/replay/cancellation tests |
| `TBO-6` | Gateway/UI Task continuation projection | reconnect/stream/API tests |
| `TBO-7` | RETRY/FORK/RESUME/R12/AAT/AIC integration matrix | multi-worker fault matrix |
| `TBO-8` | exit audit, operations, rollout/rollback | full CI + independent audit |

Later production stages do not gain authority merely by appearing in this roadmap.

## 10. Required acceptance scenarios

1. One user may have several Tasks but all consume one applicable UBQ window.
2. A Task can WAIT without keeping a synchronous response alive.
3. Task horizon expiry blocks new Task activation but does not rewrite UBQ history.
4. UBQ window rollover does not extend Task horizon.
5. TBO resource waiting never mints or resets UBQ quota.
6. AAT due delivery asks TBO, UBQ and AE rather than assuming admission.
7. R12 recovery preserves Task lifecycle and UBQ accounting.
8. Retry/fork/resume preserve Task identity/lineage without creating resource authority.
9. Unknown external tool outcome is reconciled through AE-R6 before replay.
10. Multiple workers converge on one Task activation decision where required.

## 11. Migration from the prior TBO draft

The previous TBO draft proposed:

- periodic Task allowance;
- Task-owned renewable budget epochs;
- TaskBudget cumulative ceilings as the renewal base.

Those future-planning semantics are superseded.

Migration classification:

```text
Task lifecycle/horizon concepts       KEEP / REHOME IN TBO
AAT wakeup handoff                    KEEP / REHOME IN TBO
timeout feedback concept              KEEP / OWNED BY timeout contract
periodic Task resource allowance      SUPERSEDED BY UBQ USER WINDOW
Task-owned renewable resource epoch   SUPERSEDED BY UBQ USER WINDOW
Task resource quota minting           PROHIBITED
TaskBudget historical evidence        KEEP
```

No historical completion file or SQL migration is deleted by this change.

## 12. Opening gate

`TBO-*` remains `RESERVED / NOT OPEN`.

Before TBO-0 opens:

1. UBQ-0 contract must be independently reviewed or its unresolved conflicts explicitly recorded;
2. current-main AE-R12/TBO shared ownership must be audited;
3. exact Task state-machine and timeout boundaries must be refreshed;
4. a dedicated TBO issue/owner must record exact CLAIM scope;
5. no production code may be changed under a docs-only claim.

## 13. Final invariant

```text
TBO orchestrates Tasks.
UBQ owns renewable user resource quota.
AE executes and recovers work.
AAT delivers timers/events.
Timeout contracts bound waiting and response lifetimes.
No Task/client/session/execution mints user quota.
```
