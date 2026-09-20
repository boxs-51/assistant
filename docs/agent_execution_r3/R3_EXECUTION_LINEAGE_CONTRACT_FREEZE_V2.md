# R3 — Execution Lineage Contract Freeze v2

**Repository:** `boxs-51/assistant`  
**Original freeze baseline:** `e5f7898667556b120261663d8c6d712ddb16363d`  
**Implementation audit baseline:** `340d035b837d8c52d9cf26bff9db9f2678ece964`  
**Depends on:** R2.1 Stabilization Gate PASS  
**Document status:** FROZEN CONTRACT + D0 provenance clarification  
**Implementation status:** **A1→C3 IMPLEMENTED; D0→D3 EXIT GATE PENDING**

---

## 1. Purpose

R3 freezes the identity and lineage rules for Agent executions before implementation. The immediate defect is the nested-Agent path: `AgentCapabilityDriver` currently reuses `CapabilityExecutionContext.execution_id` as the child Agent execution ID. That ID belongs to the caller execution.

Canonical delegation:

```text
Parent AgentExecution E1
    |
    +-- CapabilityInvocation I1
    |       execution_id = E1
    |
    +-- Child AgentExecution E2
            execution_id = NEW
            parent_execution_id = E1
            causation_id = I1
```

The three identities are independent:

```text
E1 != I1
E1 != E2
I1 != E2
```

R3 establishes representation and propagation only. It does not implement retry scheduling, fork behavior, TaskBranch persistence, resume claims, or TaskBudget.

---

## 2. Frozen vocabulary

### 2.1 Execution

Exactly one call to `AgentRuntime.execute()` maps to one durable `AgentExecution` identity.

```text
1 AgentRuntime.execute()
=
1 execution_id
=
1 durable AgentExecution row
```

### 2.2 Capability invocation

`invocation_id` identifies one capability invocation owned by the execution that requested it.

For nested Agent delegation:

```text
I1.execution_id = E1
```

The invocation never changes ownership to E2.

### 2.3 Delegation lineage

```text
E2.parent_execution_id = E1
```

`parent_execution_id` means only direct Agent-to-Agent delegation.

### 2.4 Retry lineage

Future retry behavior creates a new execution:

```text
E3.retry_of_execution_id = E2
```

Retry is not parent/child delegation.

### 2.5 Fork/recovery lineage

Future fork/recovery may use:

```text
base_execution_id
base_checkpoint_id
```

These fields are not delegation lineage.

### 2.6 Resume

Resume preserves execution identity:

```text
WAITING E1 -> RUNNING E1
```

Resume does not create E2 and does not set parent/retry/fork lineage.

---

## 3. Frozen invariants

### R3-I01 — child Agent receives a new execution ID

```text
child.execution_id != parent.execution_id
```

### R3-I02 — capability invocation remains owned by caller E1

```text
CapabilityInvocation.execution_id = E1
```

### R3-I03 — `parent_execution_id` is delegation-only

Do not use it for retry, fork, resume, recovery, ordinary tool calls, or transport reconnect.

### R3-I04 — retry uses `retry_of_execution_id`

R3 introduces the field only. R9 owns retry behavior.

### R3-I05 — fork/recovery fields are representation-only in R3

R3 may add nullable `branch_id`, `base_execution_id`, and `base_checkpoint_id`; it must not create TaskBranch rows or fork APIs.

### R3-I06 — resume retains the same execution and lineage

No lineage field changes solely because an execution resumes.

### R3-I07 — invocation/tool IDs are never execution IDs

Prohibited:

```text
child_execution_id = parent_execution_id
child_execution_id = invocation_id
child_execution_id = tool_call_id
```

### R3-I08 — AgentRuntime remains durable execution authority

Entry boundaries allocate candidate execution IDs. `AgentRuntime` owns durable creation/start validation.

### R3-I09 — lineage identifiers are typed fields

Task/branch/execution lineage must not exist only in generic `metadata`.

### R3-I10 — `connection_id` is routing affinity, not lineage

A current connection may be supplied to a child as an ephemeral routing hint. It never defines ancestry and never locks the entire child Agent to remote execution.

---

## 4. Identifier authority matrix

| Identifier | Meaning | Authority | Child propagation |
|---|---|---|---|
| `user_id` | authenticated principal | auth | same principal by default |
| `client_id` | stable client installation | CL | routing/context only |
| `connection_id` | one live WS generation | CL/SE connection runtime | optional routing affinity only |
| `session_id` | conversation/session | session domain | yes |
| `task_id` | logical task/objective | task runtime | yes |
| `branch_id` | Task solution path | future TaskBranch runtime | propagate if already non-null; never fabricate |
| `execution_id` | one AgentRuntime run | Agent execution boundary | child gets NEW value |
| `parent_execution_id` | direct delegating Agent | child creation boundary | set to E1 |
| `retry_of_execution_id` | retry ancestry | future retry runtime | no automatic propagation |
| `base_execution_id` | fork/recovery origin | future branch/recovery runtime | no automatic propagation |
| `base_checkpoint_id` | fork/recovery checkpoint | future branch/recovery runtime | no automatic propagation |
| `invocation_id` | capability invocation | CapabilityRuntime | remains attached to E1 |
| `tool_call_id` | model/provider tool call | inference/tool layer | not an execution identity |
| `correlation_id` | logical execution-tree correlation | root request/task | yes |
| `trace_id` | distributed trace identity | tracing layer | same trace across E1→I1→E2 |
| `causation_id` | immediate causal event/invocation | execution boundary | child gets I1 for delegation |
| `request_id` | top-level transport/request correlation | transport | same top-level request for synchronous delegation |
| `workflow_id` | workflow grouping | workflow runtime | yes |

---

## 5. Trace, causation, and request semantics

This section resolves the former trace/span ambiguity.

### 5.1 `trace_id`

`trace_id` means only distributed trace identity.

For synchronous delegation:

```text
E1.trace_id = TR1
I1.trace_id = TR1
E2.trace_id = TR1
```

R3 does not introduce `span_id`. If tracing later needs parent/child spans, add separate fields such as `span_id` / `parent_span_id`.

### 5.2 `causation_id`

For child Agent E2 created by invocation I1:

```text
E2.causation_id = I1
```

This gives a durable exact link from the child execution to the specific delegation invocation without changing invocation ownership.

### 5.3 `request_id`

For synchronous delegation within one top-level request:

```text
E2.request_id = E1.request_id
```

An asynchronously scheduled execution may receive another request ID in a future scheduling phase. R3 does not redefine that future behavior.

---

## 6. Target `AgentExecution` domain schema

Target semantic fields:

```text
AgentExecution:
    execution_id
    session_id
    agent_id

    task_id | null
    branch_id | null

    parent_execution_id | null
    retry_of_execution_id | null
    base_execution_id | null
    base_checkpoint_id | null

    correlation_id

    state
    wait_reason
    revision

    request
    result
    error
    timestamps...
```

Important scope decision:

```text
R3 DOES add first-class SQL/domain lineage fields:
    branch_id
    retry_of_execution_id
    base_execution_id
    base_checkpoint_id

R3 DOES NOT promote these existing context diagnostics to dedicated SQL columns:
    request_id
    workflow_id
    trace_id
    causation_id
```

`request_id`, `workflow_id`, `trace_id`, and `causation_id` remain first-class typed fields on `AgentExecutionContext` and are durably round-tripped through `context_state` in R3.

This resolves the v1 schema-vs-database mismatch.

---

## 7. Target `AgentExecutionContext`

Existing fields remain authoritative. R3 adds:

```text
branch_id: str | null
retry_of_execution_id: str | null
base_execution_id: str | null
base_checkpoint_id: str | null
```

The context must continue to carry:

```text
execution_id
agent_id
session_id
task_id
parent_execution_id
correlation_id
trace_id
causation_id
request_id
workflow_id
connection_id
identity
limits
```

### Context persistence rule

Creation and resume/reconstruction must round-trip every lineage value. No lineage field may disappear after process restart.

---

## 8. Target `CapabilityExecutionContext`

R3 promotes caller lineage out of generic metadata.

```text
CapabilityExecutionContext:
    identity

    execution_id        # caller E1
    invocation_id       # I1
    caller_agent_execution_id | null

    session_id
    task_id | null
    branch_id | null

    correlation_id | null
    trace_id | null
    request_id | null
    workflow_id | null

    connection_id | null

    deadline
    attempt
    metadata
    cancellation_event
```

Semantics:

```text
CapabilityExecutionContext.execution_id = caller E1
```

For Agent-owned capability calls:

```text
caller_agent_execution_id = E1
caller_agent_execution_id == execution_id
```

For DIRECT/MCP/HTTP/non-Agent callers:

```text
caller_agent_execution_id = null
```

`AgentCapabilityDriver` may set `E2.parent_execution_id` only from this typed
provenance field. A synthetic capability execution ID is never Agent ancestry.

An Agent driver must never reinterpret this as child E2.

New arguments to `CapabilityRuntime.execute_capability()` must remain optional so DIRECT/MCP/non-Agent call-sites remain source-compatible.

---

## 9. `CapabilityInvocation` semantics

Keep:

```text
CapabilityInvocation.invocation_id = I1
CapabilityInvocation.execution_id = E1
CapabilityInvocation.correlation_id = C1
CapabilityInvocation.trace_id = TR1
```

Do not overwrite `execution_id` with E2.

R3 does **not** require a new `child_execution_id` SQL column. Exact invocation→child linkage is available through the child execution's durable:

```text
parent_execution_id = E1
context_state.causation_id = I1
```

A dedicated indexed `child_execution_id` may be introduced later only if a demonstrated query path requires it.

---

## 10. Canonical Agent execution ID allocator

R3 freezes an injectable allocator contract:

```text
AgentExecutionIdFactory.new_id() -> str
```

Required users:

```text
WorkflowRuntime root Agent execution
MultiAgentCoordinator root Task execution
AgentCapabilityDriver child Agent execution
future retry/fork execution creators
```

Not users:

```text
DirectChat non-Agent execution IDs
CapabilityInvocation IDs
tool_call IDs
checkpoint IDs
ResumeClaim IDs
```

Requirements:

```text
globally unique under repository policy
independent from invocation/tool/parent IDs
injectable for deterministic tests
one shared allocation policy across root and child Agent execution paths
```

The factory allocates identity only. It does not create durable rows; `AgentRuntime` remains the durable execution authority.

---

## 11. Delegated child propagation contract

Given E1 → I1 → E2:

| Child E2 field | Required value |
|---|---|
| `execution_id` | NEW E2 |
| `parent_execution_id` | E1 |
| `session_id` | E1 session |
| `task_id` | E1 task |
| `branch_id` | E1 branch if non-null |
| `correlation_id` | E1 correlation |
| `trace_id` | E1 trace |
| `causation_id` | I1 |
| `request_id` | E1 request for synchronous delegation |
| `workflow_id` | E1 workflow |
| `identity` | same authorized principal by default |
| `connection_id` | optional current routing affinity only |
| `retry_of_execution_id` | null for fresh delegation |
| `base_execution_id` | null for fresh delegation |
| `base_checkpoint_id` | null for fresh delegation |

`invocation_id=I1` may also remain in compatibility/diagnostic metadata, but the typed `causation_id` is the semantic link.

---

## 12. Canonical call flow — root Agent

```text
HTTP/chat/task boundary
    |
    | AgentExecutionIdFactory.new_id()
    v
E1
    |
    v
AgentExecutionContext(E1)
    |
    v
AgentRuntime.execute(E1)
    |
    +-- durable AgentExecution E1 CREATED
    +-- lifecycle CAS -> RUNNING
    +-- iterations...
```

Root:

```text
parent_execution_id = null
retry_of_execution_id = null
base_execution_id = null
base_checkpoint_id = null
branch_id = supplied if an existing future TaskBranch already owns it, else null
```

---

## 13. Canonical call flow — MultiAgentCoordinator

```text
Task T1
  |
  | allocator -> E1
  v
MultiAgentCoordinator
  |
  +-- AgentExecution control-plane view uses E1
  |
  v
execute_registered_agent_task(
    execution_id=E1,
    task_id=T1,
    ...
)
  |
  v
AgentExecutionContext(E1)
  |
  v
AgentRuntime.execute(E1)
```

The bridge must never allocate a second unrelated E1 after the coordinator has supplied one.

---

## 14. Canonical call flow — nested Agent delegation

```text
Parent AgentRuntime E1
    |
    | model tool call tc1
    v
ToolExecutionRequest
    execution_id=E1
    invocation_id=I1
    tool_call_id=tc1
    |
    v
CapabilityToolExecutionAdapter
    |
    | explicit caller lineage
    v
CapabilityRuntime.execute_capability
    |
    +-- CapabilityInvocation I1
    |      execution_id=E1
    |
    v
CapabilityExecutionContext
    execution_id=E1
    invocation_id=I1
    task_id=T1
    branch_id=B1/null
    correlation_id=C1
    trace_id=TR1
    request_id=R1
    workflow_id=W1
    |
    v
AgentCapabilityDriver
    |
    | AgentExecutionIdFactory.new_id()
    v
Child E2
    |
    v
AgentExecutionContext
    execution_id=E2
    parent_execution_id=E1
    task_id=T1
    branch_id=B1/null
    correlation_id=C1
    trace_id=TR1
    causation_id=I1
    request_id=R1
    workflow_id=W1
    |
    v
AgentRuntime.execute(E2)
```

---

## 15. `connection_id` boundary

`connection_id` is not lineage.

A child may inherit the currently selected client connection only when required as a routing affinity for a remote CLIENT capability path.

It must not imply:

```text
child Agent can execute only on that client
server capabilities are blocked
connection generation is part of ancestry
a reconnect creates a new execution
```

Hard affinity applies to an individual selected remote invocation, not to the existence of the whole Agent execution.

---

## 16. Retry representation — behavior deferred

Example:

```text
E1 parent
  |
  +-- E2 child FAILED
        |
        +-- future retry -> E3
```

Future correct representation:

```text
E3.parent_execution_id = E1
E3.retry_of_execution_id = E2
```

Both dimensions coexist. Neither field substitutes for the other.

R3 does not create E3 automatically.

---

## 17. Fork representation — behavior deferred

Future R8/R9 example:

```text
B1 / E1 / checkpoint C5
    |
    +-- fork
          |
          v
        B2 / E2
```

Representation:

```text
E2.branch_id = B2
E2.base_execution_id = E1
E2.base_checkpoint_id = C5
```

R3 must not fabricate B2 or create TaskBranch persistence.

---

## 18. SQL persistence target

`agent_executions` adds nullable:

```text
branch_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
```

Recommended indexes:

```text
parent_execution_id       # existing/retain
task_id                   # existing/retain
branch_id
retry_of_execution_id
base_execution_id
```

`base_checkpoint_id` may remain unindexed in R3 unless an actual query path needs it.

No R3 migration for dedicated:

```text
trace_id
request_id
workflow_id
causation_id
```

Those stay in durable `context_state` for R3.

No R3 tables for:

```text
agent_task_branches
agent_execution_checkpoints
agent_resume_claims
TaskBudget
retry scheduler
fork resolution
```

---

## 19. Durable reconstruction contract

`DurableAgentStore.resume_execution()` or equivalent must reconstruct:

```text
task_id
branch_id
parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id

correlation_id
trace_id
causation_id
request_id
workflow_id
```

Source:

```text
dedicated AgentExecution columns for execution lineage
context_state for trace/request/workflow/causation diagnostics
```

R3 must not redesign R7 checkpoint-directed resume.

---

## 20. Event correlation contract

`CorrelationContext` should gain nullable:

```text
task_id
branch_id
```

Keep:

```text
execution_id
parent_execution_id
correlation_id
trace_id
causation_id
request_id
iteration_id
tool_call_id
invocation_id
```

For child events:

```text
execution_id = E2
parent_execution_id = E1
task_id = T1
branch_id = B1/null
correlation_id = C1
trace_id = TR1
causation_id = I1
```

Events are observability outputs, not lifecycle authorities.

---

## 21. Cancellation boundary

R3 propagates the existing cancellation context/event reference through child creation so the child is not born into an unrelated cancellation domain.

R5 owns complete cancellation-tree semantics, task-wide budget/cancellation accounting, cycle prevention, and fan-out control.

R3 must not create a competing cancellation system.

---

## 22. Required production call-site behavior

### Agent root entry

- `WorkflowRuntime`: use `AgentExecutionIdFactory` for root Agent IDs.
- `MultiAgentCoordinator`: use the same factory.
- `main.py::execute_registered_agent_task`: consume the supplied execution ID; do not allocate another.

### Capability path

- `CapabilityToolExecutionAdapter`: pass typed task/branch/correlation/trace/request/workflow lineage.
- `CapabilityRuntime.execute_capability`: accept those optional values and build typed `CapabilityExecutionContext`.
- `CapabilityInvocation.execution_id`: remain E1.

### Child Agent path

- `AgentCapabilityDriver`: allocate E2, set parent E1, set causation I1.
- `LazyAgentCapabilityDriver`: inject the same allocator into the delegate.
- capability HTTP Agent registration wiring: construct `AgentCapabilityDriver` with the canonical allocator.

### Composition/skills

- workflow composition steps propagate caller lineage.
- executable skills remain inside caller execution E1 and never create E2.

---

## 23. Prohibited implementation patterns

Reject any R3 patch containing:

```text
child execution_id = context.execution_id
child execution_id = invocation_id
parent_execution_id used for retry
parent_execution_id used for fork
branch_id invented when no branch exists
trace_id replaced with a span ID
correlation_id replaced with invocation_id
request_id replaced with child execution_id
lineage stored only in metadata
connection_id treated as permanent execution ancestry
resume creating a new execution
DIRECT chat forced into AgentExecutionIdFactory
```

---

## 24. Required test evidence

### Contract tests

```text
E1 != E2
I1 != E1
I1 != E2

CapabilityInvocation(I1).execution_id == E1

E2.parent_execution_id == E1
E2.task_id == E1.task_id
E2.branch_id == E1.branch_id when non-null
E2.correlation_id == E1.correlation_id
E2.trace_id == E1.trace_id
E2.causation_id == I1
E2.request_id == E1.request_id for synchronous delegation

E2.retry_of_execution_id is null for fresh delegation
E2.base_execution_id is null for fresh delegation
E2.base_checkpoint_id is null for fresh delegation
```

### Durable integration

Real SQLite/DurableAgentStore must show:

```text
two distinct AgentExecution rows E1 and E2
E1 remains intact while E2 runs
E2.parent_execution_id = E1
E2 context_state causation_id = I1
independent iterations/tool calls/results use their owning execution IDs
no R2 duplicate/RUNNING startup conflict
restart reconstruction preserves lineage
```

### Regression

Existing R0–R2, capability, remote WS, direct chat, and Phase 6.9/6.10 tests remain green.

---

## 25. R3 completion gate

R3 is complete only when this graph is reconstructable from durable state without timestamp inference:

```text
Task T1
  |
  +-- E1 coordinator
       |
       +-- I1 agent-researcher invocation
       |      owner execution = E1
       |
       +-- E2 researcher execution
              parent_execution_id = E1
              causation_id = I1
              task_id = T1
              correlation_id = C1
              trace_id = TR1
```

No TaskBranch implementation is required to pass R3; `branch_id` remains nullable until R8.

---

## 26. v2 change log

This v2 resolves the pre-freeze P1 findings relevant to R3:

1. SQL target now explicitly limits new dedicated columns to branch/retry/base lineage.
2. `trace_id` is trace-only; span semantics are deferred to separate fields.
3. synchronous child `request_id` propagation is frozen.
4. `AgentExecutionIdFactory` is a concrete injectable contract.
5. `connection_id` is explicitly routing affinity, never ancestry.
6. `causation_id=invocation_id` is frozen for exact invocation→child linkage.
7. `child_execution_id` on CapabilityInvocation is not required in R3.
8. non-Agent call-sites remain source-compatible through optional capability lineage parameters.

**Status:** ready for final architecture review; no implementation has been performed.
