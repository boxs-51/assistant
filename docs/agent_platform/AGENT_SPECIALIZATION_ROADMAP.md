# Agent Platform Specialization Implementation Roadmap

## Authority

- Tracker: #278
- C0-A: #279
- Policy: Issue #85 v2.5
- Baseline: `main@e2395dabdac3c45e4b97cbc6a7ee51e08ab46d72`
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

HOLD while PR #277 owns `docs/ROADMAP_NAMESPACE_REGISTRY.md`.

After #277 releases that path:
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
2. PR #277/SKV2-C0 resolves registry ownership
3. APR-C0-B namespace registration
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

APR-C0-B remains HOLD until `docs/ROADMAP_NAMESPACE_REGISTRY.md` is released by PR #277.

No production PRE-CLAIM follows from C0-A.
