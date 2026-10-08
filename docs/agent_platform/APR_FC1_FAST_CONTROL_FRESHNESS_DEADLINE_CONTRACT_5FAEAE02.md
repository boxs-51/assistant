# APR-FC1 — FAST_CONTROL Freshness, DecisionRevision & ActionDeadline Contract

Canonical tracker: Issue #278 / APR  
Stage workspace: Issue #382 / APR-FC1  
Governance: Issue #85 v2.5  
Independent PRE-CLAIM release: Issue #382 comment #6059613517  
Development baseline: main@5faeae02499d16fa71a04c77d6bfa0a5a4cf4ef2  
Baseline Architecture: #2362 / 37773300958 = GREEN/GREEN

## 1. Frozen claim and authority

```text
stage = APR-FC1
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
independent PRE-CLAIM = PASS / RELEASED
production PRE-CLAIM = HOLD / NOT RELEASED
production CLAIM = NONE
runtime/API/client/schema/migration authority = NONE
AE state/revision/CAS/HITL/fault/recovery authority = NONE
DCS/SBX/Tools/UBQ/TBO/CTX/AIC/CAS/GAC authority = NONE
merge authority = NONE
exact changed paths = 2 NEW / 2
third path = PROHIBITED
se/src/** delta = ZERO
cl/** delta = ZERO
```

The only paths released by this claim are:

1. `docs/agent_platform/APR_FC1_FAST_CONTROL_FRESHNESS_DEADLINE_CONTRACT_5FAEAE02.md`
2. `se/tests/architecture/test_apr_fc1_fast_control_contract.py`

This document and its architecture evidence freeze **requirements only**. They create no production Python class, API, database column, scheduler, engine, transport, provider operation, FAST_CONTROL tick loop, or merge authorization. A third path requires an independent PRE-CLAIM amendment before any edit.

## 2. Canonical input contracts and independent identities

APR-C0 (#279), APR-P0 (#374) and APR-X1 (#377) are already LANDED / CANONICAL / HEALTHY. APR-FC1 consumes their semantics without modifying them.

```text
owner_user_id + agent_instance_id      = AIC-owned durable Agent identity
execution_id + AgentExecution.revision = AE-owned execution and durable revision
runtime_session_id                     = execution-attached runtime session
source_session_id + source_revision    = observation source's comparable epoch/revision
decision_revision                      = conceptual local candidate provenance
action_deadline                        = conceptual local acceptance bound
world_revision / binding_generation    = GAC client-local world/binding revisions
```

None of these identities is silently inferred from another. In particular:

- `AgentExecution.owner_instance_id != agent_instance_id` (AE lease worker ownership is not Agent identity).
- `DecisionRevision != AgentExecution.revision`.
- `DecisionRevision != AgentEventEnvelope.event_id`.
- `ActionDeadline != GAC.ActionIntent.deadline_monotonic` as a cross-runtime identity or clock.
- An ephemeral RuntimeSession, GAC automation session, frame index or callback ID does not own CTX Memory.
- One Agent definition/profile may be reused; profile or FAST_CONTROL selection is **never** an authorization grant.

These are **conceptual** future shapes, not introduced production classes:

```text
FastObservation
  execution_id?                 # present when server AE execution participates
  runtime_session_id
  source_kind
  source_session_id
  source_epoch
  source_revision               # monotonic or otherwise comparable within its source epoch
  source_observed_at
  observed_clock_domain
  received_at
  ingress_clock_domain
  valid_until / max_age
  correlation_id?
  causation_id?
  result_identity
  eligibility

DecisionRevision
  runtime_session_id
  source_epoch + source_revision
  relevant_source_set / coherent_observation_snapshot
  expected_execution_revision?   # AE-owned, not manufactured by APR
  deterministic_adoption_key
  policy_version / strategy_hint_version?
  decision_identity

ActionDeadline
  runtime_session_id
  clock_domain
  not_after_monotonic?           # meaningful ONLY in its owning process/clock domain
  expiry_duration / TTL
  created_from_observation_revision
  adoption_guard

StrategyHint
  source_epoch + source_revision
  runtime_session_id
  provenance / model_policy_version
  confidence
  validity_window
  intended_goal_or_policy
```

## 3. Observation identity and freshness

An observation MUST preserve source identity, source epoch, source revision, stable result identity and causality before it is eligible for adoption. A revision is comparable only inside its explicitly declared source epoch and identity; cross-source revisions are not implicitly globally ordered.

Freshness acceptance is a **conjunction**, not a replacement for authority:

1. The observation belongs to the current authenticated owner/runtime session and the expected source epoch.
2. Its source revision is not superseded by an incompatible newer committed/eligible observation.
3. Its age and validity can be evaluated with an explicitly specified clock domain and a bounded max-age/TTL.
4. Its correlation/causation and result identity pass deduplication/idempotency checks under the owning execution or local runtime.
5. Current session, cancellation/focus/binding/approval gates remain valid.
6. Any proposed durable adoption still passes current AE state and expected-revision/CAS.

Observed-at and received-at MUST remain distinct. Provider timestamps, clock drift, network delivery order and callback completion order are not interchangeable with monotonic observation revision.

When a clock domain is not comparable (for example client monotonic vs server monotonic), the system MUST fail closed or obtain a fresh trusted observation in the consumer's clock domain. It MUST NOT subtract unrelated monotonic timestamps or assume synchronized wall clocks.

Duplicate, out-of-order, missing-source-epoch, ambiguous-revision, expired and stale observations MUST NOT dispatch a new side effect. They may be rejected or deferred for explicit refresh; replaying telemetry does not mint a new revision.

## 4. DecisionRevision and deterministic adoption

A tentative FAST_CONTROL decision binds to an exact coherent eligible observation snapshot with source epoch/revision and active runtime-session identity. This binding is `DecisionRevision`: a provenance/eligibility token, not an AE durable revision or new persistence API.

For eligible observations sharing a source execution revision, APR-FC1 inherits APR-X1's deterministic stable adoption key:

```text
(source_execution_revision, semantic_priority, source_kind,
 source_identity, completion_or_result_identity)
```

Priority is a **frozen semantic precedence class**, not whichever coroutine or callback completes first. Missing stable tie-break identity MUST fail closed/defer; no callback order, publisher order, process-local queue order or wall-clock timing fallback is permitted.

The stable ordering decides *which candidate is considered first*, never permission to override current AE revision/state/CAS or current GAC focus/binding/safety state. A stale decision loses adoption, even when its old source revision has the lexicographically earlier key.

Where AE durable state changes are required, only AE expected-revision/CAS may commit. If CAS loses, the adoption MUST fail closed, reload canonical AE state and re-evaluate remaining relevant observations. No force-write, independent durable FAST_CONTROL sequence or new checkpoint/idempotency ledger may be introduced.

A purely GAC-local tick does **not** become an AE durable commit and does not require network roundtrips or an AE write on every frame. Physical client-local actions remain guarded/serialized by GAC.

## 5. ActionDeadline and expiry

`ActionDeadline` binds one candidate acceptance attempt to a bounded TTL/expiry with an explicit owner and clock domain. It is checked immediately before adoption/dispatch in the relevant runtime; it is not a guarantee of hard real-time execution, nor a global synchronized timestamp.

**Deadline acceptance rule** (all must hold):

- Current trusted monotonic clock of the **same owning domain** is strictly before the deadline; zero/negative or unknown TTL is rejected.
- The candidate's session/epoch/source revision and coherent snapshot remain eligible.
- Newer incompatible observation, cancel, emergency stop, approval denial, focus/binding loss or owner revocation has not preempted acceptance.
- Target/routing and bounded capability authorization remain valid.
- Durable AE change, if any, still wins AE expected-revision/CAS; physical GAC-local action still wins GAC's current focus/binding/scheduler guard.

GAC already has a client-local `ActionIntent.deadline_monotonic` and `binding_generation`; APR-FC1 MUST NOT import, overwrite, rename or reinterpret either as a server ActionDeadline, TaskBudget timeout, AE wait expiry, UBQ reservation or common cross-device clock. A future bridge must explicitly translate expiry as a bounded duration with local revalidation, not copy a raw monotonic timestamp.

Expired/unknown-deadline decisions MUST NOT dispatch a new side effect; timeouts cannot retroactively undo an already-committed external effect. Reconciliation belongs to AE/CapabilityRuntime or the owning local action system.

## 6. Preemption, cancellation and race precedence

Priority, when relevant signals race in the same eligible decision window:

```text
P0  trusted emergency stop / revoked authority / unsafe environment
P1  committed terminal AE state, trusted cancel, HITL denial, lost GAC focus/binding
P2  newer incompatible source epoch/revision, superseding observation
P3  expired ActionDeadline or invalid observation/strategy TTL
P4  bounded eligible fast decision/action request
```

A lower-priority eligible action cannot override a higher-priority safety/cancel signal already effective in the owning domain. Multiple inputs within the same class use immutable source metadata and the APR-X1 stable tie-break; missing identity fails closed.

Preemption rejects **new** adoption/dispatch; an already-committed remote side effect must use AE-R6 reconciliation instead of pretending rollback. AE remains owner of durable terminalization, WAITING/HITL transitions, lease/recovery/restart and multi-worker one-winner semantics. GAC remains owner of its local emergency stop and physical input cleanup.

## 7. Bounded preselected session capability set

DCS #159/#160 already owns per-iteration `CapabilityWorkingSet.visible_capability_ids` and `CapabilityWorkingSet.revision`. FAST_CONTROL may consume a **session-scoped preselected bounded capability subset** of the currently authorized Agent envelope / selected WorkingSet under DCS/Skill/CRT/target authority.

The fast critical path MUST NOT execute full DCS group/lazy/rank/schema-token selection on every high-frequency tick. A changed Agent envelope, Skill authorization, capability target, bound client, user ownership, or capability revision invalidates the old preselected subset and requires an explicit eligible re-selection boundary.

Frozen rules:

- Subset never exceeds DCS-selected and still-authorized capabilities; capability IDs are not authority.
- A subset with zero permitted operations is valid; MUST NOT silently fall back to ALL-TOOLS.
- Unsupported/expired/revoked operations fail closed before side effects.
- Tool invocation still binds canonical capability/invocation/idempotency/routing identity.
- CLIENT_LOCAL targets MUST NOT silently route to SERVER or SANDBOX; sandbox targets MUST NOT expose host-file execution.
- Live UBQ/TBO admission, deadlines, reservations and settlement remain with their own systems, not with FAST_CONTROL's freshness check.
- No inferred direct Tool execution privilege comes from a profile, local decision, strategy hint, Skill hint, observation payload or DCS selection itself.

## 8. Split fast policy from deep reasoning

A bounded deterministic or lightweight fast policy/local controller may run on the critical path; deep LLM/Agent reasoning runs **off** that critical path and returns an advisory `StrategyHint`, not executable old actions.

A strategy hint MUST bind provenance, source session/epoch/revision, model/policy version, confidence and validity/TTL. Before adoption the consumer revalidates current source revision, deadline, capability subset, safety state, execution/session ownership and AE/GAC authority. Missing or incompatible provenance is rejected/deferred; confidence alone cannot grant authorization.

Stale StrategyHint MUST NOT directly dispatch ActionIntent, Tool, keyboard/mouse input or mutate execution state. The local deterministic safety controller retains final veto and higher precedence than model strategy.

For physical high-risk or safety-critical control, deterministic/safety-certified controllers own the hard realtime loop; LLM output is limited to goals, constraints or strategy. FAST_CONTROL contract terminology does not certify timing guarantees or physical control safety.

## 9. Client-local GAC, state and Memory fences

GAC #221 / #231 owns `cl/src/game_automation/**` capture, WorldModel, automation session, action scheduler, focus/binding checks, emergency stop and `actions/action.py::ActionIntent`. APR-FC1 does not create a server per-frame capture/input loop; normal server orchestration is high-level `game.*` capability delegation / strategy escalation only.

```text
GAC world/frame revision != AgentExecution.revision
GAC binding_generation != agent_instance_id
GAC ActionIntent != conceptual APR action/adoption request
Client local ActionDeadline clock != server process clock
FAST_CONTROL transient policy state != CTX durable Memory
StrategyHint or observation != automatic CTX Memory promotion
ResponseEmission != durable execution/memory/authorization authority
```

Fast policy caches and local observations are session-scoped and disposable. AIC owns durable Agent-instance identity; CTX owns Memory/promotion/retrieval/ContextSnapshot; AE owns execution persistence and checkpoint/recovery; CAS owns asset lifecycle; Tools/providers own physical operations; TBO/UBQ own admission/accounting; AAT owns automation triggers. No second durable replay log, checkpoint, Memory store, capability ledger or event-sourced state machine is created here.

## 10. Recovery, failure and exit boundaries

On duplicate/out-of-order completion, source invalidation, stale StrategyHint, expiry, cancel, focus loss or competing worker, the safe default is **reject/defer and refresh**, never new side effects. A restart may discard transient FAST_CONTROL queues/caches; AE canonical durable state and reconciliation are reloaded by AE, not reconstructed from partial response emissions.

Conceptual decision and deadline names do not create production symbols today. Later specialized runtime releases require their own exact-main independent production PRE-CLAIM/CLAIM, AE-R14 bilateral owner disposition for durable semantics, SBX/DCS ownership disposition for AgentRuntime/Tool surfaces, and GAC approval for any client-local change.

## 11. Focused architecture evidence / exit gate

Only the claimed architecture test may be added. It must mechanically verify:

1. exact two new paths and no production authority;
2. APR-P0 identity/profile fence and APR-X1 stable sequencer/CAS behavior on canonical source;
3. current AE execution revision and expected-revision persistence ownership;
4. source epochs, comparable revisions, explicit clock domains and failure of ambiguous/unrelated clocks;
5. distinct DecisionRevision vs AE/public-event revision, and ActionDeadline vs GAC clock;
6. deterministic same-revision ordering and cancellation/safety/preemption policy;
7. expiration, stale observation/decision/StrategyHint rejection clauses;
8. current DCS WorkingSet subset/revision, no per-tick full DCS or ALL-TOOLS fallback;
9. current GAC local ActionIntent/focus/binding responsibility;
10. no CTX Memory, AIC identity, AE persistence, UBQ/TBO, SBX, CAS, Tools/provider or GAC authority transfer;
11. exact-head Linux and Windows Architecture GREEN plus independent contract FINAL PASS before READY.

```text
independent FINAL = PENDING
READY / FROZEN = NO
merge authority = NONE
production PRE-CLAIM = HOLD / NOT RELEASED
```
