# R3 — Execution Lineage Exit Gate

**Baseline:** `340d035b837d8c52d9cf26bff9db9f2678ece964`  
**Status:** PENDING — do not mark R3 COMPLETE until every gate below is green.

## D0 — parent provenance

- [ ] direct AGENT capability creates root E2 with `parent_execution_id=null`
- [ ] nested Agent capability receives typed `caller_agent_execution_id=E1`
- [ ] mismatched typed caller/execution IDs fail closed
- [ ] `connection_id` never creates ancestry

## D1 — deterministic lineage contract

- [ ] `E1 != E2`
- [ ] `I1 != E1`
- [ ] `I1 != E2`
- [ ] `I1.execution_id == E1`
- [ ] `E2.parent_execution_id == E1`
- [ ] task/branch/correlation/trace/request/workflow propagate
- [ ] `E2.causation_id == I1`
- [ ] retry/base fields remain null for fresh delegation
- [ ] non-Agent capability context remains valid with null Agent provenance
- [ ] composition propagates provenance without fabricating it

## D2 — real durable nested graph

The integration gate must use:

```text
AgentRuntime
CapabilityToolExecutionAdapter
CapabilityRuntime
AgentCapabilityDriver
DurableAgentStore
AgentRepository
SqlCapabilityInvocationStore
SQLite
```

Required durable graph:

```text
E1 parent AgentExecution
 |
 +-- I1 Agent capability invocation
 |      execution_id = E1
 |
 +-- E2 child AgentExecution
        parent_execution_id = E1
        causation_id = I1
```

The test must also prove independent parent/child iteration and tool/result
ownership and reconstruct E2 lineage through a fresh DurableAgentStore.

## D3 — migration + regressions

Run:

```powershell
py -m pytest -v se/tests/architecture/test_r3_execution_lineage.py
py -m pytest -v se/tests/integration/test_r3_nested_agent_durable_lineage.py
py -m pytest -v se/tests/integration/test_r3_migration_smoke.py

py -m pytest -q `
  se/tests/architecture/test_r3_a1_a3_representation.py `
  se/tests/architecture/test_r3_b1_b3_capability_lineage.py `
  se/tests/architecture/test_r3_c1_c3_execution_id_authority.py `
  se/tests/architecture/test_unified_capability_drivers.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py `
  se/tests/architecture/test_roadmap_r0_r2.py `
  se/tests/architecture/test_r2_1_stabilization.py `
  se/tests/architecture/test_r2_1_stabilization_v2.py
```

Realtime/E2E regression:

```powershell
py -m pytest -q `
  se/tests/architecture/test_phase6_9_connection_invariant.py `
  se/tests/e2e/test_phase6_9_agent_client_loop.py `
  se/tests/e2e/test_phase6_9_real_tcp_websocket.py `
  se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py `
  se/tests/architecture/test_phase6_11_agent_context_assembly.py
```

If any listed filename has been renamed on the current branch, run its current
canonical replacement instead and record the mapping in the completion note.

Full regression:

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Critical non-regression invariants

```text
Realtime envelope owner stays E1 + I1
CL never allocates E2
hard connection affinity remains invocation-scoped
DIRECT stays outside AgentExecution lineage
SKILL stays inside caller E1
resume keeps the same execution_id
R3 creates no TaskBranch/ResumeClaim behavior
```

## Completion rule

R3 is COMPLETE only when:

1. D0/D1 focused contract tests pass.
2. D2 reconstructs E1→I1→E2 from durable SQLite data.
3. Alembic upgrade-to-head smoke passes and exposes the R3 lineage schema.
4. realtime/remote regressions pass.
5. the complete `se/tests + tools + cl/tests` suite passes.
6. completion evidence is recorded with the final commit SHA and test counts.
