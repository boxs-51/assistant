# R5 Remaining Audit and Sequential Implementation Plan

**Repository:** `boxs-51/assistant`  
**Audited HEAD:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`  
**Scope:** remaining R5 only  
**Out of scope:** `cl/tests/test_mcp_client_ownership.py` and its separate MCP-client ownership work stream.

# 1. Executive status

```text
R5-A Async ownership                         COMPLETE
R5-B TaskBudget durable core                 COMPLETE
R5-C Live TaskBudget accounting              COMPLETE by test evidence
R5-D Delegation depth / ancestry / cycles    MISSING
R5-E Durable Task cancellation / Task CAS    MISSING

R5 overall                                  NOT COMPLETE
```

No additional R5 subphase is required beyond D and E on the current roadmap.
R6 reconciliation, R7 ResumeClaim, R8 TaskBranch/FORK and later work remain
separate phases.

# 2. R5-D missing work

## P0-D1 — live delegation depth is hard-coded to zero

`AgentRuntime._begin_durable_execution()` currently sends:

```text
delegation_depth=0
```

to every task-scoped new execution.

This means the durable `max_delegation_depth` policy does not yet constrain
real nested Agent execution.

## P0-D2 — recursive cycle policy is persisted but not enforced

TaskBudget already stores:

```text
deny_recursive_agent_cycle
```

but no production path traverses `AgentExecution.parent_execution_id` and
checks durable ancestor `agent_id` values.

## P0-D3 — Agent -> Agent may escape TaskBudget

`AgentCapabilityDriver` currently allows a nested Agent call with:

```text
caller_agent_execution_id != None
task_id == None
```

A nested Agent must fail `TASK_BUDGET_REQUIRED`; only a direct root Agent may
remain taskless.

## P0-D4 — no durable ancestry validator

R5-D must fail closed when the parent chain contains:

```text
missing parent execution
cross-task parent execution
execution-id parent loop
```

## P0-D5 — stable TaskBudget errors are downgraded at tool boundary

`normalize_tool_exception()` currently converts a non-CapabilityError carrying
`.code` into:

```text
CAPABILITY_EXECUTION_FAILED
details.original_error_code = ...
```

R5-D requires `TASK_BUDGET_REQUIRED`, `DELEGATION_DEPTH_EXCEEDED`,
`AGENT_DELEGATION_CYCLE`, `TASK_BUDGET_CONFLICT`, etc. to remain the primary
machine-readable error code.

# 3. R5-D implementation sequence

```text
D1  Add DelegationAdmission + AGENT_DELEGATION_CYCLE
D2  Resolve durable ancestry from parent_execution_id
D3  Derive live depth in AgentRuntime; remove depth=0 shortcut
D4  Enforce task scope at AgentCapabilityDriver
D5  Preserve TaskBudget errors through tool normalization
D6  Real SQLite + boundary + regression tests
D7  Exit-gate evidence and formal close
```

No migration is required: `agent_executions.parent_execution_id`,
`agent_executions.agent_id` and `task_id` already exist and are sufficient.

# 4. R5-E missing work

R5-E is larger because it closes Task-level concurrency races rather than only
adding a policy check.

## P0-E1 — cancellation writes Task state unconditionally

`MultiAgentCoordinator.cancel_task_and_wait()` currently:

```text
mutates in-memory task to CANCELLED
cancels runners
calls DurableAgentStore.update_task()
```

The durable write bypasses `AgentTask.revision`.

A completion racing cancellation can therefore overwrite the cancellation or
vice versa.

## P0-E2 — normal Task execution completion also bypasses CAS

`execute_task()` finishes through unconditional `update_task()` for:

```text
WAITING
COMPLETED
FAILED
CANCELLED
```

Task revision exists but is not the production authority yet.

## P0-E3 — Task cancellation does not close TaskBudget atomically

A cancellation must establish, in one durable transaction:

```text
AgentTask -> CANCELLED
TaskBudget -> CLOSED
```

before future work is admitted.

Otherwise a concurrent child/new execution/inference/tool reservation can be
admitted between two separate commits.

## P0-E4 — WAITING durable executions remain resumable unless budget is closed

Closing TaskBudget must precede/process-local cancellation fan-out so that
future:

```text
resume
new execution
inference reservation
tool reservation
branch reservation
```

fail closed even if no local execution owner exists.

R7 later adds durable ResumeClaim leases; R5-E only establishes Task terminal
authority.

## P0-E5 — in-memory task state is mutated before durable winner is known

The in-memory object must be updated from the durable CAS winner, not used as
the source of truth for a correctness-sensitive transition.

## P1-E1 — direct execute and background start share lifecycle code but different runner ownership

Both must use the same durable Task transition protocol. `_running_tasks` is
only process-local convenience; AgentExecutionSupervisor remains execution
ownership authority.

## P1-E2 — terminal TaskBudget policy needs one explicit rule

Freeze for R5-E:

```text
Task CANCELLED  -> TaskBudget CLOSED
Task COMPLETED  -> TaskBudget CLOSED
Task FAILED     -> TaskBudget CLOSED
Task WAITING    -> TaskBudget OPEN
Task RUNNING    -> TaskBudget OPEN
```

Future R9 retry must schedule retry before a Task becomes terminal; a terminal
Task is never silently reopened.

# 5. R5-E implementation sequence

```text
E1  Add atomic Task + TaskBudget terminal transition service
E2  Add Task CAS helpers for nonterminal transitions
E3  Migrate coordinator ASSIGNED/WAITING -> RUNNING to CAS
E4  Migrate RUNNING -> WAITING/COMPLETED/FAILED/CANCELLED to CAS
E5  Cancellation: durable CANCELLED + budget CLOSED first
E6  Process-local runner/Supervisor cancel + drain second
E7  Prevent completion-after-cancel resurrection
E8  Block resume/new growth after cancellation
E9  Race/integration/regression tests
E10 Formal R5 completion gate
```

# 6. Frozen R5-E cancellation order

```text
HTTP/task cancellation request
        |
        v
atomic durable transaction
    AgentTask CAS -> CANCELLED
    TaskBudget CAS -> CLOSED
        |
        v
cancel process-local coordinator runner
        |
        v
AgentExecutionSupervisor.cancel_task(task_id)
        |
        v
AgentRuntime owners transition RUNNING executions to CANCELLED
and release remaining active slots
        |
        v
drain all owned tasks
        |
        v
return durable CANCELLED Task
```

This order ensures the durable Task/TaskBudget boundary rejects new growth
before cancellation propagation is merely process-local.

# 7. R5-E transaction rules

TaskBudget closure must not prevent already-active execution owners from
releasing active counters. The existing R5-C finish path intentionally does
not require OPEN for release mutations.

R5-E must not forcibly steal a live RUNNING AgentExecution from AgentRuntime.
Execution lifecycle authority remains AgentRuntime.

R5-E may invalidate future resume/new work by closing TaskBudget. Whole-process
stale RUNNING recovery remains R12.

# 8. Patch order

Apply and test in this exact order:

```text
PATCH 1  R5-D delegation policy
    ↓ focused + broad regression
PATCH 2  R5-D completion evidence
    ↓
PATCH 3  R5-E durable Task cancellation/CAS
    ↓ focused race tests + broad regression
PATCH 4  R5 final completion evidence
```

Do not apply R5-E before R5-D is green because E closes the shared Task scope
that D relies on for nested-Agent admission.

# 9. Required gates

## R5-D focused

```text
durable ancestry
depth cap
cycle policy true/false
taskless nested Agent rejection
stable tool error propagation
R5-C/R5-B/R5-A/R4 regression
```

## R5-E focused

```text
cancel-vs-complete race
cancel-vs-resume
cancel-vs-new-child
repeat cancellation idempotency
Task + TaskBudget atomic close
no terminal Task resurrection
Supervisor drain
R5-D/R5-C regression
```

## Broad gate

Use the repository suite with the explicitly separate MCP-client ownership test
excluded while that other work stream is active:

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore e:\assistant\cl\tests\test_mcp_client_ownership.py
```

# 10. R5 final exit gate

R5 can be marked COMPLETE only when:

```text
[ ] no orphan Agent/provider/tool asyncio child task
[ ] shared TaskBudget is durable and live
[ ] active slots track execution lifecycle atomically
[ ] inference/tool/usage accounting is durable/idempotent
[ ] nested Agent depth is derived from durable lineage
[ ] recursive Agent cycles obey policy
[ ] nested Agent cannot escape Task scope
[ ] Task cancellation uses revision/CAS
[ ] Task cancellation atomically closes TaskBudget
[ ] future resume/new growth is blocked after cancellation
[ ] completion cannot resurrect a cancelled Task
[ ] focused D/E gates pass
[ ] broad regression passes in declared scope
```
