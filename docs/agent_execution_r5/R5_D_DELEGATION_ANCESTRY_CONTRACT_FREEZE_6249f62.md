# R5-D Contract Freeze — Delegation Depth, Durable Ancestry and Cycle Prevention

**Audit baseline:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`  
**Previous phase:** R5-C COMPLETE  
**Document status:** CONTRACT FREEZE / READY FOR IMPLEMENTATION  
**Scope:** R5-D only

## 1. Goal

R5-D closes the remaining delegation/fan-out correctness gap in R5:

```text
Agent A
  -> Agent B
      -> Agent C
```

must be bounded by the shared TaskBudget, and recursive Agent ancestry such as:

```text
A -> B -> A
```

must be rejected when `deny_recursive_agent_cycle=true`.

R5-D does not implement Task cancellation-tree closure. That remains R5-E.

---

## 2. Current exact state on 6249f62a

R5-C made TaskBudget live in AgentRuntime, but intentionally left depth/cycle
to R5-D.

### 2.1 AgentRuntime currently hard-codes depth zero

Current task-scoped new-execution admission calls:

```text
TaskBudgetService.start_task_scoped_execution(
    ...
    delegation_depth=0,
)
```

for both root and delegated child executions.

Therefore:

```text
max_delegation_depth
```

exists durably but is not enforced by the live nested-Agent path.

### 2.2 Durable delegation lineage already exists

`agent_executions` already stores:

```text
id
task_id
agent_id
parent_execution_id
```

and `parent_execution_id` is indexed.

This is sufficient to derive durable ancestry without adding a migration.

### 2.3 AgentExecutionContext has no authoritative depth/ancestry field

This is correct for R5-D.

A process-local `delegation_depth` or `agent_ancestry` field may be useful as
derived/cache/debug data, but it must never become the durable source of truth.

### 2.4 AgentCapabilityDriver currently permits taskless Agent -> Agent

When a capability invocation comes from an Agent execution,
`caller_agent_execution_id` is propagated, but `task_id` may still be `None`.

The driver then creates a child AgentExecutionContext with:

```text
parent_execution_id = caller_agent_execution_id
task_id             = context.task_id
```

There is currently no `TASK_BUDGET_REQUIRED` rejection for nested Agent
delegation without durable Task scope.

### 2.5 TaskBudget already persists the policy needed by D

R5-B/R5-C already persist:

```text
max_delegation_depth
deny_recursive_agent_cycle
```

and expose stable error contracts including:

```text
TASK_BUDGET_REQUIRED
DELEGATION_DEPTH_EXCEEDED
```

Cycle detection itself is still absent.

---

## 3. Frozen authority model

R5-D freezes the following ownership:

```text
AgentExecution.parent_execution_id
    durable delegation-lineage authority

AgentExecution.agent_id
    durable Agent identity for ancestry checks

TaskBudget.max_delegation_depth
    durable depth policy

TaskBudget.deny_recursive_agent_cycle
    durable recursive-cycle policy

TaskBudgetService
    durable ancestry/depth/cycle policy evaluator

AgentRuntime
    new AgentExecution admission caller

AgentCapabilityDriver
    early Agent->Agent task-scope boundary
```

Do not use:

```text
metadata["delegation_depth"]
metadata["agent_ancestry"]
client-provided ancestry
capability invocation execution_id
```

as authoritative lineage.

---

## 4. Depth definition

Depth is defined from durable Agent delegation edges:

```text
root execution                    depth = 0
root -> child                     depth = 1
root -> child -> grandchild       depth = 2
```

For a new execution with no `parent_execution_id`:

```text
delegation_depth = 0
```

For a new delegated execution:

```text
delegation_depth =
    number of parent_execution_id edges from child to root
```

Admission rule:

```text
delegation_depth <= TaskBudget.max_delegation_depth
```

Otherwise raise:

```text
DELEGATION_DEPTH_EXCEEDED
```

The limit is inclusive.

Example with `max_delegation_depth=2`:

```text
A(depth 0) -> B(depth 1) -> C(depth 2)   ALLOW
C -> D(depth 3)                          REJECT
```

---

## 5. Durable ancestry algorithm

For a proposed child:

```text
child_agent_id
parent_execution_id
task_id
```

walk the durable parent chain:

```text
parent_execution_id
    -> AgentExecution(parent)
    -> parent.parent_execution_id
    -> ...
    -> root
```

Collect durable Agent IDs in order.

Example:

```text
E1 agent=A parent=NULL
E2 agent=B parent=E1
```

For proposed E3 agent=C:

```text
ancestor_execution_ids = [E2, E1]
ancestor_agent_ids     = [B, A]
delegation_depth       = 2
```

Required validation while traversing:

1. every referenced parent execution must exist;
2. every parent must belong to the same `task_id`;
3. a parent execution ID must not appear twice;
4. traversal must terminate at `parent_execution_id=NULL`;
5. malformed durable lineage fails closed.

A loop in execution IDs indicates corrupted durable lineage and must never be
treated as a valid recursive-Agent policy case.

Frozen corruption error:

```text
TASK_BUDGET_CONFLICT
```

because the durable TaskBudget/execution graph is internally inconsistent.

---

## 6. Recursive Agent cycle policy

When:

```text
TaskBudget.deny_recursive_agent_cycle == true
```

reject the proposed child if:

```text
child_agent_id in ancestor_agent_ids
```

This catches:

```text
A -> A
A -> B -> A
A -> B -> C -> B
```

Frozen stable error code:

```text
AGENT_DELEGATION_CYCLE
```

R5-D should introduce:

```text
class AgentDelegationCycleError(TaskBudgetError):
    code = "AGENT_DELEGATION_CYCLE"
```

When:

```text
deny_recursive_agent_cycle == false
```

repeated Agent IDs are allowed, but `max_delegation_depth` still applies.

The policy does not mean infinite recursion is allowed.

---

## 7. Task scope rule

Direct taskless root Agent execution remains valid:

```text
root AgentExecution
task_id = None
parent_execution_id = None
ALLOW
```

Agent-to-Agent delegation requires durable Task scope:

```text
caller_agent_execution_id != None
AND target capability kind == AGENT
AND task_id == None
    -> TASK_BUDGET_REQUIRED
```

Do not:

```text
create a pseudo task
derive a task from session_id
silently use execution_id as task_id
initialize an invisible budget
```

The caller must attach/create a real AgentTask before nested delegation can
participate in shared TaskBudget accounting.

---

## 8. Enforcement location

### 8.1 Early boundary — AgentCapabilityDriver

Before creating the child AgentExecutionContext:

```text
if caller_agent_execution_id is not None and task_id is None:
    raise TaskBudgetRequiredError(...)
```

This gives a stable failure before allocating a child execution ID/runtime.

This check is necessary but not sufficient.

### 8.2 Durable authority — TaskBudgetService

The authoritative check must run before new task-scoped AgentExecution
admission.

Proposed service contract:

```text
DelegationAdmission:
    task_id
    parent_execution_id
    child_agent_id
    delegation_depth
    ancestor_execution_ids
    ancestor_agent_ids
```

Conceptual API:

```python
async def resolve_delegation_admission(
    task_id: str,
    *,
    parent_execution_id: str | None,
    child_agent_id: str,
) -> DelegationAdmission:
    ...
```

The service loads TaskBudget and durable execution ancestry.

### 8.3 AgentRuntime

For a new task-scoped execution:

```text
admission = resolve_delegation_admission(...)
start_task_scoped_execution(
    ...,
    delegation_depth=admission.delegation_depth,
)
```

This removes the R5-C hard-coded `delegation_depth=0`.

---

## 9. Atomicity and race semantics

Depth/cycle ancestry is based on immutable execution identity fields:

```text
id
task_id
agent_id
parent_execution_id
```

R5-D must not mutate these fields after AgentExecution creation.

Because the parent chain is immutable, ancestry may be resolved immediately
before the atomic TaskBudget/new-execution admission without introducing a
lost-update race.

The actual capacity/depth admission remains protected by the existing
TaskBudget transaction/reservation.

Failure before `NEW_EXECUTION` commit must leave:

```text
no child AgentExecution row
no used_executions increment
no active_executions increment
no active_parallel_agents increment
no NEW_EXECUTION reservation
```

---

## 10. Root semantics

A task-scoped root:

```text
task_id != None
parent_execution_id = None
```

has:

```text
delegation_depth = 0
ancestor_agent_ids = []
```

It consumes:

```text
used_executions
active_executions
```

but does not consume:

```text
active_parallel_agents
```

This preserves the R5-B/R5-C definition of `max_parallel_agents`.

---

## 11. Resume semantics

Resume does not recompute a new delegation level for budget consumption.

The execution already exists with immutable lineage.

R5-D must not increment:

```text
used_executions
delegation_depth
```

on resume.

Resume continues using the R5-C active-slot reacquisition logic.

Cycle/depth policy is an admission policy for a new execution, not a repeated
resume policy.

---

## 12. Retry/fork boundaries

R5-D does not implement retry/fork.

Future semantics remain:

```text
retry_of_execution_id
    retry lineage, not delegation depth

base_execution_id/base_checkpoint_id
    fork/recovery lineage, not delegation depth

parent_execution_id
    only Agent delegation affects delegation depth
```

Do not count retry/fork links as Agent delegation edges.

---

## 13. Required repository support

Current repository has:

```text
get_execution(execution_id)
```

which is sufficient for a correct first implementation.

Optional optimization:

```text
get_execution_ancestry(...)
```

may be added if useful, but R5-D does not require a schema migration.

For bounded depth (default policy currently 8), repeated indexed parent lookup
is acceptable for R5-D correctness.

Performance batching belongs R11 unless profiling proves this is a bottleneck.

---

## 14. Stable failure contracts

R5-D freezes:

```text
TASK_BUDGET_REQUIRED
    nested Agent delegation has no durable task scope

DELEGATION_DEPTH_EXCEEDED
    derived durable depth is greater than max_delegation_depth

AGENT_DELEGATION_CYCLE
    child Agent ID repeats in durable Agent ancestry while cycle policy denies it

TASK_BUDGET_CONFLICT
    durable ancestry is malformed/inconsistent
```

These errors are non-retryable policy/correctness failures at the Agent
delegation layer unless a higher-level caller explicitly creates a new valid
Task/context.

---

## 15. Error propagation through the tool path

`CapabilityToolExecutionAdapter` already normalizes driver exceptions into a
ToolExecutionResult.

R5-D must preserve the stable `.code` from TaskBudget errors.

Required behavior:

```text
AgentCapabilityDriver raises TaskBudgetError subclass
    ->
CapabilityToolExecutionAdapter.normalize_tool_exception(...)
    ->
ToolExecutionResult.error_code == stable TaskBudget error code
```

If the current normalizer drops custom `.code`, R5-D implementation must fix
that call-site and add regression coverage.

Do not leak Python exception class names as the protocol error contract.

---

## 16. P0 findings

### P0-D1 — live depth is always zero

Current AgentRuntime supplies `delegation_depth=0`.

Result: nested Agent chains bypass `max_delegation_depth`.

### P0-D2 — recursive cycle policy is persisted but unused

`deny_recursive_agent_cycle` is stored and fingerprinted but no runtime path
consults it.

### P0-D3 — nested taskless Agent is admitted

AgentCapabilityDriver does not require task scope before spawning a child
Agent.

Result: child Agent can escape the shared TaskBudget model.

### P0-D4 — no durable ancestry validator exists

There is no current service that verifies:

```text
parent exists
same task
acyclic execution-ID parent chain
child Agent ID not repeated when policy denies recursion
```

### P0-D5 — caller metadata cannot be used as a shortcut

The current capability context carries useful request lineage, but none of its
metadata can replace a durable parent-chain traversal.

---

## 17. P1 findings

### P1-D1 — no explicit cycle error exists

Add `AGENT_DELEGATION_CYCLE`.

### P1-D2 — ancestry reconstruction currently requires N indexed lookups

Correctness is acceptable for R5-D because depth is bounded. Optimize later
only if needed.

### P1-D3 — old R3 nested-Agent integration tests have no TaskBudget

Those tests predate R5-C live task accounting.

R5-D must update/add tests deliberately instead of weakening the new
`TASK_BUDGET_REQUIRED` rule.

Taskless root tests remain valid; nested Agent tests must either create a real
TaskBudget or explicitly assert rejection.

---

## 18. Implementation plan

### D1 — domain/service contract

- add `AgentDelegationCycleError`;
- add immutable `DelegationAdmission` representation;
- add durable ancestry resolver to TaskBudgetService;
- fail closed on broken lineage.

### D2 — runtime integration

- remove hard-coded `delegation_depth=0`;
- resolve admission before task-scoped new execution;
- pass derived depth to `start_task_scoped_execution`.

### D3 — Agent capability scope boundary

- reject Agent->Agent delegation with missing task_id using
  `TASK_BUDGET_REQUIRED`;
- preserve direct taskless root execution.

### D4 — error propagation

- verify TaskBudget error `.code` survives capability/tool normalization;
- patch normalizer only if evidence shows it does not.

### D5 — regression matrix

- root depth zero;
- A->B depth one;
- A->B->C at exact depth cap succeeds;
- next child beyond cap fails with `DELEGATION_DEPTH_EXCEEDED`;
- A->A rejected;
- A->B->A rejected;
- repeated Agent allowed when cycle policy false until depth cap;
- malformed missing parent fails closed;
- cross-task parent fails closed;
- execution-ID ancestry loop fails closed;
- Agent->Agent with task_id=None fails `TASK_BUDGET_REQUIRED`;
- direct taskless root remains allowed;
- rejection creates no child execution and consumes no TaskBudget counters;
- max_parallel_agents R5-C behavior remains intact;
- R3 durable lineage still reconstructs correctly;
- R5-C focused gate remains green.

---

## 19. Exit gate

R5-D is COMPLETE only when all are true:

```text
[ ] depth is derived from durable parent_execution_id lineage
[ ] live AgentRuntime no longer hard-codes depth zero
[ ] max_delegation_depth is enforced for nested Agent execution
[ ] deny_recursive_agent_cycle is enforced
[ ] self recursion is rejected when policy=true
[ ] indirect Agent cycle is rejected when policy=true
[ ] policy=false allows repeated Agent IDs but still respects depth cap
[ ] Agent->Agent without Task scope fails TASK_BUDGET_REQUIRED
[ ] taskless direct root remains valid
[ ] malformed durable ancestry fails closed
[ ] rejection consumes no TaskBudget capacity/counters
[ ] stable error codes survive the tool/capability boundary
[ ] R5-C/R5-B/R5-A/R4 regressions remain green
```

---

## 20. Explicit non-scope

Do not implement in R5-D:

```text
Task cancellation tree             R5-E
Task/TaskBudget close coupling     R5-E
full AgentTask CAS migration       R5-E
remote invocation reconciliation   R6
ResumeClaim                         R7
TaskBranch/FORK                     R8
retry scheduler                     R9
execution lease/recovery            R12
```

---

## 21. Frozen conclusion

The R5-D source of truth is:

```text
durable AgentExecution.parent_execution_id chain
+
durable AgentExecution.agent_id
+
durable TaskBudget policy
```

not caller metadata.

The final new-execution admission flow is:

```text
Agent A execution E1
    |
    | capability invokes Agent B
    v
AgentCapabilityDriver
    require real task scope
    |
    v
AgentRuntime new child E2
    |
    v
TaskBudgetService.resolve_delegation_admission
    load durable parent chain
    derive depth
    detect malformed lineage
    enforce recursive-Agent policy
    |
    v
TaskBudgetService.start_task_scoped_execution
    enforce max_delegation_depth
    reserve TaskBudget
    INSERT E2 RUNNING
    commit atomically
```

This contract is ready for R5-D implementation.
