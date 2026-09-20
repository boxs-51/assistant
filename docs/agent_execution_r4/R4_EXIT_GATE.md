# R4 EXIT GATE — Active Budget / Wait TTL / Deadline Hierarchy

**Baseline before completion patches:** `91ffb7052c5927a65c9b3bdd4cc847d50a64f29d`  
**Completion HEAD:** `90065c730c33ab3261063183570278d4ea13a424`  
**Status:** `COMPLETE`

## Required implementation

```text
[x] A1 durable budget / wait TTL representation
[x] A2 monotonic active-budget context
[x] B1 RUNNING -> WAITING budget freeze
[x] B2 resume / expiry / race / restart
[x] C1 iteration deadline hierarchy
[x] C2 capability timeout hierarchy
[x] C3 nested Agent child budget
[x] D full regression evidence
```

All implementation items are present on completion HEAD `90065c7` and the
required R4 contracts are covered by the recorded focused/full-suite evidence.

## Frozen invariants

```text
WAITING does not consume active execution budget.
Raw monotonic deadlines are never persisted.
Resume reconstructs the same execution_id.
Legacy WAITING NULL budget fails closed.
wait_expires_at uses wall-clock UTC.
Iteration <= execution budget.
Operation <= iteration <= execution.
Normal TOOL <= tool_timeout_seconds.
AGENT/LONG_RUNNING do not use one-shot tool timeout.
Nested synchronous Agent <= parent execution remaining.
Nested synchronous Agent <= parent iteration remaining.
R3 E1/I1/E2 lineage remains unchanged.
```

## Applied completion sequence

```text
R4_C1_ITERATION_DEADLINE_v1.patch
R4_C2_CAPABILITY_TIMEOUT_HIERARCHY_v1.patch
R4_C3_NESTED_AGENT_BUDGET_v1.patch
R4_D_EXIT_GATE_v1.patch
```

## Focused R4 gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r4_a1_representation.py `
  se/tests/integration/test_r4_a1_migration.py `
  se/tests/architecture/test_r4_a2_execution_budget_context.py `
  se/tests/architecture/test_r4_b1_waiting_budget_freeze.py `
  se/tests/integration/test_r4_b1_waiting_budget_freeze.py `
  se/tests/architecture/test_r4_b2_resume.py `
  se/tests/integration/test_r4_b2_resume_restart.py `
  se/tests/architecture/test_r4_c1_iteration_deadline.py `
  se/tests/architecture/test_r4_c2_capability_timeout_hierarchy.py `
  se/tests/architecture/test_r4_c3_nested_agent_budget.py `
  se/tests/architecture/test_r4_exit_gate.py
```

## Cross-phase regression

```powershell
py -m pytest -q `
  se/tests/architecture/test_roadmap_r0_r2.py `
  se/tests/architecture/test_r2_1_stabilization.py `
  se/tests/architecture/test_r2_1_stabilization_v2.py `
  se/tests/architecture/test_r3_execution_lineage.py `
  se/tests/integration/test_r3_nested_agent_durable_lineage.py `
  se/tests/architecture/test_phase5_contracts.py `
  se/tests/architecture/test_phase5_adapters.py `
  se/tests/architecture/test_phase5_runtime.py `
  se/tests/architecture/test_phase5_tool_execution_coordinator.py `
  se/tests/architecture/test_phase6_9_continuation.py `
  se/tests/e2e/test_phase6_9_real_tcp_websocket.py `
  se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py `
  se/tests/architecture/test_phase6_11_context_assembly.py
```

## Full gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Completion evidence — 2026-09-20

```text
Focused R4 captured command:
    40 passed, 1 warning in 6.37s

Cross-phase regression:
    96 passed in 31.23s

Full suite:
    534 passed, 5 warnings in 72.41s

Failures:
    0
```

The captured focused command did not explicitly list
`test_r4_a2_execution_budget_context.py`; the full `se/tests` suite includes
that test module and completed with 534 passed and no failures. Therefore the
A2 contract is covered by the recorded full-gate evidence even though the
captured focused command was a subset of the documented focused command.

Warnings were deprecation warnings plus Windows asyncio Proactor/subprocess
cleanup warnings emitted after the suite. No warning demonstrates an R4 active
budget, WAITING TTL, deadline hierarchy, or nested-Agent budget contract
failure. Async ownership cleanup is audited separately in R5-A.

## Closure

```text
R4-A1 COMPLETE
R4-A2 COMPLETE
R4-B1 COMPLETE
R4-B2 COMPLETE
R4-C1 COMPLETE
R4-C2 COMPLETE
R4-C3 COMPLETE
R4-D  COMPLETE

R4 COMPLETE
```

R5 may begin from completion HEAD `90065c7` without reopening R4 timing
semantics unless a later regression produces direct contradictory evidence.
