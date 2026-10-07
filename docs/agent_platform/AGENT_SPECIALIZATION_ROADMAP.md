# Agent Platform Specialization Implementation Roadmap

## Authority

- Tracker: #278
- C0-A: #279
- Policy: Issue #85 v2.5
- Baseline: `main@b51f32ed3b67367bad74a3fb298b7448899ad631`
- Production authority: NONE

This roadmap coordinates future work. It does not release any production path.

---

## 1. Current implementation baseline

Current repository already provides useful foundations:

```text
AgentDefinition
AgentExecution
Task/Branch lineage
parent/delegated executions
fork/retry/recovery
max_parallel_tools
max_parallel_agents
parallel Tool execution
Agent-as-capability delegation
Agent progress/tool stream projection
MultiAgentCoordinator
```

Important current limitations:
- no canonical durable per-user `agent_instance_id` in the runtime model;
- current `AgentExecution.owner_instance_id` is recovery lease ownership;
- `AgentMemoryConfig` is not Agent-private durable Memory identity;
- canonical `InferencePort.complete()` is non-streaming;
- no FAST_CONTROL freshness/preemption contract;
- no specialized realtime/computer environment runtime;
- no canonical execution-lane/event-sequencer abstraction.

---

## 2. Roadmap

```text
APR-C0-A
  Agent platform + FAST_CONTROL docs contract
        |
APR-C0-B
  APR namespace registration
        |
        +----------------------+
        |                      |
        v                      v
AIC-0 identity           #156 / DCS foundations
        |                      |
        +----------+-----------+
                   |
                   v
APR-P0
  AgentDefinitionV2 / profile / execution-binding contracts
                   |
                   v
APR-X1
  execution-lane + event-sequencer foundation
        +----------+------------+--------------+
        |                       |              |
        v                       v              v
APR-FC1                    APR-RT1          APR-CU1
Fast Control               Realtime         Computer Use
        |                       |              |
        +-----------+-----------+--------------+
                    |
                    v
                 APR-MD1
             media/job integration
                    |
          +---------+---------+
          |                   |
          v                   v
       AAT-owned            AIC-owned
       integration          integration
                    |
                    v
                 APR-Q1
          safety/fault/quality exit
```

---

## 3. APR-C0-A — current docs candidate

Exact current candidate:

```text
ADD docs/agent_platform/AGENT_PLATFORM_RUNTIME_CONTRACT.md
ADD docs/agent_platform/AGENT_SPECIALIZATION_ROADMAP.md
```

No source/test/schema/migration delta.

C0-A freezes:
- identity layers;
- Agent core ownership;
- runtime profiles;
- FAST_CONTROL;
- fast/slow reasoning split;
- stale-decision/preemption semantics;
- specialized-runtime taxonomy;
- execution lanes;
- cross-track boundaries.

---

## 4. APR-C0-B — namespace registration

PR #277 / SKV2-C0 is LANDED and `docs/ROADMAP_NAMESPACE_REGISTRY.md` path ownership is released.

C0-B remains sequencing-HOLD only until APR-C0-A is LANDED / CANONICAL / HEALTHY. After that:
- refresh canonical main;
- create a one-path docs-only candidate;
- register `APR-*`;
- run exact-head audit;
- do not combine with production work.

---

## 5. APR-P0 — Agent profile/instance binding contracts

State: future / NOT CLAIMED.

Preconditions:
- AIC-0 stable `agent_instance_id` contract canonical;
- relevant #156/DCS foundations canonical;
- APR-C0 canonical;
- fresh current-main overlap audit.

Expected conceptual deliverables:
- versioned AgentDefinition/Profile contract;
- runtime-profile declarations;
- execution binding to Agent instance/profile;
- compatibility mapping from current AgentDefinition;
- explicit proof that AE `owner_instance_id` remains lease ownership.

Possible future paths, subject to exact PRE-CLAIM:

```text
se/src/domain/schemas/agent.py
se/src/runtimes/agent/contracts/profile.py           # new
se/src/runtimes/agent/contracts/instance_binding.py  # new
focused architecture tests
```

APR-P0 does not create AgentInstance persistence unless AIC/registration authority explicitly releases it.

---

## 6. APR-X1 — execution lanes and event sequencing

Purpose:
support concurrent work/observations/responses while preserving serialized execution authority.

Conceptual contracts:

```text
AgentObservation
DecisionCommit
ActionIntent
ResponseEmission
ExecutionLaneEvent
ExecutionEventSequencer
```

Requirements:
- one canonical revision/state commit authority;
- Tool/Sub-Agent work may remain concurrent;
- results become observations before decision adoption;
- response streaming does not mutate execution state;
- recovery/checkpoint semantics remain AE-compatible;
- cancellation/preemption ordering deterministic.

Possible future path area:

```text
se/src/runtimes/agent/contracts/
se/src/runtimes/agent/
se/src/runtimes/agent/stream.py
```

Exact ownership must be re-audited against current AE work before PRE-CLAIM.

---

## 7. APR-FC1 — Fast Control Runtime

Purpose:
support high-frequency environments where deep LLM latency would make direct decisions stale.

### 7.1 Required contracts

```text
FastControlSession
ObservationRevision
FastDecision
ActionDeadline
StaleDecisionPolicy
PreemptionPolicy
StrategyHint
FastPolicyState
FastControlWorkingSet
```

### 7.2 Decision flow

```text
observation rev N
   |
   +--> FAST PATH
   |      fast model / local policy / rules
   |      -> FastDecision(based_on=N)
   |      -> freshness/revalidation
   |      -> action
   |
   +--> SLOW PATH
          deep LLM / Strategy Agent
          -> StrategyHint(based_on=N)
          -> later revalidation
          -> FastPolicyState update
```

### 7.3 DCS interaction

Do not execute full DCS on every tick.

Preferred:

```text
session/phase admission
-> DCS + auth + routability
-> FastControlWorkingSet

fast tick
-> bounded WorkingSet reuse
-> decision
-> current-state validation
-> action
```

Refresh WorkingSet only when materially required.

### 7.4 Memory interaction

Do not put CTX retrieval in each fast tick.

Use:

```text
CTX retrieval
-> strategy synthesis
-> FastPolicyState
-> fast loop
```

### 7.5 Game Agent example

```text
high-rate:
  position
  health
  ammo
  enemy motion
  incoming attack
    -> dodge/aim/move/action

lower-rate:
  target selection
  short route
  tactical mode

slow/event-driven:
  opponent modeling
  strategic planning
  long-history analysis
```

The exact frequencies are environment-specific; APR does not hardcode them.

### 7.6 Physical control safety boundary

For safety-critical physical systems:

```text
LLM
-> goal / strategy / constraints

validated deterministic controller
-> hard realtime actuator loop
```

APR-FC1 must not silently authorize direct LLM hard-realtime actuation.

---

## 8. APR-RT1 — Realtime Runtime

Requires:
- streaming inference/provider contract;
- duplex transport/session contract;
- interruption/barge-in;
- partial output;
- Tool progress integration;
- UBQ/timeout semantics;
- client Live UX.

REALTIME and FAST_CONTROL may coexist in one Agent, but their contracts differ:
- REALTIME optimizes interaction latency/duplex communication;
- FAST_CONTROL optimizes freshness/deadline-sensitive action selection.

---

## 9. APR-CU1 — Computer Use Runtime

Requires:
- environment identity/session;
- bounded observation representation;
- Desktop/Window/browser Tool ownership;
- #156 routing/sandbox as applicable;
- privileged action approval policy;
- stale-observation/action validation;
- cancellation/recovery.

Target:

```text
EnvironmentObservation
-> Agent decision
-> EnvironmentAction
-> EnvironmentObservation
```

No ambient host authority.

---

## 10. APR-MD1 — Media integration

Image:
- prefer ordinary Tool operations.

Video/large render:
- prefer long-running job capabilities.

Agent media specialization:
- use `MEDIA_ORCHESTRATED` profile plus media Skills/Tools.

Requires fresh:
- Tools/PTC/provider audit;
- F7-T/generated-media contract;
- CAS persistence/lifecycle audit.

Do not make provider-specific media implementations Agent-core code.

---

## 11. Automation integration

AAT remains owner of:
- schedules;
- event subscriptions;
- trigger dedupe;
- automation binding;
- wakeup admission handoff.

Agent core begins work only after admitted activation.

BACKGROUND is an Agent runtime profile, not a new automation authority.

---

## 12. Multi-Agent integration

AIC remains owner of:
- stable Agent instance identity;
- Agent directory;
- communication permission;
- messages/delivery.

AE remains owner of execution.

Use sub-Agent/delegation when work has:
- independent goal;
- independent context;
- independent execution lifecycle.

Use internal fast/deep lanes when it is one Agent/task with different latency classes.

---


## 12A. Client-side implementation roadmap

APR implementation is not server-only.

### Current client ownership map

```text
cl/src/core/client_runtime.py
  auth + connection generation + reconnect + capability host lifecycle

cl/src/core/capability_runtime.py
  CLIENT Tool advertisement + invoke/cancel/reconcile dispatch

cl/src/core/realtime_client.py
  persistent gateway websocket/control transport

cl/src/core/gateway_client.py
  HTTP/API compatibility + legacy registry sync

cl/src/core/agent_engine.py
  legacy/local client ReAct loop; not target APR specialized runtime

cl/src/game_automation/**
  GAC client-local closed-loop automation runtime

cl/src/ui/**
  presentation/HITL/interaction surface; CL-UI authority
```

### Canonical future direction

```text
SE AgentRuntime
  high-level reasoning + durable Agent execution
          |
          | high-level CLIENT capability
          v
CL ClientRuntime / CapabilityRuntime
          |
          v
specialized local runtime
  FAST_CONTROL / COMPUTER environment / other bounded subsystem
```

Do not create a second network/runtime stack inside specialized client runtimes.

### AgentEngine convergence

Before APR-P0/X1/RT1/CU1 touches client execution behavior, audit `cl/src/core/agent_engine.py` together with #167/AOS-2.

Default future assumption:
- do not add new durable Agent authority to `AgentEngine`;
- do not make it the host of Agent-private Memory;
- do not build FAST_CONTROL by placing per-frame LLM/tool loops there;
- preserve compatibility until the owning migration stage explicitly removes/migrates it.

### Client Skill sync

`GatewayLLMClient.sync_registry()` eager Skill body loading belongs to SKV2-C1 cleanup.

APR stages should consume the eventual lazy Skill contract, not duplicate a second client profile/Skill registry.

---

## 12B. APR-FC1 ↔ GAC implementation mapping

GAC #221 is the concrete client implementation program for game-focused FAST_CONTROL.

APR-FC1 is the reusable platform semantics.

### Already implemented / KEEP under GAC

```text
GAC-1:
  GameSession
  GameWindowIdentity
  binding_generation
  visible-window capture baseline

GAC-2:
  ActionIntent / ActionOutcome
  ActionScheduler
  cancellation
  emergency stop
  focus guard
  keyboard/mouse execution
  deadline/cooldown/pacing
```

APR MUST NOT reimplement these under `se/src/runtimes/agent/**`.

### GAC-3

Perception Pipeline should produce bounded observations with stable frame/session provenance.

APR recommendation for evidence:
- observation/frame identity;
- capture binding generation;
- timestamp/monotonic ordering;
- reject perception output from stale binding/frame generation.

### GAC-4

WorldModel/tracking stage is the preferred owner to add a monotonic/comparable local state revision.

Conceptually:

```text
WorldStateSnapshot
  automation_session_id
  binding_generation
  world_revision
  observed_at
  entities/layout/player state
```

Exact schema remains GAC-owned.

### GAC-5

Local Behavior Runtime should bind each selected tactical action to the state revision it used.

Conceptually:

```text
LocalDecision
  decision_id
  based_on_world_revision
  action
  deadline
  priority
  preemptible
```

Before executing a new side effect:
- confirm session/binding remains current;
- confirm decision freshness policy;
- then reuse GAC-2 focus/target guard.

This is the concrete client realization of APR stale-decision/preemption semantics.

### GAC-6

Register high-level `game.*` capabilities through existing:

```text
ClientRuntime
 -> CapabilityRuntime
 -> CapabilityDispatcher
 -> GameAutomationRuntime
```

Do not expose ordinary per-frame keyboard/mouse primitives as the normal Agent-facing surface.

Hard dependency:
- #161 CRT-1 or equivalent canonical CLIENT_LOCAL routing/target identity.

### GAC-7

LLM escalation should use the canonical server AgentRuntime and APR slow-path semantics.

Preferred:

```text
local escalation policy
 -> compact WorldState/evidence
 -> server AgentExecution / deep inference
 -> StrategyHint
 -> client revalidation
 -> local FastPolicyState
```

Do not invoke deep LLM every frame.

### APR-FC1 implementation gate

APR-FC1 production work should only be opened for generic contracts/runtime seams not already owned by GAC.

If a proposed APR-FC1 change belongs entirely under `cl/src/game_automation/**`, the GAC owner should normally own that production change.

If a change touches:
- Agent execution event sequencing;
- cross-profile generic contracts;
- provider/model lane contracts;
- generic client-specialized-runtime host seams;

then APR may own it after fresh cross-track PRE-CLAIM.

---

## 13. Implementation classification

### KEEP

- AE Task/Branch/Execution/recovery authority;
- ToolExecutionCoordinator parallelism;
- AgentCapabilityDriver delegation;
- existing stream event projection;
- DCS separation;
- CTX/AIC/AAT/CAS ownership.

### EXTEND LATER

- AgentDefinition contract;
- provider-neutral inference contract for streaming;
- Agent event model;
- context/profile binding.

### ADD LATER

- Agent profile contracts;
- execution-lane contracts;
- Fast Control contracts/runtime;
- Realtime runtime/session;
- Computer environment runtime/session.

### DO NOT CREATE

- `AgentKind.IMAGE`;
- `AgentKind.VIDEO`;
- `AgentKind.GAME`;
- `AgentKind.COMPUTER`;
- `AgentKind.LIVE`;

unless future evidence proves a genuinely distinct identity/execution semantics rather than a profile.

---

## 14. Testing strategy

### APR-P0
- template vs instance vs execution identity separation;
- lease owner not confused with Agent instance;
- unsupported profile denied;
- profile does not grant Tools.

### APR-X1
- deterministic event ordering;
- concurrent Tool completion ordering;
- cancel/preempt races;
- response stream cannot mutate state;
- recovery/checkpoint compatibility.

### APR-FC1
- stale action rejected;
- newer revision preempts old decision;
- StrategyHint revalidated;
- expired policy not adopted;
- zero-deep-LLM fast path;
- session WorkingSet does not expand without DCS;
- fast loop unaffected by CTX retrieval latency;
- deterministic controller boundary in protected physical-control mode.

### APR-RT1
- barge-in;
- reconnect;
- simultaneous Tool progress + response;
- cancellation.

### APR-CU1
- stale screenshot/DOM action rejection where required;
- approval revocation;
- disconnected environment;
- no unauthorized host action.

---

## 15. Integration order

Recommended coordination order:

```text
1. APR-C0-A docs
2. APR-C0-A canonical integration
3. APR-C0-B namespace registration (registry ownership already released by landed PR #277/SKV2-C0)
4. AIC-0 stable Agent-instance identity
5. relevant #156/DCS foundations
6. APR-P0
7. APR-X1
8. APR-FC1 / APR-RT1 / APR-CU1 independently gated
9. APR-MD1
10. AAT/AIC integration under their owners
11. APR-Q1 exit
```

Branches 8 may proceed independently where path/authority audits allow; they are not required to merge in a single serial chain.

---

## 16. Stop conditions

STOP and re-audit if:
- APR needs to redefine AE execution/recovery without AE release;
- `agent_instance_id` is implemented before AIC/registration identity freeze;
- FAST_CONTROL bypasses DCS/auth/routability;
- deep LLM output directly executes after its observation becomes stale;
- CTX durable Memory is placed in the fast tick critical path as required authority;
- physical safety-critical actuation is delegated to an unbounded LLM loop;
- realtime work invents a second execution state machine;
- Computer Use gains ambient host authority;
- media runtime invents CAS persistence;
- an exact path overlaps an active owner/PR.

---

## 17. Current exact action

APR-C0-A may proceed as a two-path docs-only candidate.

PR #277 / SKV2-C0 has landed, so registry path ownership is released. APR-C0-B remains sequencing-HOLD only until APR-C0-A is canonical and must take its own fresh exact-main one-path contract claim.

No production PRE-CLAIM follows from C0-A.


---

## Current-main revalidation — 2026-10-07

This specialization roadmap was re-anchored after the material roadmap/dependency drift recorded on Issue #279.

```text
exact revalidation main = b51f32ed3b67367bad74a3fb298b7448899ad631
current-main Architecture run 37629309626 = IN_PROGRESS at replacement creation; exact-new-main health gate remains OPEN until GREEN/GREEN
prior APR-C0-A candidate = PR #280@ef6e4704855c035059bc463ebe8b34d8d0c5f447 (historical FINAL only)
replacement class = EXACT 2 docs / ZERO production-runtime-schema-migration-test delta
```

Dependency refresh consumed by this revalidation:

- PR #277 / SKV2-C0 is LANDED; `docs/ROADMAP_NAMESPACE_REGISTRY.md` is no longer owned by that candidate. APR-C0-B may be prepared only after APR-C0-A becomes canonical and still requires its own fresh exact-main one-path audit/claim.
- SKV2-P0 / Issue #276 is LANDED / CANONICAL / HEALTHY. APR consumes trusted Skill V2 descriptors/ActiveSkillSet boundaries and does not duplicate Skill authorization or body-loading authority.
- DCS-1 / Issue #159 and DCS-2 / Issue #160 are LANDED / CANONICAL / HEALTHY. APR profile work consumes CapabilityWorkingSet / bounded selected visibility instead of inventing a second capability-selection plane.
- CRT-1 / Issue #161 is LANDED / CANONICAL / HEALTHY. CLIENT_LOCAL stable-client target identity, target-aware routing and semantic fingerprinting are canonical dependencies for future specialized client runtimes.
- SBX-1 / Issue #162 is LANDED / CANONICAL / HEALTHY. EPHEMERAL_SANDBOX is now a canonical execution-boundary primitive; APR-CU1 and media/compute work must consume it rather than create ambient host authority.
- SBX-2 / Issue #163 is not canonical yet and remains separately gated. APR does not infer file/glob/terminal/python migration authority from SBX-1.
- AIC-0 remains a roadmap contract in `docs/agent_interconnect/AIC_ROADMAP.md`; no dedicated AIC-0 production issue/CLAIM was found. Therefore stable `agent_instance_id` production remains a hard dependency for APR-P0 identity binding and CTX Agent-private Memory.
- GAME-AUTO-CLIENT #221 remains the production owner for game-local FAST_CONTROL implementation under `cl/src/game_automation/**`; only GAC-0/1/2 are canonical today. GAC-3+ remains future separately gated work.
- AE, CTX, AAT, TBO/UBQ, CAS, Tools/provider and CL-UI ownership boundaries remain unchanged.

Revalidated ordering:

```text
APR-C0-A replacement docs
  -> independent current-main cross-track FINAL
  -> APR-C0-A canonical integration
  -> APR-C0-B one-path APR namespace registration
  -> AIC-0 canonical stable Agent-instance identity + applicable #156 foundations
  -> APR-P0 profile/execution binding
  -> APR-X1 serialized event-sequencer/execution lanes
  -> APR-FC1 / APR-RT1 / APR-CU1 independently gated
  -> APR-MD1
  -> APR-Q1
```

No production PRE-CLAIM, schema/migration authority, or merge authority is created by this revalidation.
