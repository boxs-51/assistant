# APR-X1 — Execution Lane + Event Sequencer Contract Freeze

Canonical policy: Issue #85 v2.5
Tracker: Issue #278
Workspace: Issue #377
Baseline: main@c86bcc5e5f5496a77de4613cf9ef933e06391634
Baseline Architecture: #2345 / 37726298010 = GREEN/GREEN

## 1. Authority

```text
stage = APR-X1
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = HOLD / NOT RELEASED
production CLAIM = NONE
schema/migration/runtime/API/client authority = NONE
AE revision/state/checkpoint authority = NONE
fault/HITL authority = NONE
SBX authority = NONE
GAC authority = NONE
merge authority = NONE
exact changed paths = 2 NEW / 2
third path = PROHIBITED
se/src/** delta = ZERO
cl/** delta = ZERO
```

Exact paths:
1. docs/agent_platform/APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md
2. se/tests/architecture/test_apr_x1_execution_lane_event_sequencer_contract.py

APR-X1 freezes sequencing semantics only. It does not implement runtime lanes,
a durable event log, a second state machine, a checkpoint store, or a new
idempotency ledger.

## 2. Current-source baseline

Current source already has bounded concurrency and event projection:

- AgentExecutionLimits.max_parallel_tools and max_parallel_agents;
- AgentRuntime concurrent Tool execution;
- AgentEventEnvelope publication/telemetry;
- AgentExecution.revision as durable AE revision;
- AE state-machine transitions;
- expected-revision/CAS persistence and checkpoint/recovery authority.

No production server type currently owns:
ExecutionEventSequencer, DecisionCommit, AgentObservation, or ResponseEmission.

## 3. Conceptual lane model

APR-X1 freezes these conceptual roles:

```text
AgentObservation
DecisionCommit
ActionIntent           # conceptual APR term only
ResponseEmission
ExecutionLaneEvent
ExecutionEventSequencer
```

These names do not release production Python symbols.

Observation lane:
- receives Tool/Sub-Agent completions;
- user/realtime/environment input;
- dependency/HITL/cancel/timeout/preemption signals;
- provider/reconciliation results.

Decision lane:
- reasons over coherent eligible observations;
- cannot directly mutate durable AE state.

Work/action lane:
- authorized Tool/Sub-Agent work may run concurrently;
- physical completion order does not establish durable state order.

Response lane:
- may publish progress/partial output;
- cannot become revision/state/checkpoint authority.

Sequencer:
- serializes observation/decision/action adoption for one execution;
- does not replace AE CAS/persistence.

## 4. Observation-before-adoption

Every asynchronous completion becomes an observation before adoption.

An observation must preserve enough causality for at least:

```text
execution_id
source_execution_revision
source invocation / child execution identity when applicable
correlation identity
causation identity
completion/result identity
eligibility/adoption state
```

A physically completed result may still be stale. If AE authority has advanced to
an incompatible revision or terminal state, adoption fails closed.

Late results cannot overwrite newer durable execution state.

## 5. One durable commit authority

DecisionCommit is a commit/adoption request, not independent persistence authority.

```text
sequencer-local order != AgentExecution.revision
public AgentEventEnvelope.event_id != AgentExecution.revision
DecisionCommit != independent persistence authority
ExecutionLaneEvent != independent checkpoint authority
```

A future implementation must commit durable mutation through AE-owned
expected-revision/CAS.

If CAS loses:
1. proposed adoption fails closed;
2. no force-write occurs;
3. canonical state/revision is reloaded;
4. still-relevant observations/decisions are re-evaluated;
5. APR-X1 does not invent a replacement revision.

## 6. Publication/event identity

Current AgentEventEnvelope remains publication/projection telemetry.

Its event_id, timestamp, delivery order, retry order, or duplicate delivery MUST NOT
become durable AgentExecution ordering authority.

A future internal ExecutionLaneEvent may carry local sequence, event identity,
execution identity, source revision, correlation/causation and source identity,
but exact schema/persistence/wire format is not released here.

## 7. Concurrent work, serialized adoption

Multiple authorized actions may run concurrently.

Their completions become observations.

The sequencer serializes adoption/dispatch authority after validating current AE
revision/state. It does not require physical work to run serially.

## 8. Idempotency and reconciliation

APR-X1 MUST NOT create a competing action-idempotency ledger.

Side-effecting execution continues to use canonical invocation/idempotency/
reconciliation identity owned by AE/CapabilityRuntime and the applicable Tool or
provider boundary.

Timeout/cancellation do not imply rollback. Late terminal results return as
observations and cannot resurrect or rewrite a newer committed outcome.

## 9. Cancellation, preemption and HITL

Cancel, timeout, preemption, dependency and HITL signals may enter the sequencer as
control observations.

APR-X1 does not own terminalization or WAITING transitions.

Once AE reaches a terminal or incompatible newer revision:
- stale DecisionCommit adoption fails closed;
- stale ActionIntent adoption fails closed;
- response emission cannot reopen execution;
- HITL approval/denial cannot bypass current WAITING/revision and trusted identity.

## 10. Restart/checkpoint/multi-worker

An in-memory lane queue or sequencer is not recovery truth.

On restart/recovery:
- durable AE execution state/revision is canonical;
- durable checkpoint/pending invocation state is canonical;
- uncommitted process-local lane events may be discarded/reconstructed;
- multi-worker one-winner behavior remains AE revision/CAS/lease/recovery-owned.

APR-X1 MUST NOT persist a second checkpoint log.

Any durable lane-event persistence requires a new AE/APR schema/persistence
PRE-CLAIM.

## 11. Response emission boundary

ResponseEmission is presentation/output progress only.

Partial/streamed output MUST NOT by itself:
- advance AgentExecution.revision;
- change execution state;
- mutate checkpoint truth;
- commit CTX Memory;
- authorize Tool/Skill/capability use;
- finalize a side effect;
- create retry/recovery truth;
- establish AgentInstance identity.

## 12. AE-R14 fence

Issue #359 remains the AE-R14 evidence/fault owner.

At release:
- PR #372 / R14-B = open draft zero-production test/evidence, Architecture #2339 SUCCESS;
- PR #373 / R14-C = open draft zero-production test/evidence, Architecture #2340 SUCCESS.

Direct path overlap with APR-X1 contract paths = ZERO.

Semantic overlap = MATERIAL BOUNDARY / FENCED.

APR-X1 receives no authority over:
- AgentExecution revision/CAS;
- state transitions;
- WAITING/resume/recovery/lease;
- checkpoint persistence;
- branch/retry/ADOPT/aggregation;
- reconciliation/idempotency;
- HITL durable wait/approval semantics;
- fault injection/R14 exit-gate behavior;
- TaskBudget/UBQ-coupled transitions.

R14-B/C are parallel non-canonical evidence candidates until separately landed.

Any future APR-X1 production change affecting these semantics requires fresh AE
#359 bilateral disposition or terminal/canonical handoff.

## 13. SBX-2 fence

Issue #163 / PR #364 owns a separate production slice including
se/src/runtimes/agent/runtime.py for sandbox lifecycle semantics.

Contract-path overlap = ZERO.

Any future APR-X1 production PRE-CLAIM including AgentRuntime is HOLD until SBX-2
is terminal/released or a fresh bilateral explicitly permits coexistence.

APR-X1 receives zero sandbox authority.

## 14. GAC ActionIntent namespace fence

GAC #221 already owns client-local:

cl/src/game_automation/actions/action.py::ActionIntent

APR-X1 ActionIntent is conceptual only.

APR-X1 MUST NOT import, mutate, rename, reinterpret or claim the GAC DTO or
cl/src/game_automation/**.

Future server production naming must be namespace-disjoint and separately claimed.

## 15. Memory/context boundary

Observation adoption does not grant CTX Memory write/promotion, Agent-private
Memory ownership, personalization mutation or ContextSnapshot persistence.

CTX remains the Memory/context authority.

## 16. Determinism

Concurrency may exist in work, observation arrival, reasoning and presentation.

Canonical state adoption must remain deterministically serialized through a single
sequencer boundary tied to AE durable authority.

For multiple eligible observations produced from the same
`source_execution_revision`, the sequencer MUST derive a stable adoption candidate
order from immutable observation metadata, never callback arrival order.

The conceptual stable key is:

```text
(
  source_execution_revision,
  semantic_priority,
  source_kind,
  source_identity,
  completion_or_result_identity
)
```

The future implementation MAY encode this key differently, but the values used for
ordering MUST be stable across retry/recovery/replay and MUST NOT depend on
wall-clock completion time, coroutine scheduling, publisher delivery order, or
process-local insertion order.

`semantic_priority` is a frozen conceptual precedence class, not a new durable
state authority:

```text
1. AE-valid terminal/cancel/timeout/preemption control observations
2. AE-valid HITL/dependency/recovery control observations
3. Tool/Sub-Agent/provider completion observations
4. user/realtime/environment input observations
5. progress/presentation-only observations
```

Within the same precedence class, `source_kind`, `source_identity`, and
`completion_or_result_identity` provide the deterministic tie-break. If any
required stable identity is absent or ambiguous, adoption MUST fail closed or defer
until a canonical identity is available; callback order MUST NOT be used as a
fallback.

This ordering only ranks candidates for serialized adoption. It does not override AE
state/revision/CAS authority, HITL identity checks, capability authorization,
idempotency/reconciliation ownership, cancellation semantics, or current-state
revalidation before each adoption.

Replay/recovery MUST NOT depend on wall-clock callback completion order, public
event delivery order, publisher retry order or task scheduling order.

## 17. Production gate remains HOLD

Before production PRE-CLAIM a fresh audit must resolve:

1. current AE-R14 disposition;
2. bilateral ownership for AE revision/state/recovery/checkpoint/fault/HITL semantics;
3. SBX-2 ownership if AgentRuntime is targeted;
4. exact runtime/contracts/events/persistence path ownership;
5. process-local vs durable lane-event design;
6. R6 invocation idempotency/reconciliation compatibility;
7. TaskBudget/UBQ/cancellation/deadline compatibility;
8. GAC ActionIntent namespace separation;
9. concurrency/race/restart evidence plan;
10. fresh Linux + Windows Architecture.

## 18. Exit gate

APR-X1 contract FINAL requires:
- exact changed paths = 2 NEW / 2;
- third path absent;
- existing se/src/** and cl/** untouched;
- AgentExecution still has durable revision;
- AE state machine remains transition vocabulary;
- persistence still exposes expected-revision/CAS conflict semantics;
- bounded parallel Tool execution remains present;
- AgentEventEnvelope remains projection-only;
- no production server ExecutionEventSequencer/DecisionCommit/AgentObservation/
  ResponseEmission implementation exists;
- observation-before-adoption and one AE-backed durable commit boundary are frozen;
- same-revision concurrent observations use a stable immutable-metadata adoption key and never callback order;
- stale adoption fails closed;
- response emission has zero durable-state authority;
- checkpoint/restart/multi-worker authority remains AE-owned;
- R14-A remains AE-owned;
- R14-B/C are parallel non-canonical candidates only;
- GAC ActionIntent remains client-local and namespace-disjoint;
- production PRE-CLAIM remains HOLD;
- exact-head Linux + Windows Architecture GREEN;
- independent APR-X1 contract FINAL PASS;
- unresolved threads = 0;
- P0/P1/P2 = 0/0/0.

Contract landing grants no production, schema, migration, state-machine, fault/HITL,
SBX, GAC or merge authority.
