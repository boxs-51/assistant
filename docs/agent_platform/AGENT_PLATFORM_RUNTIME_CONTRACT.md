# Agent Platform Runtime Contract

## Status

- Tracker: Issue #278 / APR
- Contract workspace: Issue #279 / APR-C0-A
- Policy: Issue #85 v2.5
- Audit baseline: `main@e2395dabdac3c45e4b97cbc6a7ee51e08ab46d72`
- Class: documentation / contract only
- Production authority: NONE
- Production CLAIM: NONE
- Merge authority: NONE

This contract defines the long-term Agent platform model, including stable Agent identity, runtime profiles, specialized execution surfaces, fast-control systems and concurrent reasoning/action/response.

It does not transfer implementation authority from AE, CTX, AIC, AAT, #156/DCS, Tools, CAS, UBQ, TBO, provider or client tracks.

---

## 1. Canonical identity model

The platform MUST distinguish four identities.

### 1.1 AgentDefinition / AgentTemplate

A reusable blueprint.

Owns configuration such as:
- name;
- goal;
- instruction/persona;
- default model policy;
- maximum capability policy/envelope reference;
- Skill policy/envelope reference;
- supported runtime profiles;
- delegation defaults;
- Memory defaults/preferences.

It is not a user-owned durable Memory identity.

### 1.2 AgentInstance

One durable Agent belonging to one user.

Future canonical identity:

```text
(owner_user_id, agent_instance_id)
```

Properties:
- stable across sessions and reconnects;
- stable across client reinstall/reconnection where ownership remains valid;
- bound to a versioned AgentDefinition/Template;
- anchor for Agent-private Memory;
- anchor for Agent-owned automation/subscriptions;
- lifecycle separately governed.

AIC-0 / Agent registration ownership remains authoritative for freezing the actual persistent identity contract.

### 1.3 AgentExecution

One run of an Agent.

Existing AE concepts remain authoritative:

```text
execution_id
session_id
task_id?
branch_id?
parent_execution_id?
retry/base/checkpoint lineage
revision/state/wait/recovery ownership
```

Future execution binding may include `agent_instance_id`.

IMPORTANT:

```text
AgentExecution.owner_instance_id
!=
agent_instance_id
```

The current `owner_instance_id` field is an AE lease/recovery worker-owner identity and MUST NOT be repurposed as Agent identity.

### 1.4 RuntimeSession / EnvironmentSession

An execution-attached interaction/environment session.

Examples:
- realtime voice/audio session;
- fast-control/game session;
- computer-use browser/desktop environment;
- media generation job/session.

A RuntimeSession is not Agent identity and is not a Memory owner.

---

## 2. Agent core ownership

The Agent core SHOULD own:

1. task interpretation;
2. goal-directed decision loop;
3. context consumption;
4. selected Skill consumption;
5. DCS-selected Tool visibility;
6. inference/decision requests;
7. Tool/Sub-Agent invocation requests;
8. delegation/fork planning;
9. progress/result synthesis;
10. wait/pause/resume/cancel handoff;
11. Human-in-the-Loop request handoff;
12. provenance/correlation to Task/Branch/Execution;
13. coordination of observations and results for the current execution.

The Agent core SHOULD NOT own:
- physical Tool algorithms;
- image/video model implementations;
- browser/desktop driver implementation;
- event scheduler/subscription storage;
- durable Memory persistence/retrieval authority;
- CAS bytes/lifecycle;
- UBQ quota;
- Task lifecycle policy;
- physical routing/sandbox implementation;
- UI rendering.

Canonical principle:

```text
Agent decides WHAT / WHY.
Skill guides HOW.
DCS decides WHAT IS VISIBLE NOW.
Tool performs an executable operation.
Specialized runtime provides a continuing interaction/environment surface.
Domain subsystems own durable authority.
```

---

## 3. Specialist Agents are profiles, not new fundamental kinds

Do not create one fundamental runtime kind for each application domain.

Examples:
- ResearchAgent;
- CodingAgent;
- DocumentAgent;
- ImageCreatorAgent;
- VideoCreatorAgent;
- AutomationAgent;
- GameAgent;
- ComputerOperatorAgent;
- LiveAssistantAgent.

These SHOULD normally be composed from:

```text
AgentDefinition
+ goal/instruction
+ Skills
+ maximum capability envelope
+ runtime profile(s)
+ model policy
+ delegation policy
+ Memory policy/defaults
```

The underlying Agent identity/execution model remains the same.

---

## 4. Runtime profiles

Runtime profiles describe execution behavior. They are not capability grants.

### 4.1 STANDARD

Turn/iteration-based text or multimodal work.

Typical flow:

```text
context
-> inference
-> Tool/Sub-Agent calls
-> results
-> next inference
-> final response
```

### 4.2 REALTIME

Low-latency duplex interaction.

Characteristics:
- streaming input/output;
- audio/text and future live observations;
- barge-in/interruption;
- partial response;
- realtime Tool events;
- bounded realtime session state.

REALTIME is a transport/interaction profile. It does not imply high-frequency control semantics.

### 4.3 FAST_CONTROL

High-frequency, deadline-sensitive decision systems.

Suitable examples:
- game Agents;
- rapidly changing simulations;
- low-risk interactive control systems;
- tactical reactive systems.

Required properties:
- observation revision/freshness identity;
- bounded fast decision latency;
- stale-decision rejection;
- preemption by newer observations;
- fast-policy state on the critical path;
- session-scoped/preselected capability set;
- deep reasoning off the critical path;
- revalidation of deep-strategy results before adoption.

FAST_CONTROL MUST NOT require a deep LLM roundtrip on every state update.

For safety-critical physical systems, the hard realtime controller remains deterministic/validated. LLM output is limited to goals, constraints, diagnosis or strategy unless a separately audited safety authority says otherwise.

### 4.4 COMPUTER_INTERACTIVE

Persistent observe/reason/act environment.

Typical loop:

```text
observe screen/DOM/environment
-> decide
-> pointer/keyboard/browser/window action
-> observe again
```

Requires:
- environment/session identity;
- bounded observation representation;
- privileged-action approvals/policy;
- no ambient host authority;
- fresh-state validation before side effects where necessary.

Existing Desktop/Window Tools are executable primitives, not the complete Computer Use runtime.

### 4.5 BACKGROUND

Agent activation without an active foreground conversation.

Typical source:
- AAT schedule;
- event trigger;
- Task continuation.

BACKGROUND reuses the canonical Agent execution model. It is not a separate brain.

### 4.6 MEDIA_ORCHESTRATED

Agent specialized in image/video/audio production.

Media engines remain Tool/job/provider implementations.

Examples:
- image generation/editing;
- video generation/transcoding;
- audio generation;
- composition/rendering.

---

## 5. Operation class taxonomy

Use the smallest execution primitive that matches the problem.

### Ordinary Tool

Use for one bounded model-selectable operation:

```text
web.search
document.read
image.generate
image.transform
chart.render
```

### Long-running Job Capability

Use when one logical operation has long progress/cancellation/result lifecycle:

```text
video.generate
large media render
transcode
large conversion
```

A long operation is not automatically a new Agent runtime.

### Specialized Runtime

Use when stateful continuing interaction is intrinsic:

```text
REALTIME session
FAST_CONTROL session
COMPUTER_INTERACTIVE environment
```

### Automation subsystem

Scheduling/events/wakeups remain AAT-owned.

### Agent communication

Agent directory/messages/communication remain AIC-owned.

---

## 6. Memory boundary

APR consumes CTX's three-zone model:

```text
USER_PROFILE
USER_WIDE_SESSION_HISTORY
AGENT_PRIVATE_MEMORY
```

Agent-private Memory is future-bound to:

```text
owner_user_id
+
agent_instance_id
```

Rules:
- same template for two users produces separate Agent instances;
- two Agent instances of one user do not share private Memory by default;
- RuntimeSession IDs do not own Memory;
- Tool outputs do not become Memory automatically;
- media/assets remain CAS-owned;
- Memory promotion/retrieval/Working Set selection remain CTX authority.

For FAST_CONTROL, durable Memory retrieval MUST NOT normally sit in the high-frequency critical path.

Preferred flow:

```text
CTX Memory
-> strategic synthesis / policy refresh
-> FastPolicyState
-> fast decision loop
```

---

## 7. Concurrent execution-lane model

Canonical target:

```text
                         +--> DEEP REASONING LANE -- strategy/advice -----+
                         |                                                |
                         +--> FAST DECISION LANE ---- deadline decision ---+
                         |                                                |
OBSERVATION LANE --------+--> ACTION LANE -------- Tool/Sub-Agent work ----+--> EVENT SEQUENCER
                         |                                                |
                         +--> RESPONSE LANE ------ provisional stream -----+
                         |                                                |
                         +--> MEMORY/LEARNING LANE - async promotion ------+
```

Concurrency is allowed in work, observations and presentation.

Canonical state ownership remains serialized.

### 7.1 Observation lane

Accepts:
- Tool result/progress;
- user input;
- audio/video/live observations;
- game/control observations;
- computer environment observations;
- sub-Agent messages;
- approval/cancellation/dependency events.

When freshness matters, observations should carry:

```text
observation_id
revision / sequence
observed_at
source
validity metadata
```

### 7.2 Deep reasoning lane

May use:
- large/deep LLM;
- delegated Strategy Agent;
- expensive analysis;
- historical Memory/context.

Outputs SHOULD be advisory state:

```text
StrategyHint
  hint_id
  based_on_revision
  created_at
  valid_until?
  confidence?
  priority
  policy_delta / advice
```

It MUST NOT directly execute a stale action when canonical state has advanced.

### 7.3 Fast decision lane

Runs under explicit latency/deadline.

May use:
- small/fast model;
- local learned policy;
- deterministic policy;
- rules/controller.

Decision contract candidate:

```text
FastDecision
  decision_id
  based_on_revision
  selected_action
  created_at
  action_deadline
  priority
  preemptible
```

### 7.4 Action lane

May run Tools/Sub-Agents concurrently within existing limits.

A result is an observation until incorporated into a committed state transition.

### 7.5 Response lane

May stream:
- progress;
- partial text/audio;
- Tool status;
- user-visible provisional state.

Partial output MUST NOT independently mutate Agent execution authority.

### 7.6 Memory/learning lane

May asynchronously:
- prepare summaries;
- propose Memory promotion;
- update learned/strategic state.

Durable CTX mutations still require CTX authority.

### 7.7 Event sequencer

The Event Sequencer is the only canonical commit point for execution revision/state transitions.

Invariant:

> Parallel work is permitted; parallel ownership of canonical Agent state is not.

---

## 8. FAST_CONTROL contract

### 8.1 ObservationRevision

Every fast-control action must be traceable to the state it observed.

Conceptual:

```text
ObservationRevision
  session_id
  revision
  observed_at
  source_clock?
  state_digest?
```

### 8.2 Freshness

A decision may require:

```text
current_revision == based_on_revision
```

or a bounded tolerance/freshness policy.

If the action target disappeared or newer safety/high-priority information arrived, execution must reject/revalidate/preempt the action.

### 8.3 Preemption

Higher-priority/newer observations may invalidate an uncommitted decision.

Examples:
- incoming attack;
- target no longer exists;
- environment changed;
- user interrupt;
- approval revoked;
- session reset.

### 8.4 FastPolicyState

A session-local materialization of currently usable strategy.

Conceptual:

```text
FastPolicyState
  session_id
  revision
  policy_version
  strategy_hints[]
  tactical_constraints[]
  expires_at?
```

It is not CTX Memory.

### 8.5 Session-scoped capability selection

Full DCS/schema assembly should not run on every high-frequency tick.

Preferred:

```text
session start / phase transition
-> DCS/auth/routing
-> bounded FastControlWorkingSet

tick/revision loop
-> reuse bounded WorkingSet
-> fast decision
-> current-state validation
-> action
```

WorkingSet refresh occurs on:
- phase change;
- authority change;
- capability availability change;
- explicit expansion/reselection;
- session restart.

### 8.6 Fast/slow model split

One Agent may use different reasoning classes:

```text
FAST PATH
observation
-> fast model/local policy
-> action decision

SLOW PATH
state summary/history
-> deep model/delegated Strategy Agent
-> StrategyHint/policy update
-> revalidate
-> FastPolicyState
```

Different model lanes do not imply different Agent identities.

Use a child Agent when the delegated work has an independent goal/context/execution lifecycle.

---

## 9. Image, video and media systems

### Image

Default:

```text
Agent
-> DCS
-> image.generate / image.transform / image.inspect
-> provider implementation
-> CAS/F7-T when durable
```

No dedicated Image Agent runtime is required by default.

### Video

Prefer long-running job capability:

```text
video.generate
-> job/progress
-> cancellation/status
-> durable result
```

A media-specialist Agent may orchestrate multiple jobs.

### Media iterative workflows

A MediaCreator Agent is usually:

```text
one AgentInstance
+ MEDIA_ORCHESTRATED profile
+ media Skills
+ image/video/audio Tool envelope
```

not a new fundamental runtime kind.

---

## 10. Computer Use

Computer Use requires specialized runtime/session because observations and actions are stateful.

Future contracts should separate:

```text
EnvironmentObservation
EnvironmentAction
EnvironmentSession
ApprovalState
ActionFreshness
```

Desktop/Window/browser Tool implementations remain Tool-owned.

Computer Use does not grant ambient OS authority.

---

## 11. Realtime interaction

The current canonical inference interface is one non-streaming turn:

```text
InferencePort.complete(request) -> InferenceResponse
```

Realtime requires a separately frozen streaming/provider/session contract.

Do not fake realtime semantics by spawning uncontrolled concurrent calls to the current non-streaming inference interface.

---

## 12. AgentDefinitionV2 candidate

Conceptual only:

```text
AgentDefinitionV2
  definition_id
  version
  name
  goal
  instruction

  capability_policy
  skill_policy

  supported_runtime_profiles
  supported_interaction_modes

  model_policy
  delegation_policy
  memory_defaults
```

### AgentInstance

```text
AgentInstance
  agent_instance_id
  owner_user_id
  definition_id
  definition_version
  lifecycle_state
  instance_policy_overrides
  created_at
```

### AgentExecutionBinding

```text
AgentExecutionBinding
  execution_id
  agent_instance_id?
  active_runtime_profile
  runtime_session_id?
  interaction_channel
```

All profile/policy fields are descriptive ceilings/preferences unless an owning authorization system grants access.

---

## 13. Cross-track authority

- AE: durable execution, revision, branch, retry, recovery and execution concurrency.
- #156 / #159 / #160: Agent-only entry, DCS and Tool WorkingSet.
- SKV2 #274: Skill knowledge/activation.
- CTX #15: profile/history/Agent-private Memory and model context.
- AIC: stable Agent-instance identity and inter-Agent communication.
- AAT: durable schedules/events/automation bindings.
- TBO: Task lifecycle eligibility.
- UBQ: renewable resource admission/accounting.
- Tools/PTC/provider: logical/physical executable operations.
- CAS #74: durable assets/media.
- CL-UI #242: user-facing interaction/presentation.

No reference transfers authority.

---

## 14. Final invariants

```text
AgentDefinition != AgentInstance
AgentInstance != AgentExecution
AgentExecution != RuntimeSession

FAST_CONTROL != REALTIME
FAST_CONTROL != hard realtime safety controller

StrategyHint != executable stale action
FastPolicyState != durable CTX Memory

Parallel work != parallel canonical state ownership

Specialist Agent != new fundamental Agent kind by default
```
