# R5 Completion — Cancellation, TaskBudget, Fan-Out and Async Ownership

**Status:** COMPLETE  
**Completion scope:** R5-A through R5-E  
**Final local regression evidence:** 598 passed, 0 failed, 7 warnings  
**Explicit out-of-scope test:** `cl/tests/test_mcp_client_ownership.py`

## 1. Final phase status

```text
R5-A Async ownership                         COMPLETE
R5-B TaskBudget durable core                 COMPLETE
R5-C Live TaskBudget accounting              COMPLETE
R5-D Delegation depth / ancestry / cycles    COMPLETE
R5-E Durable Task cancellation / Task CAS    COMPLETE

R5 overall                                   COMPLETE
```

The excluded MCP client ownership test belongs to a separate work stream and
is not an R5 blocker or R5 correctness exception.

## 2. What R5 now guarantees

### Async ownership

Process-local Agent execution tasks have an explicit owner.

```text
creator owns asyncio.Task
    until awaited
    or cancel + drain
    or explicit Supervisor transfer
```

`AgentExecutionSupervisor` owns live AgentRuntime tasks and provides:

```text
single process-local owner per execution_id
child cancellation isolation
parent/Task cancellation fan-out
shutdown drain
reservation-before-resume-start
```

### Durable TaskBudget

Every R5-controlled AgentTask has a durable TaskBudget with immutable policy
provenance and revision/CAS.

TaskBudget tracks:

```text
used_executions
active_executions
active_parallel_agents
active_branches
used_tool_calls
used_inference_calls
used_tokens
used_cost_usd
```

Policy limits include:

```text
max_total_executions
max_active_executions
max_active_branches
max_parallel_agents
max_total_tool_calls
max_total_inference_calls
max_total_tokens
max_total_cost_usd
max_delegation_depth
deny_recursive_agent_cycle
```

### Atomic live execution accounting

New task-scoped execution admission is atomic:

```text
TaskBudget reservation
+ AgentExecution RUNNING revision=1
+ NEW_EXECUTION ledger
```

WAITING atomically releases active capacity.

Resume atomically:

```text
WAITING -> RUNNING
+ active slot reacquire
+ RESUME_EXECUTION reservation
```

Resume does not increment `used_executions`.

Terminal/cancel/failure transitions atomically release active execution
capacity.

### Durable idempotency

TaskBudget reservations provide logical idempotency:

```text
NEW_EXECUTION    key = execution_id
RESUME_EXECUTION key = execution_id:source_revision
RELEASE_EXECUTION
TOOL_CALL        key = tool_call_id
INFERENCE        key = request_id
USAGE            key = request_id
BRANCH / RELEASE_BRANCH
```

Replaying a previously committed reservation with the same payload is
idempotent. Reusing the same key with a different payload is a conflict.

### Tool and inference accounting

Task-wide logical tool calls are charged once by `tool_call_id`.

Retries/resume replays of the same logical call do not double-charge the Task.

Inference dispatch is reserved before provider execution.

Actual token/cost usage is accounted after the response and remains
idempotent by logical request identity.

### Durable Agent delegation policy

Delegation ancestry is derived only from:

```text
AgentExecution.parent_execution_id
AgentExecution.agent_id
TaskBudget policy
```

Caller metadata is not ancestry authority.

Depth semantics:

```text
root A                 depth = 0
A -> B                 depth = 1
A -> B -> C            depth = 2
```

`max_delegation_depth` is enforced against this durable chain.

When `deny_recursive_agent_cycle=true`, R5 rejects:

```text
A -> A
A -> B -> A
A -> B -> C -> B
```

Stable policy errors include:

```text
TASK_BUDGET_REQUIRED
DELEGATION_DEPTH_EXCEEDED
AGENT_DELEGATION_CYCLE
TASK_BUDGET_CONFLICT
```

These codes survive the Agent capability/tool boundary instead of being
downgraded to `CAPABILITY_EXECUTION_FAILED`.

### Nested Agent Task scope

Direct taskless root Agent execution remains supported:

```text
parent_execution_id = None
task_id = None
ALLOW
```

Agent-to-Agent delegation requires real durable Task scope:

```text
caller_agent_execution_id != None
task_id == None
    -> TASK_BUDGET_REQUIRED
```

R5 never fabricates a pseudo Task from session, execution or connection IDs.

### Durable Task cancellation

Correctness-sensitive AgentTask state changes now use revision/CAS.

Task terminal transitions atomically couple:

```text
AgentTask terminal state
+ TaskBudget CLOSED
```

The current Task contract is:

```text
ASSIGNED -> RUNNING

RUNNING -> WAITING
RUNNING -> COMPLETED
RUNNING -> FAILED
RUNNING -> CANCELLED

ASSIGNED -> CANCELLED
WAITING  -> CANCELLED
```

Terminal TaskBudget policy:

```text
ASSIGNED   -> OPEN
RUNNING    -> OPEN
WAITING    -> OPEN

COMPLETED  -> CLOSED
FAILED     -> CLOSED
CANCELLED  -> CLOSED
```

Cancellation establishes the durable boundary before process-local fan-out:

```text
AgentTask CANCELLED + TaskBudget CLOSED
        |
        v
cancel coordinator runner
        |
        v
AgentExecutionSupervisor.cancel_task(task_id)
        |
        v
AgentRuntime cancellation + active-slot release
        |
        v
drain
```

This prevents:

```text
completion resurrecting a cancelled Task
new child execution after cancellation
resume after cancellation
new inference/tool reservation after cancellation
```

Already-admitted RUNNING execution owners may still release active counters
after TaskBudget becomes CLOSED.

## 3. R5-D evidence

The first focused R5-D run found one boundary bug:

```text
expected: TASK_BUDGET_REQUIRED
actual:   CAPABILITY_EXECUTION_FAILED
```

Root cause:

```text
AgentCapabilityDriver raised TaskBudgetRequiredError before its conversion
boundary.
CapabilityRuntime then treated the exception as generic.
```

The boundary fix converted that TaskBudget policy failure to CapabilityError
before CapabilityRuntime normalization.

Evidence after the fix:

```text
Targeted:
    4 passed in 1.02s

Focused:
    43 passed in 11.28s

Broad:
    592 passed
    0 failed
    7 warnings
    65.12s
```

## 4. R5-E evidence

Focused Task cancellation/CAS plus previous R5 regressions:

```text
49 passed in 13.84s
```

Final broad repository regression:

```text
598 passed
0 failed
7 warnings
61.74s
```

The broad gate intentionally excluded:

```text
cl/tests/test_mcp_client_ownership.py
```

because that test belongs to the independent MCP client ownership work stream.

## 5. Warning disposition

The seven warnings are known deprecations:

```text
Starlette anyio BlockingPortal alias
passlib argon2 version access
Starlette HTTP_422_UNPROCESSABLE_ENTITY constant
Alembic path_separator fallback
```

They are not R5 correctness failures.

## 6. Final R5 invariants

R5 closes with all of these true:

```text
[x] process-local Agent task ownership is explicit
[x] cancellation owners cancel + drain
[x] durable TaskBudget exists for R5-controlled Tasks
[x] legacy execution history without budget fails closed
[x] TaskBudget policy/limits are immutable
[x] live execution slots are transactionally accounted
[x] WAITING releases active capacity
[x] resume reacquires without increasing used_executions
[x] logical tool accounting is durable/idempotent
[x] inference/usage accounting is durable/idempotent
[x] Agent delegation depth is derived from durable lineage
[x] recursive Agent cycles obey policy
[x] nested Agent cannot escape Task scope
[x] Task state transitions use revision/CAS in R5 production paths
[x] terminal Task and TaskBudget CLOSED commit atomically
[x] cancellation prevents future TaskBudget growth
[x] cancellation/completion race cannot resurrect Task state
[x] focused R5-D gate passes
[x] focused R5-E gate passes
[x] final broad regression passes in declared scope
```

## 7. Explicitly deferred beyond R5

R5 completion does not claim implementation of:

```text
R6  remote result reconciliation
R7  durable ResumeClaim / claim lease
R8  TaskBranch / FORK execution
R9  retry scheduler
R10 provider retry policy
R11 performance/contention optimization
R12 execution lease / stale RUNNING recovery
```

Those phases build on the R5 guarantees rather than being required to close
R5 itself.

## 8. Final conclusion

R5 is complete for its declared scope:

```text
Cancellation
TaskBudget
Fan-Out / Delegation Policy
Async Ownership
Durable Task CAS closure
```

The next roadmap phase may start from R6 without reopening R5 unless future
regression evidence demonstrates a concrete violation of one of the invariants
above.
