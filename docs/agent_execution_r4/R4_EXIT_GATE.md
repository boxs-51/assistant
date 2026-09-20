# R4 EXIT GATE — Active Budget / Wait TTL / Deadline Hierarchy

**Baseline before completion patches:** `91ffb7052c5927a65c9b3bdd4cc847d50a64f29d`  
**Status:** `PENDING TEST EVIDENCE`

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

After applying the R4 completion patch sequence, C1-C3 may be ticked only when
their focused tests are green.

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

## Apply order

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

## Completion evidence

Fill after execution:

```text
Focused R4:
    <pending>

Cross-phase:
    <pending>

Full suite:
    <pending>

Warnings:
    <pending>
```

R4 is COMPLETE only after all three gates are green.
