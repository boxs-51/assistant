# R5-C Completion — Live TaskBudget Accounting

**Completion commit:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`  
**Status:** COMPLETE  
**Scope:** R5-C1 through R5-C7

## What R5-C closed

R5-C connected the durable TaskBudget representation introduced by R5-B to
the live Agent execution path without changing execution-local
`AgentExecutionLimits`.

The completed runtime contract is:

```text
Task creation
    AgentTask INSERT
    TaskBudget INSERT
    one transaction

New task-scoped execution
    TaskBudget admission
    AgentExecution RUNNING revision=1
    NEW_EXECUTION reservation
    one transaction

WAITING
    AgentExecution RUNNING -> WAITING CAS
    active TaskBudget slot release
    RELEASE_EXECUTION reservation
    one transaction

Resume
    AgentExecution WAITING -> RUNNING CAS
    active TaskBudget slot reacquire
    RESUME_EXECUTION reservation
    one transaction

Terminal/cancel/failure
    AgentExecution RUNNING -> terminal CAS
    active TaskBudget slot release
    one transaction
```

Inference and logical tool accounting are also durable and idempotent:

```text
INFERENCE key = request_id
USAGE key     = request_id
TOOL_CALL key = tool_call_id
```

## Idempotent resume correction

The first focused run exposed one incorrect regression-test expectation.

A previously committed resume reservation:

```text
RESUME_EXECUTION(exec-flow:2)
```

was replayed after the execution had already advanced to a later revision.
The original test expected `TASK_BUDGET_CONFLICT`.

That expectation was wrong.

The durable reservation ledger is the commit authority. Replaying the exact
same logical reservation after a lost acknowledgement must return the already
committed transition idempotently and must not consume another active slot.

The regression test was corrected to distinguish:

```text
same committed reservation
    -> idempotent replay

different never-committed stale source revision
    -> conflict
```

No production TaskBudget implementation change was required for this issue.

## Test evidence

Targeted atomic TaskBudget suite:

```text
4 passed in 22.96s
```

Focused R5-C gate plus R5-B/R5-A/R4 regressions:

```text
45 passed in 74.78s
```

Broad repository regression:

```text
584 passed
0 failed
7 warnings
424.25s
```

The command intentionally excluded:

```text
cl/tests/test_mcp_client_ownership.py
```

That test belongs to a separate MCP-client ownership work stream and is
outside R5-C scope. The exclusion is therefore recorded as scope separation,
not as an R5-C exception or failure.

The seven warnings are deprecation warnings from Starlette, passlib and
Alembic and do not represent TaskBudget correctness failures.

## R5 status after closure

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C COMPLETE
R5-D PENDING
R5-E PENDING

R5 overall NOT COMPLETE
```

## Next phase

R5-D owns:

```text
durable delegation depth derivation
max_delegation_depth enforcement
durable Agent ancestry traversal
recursive Agent cycle rejection
TASK_BUDGET_REQUIRED for Agent -> Agent delegation without task scope
```

R5-D must not use caller-provided metadata as ancestry authority. Durable
`AgentExecution.parent_execution_id` lineage is the source of truth.
