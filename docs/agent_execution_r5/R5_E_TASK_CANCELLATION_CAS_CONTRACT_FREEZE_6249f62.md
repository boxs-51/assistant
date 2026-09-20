# R5-E Contract Freeze — Durable Task Cancellation, Task CAS and Budget Closure

**Audit baseline:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`  
**Dependency:** apply and close R5-D first  
**Document status:** CONTRACT FREEZE / IMPLEMENT AFTER R5-D GREEN  
**Scope:** final R5 subphase only

## 1. Goal

R5-E makes AgentTask terminal/cancellation state durable and race-safe.

It closes the gap between the already-correct process-local
`AgentExecutionSupervisor` and the still-unconditional Task persistence paths.

The final authority split remains:

```text
MultiAgentCoordinator / Task transaction service
    AgentTask state
    TaskBudget OPEN/CLOSED state
    Task cancellation authority

AgentRuntime
    AgentExecution state
    execution active-slot release

AgentExecutionSupervisor
    process-local execution cancellation + drain
```

No component may steal another component's durable authority.

## 2. Exact current defects

### P0-E1 — cancel_task_and_wait bypasses AgentTask revision

Current order:

```text
in-memory task.status = CANCELLED
runner.cancel()
Supervisor.cancel_task(task_id)
drain
DurableAgentStore.update_task(...)
```

The durable update is unconditional.

### P0-E2 — execute_task terminal update bypasses revision

`execute_task()` also uses unconditional `update_task()` after deciding
WAITING/COMPLETED/FAILED/CANCELLED.

Cancellation and completion can race and overwrite one another.

### P0-E3 — TaskBudget is not closed with terminal Task state

`TaskBudgetService.close()` exists, but there is no atomic transaction joining:

```text
AgentTask terminal CAS
TaskBudget CLOSED CAS
```

Separate commits leave a window where a terminal/cancelled Task can admit new
TaskBudget work.

### P0-E4 — Task start is not a durable CAS transition

The coordinator sets:

```text
task.status = RUNNING
```

in memory before execution, but does not establish durable RUNNING state with
revision/CAS first.

### P0-E5 — WAITING Task may currently be executed as a new execution

Architecture requires:

```text
WAITING execution -> RESUME same execution
```

not:

```text
WAITING Task -> create unrelated new AgentExecution
```

R5-E must reject ordinary Task execute/start while Task status is WAITING.

## 3. Frozen Task state transitions for current non-branch runtime

Allowed:

```text
ASSIGNED -> RUNNING

RUNNING -> WAITING
RUNNING -> COMPLETED
RUNNING -> FAILED
RUNNING -> CANCELLED

ASSIGNED -> CANCELLED
WAITING  -> CANCELLED
```

Idempotent terminal observations:

```text
CANCELLED -> CANCELLED
COMPLETED -> COMPLETED
FAILED    -> FAILED
```

Forbidden:

```text
CANCELLED -> RUNNING
COMPLETED -> RUNNING
FAILED    -> RUNNING
WAITING   -> new execution RUNNING
```

R7 resumes the same WAITING AgentExecution; it does not use Task execute/start.

## 4. TaskBudget closure policy

Freeze:

```text
AgentTask ASSIGNED   -> TaskBudget OPEN
AgentTask RUNNING    -> TaskBudget OPEN
AgentTask WAITING    -> TaskBudget OPEN

AgentTask COMPLETED  -> TaskBudget CLOSED
AgentTask FAILED     -> TaskBudget CLOSED
AgentTask CANCELLED  -> TaskBudget CLOSED
```

Terminal TaskBudget is never silently reopened.

R9 retry/branch policy must decide to create another execution before the Task
is terminal, or use a new explicit future contract.

## 5. Atomic terminal transaction

For a terminal Task transition:

```text
BEGIN
    load AgentTask
    load TaskBudget

    validate Task source state
    CAS AgentTask revision -> terminal target
    CAS TaskBudget revision -> CLOSED + closed_at
COMMIT
```

All-or-nothing invariant:

```text
Task terminal + budget OPEN       FORBIDDEN
Task nonterminal + budget CLOSED  FORBIDDEN for R5-controlled Tasks
```

An already-CLOSED budget with the same already-terminal Task is idempotent.

A CLOSED budget paired with a nonterminal Task is `TASK_BUDGET_CONFLICT`.

## 6. Cancellation order

Frozen production cancellation order:

```text
1. Durable Task CANCELLED + TaskBudget CLOSED transaction wins first.
2. Cancel coordinator background runner, if any.
3. AgentExecutionSupervisor.cancel_task(task_id).
4. Drain runner / owned AgentRuntime tasks.
5. Return the durable winning Task state.
```

Reason: new durable growth must be blocked before process-local cancellation is
best-effort propagated.

## 7. Execution authority during Task cancellation

R5-E does not directly CAS live RUNNING AgentExecutions to CANCELLED.

Local live execution owners are signalled through the Supervisor and
AgentRuntime persists their cancellation while releasing TaskBudget active
slots.

A durable WAITING AgentExecution may remain represented as WAITING after its
Task is CANCELLED, but it is no longer resumable because TaskBudget is CLOSED.

This avoids violating AgentRuntime execution-state authority.

R7 may later add an explicit resume-claim rejection surface; R12 handles stale
RUNNING recovery.

## 8. Closed-budget release rule

R5-C intentionally permits active execution release after TaskBudget closure.

Therefore after Task cancellation:

```text
RUNNING owner receives cancellation
    ->
AgentRuntime RUNNING -> CANCELLED
    +
active_executions -= 1
active_parallel_agents -= 1 if delegated
```

must still work while TaskBudget state is CLOSED.

No new reservations are allowed while CLOSED.

## 9. Start transition

Before allocating/starting a new Task root execution:

```text
AgentTask ASSIGNED revision=N
    CAS
AgentTask RUNNING revision=N+1
```

Only after the durable RUNNING transition succeeds may the coordinator invoke
the AgentRuntime executor.

If cancellation wins first, Task start fails and no new AgentExecution is
admitted.

## 10. Completion transition

When AgentRuntime returns:

```text
WAITING result
    -> Task RUNNING -> WAITING
    -> budget stays OPEN

successful final result
    -> Task RUNNING -> COMPLETED
    -> budget CLOSED atomically

failed/timeout result
    -> Task RUNNING -> FAILED
    -> budget CLOSED atomically

caller/task cancellation
    -> cancellation transaction is authoritative
```

If completion loses CAS to cancellation, completion must not resurrect Task.

The coordinator reloads/synchronizes the durable winner instead.

## 11. In-memory model rule

`self._tasks[task_id]` is a cache/view, not durable transition authority.

After every Task CAS/terminal transaction, update the in-memory AgentTask from
the returned durable row including:

```text
revision
status
wait_reasons
output
error
updated_at
```

Never mutate in-memory terminal state before the durable winner is known.

## 12. Required service API shape

The implementation may use names equivalent to:

```python
async def transition_task(
    task_id,
    *,
    allowed_source_states,
    target_state,
    values,
) -> AgentTaskRecord

async def terminalize_task(
    task_id,
    *,
    allowed_source_states,
    target_state,
    values,
) -> AgentTaskRecord

async def cancel_task(task_id, *, values) -> AgentTaskRecord
```

`terminalize_task()` and `cancel_task()` must couple Task CAS and TaskBudget
CLOSED in one UoW.

The exact Python names are not protocol contracts; the transaction semantics
are.

## 13. Cancellation idempotency

Repeated cancellation:

```text
CANCELLED + CLOSED
    -> return same durable terminal condition
    -> do not increment revisions forever
    -> process-local cancel/drain remains safe/idempotent
```

Cancellation after COMPLETED/FAILED:

```text
do not rewrite to CANCELLED
return/raise terminal-state conflict according to coordinator API
```

Recommended coordinator behavior: return the existing terminal Task unchanged.

## 14. Resume/new-growth invalidation

After cancellation commit, all these TaskBudget operations must fail because
the budget is CLOSED:

```text
new execution
resume slot
tool-call reservation
inference reservation
branch reservation
```

Usage/release accounting that finalizes already-admitted work may still write
where required by R5-C.

## 15. Multi-worker boundary

R5-E provides durable cancellation authority through Task + TaskBudget state.

The current process signals its local Supervisor-owned executions.

Cross-worker instantaneous task signalling is not invented in R5-E. A worker
that continues after another worker closes the TaskBudget is stopped at the
next TaskBudget admission boundary; stale RUNNING recovery remains R12.

Do not introduce an ad-hoc distributed lease in R5-E.

## 16. Repository changes

Required:

```text
KEEP  AgentRepository.compare_and_set_task
KEEP  compare_and_set_task_budget
KEEP  DurableAgentStore.compare_and_set_task

ADD/USE one UoW transaction for Task + TaskBudget terminal state
STOP correctness-sensitive coordinator use of update_task()
```

`update_task()` may remain only as legacy compatibility for non-R5 callers if
tests still require it. R5 production Task lifecycle must not call it.

## 17. Required race tests

```text
cancel vs Task start
cancel vs normal completion
cancel vs failure completion
cancel vs WAITING completion
cancel vs resume
cancel vs child-Agent admission
repeat cancellation
completion after cancellation
TaskBudget CLOSED blocks new inference/tool/new execution
active execution release after CLOSED still succeeds
```

## 18. Regression requirements

Must keep green:

```text
R5-D depth/cycle/task-scope
R5-C accounting/idempotency
R5-B representation/CAS
R5-A Supervisor cancellation/drain
R4 WAITING/active budget
R3 lineage
```

The separate MCP-client ownership test remains outside this work stream.

## 19. Explicit non-scope

```text
distributed cancellation message bus   not introduced here
ResumeClaim lease                       R7
TaskBranch/FORK                         R8
retry scheduler                         R9
provider retry                          R10
stale RUNNING execution lease           R12
```

## 20. R5 final exit

After R5-E passes, R5 may close only if the combined focused and broad gates
show:

```text
no unbounded delegation
no recursive Agent cycle under deny policy
no TaskBudget escape
no Task cancel/completion resurrection
no new work after Task cancellation
no local orphan Agent task
```
