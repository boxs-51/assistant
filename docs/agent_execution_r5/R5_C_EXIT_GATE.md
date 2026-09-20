# R5-C Live TaskBudget Accounting Exit Gate

**Implementation baseline:** `d7dcbde5177fad17274b6049d80052bbb2e17b17`

**Completion commit:** `6249f62aaa8e6a4eab4c4902f09db0d999654dae`

**Status:** COMPLETE

## Scope

```text
C1 application TaskBudget policy + DI
C2 atomic AgentTask + TaskBudget creation
C3 atomic new AgentExecution admission
C4 atomic WAITING/resume/terminal active-slot accounting
C5 durable inference reservation + actual usage accounting
C6 durable logical tool-call reservation
C7 R4/R5-A/R5-B regression gate
```

## Production invariants

1. `AgentExecutionLimits` remains execution-local.
2. Task-wide limits come from `config.agent.task_budget`.
3. New multi-agent Task and TaskBudget commit together.
4. New task-scoped AgentExecution and TaskBudget admission commit together.
5. WAITING releases active TaskBudget capacity in the same transaction as the
   execution transition.
6. Resume reacquires active TaskBudget capacity in the same transaction as
   WAITING -> RUNNING.
7. Terminal/cancel/failure releases active capacity exactly once.
8. Resume never increments `used_executions`.
9. One logical inference `request_id` consumes one inference reservation.
10. Actual provider tokens/cost are accounted idempotently by request_id.
11. One logical `tool_call_id` consumes one task-wide tool reservation;
    retry/resume replay does not charge it again.
12. Taskless root execution remains source-compatible.
13. Delegation-depth/cycle enforcement remains R5-D.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r5_c1_config_policy.py `
  se/tests/integration/test_r5_c2_c4_atomic_task_budget.py `
  se/tests/integration/test_r5_c5_c6_runtime_accounting.py `
  se/tests/architecture/test_r5_c7_regression_contract.py `
  se/tests/architecture/test_r5_b_task_budget_domain.py `
  se/tests/architecture/test_r5_b_task_budget_service.py `
  se/tests/architecture/test_r5_a4_a6_ownership_integration.py `
  se/tests/architecture/test_r4_b2_resume.py `
  se/tests/architecture/test_r4_c3_nested_agent_budget.py `
  se/tests/architecture/test_r4_exit_gate.py
```

## Broad regression gate

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore e:\assistant\cl\tests\test_mcp_client_ownership.py
```

`cl/tests/test_mcp_client_ownership.py` is intentionally excluded from this
R5-C closure because it belongs to a separate MCP-client async-ownership work
stream. Its exclusion is not an R5-C failure and does not weaken any
TaskBudget/accounting invariant exercised by this phase.

## Completion evidence

```text
Initial R5-C patch check:
    PASS

Initial focused gate:
    44 passed, 1 failed

Observed failure:
    test_r5_c3_c4_execution_slot_transitions_are_atomic

Root cause:
    Test expectation contradicted durable reservation idempotency.
    Replaying an already committed RESUME_EXECUTION reservation must be
    idempotent instead of being classified as a fresh stale CAS.

Regression fix:
    R5_C_TEST_IDEMPOTENT_RESUME_FIX_v1.patch

Fix patch check:
    PASS

Targeted atomic TaskBudget gate after fix:
    4 passed in 22.96s

Focused R5-C/R5-B/R5-A/R4 gate after fix:
    45 passed in 74.78s

Broad repository regression:
    584 passed, 7 warnings in 424.25s
    0 failures
    explicit out-of-scope exclusion:
        cl/tests/test_mcp_client_ownership.py

Warnings:
    Starlette anyio BlockingPortal deprecation
    passlib argon2 version deprecation
    Starlette HTTP_422 constant deprecation
    Alembic path_separator deprecation
```

## Verified semantics

- AgentTask + initial TaskBudget commit atomically.
- New task-scoped AgentExecution admission and TaskBudget counters commit
  atomically.
- WAITING releases active capacity in the same transaction as execution CAS.
- Resume reacquires active capacity without incrementing used_executions.
- Terminal/cancel/failure paths release active capacity exactly once.
- Already committed RESUME_EXECUTION replay is idempotent and does not
  double-charge active capacity.
- A genuinely stale, previously uncommitted source revision is rejected
  without mutating AgentExecution or TaskBudget.
- Logical inference request_id consumes one inference reservation.
- Actual inference token/cost usage is accounted idempotently.
- Logical tool_call_id consumes one task-wide tool reservation across
  retry/resume replay.
- Taskless root Agent execution remains compatible.
- Delegation-depth and recursive-cycle enforcement remain R5-D scope.

## Phase status

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C1 COMPLETE
R5-C2 COMPLETE
R5-C3 COMPLETE
R5-C4 COMPLETE
R5-C5 COMPLETE
R5-C6 COMPLETE
R5-C7 COMPLETE
R5-C COMPLETE

R5-D PENDING
R5-E PENDING
R5 overall NOT COMPLETE
```
