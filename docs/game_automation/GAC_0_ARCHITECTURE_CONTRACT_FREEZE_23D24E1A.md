# GAC-0 — Game Automation Client Architecture / Ownership Contract Freeze

**Primary authority:** Issue #222  
**Parent tracker:** Issue #221 GAME-AUTO-CLIENT  
**Claim baseline:** `main@23d24e1a9f6753e7da0ffe11d23b22cab81e33f0`  
**Policy:** Issue #85 v2.5  
**Cross-track architecture reference:** Issue #156  
**Routing dependency:** Issue #161 CRT-1  
**Agent recovery authority:** Issue #107 AE-R12  
**Stage class:** CONTRACT / EVIDENCE / DOCS / ARCHITECTURE-TEST ONLY  
**Production/runtime/schema/migration delta:** ZERO  
**Production CLAIM:** NONE

## 1. Objective

GAC-0 freezes the architecture, ownership boundaries, data-flow contracts, concurrency rules, fail-closed behavior, and staged handoff for a client-local game automation runtime before any production game automation implementation begins.

The target system automates bounded tasks such as navigation, resource collection, building, simple combat, and UI interaction. The latency-sensitive perception/action loop stays local. AgentRuntime / LLM is an upper-level planner and bounded escalation path, not a frame-by-frame controller.

No game-window capture implementation, YOLO/ByteTrack dependency, keyboard/mouse executor, `game.*` capability registration, server routing change, schema change, migration, or durable asset integration is authorized by GAC-0.

## 2. Canonical entry lock

```text
Issue                  = #222
Parent                 = #221
Policy                 = #85 v2.5
development baseline   = main@23d24e1a9f6753e7da0ffe11d23b22cab81e33f0
production delta       = ZERO
runtime authority      = CLOSED
schema/migration       = CLOSED
server routing changes = CLOSED
merge authority        = NONE
```

GAC-0 may remain on this stable development baseline while unrelated drift is NON_MATERIAL under Policy #85 v2.5. Drift is MATERIAL if it changes the client capability/reconnect contract consumed by GAC, CLIENT_LOCAL target semantics, invocation identity/fingerprint semantics, Agent recovery authority, UBQ timeout/accounting ownership, or CAS persistence ownership required by a later GAC stage.

## 3. Existing client runtime authority

The repository already contains the client transport/execution spine that GAC must reuse:

```text
cl/src/core/client_runtime.py
  ClientRuntime
    owns auth, realtime generations, capability registration and reconnect

cl/src/core/capability_runtime.py
  CapabilityRuntime
    owns client-side capability advertisement and invocation dispatch binding

cl/src/core/capability_dispatcher.py
  CapabilityDispatcher
    correlates realtime invocations with the canonical local executor
```

`GameAutomationRuntime` is a future game-specific runtime below this spine. It MUST NOT replace `ClientRuntime`, create a parallel WebSocket protocol, bypass `CapabilityDispatcher`, or invent a second client invocation recovery authority.

## 4. Frozen top-level architecture

```text
Assistant server
  AgentRuntime / LLM
        |
        | high-level game.* invocation only
        v
ClientRuntime / CapabilityRuntime / CapabilityDispatcher
        |
        v
GameAutomationRuntime
        |
        +--> GameSession / WindowBinding
        |
        +--> FrameCapture
        |        |
        |        v
        +--> PerceptionPipeline
        |        |  YOLO-compatible detector
        |        |  Layout/HUD detector
        |        v
        +--> ObjectTracker
        |        |  ByteTrack-compatible tracker
        |        v
        +--> WorldModel
        |        |
        |        v
        +--> LocalPlanner / BehaviorEngine
        |        |
        |        v
        +--> ActionScheduler
                 |
                 v
             InputExecutor
                 |
                 v
             bound game window
```

Server reasoning may choose or decompose goals. It does not own the ordinary real-time capture -> perception -> behavior -> input tick.

## 5. Ownership boundaries

### 5.1 ClientRuntime remains transport authority

`ClientRuntime` continues to own:

- authenticated client identity and session activation;
- WebSocket connection generations and reconnect;
- capability registration lifecycle;
- server-confirmed capability availability;
- handoff to `CapabilityDispatcher`;
- existing resume/reconciliation integration.

GAC consumes those contracts; it does not fork them.

### 5.2 GameAutomationRuntime owns one local automation session

A future `GameAutomationRuntime` owns only game-specific local state for a selected game session:

- selected window/process binding;
- capture/perception/tracking workers;
- normalized WorldModel;
- local behavior state;
- action scheduling;
- local pause/cancel/emergency-stop state;
- ephemeral telemetry/replay state unless later persistence authority is granted.

### 5.3 One game session has one action authority

Within one bound game session, action intents are serialized through one authoritative scheduler/executor lane. Perception may be parallelized, but multiple workers MUST NOT concurrently emit uncontrolled keyboard/mouse actions for the same session.

## 6. Local-first control invariant

Normal execution is:

```text
capture
  -> detect
  -> track
  -> normalize WorldState
  -> choose local behavior transition
  -> create ActionIntent
  -> schedule/rate-limit
  -> execute
  -> observe
  -> verify ActionOutcome
  -> continue
```

The LLM MUST NOT be required for every frame, detector result, movement tick, click, or key transition.

Server round-trip latency MUST NOT sit inside the inner movement/combat/collection control loop.

## 7. Frozen semantic data boundaries

GAC uses architecture-level concepts equivalent to the following. Exact production language types are deferred to the production stage that owns them.

### GameSessionIdentity

Stable process-local identity for one automation session. It is not an Agent execution ID, CapabilityInvocation ID, WebSocket connection ID, or durable server resource ID.

### GameWindowBinding

Identifies the exact selected desktop game target with enough evidence to reject accidental retargeting. A future implementation should include a process identity plus native window identity and a monotonically changing local binding generation.

### FrameObservation

One captured observation stamped with at least automation-session identity, binding generation, and monotonic frame sequence/time.

### Detection

Detector output for one frame: class/label, geometry, confidence and detector-specific metadata. Detection is observation only and MUST NOT directly execute input.

### TrackedEntity

Temporal identity projected from observations by a tracker such as ByteTrack. Tracker IDs are local perception identities; they are not durable global game-object IDs unless a game adapter separately proves such semantics.

### LayoutObservation

Normalized UI/HUD/layout evidence such as inventory panel, toolbar, minimap, dialog, health region or interaction prompt.

### WorldState

Immutable/snapshot-style normalized state consumed by behavior logic. It combines player/session state, entities, layout observations, objective progress and confidence/evidence metadata.

### ActionIntent

A requested local action expressed in game semantics or bounded input semantics plus preconditions, deadline/cancellation context and expected verification rule.

### ActionOutcome

Terminal local observation of an attempted action: success, blocked, cancelled, timeout, lost-target, stale-state, failed or equivalent structured result.

### BehaviorResult

Result of a bounded behavior step/task, including progress and a machine-readable blocked/failure reason.

### EscalationDecision

Explicit decision that local policy cannot reliably continue and may request upper-level planning with compact evidence.

### GameAdapterProfile

Per-game configuration/adapter authority for controls, object classes, UI regions, detector references, interaction rules and behavior parameters.

## 8. Session generation and stale-result fencing

Every asynchronous observation/result participating in control must be attributable to the currently authoritative automation session and window binding.

Frozen logical identity tuple:

```text
(automation_session_id, binding_generation, frame_sequence)
```

Required behavior:

1. rebinding/replacing the selected game window increments `binding_generation`;
2. results produced for an older binding generation are stale and cannot update the current actionable WorldState;
3. a stale frame/detection/tracker result cannot produce a new ActionIntent;
4. stopping and starting a new automation session cannot reuse stale action/perception ownership from the previous session;
5. WebSocket reconnect does not by itself create a second local automation owner.

This local stale-result fencing is distinct from AE-R12 distributed Agent execution lease/fencing authority.

## 9. Concurrency / worker model

The architecture explicitly separates:

```text
UI thread
  != capture worker
  != perception worker
  != behavior/action scheduler
  != ClientRuntime realtime receiver
```

Frozen concurrency invariants:

### GAC0-I01 — UI non-blocking
Model inference, frame capture and action execution cannot block the desktop client UI thread.

### GAC0-I02 — realtime receiver non-blocking
YOLO inference, tracking, behavior execution and keyboard/mouse action execution cannot run synchronously on the ClientRuntime realtime receiver path.

### GAC0-I03 — one session action writer
One bound game session has one serialized action scheduling/execution authority.

### GAC0-I04 — bounded queues
Capture/perception/action handoff queues are bounded. Backpressure cannot grow memory without limit.

### GAC0-I05 — freshness over backlog
For perception feeding real-time decisions, a newer authoritative frame may supersede obsolete queued frames. The runtime must not build an arbitrarily old control backlog merely to process every captured frame.

### GAC0-I06 — stale generation rejection
Frame/perception/behavior results from a stale automation-session or binding generation cannot mutate current actionable state or emit actions.

### GAC0-I07 — cancellation propagation
Pause/stop/cancel is observable across capture, perception, behavior and action workers. Stop prevents admission of new actions.

### GAC0-I08 — reconnect uniqueness
Client transport reconnect may replace a WebSocket generation but cannot duplicate local `GameAutomationRuntime` ownership for the same active session.

## 10. Window binding and fail-closed input safety

Future input execution is permitted only against the explicitly selected and currently validated game target.

### GAC0-I09 — exact target binding
A window handle/title match alone is insufficient if it can silently identify a different process/window after replacement. The future implementation must validate the selected binding according to the platform adapter.

### GAC0-I10 — target loss fails closed
If the bound game window/process is destroyed, replaced, becomes invalid, or cannot be validated, new input actions stop until explicit safe rebind/resume semantics succeed.

### GAC0-I11 — focus/input guard
Where the chosen input mechanism requires foreground/focus, failure to establish the required target state fails closed rather than sending input to the ambient desktop/another application.

### GAC0-I12 — local emergency stop
Emergency stop is process-local, immediately available, and does not depend on server reachability, AgentRuntime, LLM inference, or a successful WebSocket round trip.

## 11. ActionScheduler pacing semantics

The future scheduler must represent these concepts separately:

- configurable base action delay;
- minimum inter-action interval;
- action-specific cooldown;
- observation/verification wait;
- bounded retry count/backoff;
- action deadline;
- pause/cancellation;
- emergency stop.

These are local execution pacing/safety semantics. They are not UserResourceBudget quota authority and are not provider/Agent timeout authority.

### GAC0-I13 — no unbounded action loop
Every behavior/action loop has a cancellation path and bounded progress/failure policy. No infinite blind click/key loop is acceptable.

### GAC0-I14 — verify before repeat
Repeated side-effecting actions require observable progress/verification or an explicitly bounded retry policy.

## 12. Perception boundary

GAC-0 freezes adapters, not concrete ML dependencies.

YOLO is a detector implementation candidate behind a detector interface. ByteTrack is a tracker implementation candidate behind a tracker interface. Layout/HUD recognition is a separate observation concern and may use detector, template, OCR, feature, game-specific or composite implementations in later stages.

No GAC-0 authority exists to:

- add/download YOLO models;
- add ByteTrack dependencies;
- choose CUDA/DirectML/ONNX/TensorRT backends;
- download model weights;
- implement GPU resource policy;
- modify packaging for ML runtimes.

Those decisions belong to GAC-3/GAC-4 or later exact production CLAIMs.

## 13. WorldModel boundary

Perception does not command actions directly.

Frozen flow:

```text
raw frame
  -> Detection / LayoutObservation
  -> TrackedEntity
  -> WorldState snapshot
  -> behavior/local planner
  -> ActionIntent
```

### GAC0-I15 — evidence before action
Behavior decisions consume a normalized WorldState/evidence snapshot. A raw detector callback cannot directly inject keyboard/mouse input.

### GAC0-I16 — confidence is explicit
WorldState carries enough confidence/freshness metadata for local behavior to reject ambiguous or stale evidence rather than treating every detector output as truth.

## 14. Local behavior and LLM escalation

Behavior priority is local-first:

```text
reactive rule
  -> bounded FSM / Behavior Tree / local planner
  -> escalate only when local policy cannot proceed reliably
```

Candidate escalation reasons include:

- repeated bounded behavior failure;
- low-confidence or contradictory WorldState;
- unknown UI/layout transition;
- high-level task decomposition not represented by local behavior;
- explicit user request for planning.

### GAC0-I17 — no LLM-per-frame design
LLM/Agent inference is not part of the mandatory frame cadence.

### GAC0-I18 — compact escalation evidence
Default escalation input is a compact structured state/evidence summary. Raw unrestricted continuous frame streaming to the model is not the default architecture.

## 15. GameAdapter boundary

Game-specific knowledge lives behind a GameAdapter/Profile boundary rather than being hard-coded into the generic runtime core.

A future adapter may define:

- game/process matching rules;
- control/key bindings;
- semantic object classes;
- UI/HUD regions;
- detector/model references;
- interaction distances and timing;
- action verification rules;
- navigation/collection/build/combat parameters;
- task-specific behavior extensions.

### GAC0-I19 — adapter cannot widen host authority
A game adapter configures game semantics; it does not gain arbitrary filesystem/process/network or server-routing authority merely by being loaded.

## 16. Model-facing capability boundary

Normal future model-facing capabilities are high-level operations such as:

```text
game.observe
game.navigate
game.collect
game.build
game.combat
game.execute_task
game.stop
```

Raw keyboard/mouse primitives may exist as internal implementation details but are not the normal Agent/LLM capability surface.

### GAC0-I20 — no server micro-control
The server does not normally drive movement by emitting per-frame `key_down`, `key_up`, `mouse_move`, or click sequences over WebSocket.

### GAC0-I21 — capability result is structured
A future `game.*` invocation returns structured completion/progress/blocked/cancelled/failure semantics rather than relying only on unstructured text.

Exact capability schemas, idempotency classes, effect declarations and registration metadata belong to GAC-6 and must be audited against then-canonical client/routing contracts.

## 17. #156 / #161 target-routing boundary

The intended semantic target for a game automation invocation is:

```text
target_class = CLIENT_LOCAL
resource     = explicitly selected game automation session/window
```

This is a dependency statement, not GAC-0 implementation authority.

GAC-0 MUST NOT implement or redefine:

- `CapabilityInvocationTarget`;
- `ResourceScope`;
- server `CapabilityRoutingPolicy`;
- semantic request fingerprint;
- connection/reconnect invocation identity;
- cross-client fallback semantics.

Issue #161 remains RESERVED / NOT CLAIMED at this baseline. Future GAC-6 integration must consume the canonical target-aware routing contract that exists at that time and must preserve the invariant that a CLIENT_LOCAL game resource never silently falls back to a server or foreign client merely because a logical capability ID matches.

## 18. AE-R12 boundary

Issue #107 / AE-R12 owns durable Agent execution lease/recovery/fencing semantics.

GAC local concepts such as `automation_session_id`, `binding_generation`, worker cancellation, or selected HWND/process identity are not substitutes for:

- Agent `execution_id`;
- CapabilityInvocation identity;
- distributed execution lease;
- durable recovery claim;
- R6/R7 reconciliation;
- AE-R12 fencing authority.

GAC MUST NOT interpret transport reconnect or local window rebinding as authority to blindly replay an unknown remote side effect.

## 19. UBQ boundary

UBQ #141-#149 owns user budget/quota and timeout migration.

GAC local pacing values such as 100 ms click delay, capture interval, action cooldown, or local behavior retry count are not renewable user-resource accounting and cannot mutate UBQ durable counters without a separately released integration stage.

Future GAC capability integration should preserve canonical `capability_id` and `invocation_id` semantics needed by UBQ accounting.

## 20. CAS / telemetry boundary

Local frame buffers, screenshots, detections, tracks and replay records are ephemeral client data by default.

Recorder/replay should be architecturally capable of representing:

```text
timestamp
frame or frame reference
detections
tracks
WorldState
decision
ActionIntent
ActionOutcome
```

GAC-0 does not claim CAS #74 FileAsset/FileBlob/FileReference lifecycle, object storage, provider hydration, retention or GC. Any durable upload/persistence bridge requires separate CAS-compatible authority.

## 21. Client UI configuration boundary

The future client UI may configure at least:

- selected game window;
- game adapter/profile;
- detector/model reference after ML stages exist;
- confidence thresholds;
- base action delay;
- action/cooldown timing;
- enable/disable behavior families;
- LLM escalation enablement/policy;
- start / pause / stop / emergency-stop controls.

The UI is a controller/view of runtime state. UI callbacks do not become a second unsynchronized owner of capture/perception/action mutable state.

## 22. Security and explicit non-scope

The initial GAC roadmap uses ordinary desktop capture and ordinary keyboard/mouse input mechanisms.

The following remain CLOSED unless a future independently reviewed stage explicitly changes authority:

- process memory reading or writing;
- DLL/code injection;
- process patching;
- anti-cheat bypass;
- kernel drivers;
- credential/session extraction;
- hidden privilege escalation;
- arbitrary host command execution through a game adapter.

## 23. Proposed staged roadmap

Only GAC-0 is claimed by this freeze.

```text
GAC-0  architecture / ownership contract freeze
GAC-1  GameSession + WindowBinding + capture baseline
GAC-2  Action Runtime + scheduler + cancellation/emergency stop
GAC-3  Perception pipeline + YOLO adapter
GAC-4  ByteTrack + WorldModel + layout/HUD state
GAC-5  Local Behavior Runtime: navigation + collect first
GAC-6  Assistant client capability integration (game.*)
GAC-7  LLM escalation / high-level planning
GAC-8  Recorder + deterministic replay + evaluation
GAC-9  Multi-game adapter/profile SDK
```

No later stage gains production authority merely because it appears in this roadmap.

Build and combat may be split into smaller separately claimed stages after the navigation/collect behavior baseline is proven.

## 24. GAC-0 exact owned scope

Exactly two files:

```text
docs/game_automation/GAC_0_ARCHITECTURE_CONTRACT_FREEZE_23D24E1A.md
cl/tests/test_gac_0_architecture_contract_freeze.py
```

GAC-0 must not modify `cl/src/**`, `se/src/**`, `tools/**`, migrations, dependency manifests, ML model files, packaging, or workflow production semantics.

## 25. Stop conditions

Stop GAC-0 progression and re-audit if:

- the exact two-file scope expands;
- production/runtime/schema/migration code becomes necessary;
- current main changes a client-runtime contract materially used by this freeze;
- #156/#161 routing authority changes the intended CLIENT_LOCAL semantics;
- AE-R12 changes invocation/reconnect/replay assumptions materially used by GAC;
- a new P0/P1 invalidates local-first, fail-closed target safety, cancellation, or single-action-owner assumptions;
- implementation would require authority owned by UBQ, CAS, Tools V1, Agent recovery, or another active track.

## 26. GAC-0 exit gate

GAC-0 reaches FINAL GREEN only when:

1. the exact two-file docs/test scope is preserved;
2. production/runtime/schema/migration delta remains ZERO;
3. local-first ownership and the high-level capability boundary are frozen;
4. session/binding generation and stale-result rejection are frozen;
5. UI/realtime/capture/perception/action concurrency separation and bounded backpressure are frozen;
6. exact window binding, target-loss fail-closed behavior, cancellation and local emergency stop are frozen;
7. #156/#161, AE-R12, UBQ and CAS authority boundaries remain explicit;
8. exact-head required Architecture CI is GREEN;
9. independent audit reports no blocking GAC-0 P0/P1;
10. integration/merge follows Policy #85 v2.5.

## 27. GAC-1 opening gate

Only after GAC-0 is canonical may GAC-1 request an independent PRE-CLAIM/release for a production slice limited to:

```text
GameSession
WindowBinding
GameWindowManager
FrameSource interface
minimal window capture baseline
session lifecycle
focus/target-loss fail-closed handling
```

GAC-1 does not inherit production authority automatically from GAC-0. It requires a fresh exact-main dependency/path audit, explicit owned files, focused tests, cross-platform evidence appropriate to its implementation, independent audit, and Policy #85 integration handling.

## 28. Frozen summary

```text
real-time control location = CLIENT LOCAL
LLM role                  = HIGH-LEVEL PLANNER / BOUNDED ESCALATION
transport authority       = existing ClientRuntime
remote dispatch authority = existing CapabilityDispatcher
target intent             = CLIENT_LOCAL (future canonical routing contract)
action writer             = ONE SERIALIZED AUTHORITY PER GAME SESSION
queues                    = BOUNDED
stale result behavior     = REJECT BY SESSION/BINDING GENERATION
target loss               = FAIL CLOSED
emergency stop            = LOCAL / SERVER-INDEPENDENT
durable media authority   = NONE
production authority      = NONE
next stage                = GAC-1 PRE-CLAIM only after GAC-0 canonical
```