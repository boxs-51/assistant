# Agent Execution / Continuation / Branching Roadmap v2

## Contract Freeze, Server–Client Impact, Persistence, Concurrency, Performance and Implementation Phases

**Repository:** `boxs-51/assistant`  
**Audited baseline:** `9187526a6ee7517610e22a94e157c381603e36d4`  
**Document status:** PRE-IMPLEMENTATION / CONTRACT REVIEW  
**Implementation status:** No implementation patch is defined by this document.

---

# 0. Executive Summary

This roadmap defines the target architecture for Agent lifecycle, durable pause/resume, Task branching, retry, nested Agents, remote client capability execution, reconnect, persistence, exactly-once safety, multi-worker concurrency, provider retry/fallback, crash recovery and SE↔CL protocol migration.

Core decisions:

```text
Task
    = logical objective

Branch
    = one independent path to solve a Task

Execution
    = one AgentRuntime run

Checkpoint
    = immutable durable continuation point

WAITING
    = the only resumable execution state

wait_reason
    = why execution is waiting

RESUME
    = same Task + same Branch + same execution_id

RETRY
    = same Task + same Branch + NEW execution_id

FORK
    = same Task + NEW branch_id + NEW execution_id

SUBTASK
    = NEW task_id with parent_task_id
```

Critical corrections from the previous roadmap:

1. `Branch` is **1:N Execution**, not 1:1.
2. `WAITING` pauses active execution budget; waiting has a separate TTL.
3. `parent_execution_id` is only for Agent delegation lineage.
4. Retry and fork use separate lineage fields.
5. Agent/Task/Branch mutation requires durable `revision` / compare-and-set semantics.
6. Resume must solve the case where a client side effect completed but its result was lost.
7. Branches require isolated context overlays.
8. Branching requires normalized persistence before implementation.
9. SE↔CL WAITING migration needs compatibility, not an abrupt wire break.
10. Task budgets are shared across forks/retries/children.
11. Server restart/recovery is a first-class lifecycle scenario.
12. Branch state must not duplicate Execution state unnecessarily.

---

# 1. Goals

The architecture must guarantee:

1. One `AgentRuntime.execute()` maps to exactly one `execution_id`.
2. Parent and child Agents never share an execution ID.
3. `WAITING` means resumable durable suspension.
4. Terminal executions are never resurrected.
5. A Task can outlive individual executions.
6. A Task can own multiple branches.
7. A Branch can own multiple sequential executions.
8. A fork creates a new Branch and new Execution.
9. A retry creates a new Execution in the same Branch.
10. A subtask creates a new Task.
11. Remote side effects are never blindly replayed after disconnect.
12. Resume remains correct under multi-worker/multi-instance SE.
13. Branch fan-out is bounded.
14. Branch context remains isolated until explicit aggregation.
15. CL remains compatible during WAITING protocol migration.
16. Checkpoint persistence scales beyond test-sized workloads.
17. Provider retry cannot silently consume Agent budgets.
18. All child async tasks have explicit ownership.
19. Crash recovery cannot leave zombie `RUNNING` executions.
20. CI must verify race/reconnect/restart scenarios.

---

# 2. Non-Goals for Initial Waves

The first implementation waves do not need:

- arbitrary distributed DAG orchestration;
- cross-user collaborative branches;
- automatic cross-installation resume;
- hundreds of speculative branches;
- universal exactly-once semantics for arbitrary external systems;
- automatic transcript merge;
- distributed consensus beyond DB transaction/CAS guarantees.

The architecture must not block these future capabilities.

---

# 3. Canonical Vocabulary

## 3.1 Session

A Session is the long-lived conversation or interaction container.

```text
session_id
```

A Session may contain multiple Tasks, turns, branches and executions.

`session_id` is never a transport affinity key.

## 3.2 Task

A Task is a logical objective, for example:

```text
"Research current weather"
"Analyze repository architecture"
"Generate a report"
```

A Task survives execution failure, timeout, reconnect and retry.

## 3.3 Branch

A Branch is one independent solution path inside a Task.

Canonical cardinality:

```text
Task
  1 → N Branch

Branch
  1 → N Execution
```

The Branch→Execution relation must be 1:N because retry creates a new execution in the same branch.

## 3.4 Execution

An Execution is exactly one call to `AgentRuntime.execute()`.

Invariant:

```text
1 AgentRuntime.execute()
    =
1 execution_id
    =
1 durable AgentExecution
```

## 3.5 Checkpoint

A Checkpoint is an immutable durable safe point usable for:

```text
RESUME
FORK
RETRY_FROM_CHECKPOINT
RECOVERY
```

## 3.6 Capability Invocation

A Capability Invocation is one call made by an Agent execution.

For nested Agent delegation:

```text
Parent Agent Execution E1
    ↓
CapabilityInvocation I1
    ↓
Child Agent Execution E2
```

`I1.execution_id = E1`.  
`E2.execution_id` is new.

---

# 4. Canonical Identity Contract

| Identifier | Meaning | Lifetime | Authority |
|---|---|---|---|
| `user_id` | Authenticated principal | Auth lifecycle | SE Auth |
| `client_id` | Stable client installation | App installation | CL |
| `connection_id` | One WebSocket generation | Connection | CL, validated by SE |
| `session_id` | Conversation/session | Session | Domain |
| `task_id` | Logical objective | Task | Task runtime |
| `branch_id` | Independent Task path | Branch | Task runtime |
| `execution_id` | One AgentRuntime run | Execution | AgentRuntime |
| `parent_execution_id` | Delegating Agent execution | Child execution | AgentRuntime |
| `retry_of_execution_id` | Previous execution retried | Retry lineage | Task/Agent runtime |
| `base_execution_id` | Fork/recovery origin execution | Branch lineage | Task runtime |
| `checkpoint_id` | Durable safe point | Checkpoint | AgentRuntime/continuation |
| `invocation_id` | Capability invocation | Invocation | CapabilityRuntime |
| `tool_call_id` | Provider/model tool call | Tool call | AgentRuntime |
| `request_id` | Inbound transport request | Request | Transport |
| `correlation_id` | Correlation for logical execution tree | Root request/task | Runtime |
| `trace_id` | Observability trace | Trace | Observability |
| `workflow_id` | Optional workflow group | Workflow | WorkflowRuntime |

---

# 5. Lineage Must Not Be Overloaded

## 5.1 Delegation lineage

```text
Coordinator E1
    ↓ delegates
Researcher E2
```

```text
E2.parent_execution_id = E1
```

## 5.2 Retry lineage

```text
E1 FAILED
    ↓ retry
E2 RUNNING
```

```text
E2.retry_of_execution_id = E1
```

Do not encode retry with `parent_execution_id`.

## 5.3 Fork lineage

```text
B1 / E1 / C4
    ↓ fork
B2 / E2
```

```text
B2.parent_branch_id = B1
B2.base_execution_id = E1
B2.base_checkpoint_id = C4
```

Optionally E2 may duplicate immutable origin references for query efficiency.

---

# 6. Canonical Execution State

Only:

```text
CREATED
RUNNING
WAITING
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

Remove semantic states such as:

```text
WAITING_AGENT
WAITING_FOR_CONNECTION
WAITING_FOR_HITL
WAITING_FOR_RESOURCE
```

Use:

```text
state = WAITING
wait_reason = ...
```

---

# 7. WaitReason Contract

Proposed enum:

```text
NONE
CONNECTION
HUMAN_APPROVAL
DEPENDENCY
RESOURCE
EXPLICIT_PAUSE
RECOVERY
RETRY_BACKOFF
AGENT
```

Important rule:

> `WAITING` means **durable suspension**, not every synchronous `await`.

Examples that usually remain `RUNNING`:

```text
short provider retry backoff
normal parent await child Agent while coroutine ownership remains active
normal tool execution
```

Examples appropriate for `WAITING`:

```text
connection unavailable
HITL awaiting user decision
explicit pause
durable dependency wait
server recovery
durably scheduled long retry
```

---

# 8. WAITING Invariants

```text
state == WAITING
    ⇒ wait_reason != NONE

state != WAITING
    ⇒ wait_reason == NONE/null
```

`WAITING` means:

- execution is non-terminal;
- durable state is sufficient to resume;
- active runtime work can be released;
- continuation trigger is known;
- resume authorization is evaluable;
- `execution_id` remains the same.

---

# 9. Execution State Machine

```text
CREATED
   │
   ├──────────────→ CANCELLED
   │
   └→ RUNNING
        │
        ├─────────→ COMPLETED
        ├─────────→ FAILED
        ├─────────→ CANCELLED
        ├─────────→ TIMEOUT
        │
        └─────────→ WAITING
                       │
                       ├────────→ RUNNING
                       ├────────→ FAILED
                       ├────────→ CANCELLED
                       └────────→ TIMEOUT
```

Forbidden:

```text
COMPLETED → RUNNING
FAILED    → RUNNING
CANCELLED → RUNNING
TIMEOUT   → RUNNING
```

Continuation after terminal state always uses a new execution.

---

# 10. Active / Suspended / Terminal

```text
ACTIVE:
    CREATED
    RUNNING

SUSPENDED:
    WAITING

TERMINAL:
    COMPLETED
    FAILED
    CANCELLED
    TIMEOUT
```

---

# 11. Task State

Task states:

```text
CREATED
ASSIGNED
RUNNING
WAITING
COMPLETED
FAILED
CANCELLED
```

Do not keep Task-level `WAITING_FOR_CONNECTION`.

---

# 12. Task WAITING Semantics

A Task may have multiple blocked branches:

```text
T1
├ B1/E1 WAITING(CONNECTION)
├ B2/E2 WAITING(HUMAN_APPROVAL)
└ B3/E3 FAILED
```

Therefore Task should expose:

```text
state = WAITING
wait_reasons = {CONNECTION, HUMAN_APPROVAL}
```

Per-execution `wait_reason` remains singular.

Optional UI fields:

```text
primary_wait_reason
blocked_branches[]
```

---

# 13. Task Aggregation Rules

```text
if Task explicitly CANCELLED:
    CANCELLED

elif accepted authoritative result exists:
    COMPLETED

elif any branch has CREATED/RUNNING execution:
    RUNNING

elif any branch has resumable WAITING execution:
    WAITING

elif policy still allows retry/fork:
    scheduler policy decides WAITING vs FAILED

else:
    FAILED
```

One branch failure never directly means Task failure.

---

# 14. Branch State Must Not Duplicate Execution State

Do not persist both runtime lifecycle states independently.

Recommended Branch persistence:

```text
BranchResolutionState:
    OPEN
    ADOPTED
    SUPERSEDED
    DISCARDED
    CANCELLED
```

Runtime display status is derived from current execution.

Example:

```text
B1.resolution = OPEN
B1.current_execution_id = E2
E2.state = WAITING
```

UI may show B1 as WAITING without persisting duplicate truth.

---

# 15. Canonical TaskBranch Contract

```text
TaskBranch:
    branch_id
    task_id
    parent_branch_id

    base_execution_id
    base_checkpoint_id

    current_execution_id

    resolution_state
    revision

    created_by
    reason

    created_at
    updated_at
```

One branch may contain:

```text
E1 FAILED
E2 FAILED
E3 RUNNING
```

---

# 16. Canonical AgentExecution Contract

Recommended fields:

```text
execution_id
session_id
task_id
branch_id
agent_id

parent_execution_id
retry_of_execution_id

base_execution_id
base_checkpoint_id

correlation_id
trace_id
request_id
workflow_id

state
wait_reason

revision

remaining_active_budget_seconds
wait_expires_at

result
error

created_at
started_at
updated_at
completed_at
```

---

# 17. RESUME / RETRY / FORK / SUBTASK

## RESUME

```text
same task_id
same branch_id
same execution_id
```

## RETRY

```text
same task_id
same branch_id
NEW execution_id
retry_of_execution_id = previous execution
```

## FORK

```text
same task_id
NEW branch_id
NEW execution_id
base_checkpoint_id = source checkpoint
```

## SUBTASK

```text
NEW task_id
parent_task_id = parent Task
```

---

# 18. Execution Authority

## TaskRuntime / MultiAgentCoordinator owns

```text
AgentSession
AgentTask
TaskBranch
TaskBudget
branch scheduling
branch resolution
task cancellation
task retry/fork policy
```

## AgentRuntime owns

```text
AgentExecution
AgentIteration
AgentToolCall
AgentToolResult
execution checkpoint creation
execution transitions
execution terminal state
execution-local usage
```

## CapabilityRuntime owns

```text
CapabilityInvocation
CapabilityInvocationAttempt
implementation routing
authorization
connection affinity
capability lifecycle
```

## Provider Runtime owns

```text
provider request
provider retry
provider fallback
provider task ownership
provider error normalization
```

---

# 19. Durable AgentExecution Authority

`AgentRuntime.execute(context)` must:

```text
NEW:
    CREATE AgentExecution
    validate uniqueness

RESUME:
    LOAD AgentExecution
    validate WAITING state
    validate revision/checkpoint
```

Invariant:

```text
1 AgentRuntime.execute
=
1 durable AgentExecution
```

No control-plane component may create execution X and execute runtime Y using unrelated ID.

---

# 20. Nested Agent Contract

Parent E1 calls Agent capability I1:

```text
I1.execution_id = E1
```

Child:

```text
execution_id = E2
parent_execution_id = E1
```

Child inherits:

```text
session_id
task_id
branch_id unless policy creates a branch
correlation_id
trace lineage
identity
cancellation lineage
shared TaskBudget
```

Child does not receive a fresh unlimited budget.

---

# 21. Delegation Depth / Cycle Prevention

Track:

```text
delegation_depth
agent_ancestry
```

Policies:

```text
max_delegation_depth
deny_recursive_agent_cycle
```

Example:

```text
A → B → A
```

is rejected unless recursion is explicitly supported.

---

# 22. Shared Task Budget

Recommended:

```text
TaskBudget:
    max_total_executions
    max_active_executions
    max_active_branches
    max_parallel_agents
    max_total_tool_calls
    max_total_inference_calls
    max_total_tokens
    max_total_cost
    max_delegation_depth

    used_executions
    active_executions
    used_tool_calls
    used_inference_calls
    used_tokens
    used_cost
```

Fork/retry/child Agent never resets this budget.


---

# 23. Active Budget vs Waiting TTL

A durable `WAITING` execution cannot use one simple absolute deadline.

Separate:

```text
active execution budget
```

from:

```text
waiting TTL
```

Example:

```text
active budget = 60s

RUNNING for 20s
→ remaining_active_budget = 40s

WAITING(CONNECTION) for 10 minutes

RESUME
→ remaining_active_budget = 40s
```

Waiting time does not consume active budget unless policy explicitly says otherwise.

---

# 24. Wait TTL

Every WAITING execution may have:

```text
wait_expires_at
```

Suggested policies:

```text
CONNECTION       → hours/day
HUMAN_APPROVAL   → days/configurable
EXPLICIT_PAUSE   → configurable
DEPENDENCY       → dependency policy
RECOVERY         → short recovery window
RETRY_BACKOFF    → scheduled retry time + guard window
```

Default recommendation on expiry:

```text
WAITING → TIMEOUT
```

---

# 25. Monotonic Clock Persistence

Inside one process, elapsed runtime should use `time.monotonic()`.

Do not persist raw monotonic timestamps across restart.

Persist:

```text
remaining_active_budget_seconds
```

On resume:

```text
runtime_deadline_monotonic =
time.monotonic() + remaining_active_budget_seconds
```

Wall clock is appropriate for:

```text
wait_expires_at
created_at
updated_at
```

---

# 26. Deadline Hierarchy

```text
Task budget
    ↓
Execution active budget
    ↓
Iteration budget
    ↓
Operation budget
```

Child operations cannot exceed parent remaining budget.

---

# 27. Tool Timeout

For normal one-shot TOOL:

```text
effective_timeout =
min(
    execution_remaining,
    iteration_remaining,
    tool_timeout_seconds
)
```

---

# 28. Agent / LONG_RUNNING Timeout

Nested `AGENT/LONG_RUNNING` must not use tool timeout.

```text
child_execution_budget =
min(
    parent_execution_remaining,
    delegated_agent_budget,
    task_remaining_budget
)
```

If delegated Agent budget is not introduced yet:

```text
child budget = min(parent remaining, task remaining)
```

---

# 29. Iteration Timeout

`iteration_timeout_seconds` must become actual runtime enforcement.

```text
iteration_budget =
min(
    execution_remaining,
    configured_iteration_timeout
)
```

Context build, inference and tool batch must remain inside it.

---

# 30. Short Retry Backoff vs WAITING

Do not persist every short retry as Agent WAITING.

Example:

```text
provider backoff = 1.5 seconds
coroutine remains active
```

Keep:

```text
AgentExecution.state = RUNNING
```

Use `WAITING(RETRY_BACKOFF)` only when retry is durably scheduled and runtime ownership is released.

---

# 31. Async Task Ownership

Rule:

> The component that calls `asyncio.create_task()` owns the child task until explicitly transferred.

Before owner scope exits:

```text
await task
or
cancel + await/gather
or
transfer to explicit supervisor
```

This must hold on:

```text
success
failure
timeout
cancellation
parent cancellation
server shutdown
```

Target invariant:

```text
0 "Task exception was never retrieved"
```

---

# 32. ProviderInferenceAdapter Ownership

`ProviderInferenceAdapter` creates provider tasks.

Therefore it must retrieve/cancel them even if its outer call is cancelled by:

```text
Agent timeout
iteration timeout
Task cancel
parent Agent cancellation
transport abort
```

This remains a P0 fix.

---

# 33. Cancellation Contract

## Root downward cancellation

```text
Task cancel
→ Branch execution
→ Child Agent
→ Capability invocation
→ Provider/tool
```

## Child local failure

Child failure does not automatically cancel parent.

Parent receives structured failure and may:

```text
retry
fallback
fork
continue
fail
```

## Cancel Execution

```text
cancel E1
```

terminates only E1-owned work.

Other Task branches may continue.

## Cancel Task

```text
cancel T1
```

must:

```text
cancel all active executions
invalidate pending resume claims
prevent future resume
prevent retry
prevent fork
close TaskBudget
T1 → CANCELLED
```

---

# 34. Pause Contract

Pause is not cancel.

Execution pause:

```text
RUNNING
→ safe checkpoint
→ WAITING(EXPLICIT_PAUSE)
```

Task pause:

```text
request all active branches to safe checkpoint
→ no active execution remains
→ Task WAITING
```

---

# 35. Checkpoint Contract

Recommended:

```text
checkpoint_id
execution_id
task_id
branch_id

parent_checkpoint_id

iteration

transcript_ref
transcript_version

pending_invocation_id
pending_tool_call_id
pending_capability_id

wait_reason

remaining_active_budget_seconds

origin_connection_id
current_connection_id
origin_client_id

side_effect_watermark

created_at
```

Checkpoint is immutable.

---

# 36. Safe Checkpoint Definition

A checkpoint is RESUME-safe only if runtime can reconstruct:

```text
Agent context
iteration
transcript
usage
pending invocation identities
committed tool results
remaining active budget
connection affinity metadata
```

A checkpoint is FORK-safe only if side-effect replay is also safe.

---

# 37. Side-Effect Watermark

Checkpoint lineage must know which capability outcomes are already committed.

Do not duplicate an unbounded list in every checkpoint.

Prefer durable invocation ledger lookup or compact watermark/reference.

---

# 38. Resume Must Not Replay Committed Results

Invariant:

```text
If I1 has terminal committed result,
RESUME reuses I1 result
and never executes I1 again.
```

---

# 39. Distributed Unknown Outcome Problem

Critical case:

```text
SE sends invocation I1
CL executes side effect successfully
CL prepares capability.result
network drops before SE receives result
```

SE cannot know whether:

```text
tool never executed
```

or:

```text
tool executed successfully
```

This is an **unknown outcome** and must be explicitly represented.

---

# 40. Invocation Reconciliation Contract

After connection loss:

```text
same logical invocation
→ same invocation_id
```

Preferred flow:

```text
SE:
I1 pending / outcome unknown
→ E1 WAITING(CONNECTION)

CL reconnects

SE:
reconcile I1

CL:
if terminal outcome for I1 exists:
    replay exact result/error/cancelled

SE:
commit I1 outcome
continue E1
```

---

# 41. Client TerminalOutcome Cache

Current in-process replay model is correct for reconnect within same CL process.

Required invariants:

```text
duplicate while RUNNING:
    do not execute again

duplicate after TERMINAL:
    replay exact terminal outcome

one invocation:
    exactly one terminal outcome
```

---

# 42. Client Process Restart

In-memory terminal cache disappears after process restart.

For side-effecting capability execution, recommended:

```text
ClientInvocationLedger
```

Fields:

```text
invocation_id
capability_id
principal_id
state
terminal_type
result/error
side_effect_committed
created_at
expires_at
```

It may use lightweight local SQLite.

Not every read-only invocation must be persisted if performance requires a hybrid strategy.

---

# 43. Capability Idempotency Contract

Capabilities should declare:

```text
side_effecting: bool
idempotency:
    IDEMPOTENT
    DEDUPLICATED
    NON_IDEMPOTENT
    UNKNOWN
```

Meaning:

```text
IDEMPOTENT:
    repeated identical execution safe

DEDUPLICATED:
    external idempotency key supported

NON_IDEMPOTENT:
    duplicate execution unsafe

UNKNOWN:
    unsafe by default
```

---

# 44. Unknown Outcome Policy

After CL restart:

```text
pending invocation outcome unknown
```

Policy:

```text
IDEMPOTENT:
    may re-execute

DEDUPLICATED:
    may re-execute with stable idempotency key

NON_IDEMPOTENT:
    no automatic replay
    reconciliation/HITL/fail-safe required

UNKNOWN:
    no automatic replay
```

---

# 45. Safe Fork Contract

Fork is allowed only when:

```text
checkpoint durable
AND branch context isolatable
AND committed side effects not replayed
AND unknown side effects reconciled
AND TaskBudget permits execution
```

---

# 46. Branch Context Isolation

Branches sharing a Session must not automatically see each other's branch-local history.

Correct:

```text
Base C0
├ B1 overlay: m1,m2
└ B2 overlay: n1,n2
```

B1 context:

```text
C0 + m1 + m2
```

B2 context:

```text
C0 + n1 + n2
```

Not:

```text
C0 + m1 + n1 + m2 + n2
```

unless explicit aggregation happens.

---

# 47. Branch-Aware Context Assembly

Context assembly must explicitly combine:

```text
shared session/base context
base checkpoint transcript
branch-local overlay
execution-local transcript
accepted upstream results
```

Order must be deterministic.

---

# 48. Branch Resolution

Do not concatenate transcripts as a merge strategy.

Canonical operations:

```text
ADOPT
AGGREGATE
SUPERSEDE
DISCARD
```

## ADOPT

Choose one branch result as Task result.

## AGGREGATE

Create a dedicated aggregation execution that consumes explicit branch results.

## SUPERSEDE

Replace the authority of one branch with another.

## DISCARD

Ignore the branch.

---

# 49. Concurrent Branch Completion

If two branches complete nearly simultaneously, only one ADOPT operation may win.

Use Task-level revision/CAS:

```text
UPDATE task
SET accepted_branch_id=B1, state=COMPLETED, revision=N+1
WHERE id=T1
AND revision=N
AND state != COMPLETED
```

One transaction wins.

---

# 50. Multi-Worker Resume Race

`asyncio.Lock` protects only one process.

With multiple workers:

```text
worker A sees checkpoint C1 current
worker B sees checkpoint C1 current
```

Both may resume without durable CAS.

Required:

```text
AgentExecution.revision
transactional compare-and-set
```

Example:

```text
WAITING/revision=7
→ RUNNING/revision=8
WHERE revision=7
```

Only one request succeeds.

---

# 51. Revision Contract

Entities that should support optimistic concurrency:

```text
AgentExecution.revision
AgentTask.revision
TaskBranch.revision
ResumeClaim.revision
CapabilityInvocation.revision
TaskBudget.revision
```

Checkpoint is immutable.

---

# 52. ResumeClaim Contract

The current continuation "branch" is semantically a resume claim, not Task branching.

Recommended:

```text
ResumeClaim:
    claim_id
    execution_id
    checkpoint_id

    connection_id
    client_id
    user_id

    state
    revision

    created_at
    expires_at
```

States:

```text
CREATED
ACCEPTED
REJECTED
EXPIRED
CONSUMED
```

---

# 53. Resume Trigger Matrix

| wait_reason | Trigger | Auto resume default |
|---|---|---|
| `CONNECTION` | CL reconnect + capability registration | Yes |
| `HUMAN_APPROVAL` | User/HITL decision | No |
| `DEPENDENCY` | Server dependency completion | Usually yes |
| `RESOURCE` | Scheduler/resource event | Usually yes |
| `EXPLICIT_PAUSE` | Explicit user/system resume | No |
| `RECOVERY` | Recovery coordinator | Policy |
| `RETRY_BACKOFF` | Durable timer | Yes |
| `AGENT` | Child completion signal | Yes if durably suspended |

---

# 54. Client PendingResume Registry

ClientRuntime should keep resumable tickets:

```text
PendingResumeTicket:
    execution_id
    checkpoint_id
    wait_reason
    pending_capability_id
    origin_client_id
    expires_at
```

After:

```text
connection registered
→ capabilities registered
→ READY
```

Client may auto-resume tickets whose policy permits it.

---

# 55. Server Crash / Recovery Contract

Case:

```text
E1 persisted RUNNING
server process dies
```

After restart, E1 cannot remain zombie RUNNING.

Recovery examines:

```text
execution state
latest checkpoint
pending invocation
owner lease
revision
```

Outcomes:

```text
recoverable:
    RUNNING stale → WAITING(RECOVERY)

not recoverable:
    RUNNING stale → FAILED
    error=UNRECOVERABLE_SERVER_RESTART
```

---

# 56. Execution Lease

For multi-worker robustness, consider:

```text
owner_instance_id
lease_expires_at
```

Worker refreshes lease while actively owning execution.

On lease expiry, recovery coordinator may use CAS to claim recovery.

---

# 57. Current Continuation Persistence Scalability Problem

Rewriting the entire checkpoint/branch history as one JSON document creates write amplification.

If checkpoints contain growing transcripts:

```text
C1 10 KB
C2 20 KB
C3 30 KB
...
```

repeated rewrite trends toward O(N²) copied bytes.

Branching amplifies this.

Normalized persistence must precede general Task branching.

---

# 58. Normalized Persistence Target

## agent_executions

```text
id
session_id
task_id
branch_id
agent_id

parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id

state
wait_reason
revision

remaining_active_budget_seconds
wait_expires_at

result
error

created_at
started_at
updated_at
completed_at
```

Indexes:

```text
task_id
branch_id
parent_execution_id
state
wait_expires_at
```

## agent_task_branches

```text
branch_id PK
task_id
parent_branch_id

base_execution_id
base_checkpoint_id
current_execution_id

resolution_state
revision

created_by
reason
created_at
updated_at
```

## agent_execution_checkpoints

```text
checkpoint_id PK
execution_id
task_id
branch_id

parent_checkpoint_id

iteration
transcript_ref
transcript_version

pending_invocation_id
pending_tool_call_id
pending_capability_id

wait_reason
remaining_active_budget_seconds

origin_connection_id
origin_client_id

created_at
```

## agent_resume_claims

```text
claim_id PK
execution_id
checkpoint_id
connection_id
client_id
user_id
state
revision
created_at
expires_at
```

## agent_task_budgets

```text
task_id PK
revision
limits...
usage counters...
```

---

# 59. Checkpoint Transcript Storage

Avoid copying full transcript into every checkpoint.

Preferred strategy:

```text
periodic full snapshot
+
small deltas
```

Example:

```text
C10 full snapshot
C11 delta
C12 delta
...
C20 full snapshot
```

This bounds reconstruction cost while reducing writes.

---

# 60. Garbage Collection / Retention

Define retention for:

```text
terminal outcomes
checkpoints
resume claims
superseded branches
failed retry executions
provider attempts
client invocation ledger
```

GC must never delete data required for an active WAITING execution.

---

# 61. SE↔CL Wire Contract

Current protocol is v1.

Internal domain normalization and wire migration are separate.

Target internal shape:

```json
{
  "state": "WAITING",
  "wait_reason": "CONNECTION",
  "execution_id": "E1",
  "checkpoint_id": "C1"
}
```

During migration, CL must accept legacy:

```json
{
  "status": "WAITING_FOR_CONNECTION",
  "execution_id": "E1",
  "checkpoint_id": "C1"
}
```

and new:

```json
{
  "status": "WAITING",
  "wait_reason": "CONNECTION",
  "execution_id": "E1",
  "checkpoint_id": "C1"
}
```

---

# 62. Protocol Version Strategy

Do not bump protocol for compatible optional fields.

Potential strategy:

```text
v1:
    accepts legacy + canonical WAITING payloads

v2:
    removes legacy WAITING_FOR_CONNECTION shape
```

Freeze exact migration behavior in R0/R1.

---

# 63. Realtime Message Surface

Keep wire surface minimal.

Existing useful messages:

```text
execution.resume
execution.resume.accepted
```

Potential future events only when consumers need them:

```text
execution.waiting
execution.resume.rejected
execution.reconciled
task.branch.created
task.branch.resolved
```

---

# 64. Branch Control Surface

Branch orchestration should remain server-owned.

Prefer control APIs:

```text
POST /tasks/{task_id}/fork
POST /tasks/{task_id}/branches/{branch_id}/adopt
POST /tasks/{task_id}/branches/{branch_id}/discard
POST /executions/{execution_id}/pause
POST /executions/{execution_id}/resume
```

Use WebSocket for:

```text
remote capability execution
connection lifecycle
resume transport
live events
```

---

# 65. Client Impact

Expected CL changes:

```text
GatewayLLMClient
GatewayRealtimeClient
ClientRuntime
CapabilityDispatcher
protocol schemas
PendingResume registry
ClientInvocationLedger
UI bridge/state handling
```

CL does not own Task branching.

---

# 66. Server Impact

Expected SE changes:

```text
AgentExecution schemas
AgentRuntime
Agent state machine
DurableAgentStore
Task/MultiAgent coordinator
ContinuationService
CapabilityRuntime integration
AgentCapabilityDriver
ContextAssembler
WorkflowRuntime
events_router
storage models/migrations
Provider retry
Recovery service
```

---

# 67. Error Taxonomy

Required distinctions:

```text
AGENT_TIMEOUT
AGENT_CANCELLED
AGENT_EXECUTION_FAILED

CAPABILITY_TIMEOUT
CAPABILITY_CANCELLED
CAPABILITY_EXECUTION_FAILED

REMOTE_CONNECTION_LOST
REMOTE_OUTCOME_UNKNOWN
REMOTE_RESULT_RECONCILIATION_REQUIRED

WAIT_EXPIRED
STALE_CHECKPOINT
STALE_RESUME_CLAIM
BRANCH_CONFLICT
TASK_BUDGET_EXCEEDED
DELEGATION_DEPTH_EXCEEDED
UNRECOVERABLE_SERVER_RESTART
```

Do not collapse all child Agent failures into generic capability failure.

---

# 68. Provider Retry Contract

Retry only when:

```text
error retryable
AND retry budget available
AND retry delay fits remaining operation budget
```

Delay:

```text
effective_delay =
max(local_backoff, provider_retry_hint)
```

If provider asks for 50s but only 8s remain:

```text
do not retry same provider
→ fallback immediately
```

---

# 69. Provider Fallback Contract

Provider is viable only if:

```text
configured
AND health eligible
AND target model resolvable/available
AND required capability supported
AND deadline can still be met
```

`configured != usable`.

---

# 70. HITL Boundary

For client-executed side effects, local client HITL remains final consent boundary unless policy explicitly changes.

Fork/retry must never bypass prior denial or repeat a side effect without reconciliation.



---

# 71. Practical Scenario Simulation Matrix

## S1 — Normal Agent execution

```text
T1
B1
E1 CREATED
→ RUNNING
→ COMPLETED
```

Expected:

- one durable execution;
- no unnecessary WAITING;
- no checkpoint churn;
- one terminal result.

## S2 — Parent delegates child Agent

```text
E1 coordinator
→ CapabilityInvocation I1
→ E2 researcher
```

Expected:

```text
I1.execution_id = E1
E2.parent_execution_id = E1
E2.execution_id != E1
```

No parent/child iteration collision.

## S3 — Child Agent exceeds its own budget

Expected:

```text
E2 → TIMEOUT
```

Parent E1 receives structured Agent failure and may continue if policy/budget permits.

## S4 — Disconnect before remote dispatch

SE knows side effect has not started.

Expected:

```text
E1 → WAITING(CONNECTION)
```

Reconnect resumes same E1 and same invocation identity.

## S5 — Disconnect while remote tool is running

Outcome may be unknown.

Expected:

```text
E1 → WAITING(CONNECTION)
I1 → reconciliation required
```

No automatic new invocation.

## S6 — Side effect completed but result lost

Example:

```text
send_email()
```

completed on CL, network drops before result reaches SE.

Expected:

- same `invocation_id` reconciled;
- CL replays stored terminal outcome;
- email is not sent twice.

## S7 — CL process restart after side effect

If durable ledger contains I1:

```text
replay exact terminal outcome
```

If not and capability is non-idempotent:

```text
REMOTE_OUTCOME_UNKNOWN
→ HITL/reconciliation/fail-safe
```

## S8 — Same installation reconnect

```text
old connection K1
new connection K2
same client_id
same user_id
```

Expected auto-resume for `WAITING(CONNECTION)` after K2 is READY and capabilities are registered.

## S9 — Different installation reconnect

Default policy:

```text
reject
```

unless cross-client resume is explicitly enabled.

## S10 — Two concurrent resume requests

Expected:

```text
one CAS wins
one STALE/CONFLICT
```

Only one AgentRuntime resumes E1.

## S11 — Long WAITING duration

Execution:

```text
60s active budget
20s consumed
10 minutes WAITING
```

Resume:

```text
40s active budget remains
```

## S12 — Wait TTL expires

Expected:

```text
WAITING → TIMEOUT
```

Resume claim rejected.

## S13 — SE crash during RUNNING

Expected recovery:

```text
stale RUNNING
→ WAITING(RECOVERY)
```

if recoverable, otherwise FAILED.

## S14 — SE crash during WAITING

Checkpoint survives.

Expected:

```text
same execution_id
same checkpoint lineage
still WAITING
```

## S15 — Retry failed execution

```text
B1/E1 FAILED
→ retry
B1/E2 RUNNING
```

```text
E2.retry_of_execution_id = E1
```

## S16 — Fork from checkpoint

```text
B1/E1/C4
→ FORK
B2/E2
```

Original B1 is not mutated.

## S17 — Branch context isolation

B1 local messages must not appear in B2 context unless explicit aggregation occurs.

## S18 — Two branches complete at nearly same time

Both attempt ADOPT.

Expected:

```text
one Task CAS wins
one branch becomes non-authoritative
```

## S19 — Task cancellation with multiple branches

Expected:

```text
Task → CANCELLED
all active executions cancel
all resume claims invalidate
no new retry/fork
```

## S20 — Provider asks retry after 50s, only 8s remain

Expected:

```text
skip same-provider retry
fallback immediately
```

## S21 — Provider short retry 1–2 seconds

Expected:

```text
Execution stays RUNNING
```

No durable WAITING churn.

## S22 — Recursive Agent cycle

```text
A → B → A
```

Expected depth/cycle policy rejection.

## S23 — Fork storm

Agent requests many forks.

Expected TaskBudget rejects beyond configured bounds.

## S24 — One branch WAITING, one RUNNING

```text
B1/E1 WAITING(CONNECTION)
B2/E2 RUNNING
```

Expected:

```text
Task = RUNNING
```

## S25 — Multiple wait reasons

```text
B1 WAITING(CONNECTION)
B2 WAITING(HUMAN_APPROVAL)
```

Expected:

```text
Task = WAITING
wait_reasons={CONNECTION,HUMAN_APPROVAL}
```

## S26 — Duplicate capability frame while CL invocation RUNNING

Expected:

```text
ignore duplicate as new work
do not create second side effect
```

## S27 — Duplicate capability frame after CL invocation terminal

Expected:

```text
replay exact cached/durable terminal outcome
```

## S28 — User pauses execution during tool-free reasoning

Expected:

```text
reach safe checkpoint
WAITING(EXPLICIT_PAUSE)
```

## S29 — User pauses while non-cancellable side effect is in progress

Expected:

- do not claim paused until side effect reaches known safe boundary;
- persist pending invocation state;
- if transport disappears, use reconciliation rules.

## S30 — Branch B1 is superseded while its execution is still RUNNING

Policy must decide:

```text
cancel execution immediately
or
allow completion but ignore result
```

Recommended default:

```text
cancel if safe;
otherwise detach result authority and allow controlled completion.
```

## S31 — Task completes while another branch is WAITING

After authoritative ADOPT:

```text
Task COMPLETED
```

Other branch:

```text
SUPERSEDED
```

Its resume claims must be invalidated.

## S32 — Provider task raises after Agent timeout

Expected:

```text
owner retrieves exception
no "Task exception was never retrieved"
```

## S33 — Retry after terminal TIMEOUT

Expected new execution:

```text
E1 TIMEOUT
E2 CREATED
retry_of=E1
```

Never E1→RUNNING.

## S34 — Retry from older checkpoint after newer branch work exists

Must validate checkpoint lineage and Task/Branch revision.

Reject stale replay if it would violate branch policy.

## S35 — Client capability registration missing after reconnect

Execution remains:

```text
WAITING(CONNECTION)
```

Resume is rejected with:

```text
PENDING_CAPABILITY_NOT_READY
```

until capability registration is available or another routing choice exists.

## S36 — Server-side alternative capability becomes available during client disconnect

Policy may continue server-side without waiting only if:

- routing contract permits substitution;
- operation semantics are compatible;
- side effect was not already dispatched to client;
- pending invocation outcome is known safe.

Otherwise remain WAITING.

## S37 — Same Task has root branch completed but aggregation requested

If user explicitly requests aggregate, Task should not auto-finalize too early.

Task resolution policy must distinguish:

```text
first-success
explicit-adopt
collect-N
aggregate-all
```

## S38 — Database CAS conflict during checkpoint creation

Operation must reload latest state and either:

```text
retry state transition safely
or
return conflict
```

It must not silently overwrite newer checkpoint state.

## S39 — Recovery worker races user resume

Both use revision/CAS.

Only one may transition WAITING/RECOVERY to RUNNING.

## S40 — Old CL receives new WAITING shape

Compatibility adapter must prevent parsing failure.

---

# 72. Performance Risks

## 72.1 State transition write amplification

Do not persist every short await as WAITING.

## 72.2 Checkpoint transcript duplication

Use references/snapshot+delta, not full transcript per checkpoint.

## 72.3 Branch explosion

Task-level limits are mandatory.

## 72.4 Database transaction count

Group logically atomic lifecycle changes:

```text
execution terminal state + result
branch ADOPT + Task COMPLETED
ResumeClaim consumed + execution RUNNING
```

## 72.5 TaskBudget hot-row contention

Start correctness-first with transactional/CAS updates.

If profiling later proves contention:

```text
atomic counters
usage shards
batched accounting
```

may be introduced.

## 72.6 Event volume

Emit durable lifecycle events, not every internal await.

Useful events:

```text
execution.created
execution.started
execution.waiting
execution.resumed
execution.completed
execution.failed
execution.cancelled
execution.timed_out

task.branch.created
task.branch.resolved

resume.claimed
resume.rejected
```

Only add events with real consumers.

---

# 73. Event Idempotency

Recommended event correlation:

```text
event_id
task_id
branch_id
execution_id
correlation_id
trace_id
revision
```

Consumers should ignore stale revisions where appropriate.

---

# 74. ROADMAP IMPLEMENTATION PHASES

This sequencing supersedes the previous R0–R13 order.

---

# Phase R0 — Contract Freeze

## Goal

Freeze semantics before modifying runtime code.

## Scope

- Task/Branch/Execution cardinality.
- Delegation/retry/fork lineage.
- Single WAITING state.
- WaitReason.
- Active budget vs wait TTL.
- Terminal invariants.
- RESUME/RETRY/FORK/SUBTASK.
- Capability idempotency.
- Error taxonomy.
- revision/CAS.
- protocol migration.
- TaskBudget.

## Deliverables

1. Contract document.
2. Execution state matrix.
3. Task aggregation matrix.
4. Branch resolution matrix.
5. Identity/lineage matrix.
6. Timeout matrix.
7. Resume trigger matrix.
8. Error taxonomy.
9. Persistence ownership matrix.
10. Protocol compatibility matrix.
11. Contract-only tests.

## Exit Gate

No ambiguity remains around:

```text
task_id
branch_id
execution_id
parent_execution_id
retry_of_execution_id
base_execution_id
checkpoint_id
invocation_id
WAITING
wait_reason
revision
remaining_active_budget
wait_expires_at
```

---

# Phase R1 — WAITING Normalization + Compatibility Layer

## Goal

Unify resumable state without breaking existing CL.

## Server internal changes

Replace legacy waiting states with:

```text
state=WAITING
wait_reason=<reason>
```

## Compatibility mapping

```text
WAITING_FOR_CONNECTION → WAITING + CONNECTION
WAITING_AGENT          → WAITING + AGENT
```

## Client migration

CL accepts both legacy and canonical WAITING payloads.

## Affected areas

SE:

```text
domain AgentExecution schema
Agent state machine
WorkflowRuntime
AgentRuntime
ContinuationService
Task/MultiAgent DTO
events
persistence serializers
```

CL:

```text
GatewayLLMClient stream parser
Gateway schemas
UI state handling
resume ticket handling
```

## Tests

- canonical WAITING requires reason;
- non-WAITING rejects reason;
- legacy payload normalizes correctly;
- old CL compatibility fixture;
- new CL accepts old server payload.

## Exit Gate

No production runtime logic depends on old execution waiting enums.

---

# Phase R2 — Durable AgentExecution Authority + Revision/CAS

## Goal

Make AgentRuntime the authoritative execution lifecycle owner and make transitions multi-worker safe.

## Implement

- AgentRuntime CREATE/LOAD logic.
- durable execution existence before iteration 1.
- `revision`.
- CAS state transitions.
- transactional terminal update.
- remove coordinator/runtime split identity.
- idempotent execution startup guard.

## Tests

- duplicate execution creation rejected;
- resume same execution exactly once;
- stale revision rejected;
- two resume requests: one winner;
- terminal execution cannot become RUNNING;
- durable record always exists before checkpoint/iteration writes.

## Exit Gate

```text
1 AgentRuntime.execute
=
1 AgentExecution record
```

and all lifecycle mutations are CAS-safe.

---

# Phase R3 — Execution Lineage

## Goal

Separate delegation, retry and fork ancestry.

## Implement

```text
parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
task_id
branch_id
```

## Nested Agent

Every Agent capability call creates a new child execution ID.

## Tests

- parent/child iteration IDs never collide;
- child checkpoints do not overwrite parent;
- retry does not appear as child delegation;
- fork does not appear as delegation;
- correlation/session/trace propagate correctly.

## Exit Gate

Execution graph is semantically unambiguous.

---

# Phase R4 — Active Budget / Wait TTL / Deadline Hierarchy

## Goal

Make WAITING compatible with durable continuation.

## Implement

- `remaining_active_budget_seconds`.
- `wait_expires_at`.
- iteration budget.
- inference budget.
- tool timeout.
- nested Agent budget.
- parent upper bounds.
- monotonic deadline reconstruction.

## Tests

- active budget freezes while WAITING;
- wait TTL expires correctly;
- LONG_RUNNING Agent not killed by tool timeout;
- child budget never exceeds parent;
- process restart reconstructs remaining budget;
- short provider backoff stays RUNNING.

## Exit Gate

No conflict exists between durable waiting and timeout semantics.

---

# Phase R5 — Cancellation, TaskBudget, Fan-Out and Async Ownership

## Goal

Prevent orphan tasks and unbounded execution growth.

## Implement

- ProviderInferenceAdapter child-task cleanup.
- cancellation propagation.
- TaskBudget ledger.
- max active branches.
- max total executions.
- max parallel agents.
- max delegation depth.
- cycle prevention.
- atomic budget reservation/release.

## Tests

- parent cancellation;
- child Agent cancellation;
- provider late exception;
- recursive Agent cycle;
- fork storm;
- concurrent budget reservation;
- Task cancellation invalidates future resume/retry/fork.

## Exit Gate

No orphan async task and no uncontrolled branching/delegation.

---

# Phase R6 — Remote Invocation Reconciliation / Idempotency

## Goal

Solve remote side-effect ambiguity before general continuation and branching.

## Implement

- capability idempotency declaration;
- unknown outcome semantics;
- stable same-invocation replay;
- reconciliation path;
- CL terminal outcome guarantees;
- durable ClientInvocationLedger for unsafe side effects or equivalent;
- HITL/fail-safe for unknown non-idempotent outcomes.

## Tests

- disconnect before dispatch;
- disconnect during tool;
- side effect finished/result lost;
- same-process reconnect;
- CL process restart;
- idempotent replay;
- deduplicated replay;
- non-idempotent unknown outcome blocked.

## Exit Gate

No automatic duplicate side effect after connection loss.

---

# Phase R7 — Durable Checkpoint + ResumeClaim + Resume Protocol

## Goal

Implement robust same-execution resume.

## Implement

- normalized checkpoint persistence;
- ResumeClaim;
- stale checkpoint validation;
- CAS claim consumption;
- user/client/connection authorization;
- PendingResumeTicket on CL;
- auto-resume for CONNECTION;
- manual resume for HITL/pause;
- recovery integration.

## Canonical flow

```text
E1 WAITING(CONNECTION)
→ checkpoint C1
→ CL reconnect K2
→ connection registered
→ capabilities registered
→ execution.resume(E1,C1)
→ server CAS
→ ResumeClaim consumed
→ E1 RUNNING
```

## Tests

- same-client resume;
- foreign-client rejection;
- stale checkpoint;
- duplicate resume race;
- server restart while WAITING;
- committed result not replayed;
- capability-not-ready rejection;
- old connection_id cannot resume.

## Exit Gate

Resume is durable, idempotent and multi-worker safe.

---

# Phase R8 — Normalized TaskBranch Persistence + Branch Context + FORK

## Goal

Introduce true Task branches after execution/resume safety is stable.

## Implement

- `agent_task_branches`.
- branch resolution state.
- Branch 1:N Execution.
- fork from safe checkpoint.
- branch-local context overlay.
- base transcript reference.
- Task branch policy.
- branch-aware ContextAssembler.

## Tests

- two branches run independently;
- branch-local message isolation;
- fork from unsafe checkpoint rejected;
- unknown side-effect blocks fork;
- TaskBudget limits enforced;
- original branch remains immutable after fork.

## Exit Gate

Fork is safe, isolated and durable.

---

# Phase R9 — Retry + Branch Resolution + Task Aggregation

## Goal

Complete Task multi-execution semantics.

## Implement

- retry-as-new-execution in same branch;
- ADOPT;
- SUPERSEDE;
- DISCARD;
- AGGREGATE execution;
- Task completion CAS;
- branch result authority.

## Tests

- retry E1→E2 same branch;
- terminal execution never resurrects;
- simultaneous branch completion;
- one ADOPT winner;
- explicit aggregate;
- one branch failure does not fail Task;
- completed Task invalidates outstanding resume claims.

## Exit Gate

Task resolution is deterministic under races.

---

# Phase R10 — Provider Retry / Fallback Hardening

## Goal

Make provider behavior deadline-aware.

## Implement

- parse provider retry hints;
- deadline-aware retry;
- provider retry budget;
- immediate fallback when retry delay does not fit;
- provider/model availability-aware routing;
- stable provider errors.

## Tests

- Gemini 429 RetryInfo > remaining budget;
- retry inside budget;
- mapped Ollama model missing;
- fallback exhaustion;
- cancellation during backoff;
- provider task cleanup.

## Exit Gate

No retry storm and no hidden Agent-budget violation.

---

# Phase R11 — Persistence / Performance Hardening

## Goal

Scale continuation and branching.

## Implement

- checkpoint snapshot/delta strategy;
- transcript refs;
- indexes;
- retention/GC;
- transaction batching;
- query optimization;
- TaskBudget contention monitoring;
- load benchmarks.

## Metrics

```text
checkpoint bytes written
resume latency p50/p95/p99
checkpoint reconstruction p50/p95/p99
branch create latency
DB writes per Agent iteration
rows per Task
memory per active execution
```

## Exit Gate

No O(N²) checkpoint growth.

---

# Phase R12 — Crash Recovery / Execution Lease

## Goal

Recover stale RUNNING executions safely.

## Implement

- `owner_instance_id`.
- lease expiry.
- recovery scanner/coordinator.
- stale RUNNING detection.
- `WAITING(RECOVERY)`.
- pending invocation reconciliation.
- unrecoverable failure path.

## Tests

- kill worker during provider call;
- kill worker during remote invocation;
- restart with WAITING execution;
- competing recovery workers;
- recovery vs user resume race;
- no duplicate resume.

## Exit Gate

Server crash cannot leave zombie RUNNING execution indefinitely.

---

# Phase R13 — Protocol and Data Migration Cleanup

## Goal

Remove temporary compatibility only after rollout confidence.

## Includes

- old waiting enums;
- old `WAITING_FOR_CONNECTION` wire form;
- old continuation JSON;
- legacy execution records;
- deprecated continuation branch naming;
- remaining SE/CL schema drift;
- legacy TextContent edge cleanup if still applicable.

## Exit Gate

Compatibility removal is backed by migration tests/telemetry.

---

# Phase R14 — CI / Fault Injection / Production Exit Gates

## Goal

Turn tests into architecture evidence.

## Required suites

```text
contract
state-machine
identity-lineage
persistence
agent-runtime
capability-runtime
continuation
reconciliation
branching
TaskBudget
provider-retry
real-WebSocket
multi-worker-race
server-restart
client-restart
HITL
fault-injection
```

## Fault injection points

```text
before remote dispatch
after dispatch
before side effect
after side effect
before result send
after result send
before SE commit
after SE commit
during resume CAS
during ADOPT CAS
during provider retry
during checkpoint persistence
during TaskBudget reservation
```

## Exit Gate

Critical P0 flows have real integration/E2E tests, not only unit tests.

---

# 75. Recommended Patch Groups

## Group A — Core Execution Safety

```text
R0
R1
R2
R3
R4
R5
```

Establishes:

```text
WAITING
execution ownership
lineage
deadline semantics
CAS
cancellation
TaskBudget
```

## Group B — Remote Safety and Resume

```text
R6
R7
```

No general FORK before this group passes.

## Group C — Task Branching

```text
R8
R9
```

## Group D — Runtime Hardening

```text
R10
R11
R12
```

## Group E — Cleanup / Release Gate

```text
R13
R14
```

---

# 76. Global P0 Exit Gate Before FORK

R8 must not start until:

- [ ] Only canonical execution wait state is `WAITING`.
- [ ] Every WAITING execution has `wait_reason`.
- [ ] Terminal execution cannot return to RUNNING.
- [ ] AgentRuntime owns one durable execution per execute call.
- [ ] Durable transitions use revision/CAS.
- [ ] Parent/child Agents have different execution IDs.
- [ ] Delegation/retry/fork lineage are separate.
- [ ] LONG_RUNNING Agent does not use tool timeout.
- [ ] Active budget freezes while WAITING.
- [ ] Wait TTL is separate from active budget.
- [ ] Remaining active budget survives restart.
- [ ] Provider child tasks cannot leak.
- [ ] Task-level branch/execution/depth limits exist.
- [ ] Remote unknown outcome is modeled.
- [ ] Same invocation ID is reconciled after disconnect.
- [ ] Non-idempotent unknown outcome is not blindly replayed.
- [ ] Resume uses same execution ID.
- [ ] Resume is CAS-protected.
- [ ] Concurrent resume cannot start duplicate AgentRuntime.
- [ ] CL accepts legacy + canonical WAITING payloads.
- [ ] Resume never replays committed tool result.
- [ ] Real WebSocket reconnect E2E passes.
- [ ] Restarted WAITING execution remains resumable.

---

# 77. Global Exit Gate Before Branch ADOPT/AGGREGATE

Before Task can complete through branch resolution:

- [ ] Branch is 1:N Execution.
- [ ] Branch context is isolated.
- [ ] Branch resolution state is separate from Execution state.
- [ ] Task completion uses revision/CAS.
- [ ] Exactly one accepted branch may win.
- [ ] Shared TaskBudget spans all branches.
- [ ] Fork storm bounded.
- [ ] Side-effect safety enforced at fork boundary.
- [ ] Retry creates new execution.
- [ ] Retry stays in same branch.
- [ ] Fork creates new branch and execution.
- [ ] Subtask creates new Task.
- [ ] Aggregation is explicit.
- [ ] Completed Task invalidates obsolete resume paths.

---

# 78. Canonical Architecture

```text
SESSION S1
│
└── TASK T1
    │
    ├── TaskBudget TB1
    │
    ├── BRANCH B1
    │   │ resolution=OPEN
    │   │
    │   ├── EXECUTION E1
    │   │   state=FAILED
    │   │
    │   └── EXECUTION E2
    │       retry_of=E1
    │       state=WAITING
    │       wait_reason=CONNECTION
    │       │
    │       └── CHECKPOINT C2
    │           pending_invocation=I7
    │
    ├── BRANCH B2
    │   │ parent_branch=B1
    │   │ base_checkpoint=C1
    │   │ resolution=OPEN
    │   │
    │   └── EXECUTION E3
    │       state=RUNNING
    │
    └── BRANCH B3
        │ resolution=ADOPTED
        │
        └── EXECUTION E4
            state=COMPLETED
```

Nested Agent inside E3:

```text
E3 coordinator
│
└── CapabilityInvocation I20
    execution_id=E3
    capability=agent-web-researcher
    │
    └── Child Agent Execution E5
        parent_execution_id=E3
        task_id=T1
        branch_id=B2
```

---

# 79. Canonical Reconnect Flow

```text
E2 RUNNING
│
├─ remote invocation I7
│
└─ connection K1 lost
      │
      ├─ terminal outcome already known?
      │       yes → persist/reuse
      │
      └─ outcome unknown
              │
              ↓
      E2 WAITING(CONNECTION)
              │
              └─ checkpoint C2
                     │
                     ↓
              CL reconnect K2
              same user/client
                     │
                     ↓
              capabilities registered
                     │
                     ↓
              ResumeClaim RC1
                     │
                     ↓ CAS
              E2 RUNNING
                     │
                     ↓
              reconcile same I7
```

---

# 80. Canonical Fork Flow

```text
Task T1
Branch B1
Execution E1
Checkpoint C5
    │
    ├─ RESUME
    │    same B1
    │    same E1
    │
    └─ FORK
         ↓
       Branch B2
       base_checkpoint=C5
       Execution E2
```

---

# 81. Canonical Retry Flow

```text
Task T1
Branch B1
│
├ E1 FAILED
│
└ E2 CREATED
    retry_of_execution_id=E1
```

No new branch.

---

# 82. Canonical Subtask Flow

```text
Task T1
│
└─ Execution E1
    │
    └─ logical sub-objective
         ↓
       Task T2
       parent_task_id=T1
```

---

# 83. Decision Log — Proposed Defaults

## D1 — Single waiting state

```text
APPROVE:
WAITING + wait_reason
```

## D2 — Branch cardinality

```text
APPROVE:
Task 1:N Branch
Branch 1:N Execution
```

## D3 — Terminal resurrection

```text
PROHIBIT
```

## D4 — Retry

```text
same Task
same Branch
new Execution
```

## D5 — Fork

```text
same Task
new Branch
new Execution
```

## D6 — Subtask

```text
new Task
```

## D7 — WAITING timeout

```text
active budget pauses
wait TTL continues
```

## D8 — Resume client policy

Default:

```text
same user_id
same client_id
new connection_id
```

Cross-client resume requires explicit policy.

## D9 — Branch context

```text
branch-local overlay
explicit aggregation only
```

## D10 — Multi-worker correctness

```text
durable revision/CAS mandatory
```

## D11 — Remote side-effect disconnect

```text
reconcile same invocation_id
never blindly replay unknown non-idempotent operation
```

## D12 — Task budget

```text
shared across child Agents, retries and forks
```

## D13 — Branch runtime state

```text
derive from current execution
persist only branch resolution state
```

## D14 — Server crash

```text
stale RUNNING
→ WAITING(RECOVERY) if safe
→ FAILED if not recoverable
```

## D15 — Auto resume

Default:

```text
CONNECTION       → yes
HUMAN_APPROVAL   → no
EXPLICIT_PAUSE   → no
DEPENDENCY       → yes
RESOURCE         → policy
RECOVERY         → policy
```

---

# 84. Final Recommended Implementation Order

```text
R0  Contract Freeze
 ↓
R1  WAITING normalization + CL compatibility
 ↓
R2  AgentExecution authority + revision/CAS
 ↓
R3  execution lineage
 ↓
R4  active budget / wait TTL / deadline hierarchy
 ↓
R5  cancellation + TaskBudget + fan-out/depth + async ownership
 ↓
R6  remote invocation reconciliation + idempotency
 ↓
R7  durable checkpoint + ResumeClaim + reconnect protocol
 ↓
R8  normalized TaskBranch + branch context + FORK
 ↓
R9  RETRY + branch resolution + Task aggregation
 ↓
R10 provider retry/fallback
 ↓
R11 persistence/performance
 ↓
R12 crash recovery / execution lease
 ↓
R13 protocol/data compatibility cleanup
 ↓
R14 CI + fault injection + production gates
```

Critical stop condition:

> **Do not implement general Task FORK before R6 and R7 pass.**

Otherwise disconnect/retry/fork interactions can duplicate side effects and corrupt lineage.

---

# 85. Final Architectural Invariants

```text
Task
    = logical objective

Branch
    = independent Task path

Branch
    = may contain multiple executions

Execution
    = exactly one AgentRuntime run

WAITING
    = only resumable execution state

wait_reason
    = durable suspension reason

RESUME
    = same execution

RETRY
    = new execution, same branch

FORK
    = new branch + new execution

SUBTASK
    = new Task

parent_execution_id
    = Agent delegation lineage only

retry_of_execution_id
    = retry lineage

base_checkpoint_id
    = fork/recovery origin

TERMINAL
    = never resurrect

active budget
    = freezes during durable WAITING

wait TTL
    = continues while waiting

remote invocation after disconnect
    = reconcile same invocation_id

unknown non-idempotent side effect
    = never blindly retry

AgentRuntime
    = AgentExecution authority

TaskRuntime / MultiAgentCoordinator
    = Task/Branch authority

CapabilityRuntime
    = CapabilityInvocation authority

ProviderRuntime
    = Provider-call authority

CL
    = transport + local capability execution + local HITL + terminal replay

SE
    = Task/Branch/Execution orchestration authority

multi-worker correctness
    = durable CAS, not in-memory lock

branch context
    = isolated until explicit aggregation

Task budgets
    = shared across executions and branches
```

---

# 86. Required R0–R5 Test Matrix Before First Patch Series Is Considered Complete

## State contract tests

- WAITING requires reason.
- Non-WAITING clears/rejects wait_reason.
- Terminal cannot RUNNING.
- WAITING can RUNNING through valid resume.

## Identity tests

- Parent/child execution IDs differ.
- Retry relation separate from parent relation.
- Fork relation separate from parent relation.
- Capability invocation remains associated with invoking execution.

## Persistence tests

- AgentExecution exists before iteration.
- Parent and child iterations cannot overwrite.
- CAS rejects stale revision.
- terminal result update is atomic.
- TaskBudget updates are atomic.

## Deadline tests

- WAITING freezes active budget.
- wait TTL expires.
- child deadline bounded by parent.
- LONG_RUNNING Agent not tool-timed-out.
- process restart reconstructs remaining budget.

## Cancellation tests

- root Task cancel fan-out.
- execution-only cancel does not cancel other branches.
- provider child task retrieved on outer cancellation.
- cancellation does not leave orphan Task.

## Fan-out tests

- delegation depth limit.
- Agent cycle rejection.
- max active branches.
- max total executions.
- max parallel agents.
- concurrent budget reservation.

## Compatibility tests

- legacy WAITING payload → canonical internal state.
- new CL accepts legacy payload.
- old expected stream shape remains parseable during migration.

---

# 87. R6–R7 Remote Safety Test Matrix

- disconnect before dispatch;
- disconnect after dispatch but before local execution;
- disconnect during local execution;
- side effect completed before disconnect;
- result frame lost;
- duplicate invocation while RUNNING;
- duplicate after TERMINAL;
- reconnect same CL process;
- CL process restart;
- same user/same client reconnect;
- same user/different client rejection;
- stale checkpoint rejection;
- duplicate resume race;
- server restart during WAITING;
- pending capability missing after reconnect;
- committed result never executed twice;
- non-idempotent unknown outcome requires HITL/reconciliation.

---

# 88. R8–R9 Branching Test Matrix

- Branch 1:N Execution.
- retry stays same branch.
- fork creates new branch.
- branch-local transcript isolation.
- fork from stale checkpoint rejected.
- fork from unsafe side-effect boundary rejected.
- simultaneous branch completion.
- one ADOPT winner.
- aggregate uses explicit results.
- Task remains RUNNING when another branch still active.
- Task WAITING aggregates wait reasons.
- Task cancel invalidates all branches.
- Task complete supersedes obsolete branches.
- fork storm bounded by TaskBudget.

---

# 89. R10–R12 Hardening Test Matrix

## Provider

- retry hint larger than deadline.
- retry inside deadline.
- fallback model missing.
- cancellation during backoff.
- fallback chain exhausted.

## Persistence/performance

- 1k checkpoints without quadratic file/JSON growth.
- long transcript snapshot/delta reconstruction.
- indexes used for current execution/branch lookups.
- GC skips active WAITING lineage.

## Recovery

- worker killed during inference.
- worker killed during remote invocation.
- worker killed immediately after checkpoint.
- two recovery workers race.
- recovery worker races client resume.
- stale lease reclaimed exactly once.

---

# 90. Review Checklist Before Coding

Approve or modify each:

- [ ] `WAITING + wait_reason`.
- [ ] WaitReason values.
- [ ] Task 1:N Branch.
- [ ] Branch 1:N Execution.
- [ ] Branch resolution states.
- [ ] Execution lineage fields.
- [ ] Active budget semantics.
- [ ] Wait TTL semantics.
- [ ] Resume trigger defaults.
- [ ] same-user/same-client default resume policy.
- [ ] TaskBudget limits.
- [ ] delegation cycle policy.
- [ ] capability idempotency enum.
- [ ] unknown-outcome behavior.
- [ ] ClientInvocationLedger requirement.
- [ ] ResumeClaim fields.
- [ ] branch-local context model.
- [ ] checkpoint storage model.
- [ ] CAS/revision entities.
- [ ] crash recovery policy.
- [ ] protocol compatibility strategy.
- [ ] phase sequencing R0–R14.

---

# 91. First Implementation Scope After Approval

The first implementation series should cover only:

```text
R1 — WAITING normalization + compatibility
R2 — AgentExecution authority + CAS
R3 — execution lineage
R4 — active budget / wait TTL / deadlines
R5 — cancellation + TaskBudget + async ownership
```

Do **not** implement Task FORK in this first series.

The second series should be:

```text
R6 — remote reconciliation/idempotency
R7 — durable resume
```

Only after these gates pass should the project move to:

```text
R8/R9 — Task branching/retry/aggregation
```

---

# 92. Final Stop Conditions

Stop implementation and return to contract review if any of these occur:

1. A terminal execution needs to become RUNNING to make a test pass.
2. Parent and child Agent need the same execution ID.
3. Fork needs to reuse an execution ID.
4. Retry needs a new branch to work around persistence.
5. A non-idempotent remote side effect must be replayed without known outcome.
6. Two workers can resume the same execution without DB-level conflict.
7. Branches require reading each other's local transcript implicitly.
8. A fork/retry resets Task limits.
9. WAITING duration consumes or resets active budget unexpectedly.
10. Client compatibility requires silently changing protocol meaning without migration.
11. Checkpoint writes grow quadratically with execution length.
12. Server crash leaves a permanent RUNNING record with no owner.
13. An async task can outlive its owner without a supervisor.
14. Task completion can accept two branches concurrently.

Any one of these indicates the implementation has violated the frozen architecture and should not be patched around locally.

---

# 93. Final Conclusion

The target architecture is:

```text
SESSION
  └ TASK
      ├ shared TaskBudget
      ├ Branch B1
      │   ├ Execution E1
      │   └ Execution E2 (retry)
      ├ Branch B2
      │   └ Execution E3 (fork)
      └ Branch B3
          └ Execution E4

Each Execution
  ├ immutable identity
  ├ independent lifecycle
  ├ durable checkpoints
  ├ revision/CAS
  └ terminal means terminal

WAITING
  ├ one state
  ├ reason field
  ├ active budget frozen
  ├ wait TTL active
  └ same execution resumes

Remote execution
  ├ same invocation reconciled after disconnect
  ├ terminal result replayed
  └ unknown non-idempotent side effect never blindly repeated

Branching
  ├ isolated context
  ├ bounded by shared TaskBudget
  ├ fork = new branch + execution
  ├ retry = new execution in same branch
  └ completion resolved transactionally
```

This roadmap should be treated as the architecture contract baseline for the next implementation work.
