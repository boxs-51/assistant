# R5-C Live TaskBudget Accounting Exit Gate

**Baseline:** `d7dcbde5177fad17274b6049d80052bbb2e17b17`

**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

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

## Full gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Completion evidence

```text
Patch check: <pending>
Focused:     <pending>
Full:        <pending>
```

## Phase status

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C1 IMPLEMENTED
R5-C2 IMPLEMENTED
R5-C3 IMPLEMENTED
R5-C4 IMPLEMENTED
R5-C5 IMPLEMENTED
R5-C6 IMPLEMENTED
R5-C7 IMPLEMENTED
R5-C PENDING TEST EVIDENCE

R5-D PENDING
R5-E PENDING
R5 overall NOT COMPLETE
```
