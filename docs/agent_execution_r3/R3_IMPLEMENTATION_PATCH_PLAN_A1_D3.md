# R3 Execution Lineage — Implementation Patch Plan A1→D3

**Repository:** `boxs-51/assistant`  
**Original planning baseline:** `e5f7898667556b120261663d8c6d712ddb16363d`  
**Current implementation baseline:** `340d035b837d8c52d9cf26bff9db9f2678ece964`  
**Contract basis:** `R3_EXECUTION_LINEAGE_CONTRACT_FREEZE_V2.md`  
**Prerequisite:** R2.1 Stabilization Gate PASS  
**Status:** **A1→C3 IMPLEMENTED; D0→D3 CLOSURE IN PROGRESS**

---

## 1. Goal

Implement R3 without mixing later phases.

Final graph to prove:

```text
Task T1
  |
  +-- E1 parent AgentExecution
       |
       +-- I1 Agent capability invocation
       |      owner execution = E1
       |
       +-- E2 child AgentExecution
              E2 != E1
              parent_execution_id = E1
              causation_id = I1
              task/correlation/trace preserved
```

R3 must not implement:

```text
TaskBranch table
retry scheduler
fork API
ResumeClaim
normalized ExecutionCheckpoint
R4 active budget
R5 TaskBudget
R6 reconciliation
R7 resume protocol
```

---

## 2. Patch dependency graph

```text
A1 -> A2 -> A3
 |     |
 v     v
B1 -> B2 -> B3
             |
             v
C1 -> C2 -> C3
             |
             v
D1 -> D2 -> D3
```

Hard rule:

```text
C3 child execution behavior cannot land
before A/B contracts and persistence propagation exist.
```

Each slice must be reviewable and testable independently where possible.

---

# Group A — Domain, persistence, event representation

## A1 — Domain lineage schema + AgentExecutionContext

### Objective

Introduce representation only; no child-allocation behavior yet.

### Files

```text
se/src/domain/schemas/agent_execution.py
se/src/runtimes/agent/contracts/context.py
se/tests/architecture/test_phase5_contracts.py
new/updated focused R3 contract tests
```

### Changes

Add nullable:

```text
branch_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
```

to `AgentExecution` and `AgentExecutionContext`.

`AgentExecutionContext.create()` must accept and retain them.

Freeze existing fields:

```text
trace_id
causation_id
request_id
workflow_id
```

as typed context fields.

### Invariants

```text
parent_execution_id is delegation-only
branch_id may be null
no branch fabrication
retry/base fields have no behavior in A1
```

### Tests

```text
context create/model round-trip
AgentExecution validation with nullable lineage
parent/retry/base dimensions coexist without aliasing
```

### Exit gate A1

All existing R0–R2 contract tests pass; no runtime behavior change.

---

## A2 — SQL model + migration + durable round-trip

### Objective

Make R3 execution lineage durable.

### Files

```text
se/src/infrastructure/storage/models/sql/agent/execution.py
se/src/infrastructure/storage/migrations/sql/versions/<r3_lineage>.py
se/src/runtimes/agent/persistence.py
se/src/runtimes/agent/runtime.py
se/tests/architecture/test_roadmap_r0_r2.py
new R3 persistence tests
```

### SQL columns

```text
branch_id nullable
retry_of_execution_id nullable
base_execution_id nullable
base_checkpoint_id nullable
```

Indexes:

```text
branch_id
retry_of_execution_id
base_execution_id
```

Retain existing:

```text
task_id
parent_execution_id
```

Do not add dedicated SQL columns for:

```text
trace_id
request_id
workflow_id
causation_id
```

These remain durable through `context_state`.

### Runtime/persistence

Initial `AgentExecution` durable save must include R3 columns.

Checkpoint/context-state persistence must preserve:

```text
trace_id
causation_id
request_id
workflow_id
```

`DurableAgentStore.resume_execution()` must reconstruct all R3 lineage.

### Tests

Real SQLite:

```text
create execution with all nullable lineage fields
load row
resume/reconstruct context
assert exact round-trip
```

### Exit gate A2

Process restart/reconstruction cannot erase any R3 lineage dimension.

---

## A3 — Event correlation representation

### Objective

Make task/branch lineage observable without making events authoritative.

### Files

```text
se/src/runtimes/agent/contracts/events.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/events.py   # verify/generic mapping
se/tests/architecture/test_phase5_contracts.py
se/tests/architecture/test_phase5_10_exit_gate.py
```

### Changes

Add nullable to `CorrelationContext`:

```text
task_id
branch_id
```

AgentRuntime `_publish()` supplies:

```text
task_id=context.task_id
branch_id=context.branch_id
```

Keep:

```text
execution_id
parent_execution_id
correlation_id
trace_id
causation_id
request_id
```

### Tests

Parent/child event envelopes prove:

```text
parent event execution=E1
child event execution=E2
child.parent=E1
same task/correlation/trace
```

### Exit gate A3

EventBus serialization remains source-compatible and no consumer sees missing required fields because additions are optional.

---

# Group B — Capability caller-lineage propagation

## B1 — Typed `CapabilityExecutionContext`

### Objective

Move execution semantics out of arbitrary metadata.

### Files

```text
se/src/runtimes/capability/contracts/context.py
se/tests/architecture/test_phase3.py
se/tests/architecture/test_phase6_4_remote_client_driver.py
```

### Add optional fields

```text
task_id
branch_id
correlation_id
trace_id
```

Keep existing:

```text
execution_id     # caller E1
invocation_id    # I1
request_id
session_id
workflow_id
connection_id
```

### Source compatibility

All new fields default null so DIRECT/MCP/HTTP capability execution remains valid.

### Tests

Create contexts both:

```text
Agent-owned with lineage
non-Agent/direct with null lineage
```

### Exit gate B1

No non-Agent call-site must be forced to fabricate task/branch/execution ancestry.

---

## B2 — CapabilityRuntime API/context/invocation propagation

### Objective

Carry typed caller lineage through CapabilityRuntime while preserving invocation ownership.

### Files

```text
se/src/runtimes/capability/runtime.py
se/src/runtimes/capability/contracts/invocation.py   # verify, minimal change only if needed
se/src/infrastructure/storage/models/sql/capability/invocation.py # verify
se/src/infrastructure/storage/repositories/capability_invocations.py # verify
se/tests/architecture/test_capability_invocation_lifecycle.py
```

### API additions

Optional `execute_capability()` args:

```text
task_id
branch_id
correlation_id
trace_id
```

Build typed `CapabilityExecutionContext`.

Populate `CapabilityInvocation`:

```text
execution_id = E1
correlation_id = explicit correlation_id
trace_id = explicit trace_id
```

Metadata fallback may remain temporarily for compatibility, but explicit typed argument wins.

### Critical invariant

Never set:

```text
CapabilityInvocation.execution_id = future child E2
```

### Tests

Assert durable invocation I1:

```text
execution_id == E1
correlation_id == C1
trace_id == TR1
```

### Exit gate B2

Invocation lifecycle/CAS tests remain green.

---

## B3 — Agent tool adapter + composition propagation

### Objective

Feed typed lineage into B2 from real callers.

### Files

```text
se/src/runtimes/agent/adapters/tool.py
se/src/runtimes/capability/composition.py
se/src/runtimes/capability/drivers/skill_driver.py   # verify/diagnostic metadata
se/tests/architecture/test_phase5_adapters.py
se/tests/architecture/test_phase6_7_workflow_composition.py
```

### Agent tool adapter

Pass:

```text
execution_id=context.execution_id
invocation_id=request.invocation_id

task_id=context.task_id
branch_id=context.branch_id
correlation_id=context.correlation_id
trace_id=context.trace_id

request_id=context.request_id
session_id=context.session_id
workflow_id=context.workflow_id
connection_id=request/context routing affinity
```

### Composition

Every composed step keeps caller execution E1 and caller lineage but receives its own invocation ID according to existing CapabilityRuntime behavior.

### Skill driver

Executable skill remains inside E1. It does not create an AgentExecution child.

### Exit gate B3

Tool/skill/workflow behavior remains unchanged except typed lineage becomes available.

---

# Group C — Execution ID authority + actual child creation

## C1 — Canonical `AgentExecutionIdFactory`

### Objective

Create one injectable Agent execution identity policy.

### Proposed ownership

A small neutral runtime/domain utility, exact file chosen during implementation review, for example:

```text
se/src/runtimes/agent/ids.py
```

Conceptual contract:

```text
AgentExecutionIdFactory.new_id() -> str
```

### Requirements

```text
injectable
deterministic fake for tests
no dependence on parent/invocation/tool IDs
used only for AgentExecution identities
```

### Wiring target

Application container/bootstrap exposes one factory instance/policy.

### Tests

```text
factory uniqueness
injectable deterministic fake
no DirectChat coupling
```

### Exit gate C1

No production behavior has changed yet; allocator is available for C2/C3.

---

## C2 — Root Agent allocation migration

### Objective

Remove independent inline Agent execution ID allocation.

### Files

```text
se/src/runtimes/workflow/runtime.py
se/src/runtimes/agent/coordinator.py
se/src/main.py
se/src/application/container.py
bootstrap/main wiring
related root-Agent tests
```

### WorkflowRuntime

Replace inline root Agent UUID allocation with factory.

Root lineage:

```text
parent_execution_id=null
retry_of_execution_id=null
base_execution_id=null
base_checkpoint_id=null
```

### MultiAgentCoordinator

Use same factory for Task execution E1.

### `execute_registered_agent_task`

Consume supplied E1. Never allocate a replacement.

Pass any already-owned task/branch/lineage fields.

### Important non-goal

Do not migrate `DirectChatRuntime` IDs to this factory.

### Exit gate C2

Existing root Agent API/E2E tests remain green and still produce one durable row per runtime execution.

---

## C3 — `AgentCapabilityDriver` NEW E2 + wiring

### Objective

Fix the R3 P0 blocker.

### Files

```text
se/src/runtimes/capability/drivers/agent_driver.py
se/src/runtimes/capability/local_support_loader.py
se/src/transport/gateway/api/v1/capability_router.py
application/bootstrap wiring as required
se/tests/architecture/test_unified_capability_drivers.py
```

### Child creation

Given caller context E1/I1:

```text
E2 = AgentExecutionIdFactory.new_id()
```

Create child context:

```text
execution_id=E2
parent_execution_id=E1

session_id=context.session_id
task_id=context.task_id
branch_id=context.branch_id

correlation_id=context.correlation_id
trace_id=context.trace_id
causation_id=context.invocation_id
request_id=context.request_id
workflow_id=context.workflow_id

retry_of_execution_id=null
base_execution_id=null
base_checkpoint_id=null
```

`connection_id` may be copied only as current routing affinity; it is not ancestry.

Share current cancellation event/reference; R5 later owns full cancellation-tree semantics.

### Lazy/HTTP construction

Both current direct constructors of `AgentCapabilityDriver` must receive the same factory:

```text
LazyAgentCapabilityDriver
capability_router.register_agent_capability
```

### Result

Child terminal result returns through I1 to E1.

### Exit gate C3

The current duplicate-RUNNING conflict disappears because E2 has its own durable row.

---

# D0 — Direct AGENT provenance closure

Generic capability execution may allocate a synthetic capability
`execution_id`. That ID is not automatically a durable AgentExecution parent.

Add typed provenance:

```text
CapabilityExecutionContext.caller_agent_execution_id
```

Rules:

```text
Agent tool path:
  caller_agent_execution_id = E1
  caller_agent_execution_id == execution_id

direct/non-Agent path:
  caller_agent_execution_id = null

AgentCapabilityDriver:
  E2.parent_execution_id = caller_agent_execution_id
```

This keeps direct AGENT execution valid as a root E2 and prevents dangling
parent lineage.

# Group D — Proof and regression

## D1 — R3 contract/unit suite

### New file

```text
se/tests/architecture/test_r3_execution_lineage.py
```

### Required assertions

```text
E1 != E2
I1 != E1
I1 != E2

I1.execution_id == E1

E2.parent_execution_id == E1
E2.task_id == E1.task_id
E2.branch_id == E1.branch_id when present
E2.correlation_id == E1.correlation_id
E2.trace_id == E1.trace_id
E2.causation_id == I1
E2.request_id == E1.request_id

fresh delegation retry/base fields == null

connection_id never changes lineage semantics
non-Agent CapabilityExecutionContext remains valid with null lineage
```

Also add targeted updates to existing contract/adapter/invocation tests.

### Exit gate D1

All deterministic unit/architecture lineage contracts pass.

---

## D2 — Real durable nested-Agent integration

### New file

```text
se/tests/integration/test_r3_nested_agent_durable_lineage.py
```

### Real components

```text
AgentRuntime
CapabilityToolExecutionAdapter
CapabilityRuntime
AgentCapabilityDriver
DurableAgentStore
AgentRepository
SQLite
```

Use deterministic fake inference to force:

```text
E1 inference
-> Agent capability tool call I1
-> E2
-> E2 completes
-> result returns to E1
-> E1 second inference/final answer
```

### Durable assertions

```text
two distinct agent_executions rows
E1 not overwritten
E2.parent_execution_id == E1
E2 context_state causation_id == I1
CapabilityInvocation I1.execution_id == E1

independent iteration IDs
child tool/result ownership uses E2
parent continuation uses E1
both correlation/trace fields preserved
```

### Restart proof

Reload child context from DurableAgentStore and assert lineage survives reconstruction.

### Exit gate D2

The exact E1→I1→E2 graph is reconstructable from durable data alone.

---

## D3 — Full regression / remote transport gate

### Suites to run

At minimum:

```text
R0-R2 roadmap tests
R2.1 stabilization tests

phase5 contracts/adapters/runtime/tool coordinator
capability invocation lifecycle
unified capability drivers
workflow composition

phase6.9 connection invariant
phase6.9 Agent/client loop
phase6.9 real TCP websocket
phase6.10 true websocket Agent/client loop
phase6.11 context assembly

offline API/E2E suite
```

### Critical regression invariants

```text
Realtime envelope still uses owner execution E1 + invocation I1
CL dispatcher never allocates E2
hard connection affinity unchanged
DIRECT mode unchanged
SKILL does not create E2
resume still uses same execution ID
R3 does not create TaskBranch/ResumeClaim behavior
```

### Exit gate D3

No P0/P1 regression and all R3 durable lineage evidence passes.

---

## 3. File-by-file execution order

Recommended code-review order:

```text
A1
  agent_execution.py
  agent/contracts/context.py

A2
  sql/agent/execution.py
  migration
  agent/persistence.py
  agent/runtime.py

A3
  agent/contracts/events.py
  agent/runtime.py
  agent/events.py verify

B1
  capability/contracts/context.py

B2
  capability/runtime.py
  capability invocation contract/store verify

B3
  agent/adapters/tool.py
  capability/composition.py
  skill_driver.py verify

C1
  AgentExecutionIdFactory + container wiring

C2
  workflow/runtime.py
  agent/coordinator.py
  main.py

C3
  capability/drivers/agent_driver.py
  capability/local_support_loader.py
  capability_router.py

D1-D3
  tests only except defects discovered by regressions
```

---

## 4. Commit strategy

Keep commits small enough for rollback:

```text
commit A: R3 A1-A3 representation only
commit B: R3 B1-B3 typed propagation
commit C1: execution ID allocator + root migration
commit C2: child Agent creation fix
commit D: R3 test/exit-gate proof
```

Do not combine R2.1 fixes or R6/R7 code into these commits.

---

## 5. Review checkpoints

### After A

Question:

```text
Can every R3 lineage field round-trip without behavior change?
```

### After B

Question:

```text
Can caller E1 lineage reach AgentCapabilityDriver without metadata guessing?
```

### After C

Question:

```text
Does every AgentRuntime.execute() now receive a unique execution identity,
and does child E2 retain parent E1 + causation I1?
```

### After D

Question:

```text
Can the durable database alone reconstruct E1 -> I1 -> E2?
```

---

## 6. R3 no-go conditions

Stop implementation if any patch introduces:

```text
TaskBranch persistence
branch fabrication
retry/fork execution behavior
ResumeClaim/checkpoint normalized tables
child execution ownership on CL
connection_id as execution ancestry
CapabilityInvocation.execution_id = E2
second lifecycle authority
```

Those indicate phase leakage.

---

## 7. Final planned exit

After D3:

```text
R2.1 PASS
R3 lineage PASS
R4/R5 may proceed

R6/R7 remain blocked until their own prerequisites/gates
R8 remains blocked until R6/R7 global gate
```

No R3 code has been generated by this plan.
