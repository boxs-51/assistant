# R3 D0 + D1→D3 v1 — Patch Review

**Repository:** `boxs-51/assistant`  
**Pinned baseline:** `340d035b837d8c52d9cf26bff9db9f2678ece964`  
**Scope:** close R3 only; no R4/R5/R6/R7 behavior.

## Decision

Direct AGENT capability execution remains supported.

The patch introduces typed provenance:

```text
CapabilityExecutionContext.caller_agent_execution_id
```

Semantics:

```text
Agent-owned nested call:
    execution_id = E1
    caller_agent_execution_id = E1

Direct/non-Agent call:
    execution_id = capability invocation owner/tracking ID
    caller_agent_execution_id = null
```

`AgentCapabilityDriver` creates:

```text
E2 = new AgentExecution ID
E2.parent_execution_id = caller_agent_execution_id
E2.causation_id = I1
```

Therefore a direct AGENT call produces root E2 (`parent=null`) and cannot create a dangling parent from a synthetic capability execution ID.

## Production files

- `se/src/runtimes/capability/contracts/context.py`
- `se/src/runtimes/capability/runtime.py`
- `se/src/runtimes/agent/adapters/tool.py`
- `se/src/runtimes/capability/composition.py`
- `se/src/runtimes/capability/drivers/agent_driver.py`

No SQL/domain migration is added by D0.

## Compatibility test updates

- `test_r3_b1_b3_capability_lineage.py`
- `test_r3_c1_c3_execution_id_authority.py`
- `test_unified_capability_drivers.py`

These tests now explicitly mark Agent-owned invocation provenance where they expect delegation ancestry.

## D1

New:

```text
se/tests/architecture/test_r3_execution_lineage.py
```

Covers:

- direct AGENT root E2 without dangling parent;
- typed nested E1→I1→E2;
- invocation ownership remains E1;
- mismatched provenance fails closed;
- connection affinity never creates ancestry;
- non-Agent context remains source-compatible;
- workflow composition propagates provenance without inventing it.

## D2

New:

```text
se/tests/integration/test_r3_nested_agent_durable_lineage.py
```

Uses real:

- AgentRuntime
- CapabilityToolExecutionAdapter
- CapabilityRuntime
- AgentCapabilityDriver
- PythonCapabilityDriver
- DurableAgentStore
- AgentRepository
- SqlCapabilityInvocationStore
- SQLite

Execution:

```text
E1 parent iteration 1
  -> I1 Agent capability
  -> E2 child
      -> child ordinary tool
      -> child final answer
  -> child result returns to E1
E1 iteration 2
  -> parent final answer
```

The test then queries SQL rows and reconstructs E2 through a fresh DurableAgentStore.

## D3

New:

```text
se/tests/integration/test_r3_migration_smoke.py
docs/agent_execution_r3/R3_EXIT_GATE.md
```

Migration smoke executes real `alembic upgrade head` on disposable SQLite and verifies the R3 lineage columns/indexes.

The exit-gate document defines focused R3, realtime, offline E2E, and full-suite commands.

Canonical R3 docs are updated from stale `NO R3 CODE` status to:

```text
A1→C3 implemented
D0→D3 closure pending
```

They are intentionally not marked R3 COMPLETE yet.

## Static validation

```text
git apply --stat     PASS
git apply --numstat  PASS

AST:
test_r3_execution_lineage.py                 PASS
test_r3_nested_agent_durable_lineage.py      PASS
test_r3_migration_smoke.py                   PASS
```

Patch size:

```text
14 files changed
936 insertions
6 deletions
```

SHA256:

```text
4fa5e4c6c7f0bb87cfb8fc4bf7e5f8bc61b0b852fa72ebe1d7f73492f4128650
```

## Required apply/test order

```powershell
py tools\patch_applier.py --check docs\R3_D0_D1_D3_v1.patch
py tools\patch_applier.py docs\R3_D0_D1_D3_v1.patch

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

py -m pytest -q `
  se/tests/architecture/test_phase6_9_routing.py `
  se/tests/architecture/test_phase6_9_continuation.py `
  se/tests/e2e/test_phase6_9_agent_client_loop.py `
  se/tests/e2e/test_phase6_9_real_tcp_websocket.py `
  se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py `
  se/tests/architecture/test_phase6_11_context_assembly.py `
  se/tests/e2e/test_v1_offline.py

py -m pytest -q se/tests tools cl/tests
```

Do not mark R3 COMPLETE until D1, D2, migration smoke, realtime gates, and the full suite are all green.
