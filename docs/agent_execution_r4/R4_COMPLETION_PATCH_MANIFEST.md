# R4 COMPLETION PATCH MANIFEST

**Repository:** `boxs-51/assistant`  
**Audited HEAD:** `91ffb7052c5927a65c9b3bdd4cc847d50a64f29d`  
**Starting status:** A1/A2/B1/B2 complete; C1/C2/C3/D remaining.

## Apply order

### 1. R4-C1 — Iteration Deadline

```powershell
py tools\patch_applier.py --check docs\R4_C1_ITERATION_DEADLINE_v1.patch
py tools\patch_applier.py docs\R4_C1_ITERATION_DEADLINE_v1.patch
py -m pytest -v se/tests/architecture/test_r4_c1_iteration_deadline.py
```

SHA256:

```text
3e3bba45479341e696060a732ce60bad6256e12349ae8389290ae8dc03efcd15
```

### 2. R4-C2 — Capability Timeout Hierarchy

```powershell
py tools\patch_applier.py --check docs\R4_C2_CAPABILITY_TIMEOUT_HIERARCHY_v1.patch
py tools\patch_applier.py docs\R4_C2_CAPABILITY_TIMEOUT_HIERARCHY_v1.patch
py -m pytest -v se/tests/architecture/test_r4_c2_capability_timeout_hierarchy.py
```

SHA256:

```text
d3f9784b7aa238f24a2534ca9290f74b4fdfb011d7bd2b108796162ed57ee802
```

### 3. R4-C3 — Nested Agent Budget

```powershell
py tools\patch_applier.py --check docs\R4_C3_NESTED_AGENT_BUDGET_v1.patch
py tools\patch_applier.py docs\R4_C3_NESTED_AGENT_BUDGET_v1.patch
py -m pytest -v se/tests/architecture/test_r4_c3_nested_agent_budget.py
```

SHA256:

```text
85bb248d2cf55107117b2dc6c9d45573ee4a279157af29db1b3c7da24ff4568d
```

### 4. R4-D — Exit Gate

```powershell
py tools\patch_applier.py --check docs\R4_D_EXIT_GATE_v1.patch
py tools\patch_applier.py docs\R4_D_EXIT_GATE_v1.patch
py -m pytest -v se/tests/architecture/test_r4_exit_gate.py
```

SHA256:

```text
efebc2fb450293464a81dbaba362fb4a66526a32288af7e9a0aef1e174b2cce8
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

## Expected final R4 state after green gates

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

Do not begin R5/R7 implementation until these gates are green and
`R4_EXIT_GATE.md` is updated with the actual evidence.
