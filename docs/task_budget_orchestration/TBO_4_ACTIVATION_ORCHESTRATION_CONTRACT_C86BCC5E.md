# TBO-4 activation eligibility + idempotent orchestration contract

Canonical workspace: Issue #381  
Canonical policy: Issue #85 v2.5  
Canonical roadmap: docs/task_budget_orchestration/TBO_ROADMAP_CONTRACT_FREEZE.md  
Canonical predecessor: Issue #333 / TBO-3 — COMPLETE / LANDED / CANONICAL / HEALTHY  
Independent contract PRE-CLAIM: #6059226191 / PASS / RELEASED  
Owner contract CLAIM: #6059231200 / ACTIVE  
Development baseline: main@c86bcc5e5f5496a77de4613cf9ef933e06391634

## 1. Authority and exact scope

This freeze is **CONTRACT + ARCHITECTURE EVIDENCE ONLY**.

Exact owned paths:

1. docs/task_budget_orchestration/TBO_4_ACTIVATION_ORCHESTRATION_CONTRACT_C86BCC5E.md
2. se/tests/architecture/test_tbo4_activation_orchestration_contract.py

No third path is authorized.

**Production/runtime/schema/migration/repository/API/client delta: ZERO.**

**Production PRE-CLAIM: HOLD / NOT RELEASED.**

No production authority is implied by a GREEN contract freeze.

Hard non-scope:
- any se/src/** or cl/src/**;
- schema/migration/repository changes;
- UBQ policy/window/reservation/debit/refund/reset/rollover mutation;
- AE WAITING/ResumeClaim/recovery/lease mutation;
- AAT timer/event scheduling or delivery;
- APR/AIC AgentInstance persistence;
- Gateway/UI projection;
- TBO-5+ implementation.

## 2. Stage purpose

The canonical roadmap defines TBO-4 as:

~~~text
activation eligibility service
+
idempotent orchestration decision

exit gate =
multi-worker / CAS / restart tests
~~~

Canonical ordering remains:

~~~text
trigger / request
  -> TBO Task eligibility
  -> UBQ activation gate
  -> AE/R8 execution admission
  -> process-local execution start
~~~

The phrase 'UBQ activation gate' in this document is intentionally **not** a resource debit.
Actual inference/tool/token/compute/cost reservation remains owned by canonical UBQ resource-specific admission seams.

## 3. Existing authority reused, not replaced

Current source already provides:
- TBO-2 horizon/review eligibility at the locked TaskBudgetService.transition_task(... ASSIGNED -> RUNNING) seam;
- R8 atomic first-root execution admission and durable Task/TaskBranch/AgentExecution lineage;
- execution-revision CAS;
- R8-F activation winner/loser semantics for execution-scoped activation;
- AgentExecutionSupervisor for process-local duplicate exclusion and owned task draining;
- R12 durable lease/recovery for stale RUNNING work.

TBO-4 MUST NOT create:
- a second execution identity system;
- a second durable execution lease;
- a process-local map as distributed authority;
- a new WAITING/resume protocol.

MultiAgentCoordinator._running_tasks[task_id] remains root/legacy process-local convenience only.
It is not TBO-4 multi-worker idempotency authority.

## 4. Canonical logical activation identity

TBO-4 freezes one logical activation request identity:

~~~text
ActivationDecisionKey =
    (task_id, activation_request_id)
~~~

activation_request_id is an opaque immutable idempotency key issued by a trusted trigger adapter.

Rules:
1. the same (task_id, activation_request_id) MUST converge on the same durable activation decision;
2. replay MUST NOT create a second AgentExecution;
3. reusing one activation_request_id for a different Task is a conflict;
4. client/session/connection/retry/fork/resume identity MUST NOT mint a fresh activation merely by changing transport identity;
5. future AAT delivery may supply its stable event/delivery identity only under TBO-5 authority;
6. the key is idempotency identity, not authorization; Task ownership and policy checks remain mandatory.

A future production implementation MUST durably bind the decision key to exactly one canonical R8/AE execution admission or to a durable denial.
The canonical ordering for **every** same-key request, before examining Task source state or invoking TBO-2 / UBQ / R8, is:
1. authenticate the trusted principal and enforce Task ownership/authorization (without minting any activation authority);
2. read the canonical durable decision for the exact ActivationDecisionKey under replay-safe transaction/CAS coordination;
3. **if a durable decision exists, return/observe that persisted ALLOW or DENY**; NEVER reevaluate Task state, horizons, UBQ owner/eligibility or R8 admission for the same key;
4. **only if no decision exists**, evaluate source-state eligibility and decide/commit the first durable outcome through the canonical Task/R8 CAS boundary; competing same-key workers must observe one durable winner rather than replacing it.

A persisted DENY is immutable for that key even if a transient UBQ_ACTIVATION_DEFERRED or UBQ_OWNER_UNRESOLVED condition later clears while the Task remains ASSIGNED. Rechecking eligibility after such a denial requires a **different trusted activation_request_id** and its own fresh Task/R8 conflict and authorization checks; it is never an automatic same-key retry escalation. A persisted ALLOW remains bound to the original execution on replay, including after Task state changes. A missing/corrupt decision receipt MUST fail closed rather than reinterpret an existing admitted execution as a fresh request.
This contract does not choose a schema path.
If current R8 receipts cannot preserve the key without new representation, schema/repository work requires a fresh production PRE-CLAIM.

## 5. Canonical decision dispositions

TBO-4 freezes these logical dispositions:

~~~text
ACTIVATION_ALLOWED
ACTIVATION_REPLAY
TASK_NOT_ACTIVATION_READY
TASK_ALREADY_ACTIVE
TASK_TERMINAL
TASK_HORIZON_EXPIRED
REVIEW_REQUIRED
DEFER_TO_AE_CONTINUATION
UBQ_ACTIVATION_DEFERRED
UBQ_OWNER_UNRESOLVED
ACTIVATION_CONFLICT
~~~

These are orchestration dispositions, not new AgentTaskStatus or AgentExecutionState values.

`ACTIVATION_REPLAY` is a response indicating that a pre-existing **durable canonical decision** was observed. It is NOT a third durable allow/deny outcome. Every replay MUST recover the underlying persisted allow-versus-denial outcome before applying execution-binding rules; an allowed replay preserves the same bound execution and a denied replay preserves zero executions.

An allow decision does not itself grant inference/tool resource quota.

## 6. Source-state matrix

The current conservative TBO-4 activation matrix applies **only after a replay-safe lookup proves no durable decision exists for the exact ActivationDecisionKey**. Existing decision lookup and trusted authorization ALWAYS precede this table, even if the Task is still ASSIGNED, WAITING or terminal. A same-key durable DENY must be returned unchanged without running any later TBO-2, UBQ or R8 gate; a same-key durable ALLOW must replay its original bound execution without starting another one.

| Durable Task state / path | TBO-4 new-Execution disposition |
|---|---|
| ASSIGNED + no existing decision for key | evaluate TBO-2 horizons, then UBQ activation gate, then R8 admission; atomically persist one ALLOW or DENY |
| CREATED | TASK_NOT_ACTIVATION_READY |
| RUNNING + an existing same-key durable decision | handled by the earlier canonical receipt replay, not by this source-state evaluation |
| RUNNING + different decision key | TASK_ALREADY_ACTIVE; no new root execution |
| WAITING/RESOURCE | DEFER_TO_AE_CONTINUATION; existing Execution resume remains AE-owned |
| any other WAITING reason | DEFER_TO_AE_CONTINUATION; no new execution |
| COMPLETED, FAILED, CANCELLED | TASK_TERMINAL; no resurrection |
| RETRY/FORK/RESUME path | existing R8/R9/R7 authority; not rewritten as TBO-4 root activation |

For a fresh, previously undecided ASSIGNED request, TBO-2 eligibility remains authoritative:
- TASK_HORIZON_EXPIRED dominates activation;
- REVIEW_REQUIRED denies activation without manufacturing Task WAITING;
- denial mutates no UBQ state and creates no AgentExecution.

task_mode = RECURRING does not by itself make a WAITING or terminal Task activation-ready.
TBO-4 does not invent recurrence scheduling/cadence or terminal resurrection.
A future trigger can request activation only when a separately canonical lifecycle rule has made the durable Task state activation-ready.

## 7. UBQ activation gate

TBO-4 MUST NOT invent a speculative resource reservation merely to make activation atomic.

The TBO-4 UBQ boundary is a **non-mutating activation gate** with semantic outcomes:

~~~text
UBQ_ACTIVATION_ELIGIBLE
UBQ_ACTIVATION_DEFERRED
UBQ_OWNER_UNRESOLVED
~~~

Required meaning:
- owner/principal resolution follows canonical UBQ server-trusted ownership rules;
- UBQ_OWNER_UNRESOLVED fails closed before AE/R8 execution admission;
- the gate creates no UBQ window;
- the gate does not roll a window;
- the gate does not debit, reserve, settle, refund, reset, or mint resource quota;
- unknown future inference/tool demand MUST NOT be guessed at activation time;
- actual resource-specific admission remains in canonical UBQ inference/tool seams;
- a prior TBO-3 RESOURCE_DEFERRED continuation is not cleared by TBO-4 probing.

If no current production UBQ primitive can provide this read-only gate, production TBO-4 requires an independently released UBQ bilateral path rather than reinterpreting an existing mutating service.

## 8. TBO decision vs R8/AE admission

TBO-4 owns eligibility/idempotency orchestration only.

R8/AE remains the authority that creates or admits the durable AgentExecution.

For an allowed root activation:

~~~text
lock/revalidate Task eligibility
  -> bind ActivationDecisionKey
  -> UBQ non-mutating activation gate
  -> R8 atomic root execution admission
  -> commit one canonical decision bound to one execution_id
  -> reserve/start through AgentExecutionSupervisor
~~~

There MUST NOT be a durable committed ACTIVATION_ALLOWED state that is unbound to a canonical execution admission and can later be interpreted as permission to create an arbitrary second execution.

Equivalent implementations may use:
- one transaction/CAS that binds the decision and R8 admission; or
- an idempotent durable receipt whose retry path can only complete/observe the same R8 admission.

Any representation choice requires a separate production PRE-CLAIM.

## 9. Multi-worker winner/loser contract

For two workers racing the same ActivationDecisionKey, **durable decision lookup/replay occurs before Task/UBQ/R8 reevaluation**; execution admission is conditional on the underlying persisted canonical allow/deny outcome, not on the response disposition. In particular, `ACTIVATION_REPLAY` of a prior allow MUST retain the original binding, and MUST NOT be treated as a denial merely because its response disposition differs from `ACTIVATION_ALLOWED`:

~~~text
all same-key races:
    exactly one canonical durable decision

if persisted canonical decision outcome == ALLOW:
    exactly one canonical execution binding
    at most one local execution start
    allowed ACTIVATION_REPLAY observes the original execution binding

if persisted canonical decision outcome == DENY:
    exactly zero execution bindings
    exactly zero local execution starts
    same-key denied races converge on the same durable denial
~~~

A denied request (including terminal, WAITING, horizon-expired, review-required, UBQ-deferred or unresolved-owner) MUST NOT create or bind an AgentExecution. Replaying that denial returns the same canonical decision without starting any work. ACTIVATION_REPLAY reports an existing canonical decision; it is not an independent execution-admission grant.

A loser:
- creates no duplicate AgentExecution;
- dispatches zero inference;
- dispatches zero tools;
- performs no winner cleanup mutation;
- returns/observes ACTIVATION_REPLAY or the canonical denial.

For different decision keys racing the same currently activation-ready Task revision:
- at most one may win the Task/R8 activation boundary;
- the loser observes TASK_ALREADY_ACTIVE or the canonical winner;
- a different key is never permission to create a sibling root execution.

Fork/delegated execution concurrency remains R8 authority and is not constrained by the root Task-level duplicate rule.

## 10. Process-local Supervisor boundary

AgentExecutionSupervisor remains process-local.

Its reservation token:
- is not activation_request_id;
- is not a durable TBO decision receipt;
- is not an R12 lease;
- cannot prove another worker is absent.

Supervisor reservation/start occurs only after the durable TBO/R8 decision has selected the canonical execution identity.

A Supervisor loser or cancellation path MUST NOT mutate the winner's durable decision.

## 11. Crash/restart matrix

TBO-4 freezes the following crash cuts:

| Crash cut | Required restart behavior |
|---|---|
| before any durable decision/admission commit | same decision key may retry normally |
| after a denial is durably recorded | replay returns the same denial; no execution |
| after decision key is bound to R8 admission but before local Supervisor start | replay observes the same execution; no second execution |
| after durable RUNNING activation but before local runtime work | R8 fail-close / R12 stale-RUNNING recovery authority applies; TBO does not create a replacement |
| after local start succeeds | normal AE/R12 lifecycle; duplicate decision replay returns canonical existing identity |
| after process death with stale RUNNING lease | R12 only; TBO-4 cannot adopt/recover by itself |

The forbidden crash state is:

~~~text
committed durable ACTIVATION_ALLOWED
+
no canonical execution binding
+
retry is allowed to mint a new execution
~~~

A production design that can enter that state fails the TBO-4 contract.

## 12. TBO-3 and AE continuation boundary

TBO-3 RESOURCE_DEFERRED and AE WAITING/RESOURCE do not grant TBO-4 authority to create a replacement execution.

For an existing WAITING execution:

~~~text
TBO-4 new-Execution activation = DEFER_TO_AE_CONTINUATION
~~~

ResumeClaim, checkpoint revision, recovery, cancellation winner and no-duplicate-resume semantics remain AE-owned.

TBO-4 may consume a future canonical 'continuation is now eligible' signal only as routing evidence.
It cannot perform the resume transition itself.

## 13. AAT / TBO-5 boundary

TBO-4 accepts a trigger request; it does not schedule one.

Automatic timer/event delivery belongs to TBO-5 / AAT handoff.

TBO-4 MUST NOT:
- register timers;
- enqueue wakeups;
- create AAT events;
- interpret clock passage as an activation request;
- grant admission merely because an event is due.

A future AAT delivery MUST pass through the same TBO-4 decision key, eligibility, UBQ gate and R8 admission rules.

## 14. Acceptance matrix

A future production implementation must prove at least:

1. same decision key, one worker, repeated call -> same durable decision; if persisted outcome ALLOW, the original canonical execution identity even on ACTIVATION_REPLAY; if persisted outcome DENY, zero execution identities;
2. same decision key, many workers -> one durable canonical decision; if persisted outcome ALLOW, exactly one execution binding and at most one local start even on ACTIVATION_REPLAY; if persisted outcome DENY, zero execution bindings and zero local starts;
3. different decision keys racing one ASSIGNED Task -> at most one root activation;
4. replay after process restart -> same durable decision; if persisted outcome ALLOW, the original bound execution identity even when response is ACTIVATION_REPLAY; if persisted outcome DENY, the same durable denial with zero execution;
5. same key replay after a durable UBQ_ACTIVATION_DEFERRED or UBQ_OWNER_UNRESOLVED denial while Task remains ASSIGNED -> unchanged denial, zero execution, no UBQ recheck/R8 admission; only a new trusted decision key may request fresh evaluation;
7. Task horizon expired -> TASK_HORIZON_EXPIRED, zero execution, zero UBQ mutation;
7. review horizon reached -> REVIEW_REQUIRED, zero execution, zero Task WAITING fabrication;
8. RUNNING Task -> no second root execution;
9. WAITING/RESOURCE -> DEFER_TO_AE_CONTINUATION, no new execution;
10. terminal Task -> no resurrection;
11. unresolved UBQ owner -> fail closed before R8 admission;
12. UBQ activation gate -> zero window creation/rollover/debit/reservation/refund/reset;
13. Supervisor collision -> no durable duplicate and no loser cleanup of winner;
14. crash after R8 admission/before local start -> same execution on replay; R12 remains recovery authority;
15. fork/retry/resume authority remains unchanged;
16. future AAT duplicate delivery reuses the same decision identity under TBO-5, never bypassing TBO-4.

## 15. Exit gate for this contract parent

This zero-production parent is eligible for FINAL only when:
- exact scope remains 2 NEW / 2;
- focused architecture evidence is GREEN;
- exact-head Linux + Windows Architecture is GREEN/GREEN;
- independent contract FINAL finds no blocking P0/P1/P2;
- current-main drift is classified;
- unresolved review threads are zero.

Only after a GREEN contract parent may a fresh independent audit consider a separate TBO-4 production PRE-CLAIM with exact paths.

No production authority is implied by this contract.
