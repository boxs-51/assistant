# TBO — Task Budget & Orchestration roadmap

**Namespace:** `TBO-*`
**Baseline for this planning document:** `main@a524ba870aec3ac73be7311d068d43fc1c963cda` (2026-09-27)
**State:** `RESERVED / NOT OPEN`
**Opening date:** not assigned
**Authority now:** contract and coordination only; no production, schema, migration, scheduler, or API implementation claim.

## 1. Objective and fixed decisions

TBO gives a logical Task a durable, bounded way to continue through many Agent executions and periods of inactivity. The Agent can propose a task horizon and allocate time to individual operations. An authorized policy can replenish a periodic allowance without resetting cumulative Task limits. AAT owns future event subscriptions, Agent-owned tools, and generic wakeup scheduling; TBO supplies only budget eligibility and renewal decisions to that scheduler.

The following decisions are frozen for this roadmap:

1. `Task` is the logical objective; `Execution` is one runtime activation. The Task can outlive an Execution. A chat turn that participates in TBO needs a durable Task identity before it can use shared TaskBudget authority.
2. Four clocks/limits are distinct: transport connection idle timeout, per-operation timeout, active Execution allowance, and Task horizon. A periodic allowance is an additional quota, not a replacement for these clocks.
3. Existing TaskBudget cumulative counters and hard ceilings span retries, forks, child work where policy says so, and restarts. Periodic allowances use a separate durable ledger/epoch and never zero cumulative counters.
4. When the user does not set a Task horizon, the Agent proposes one within a server-owned maximum. The system grants a bounded proposal before long-running work. An initial bounded bootstrap inference may be used to obtain that proposal. The Agent cannot grant itself a larger user/system ceiling.
5. A finite Task has a completion horizon. A recurring Task has a bounded review/authorization horizon and per-activation allowance; it need not keep an Execution RUNNING while awaiting its next trigger. Renewal of periodic allowance does not silently extend either horizon.
6. Only a policy explicitly granting periodic replenishment may replenish it automatically. The Agent may adjust future operation slices inside remaining Task authority. An exhausted Execution is never silently refilled; a later activation needs normal admission. Exhaustion of cumulative user/system limits requires an authorized increase or terminal disposition.
7. An expired operation/allowance and an unknown external side effect are different facts. Timeout feedback must not imply a tool did nothing; R6 invocation reconciliation decides whether replay is safe.
8. Budget renewal is a server-side durable decision guarded by revision/CAS, ownership fences, idempotency keys, and policy. AAT may request activation when the eligibility time arrives, but only TBO can grant a new budget epoch. Agent calls request changes; they do not directly mint authority.
9. Public UI delivery reuses the agreed response and tool-call event surfaces. Budget state and timeout scope are fields of those responses; TBO does not add a broad lifecycle event stream by implication.

## 2. Baseline and gap

At the planning baseline, `AgentExecutionLimits` has active, iteration, inference, tool, and optional task timeout fields. Chat startup places the proposed Task deadline in Execution metadata. `agent.budget.configure` can set the first deadline and adjust operation allocations for chat executions without a `task_id`. `TaskBudget` has durable cumulative execution/tool/inference/token/cost counters but no periodic epoch or Task horizon. RETRY creates a new Execution budget. Execution `WAITING` has reasons, but no released budget-window contract.

Consequently, a metadata-only deadline cannot govern all retries, forks, resumes, workers, and recurring wakeups. This roadmap adds Task-level authority without redefining AE-R4 active time, AE-R5 TaskBudget counters, or AE-R9 retry semantics.

## 3. Budget model

| Layer | Authority and accounting | Exhaustion disposition |
|---|---|---|
| Transport idle/response wait | Gateway/client connection; does not grant execution time | Disconnect or reconnect transport; Task state is decided separately |
| Operation slice | One inference, tool call, or iteration; Agent may request a different next slice within higher bounds | Structured timeout feedback with `scope`, `remaining`, and safe retry guidance |
| Active Execution allowance | Durable active time consumed while RUNNING; WAITING time follows the AE-R4 rule | Park or end this Execution according to resumability; a fresh Execution requires ordinary admission |
| Periodic Task allowance | Durable epoch with start/end, granted quota, used/reserved quota, renewal rule, and next eligibility | `WAITING` for renewal only if policy allows; otherwise wait for authority or finish |
| Task horizon and cumulative ceilings | User/system authorization, with bounded Agent proposal when omitted; existing TaskBudget totals remain monotone | No automatic top-up; request authorized extension or make a truthful terminal result |

Quota dimensions are explicit: active compute time, inference calls, tool calls, tokens, and cost. A policy may enable renewal for some dimensions and not others. A window can replenish only up to the remaining cumulative ceiling. Parallel branches reserve from the same Task authority; a periodic window cannot grant more than its remaining quota through concurrent admissions.

The effective upper bound for a dispatch is the minimum of its operation slice, remaining active Execution allowance, remaining period allowance where applicable, Task horizon, and any inherited parent/subtask cap. Provider retry stays inside the same logical inference call and its AE-R10 deadline.

## 4. Task modes and state transitions

### Finite Task

- User deadline wins when supplied. Otherwise the Agent proposes `task_horizon` within server policy; the proposal is stored once with its source and policy version.
- The Task may enter WAITING for an event, connection, user action, or a permitted periodic refill. Waiting does not consume active Execution time, but the finite Task horizon continues as wall time unless the user explicitly selected a different policy.
- At the horizon, block new dispatch/admission and settle in-flight work safely. Return a partial result and reason when possible. An authorized extension is a new revisioned policy change, not a silent reset.

### Recurring Task

- The user or server authorizes recurrence and an outer review/expiry horizon. An Agent may propose intervals and a narrower horizon; it cannot turn a finite Task into indefinite recurrence by itself.
- An AAT-owned trigger may request activation. TBO admits it with an allowance for that occurrence or period. Completion of one occurrence does not close the recurring Task. Idle periods hold no RUNNING Execution.
- At review expiry or cumulative exhaustion, stop future triggers and request renewal or close according to the policy. No missed trigger creates unlimited catch-up work.

### Exhaustion decision table

| Condition | Agent-visible result | Durable action |
|---|---|---|
| Operation timeout, Task authority remains | `OPERATION_TIMEOUT`, `scope`, `remaining`, invocation outcome | Feed result to Agent; it may reallocate and continue after reconciliation |
| Execution allowance exhausted | `EXECUTION_BUDGET_EXHAUSTED` | Preserve checkpoint and use legal RESUME/RETRY admission; never refill the same exhausted Execution implicitly |
| Period allowance exhausted, renewable | `WINDOW_EXHAUSTED`, `next_eligible_at` | Durable `WAITING` with a budget wait reason and eligibility record; AAT may schedule the wakeup; release active ownership |
| Period allowance exhausted, not renewable | `WINDOW_LIMIT_REACHED` | Wait for authorized allocation or terminate according to Task policy |
| Cumulative ceiling or horizon reached | `TASK_LIMIT_REACHED` / `TASK_HORIZON_REACHED` | Block new admissions/triggers; request authorized extension or settle Task |
| Unknown tool outcome at any boundary | `OUTCOME_UNKNOWN` with invocation reference | Reconcile through AE-R6 before any replay |

These names are proposed stable semantic codes; the exact DTO mapping and whether a particular wait resumes the same Execution or starts a new one must be frozen against the live R7/R9 state machine in TBO-0. `WAITING` remains the only resumable Execution state. Do not introduce an unowned terminal-to-RUNNING transition.

## 5. Durable representation and authority

TBO-0 must choose the exact schema after auditing the current migration head. The intended representation is:

- Task budget policy: mode, horizon, source (`user`, `agent_proposal`, `system`), policy version/fingerprint, renewal permission, dimensions and hard ceilings, revision.
- Period ledger: `task_id`, epoch identity, `[starts_at, ends_at)`, grants, used/reserved values, `next_eligible_at`, renewal receipt/idempotency key, revision. Cumulative TaskBudget counters remain authoritative for lifetime totals.
- Renewal eligibility: `task_id`, due time, period identity, policy revision and idempotency key. Generic wakeup/subscription records and trigger receipts belong to AAT.
- Timeout feedback: stable code, scope, configured limit, elapsed/remaining amount, task/window status, next eligible time if any, and external invocation outcome when relevant.

Use a server clock for durable wall-time decisions and a monotonic clock for in-process elapsed measurement. Persist absolute UTC boundaries and consumed duration, not process-local monotonic values. Reservations, renewal and admission must be atomic with TaskBudget/Task state and use the established lock/CAS ordering. Replayed requests return the original receipt; a concurrent contender cannot mint a second epoch.

Legacy TaskBudget rows without TBO policy keep their present one-shot behavior until an explicit migration/adoption path is approved. Missing policy must never be interpreted as unlimited renewal.

## 6. Budget interface and AAT handoff

TBO may expose `agent.budget.propose` and `agent.budget.allocate` for Task horizon proposals and future operation slices. These are proposed budget-specific tools; every call carries the required purpose/description and parameters, and returns a committed budget receipt or structured denial. Existing `agent.budget.configure` needs an explicit migration decision at TBO-0.

TBO publishes only durable renewal eligibility and an idempotent budget-admission decision. AAT owns generic timers, event subscriptions, Agent-owned tool publication and wakeup delivery. AAT cannot treat due time as a budget grant: every activation must ask TBO for the current policy/epoch decision. AAT downtime must not cause TBO to mint multiple epochs or silently restart an expired Task.

## 7. Ownership and dependencies

| Existing authority | TBO relationship |
|---|---|
| AE-R4/R5 | Reuse active-time/WAITING TTL semantics and TaskBudget cumulative counters; TBO owns only new period/horizon policy and renewal ledger |
| AE-R6/R7 | Reuse invocation reconciliation, checkpoint, ResumeClaim and legal WAITING transitions; do not replay unknown side effects |
| AE-R8/R9 | Reuse Task/Branch/Execution identity, fork/retry admission and aggregate accounting; period grants remain shared across branches |
| AE-R10 | Reuse provider retry/fallback deadline and logical-call accounting; TBO does not create provider retry policy |
| AE-R11/R12 | Reuse checkpoint storage and lease/fencing/recovery; stale owner cannot renew, dispatch, or activate a trigger |
| AAT/AIC | AAT owns generic timers, subscriptions and Agent-owned tool activation; AIC owns communication between connected Agents; both consume TBO budget admission without owning grants or renewal |
| TV1/PTC/Capability/CTX/CAS | Tool declarations/routing, context and assets remain with their owners; TBO defines budget attribution only; shared paths require fresh overlap audit |

Issue #85 repository policy controls CLAIM, CI, independent audit and merge/wave authorization. An issue discussing TBO or listing files does not transfer implementation authority from an active AE stage.

## 8. Reserved stages and gates

| Stage | Deliverable | Evidence to release next stage |
|---|---|---|
| `TBO-0` | Fresh HEAD audit, exact ownership/DTO/state-machine/schema contract, existing tests inventory, risk matrix; docs and architecture contract tests only | Independent contract review; exact stage CLAIM |
| `TBO-1` | Task identity for long-running chat and finite/recurring policy DTO; bounded first Agent proposal and user override rules | API/serialization/permission tests; no Task with unowned authority |
| `TBO-2` | Durable Task horizon and periodic ledger, migration and legacy adoption; atomic grant/reservation/renewal | Migration up/down, concurrent CAS/idempotency, restart and cumulative-counter tests |
| `TBO-3` | Exhaustion classification, Agent feedback, allocation tool and legal WAITING transition | Fake-clock operation/window/horizon matrix; one visible result and no hidden top-up |
| `TBO-4` | Renewal decision service with bounded eligibility lookup, lease/fence and idempotent epoch grant | Two-worker race, restart, backpressure, clock-boundary and no-double-grant tests |
| `TBO-5` | Durable budget-eligibility handoff and admission contract for future AAT wakeups | Duplicate/replayed due request, cancellation, expiry, no budget minted by AAT; standalone TBO grant remains correct before AAT opens |
| `TBO-6` | Gateway/UI response/tool-call DTO projection, reconnect and long-task experience | Stream/reconnect and live chat tests; no unexpected event taxonomy expansion |
| `TBO-7` | Cross-track integration: RETRY/FORK/RESUME/R12 recovery/R6 unknown invocation/provider fallback | Multi-worker/fault matrix and exact-head Linux/Windows Architecture |
| `TBO-8` | Full exit audit, operational metrics and rollout/rollback plan | Independent audit, full suite, migration-head proof and issue checkpoint |

Each stage needs its own exact-head claim, owned paths, migration-head check where relevant, targeted tests and applicable repository CI. Later stages do not gain production authority merely by appearing here. The stage boundaries may be refined at TBO-0 only with an explicit recorded contract change; the namespace and safety invariants above remain reserved.

## 9. Required acceptance scenarios

1. A five-minute tool call fits an authorized slice and does not fail because the transport briefly goes idle; reconnect observes the same durable Task.
2. A slow inference times out with `scope=inference`; the Agent receives feedback, reallocates within remaining authority and continues without resetting cumulative usage.
3. A period ends during a long Task. Exactly one renewal is granted when policy allows; otherwise Task waits for authority. No branch receives a duplicate grant.
4. A Task holds durable renewal eligibility over a server restart. Once AAT is available, a replayed timer/event requests activation at most once. No Execution consumes active budget while dormant.
5. A finite horizon or user cap ends. Automatic period renewal does not extend it. Agent reports partial progress and the required next authorization.
6. An external tool outcome is unknown when budget expires. The system reconciles before any repeated side effect.
7. Retry, fork, resume and R12 recovery all preserve the same Task horizon, cumulative totals and period ledger.
8. Duplicate eligibility requests, stale lease holders and cancelled Tasks cannot create new budget epochs or admissions.
9. AAT and AIC activations are charged to the correct Task and cannot grant their own budget.

## 10. Opening gate and current decision

`TBO-*` is reserved now and has **no opening date**. Before opening `TBO-0`, record a current-main HEAD audit, verify active AE-R12 ownership and the Agent response/tool stream contract (including Issue #134 disposition), identify a dedicated TBO issue/owner, and post an exact docs/tests-only CLAIM under Issue #85 policy. Before any production stage, resolve shared-file/schema ownership and obtain that stage's release and CI/audit gate. The activation decision must be recorded explicitly; this reservation is not permission to start implementation or publish a scheduler.
