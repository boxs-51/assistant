# R3 C1→C3 — Exact Call-Site Audit

**Repository:** `boxs-51/assistant`  
**Remote audited baseline:** `d9f0cb389a9def61db8b56dc0b52f6c2960d27d7`  
**Required local prerequisite:** R3-B1→B3 already applied and green  
**Observed local gate from user:** B focused `7/7`, compatibility `38/38`, A `3/3`, R0–R2 `9/9`, full regression `481 passed`  
**Scope:** C1 → C3 only  
**Excluded:** D1 contract/architecture exit gate, D2 real-SQLite nested durable graph, D3 full final R3/realtime regression design.

## 1. Status before C

R3-A1→A3 is COMPLETE.

R3-B1→B3 is COMPLETE by local test evidence.

The remaining P0 at the C boundary is still:

```text
AgentCapabilityDriver
    child Agent execution_id = caller CapabilityExecutionContext.execution_id
```

With B applied, `CapabilityExecutionContext.execution_id` is explicitly the caller E1. Reusing it for the child violates the frozen invariant:

```text
1 AgentRuntime.execute() = 1 execution_id = 1 durable AgentExecution
```

Correct graph:

```text
Parent AgentExecution E1
    |
    +-- CapabilityInvocation I1
    |       execution_id = E1
    |
    +-- Child AgentExecution E2
            execution_id = NEW
            parent_execution_id = E1
            causation_id = I1
```

## 2. Exact execution-ID allocation call-sites

Audit found only three production AgentExecution creation boundaries that must use the canonical factory in C:

### Root chat-Agent

`se/src/runtimes/workflow/runtime.py`

Current:

```text
execution_id = "agent_" + uuid
```

Target:

```text
execution_id = AgentExecutionIdFactory.new_id()
```

`WorkflowRuntime` accepts an optional injected factory so existing unit construction remains source-compatible.

### Root multi-agent Task

`se/src/runtimes/agent/coordinator.py`

Current:

```text
execution_id = "exec_" + uuid
```

This ID is also passed into the canonical executor/AgentRuntime bridge.

Target:

```text
execution_id = shared AgentExecutionIdFactory.new_id()
```

This preserves the existing invariant that the coordinator allocates E1 once and `main.py::execute_registered_agent_task()` consumes that exact E1 without allocating another execution ID.

### Nested Agent capability

`se/src/runtimes/capability/drivers/agent_driver.py`

Current P0:

```text
child.execution_id = context.execution_id
```

where `context.execution_id` is caller E1.

Target:

```text
child.execution_id = factory.new_id()       # E2
child.parent_execution_id = context.execution_id  # E1
child.causation_id = context.invocation_id        # I1
```

## 3. C1 — canonical allocator

### ADD

`se/src/runtimes/agent/ids.py`

Concrete contract:

```text
AgentExecutionIdFactory.new_id() -> str
```

Default policy uses UUID-backed `exec_...` IDs.

The constructor accepts a token factory to support deterministic tests without changing production allocation semantics.

The factory allocates identity only. It does not persist rows and does not perform lifecycle transitions. `AgentRuntime` remains durable execution authority.

### Explicit non-users

C does not migrate:

- DirectChat IDs;
- capability invocation IDs;
- tool-call IDs;
- inference request IDs;
- checkpoint IDs;
- connection/message/session/task IDs.

## 4. C2 — root allocation migration

### MODIFY — `WorkflowRuntime`

Inject optional `AgentExecutionIdFactory`.

Replace inline `agent_<uuid>` Agent execution allocation with `factory.new_id()`.

Correlation generation remains separate. `correlation_id` is not an execution ID.

### MODIFY — `MultiAgentCoordinator`

Inject optional `AgentExecutionIdFactory`.

Replace inline `exec_<uuid>` allocation with the factory.

Existing task/session/message/correlation UUID allocation remains unchanged because those are not AgentExecution identities.

### KEEP — `main.py::execute_registered_agent_task`

No second allocation is added.

It continues to accept:

```text
execution_id
correlation_id
parent_execution_id
```

from the coordinator and builds `AgentExecutionContext` with exactly that execution ID.

## 5. Shared production wiring

### MODIFY — `ApplicationContainer`

Add:

```text
agent_execution_id_factory
```

as an optional application dependency.

### MODIFY — `bootstrap_runtime_kernel`

Create exactly one production factory instance:

```text
agent_execution_id_factory = AgentExecutionIdFactory()
```

Wire the same instance into:

- `ApplicationContainer`;
- `MultiAgentCoordinator`;
- `WorkflowRuntime`.

`AgentRuntime` itself does not allocate IDs and therefore does not need the factory.

## 6. C3 — child Agent creation

### MODIFY — `AgentCapabilityDriver`

Constructor accepts optional injected factory for backward-compatible direct tests.

Production child creation becomes:

```text
E2 = factory.new_id()

AgentExecutionContext(
    execution_id = E2,
    parent_execution_id = E1,
    causation_id = I1,

    session_id = caller session,
    task_id = caller task,
    branch_id = caller branch,
    correlation_id = caller correlation,
    trace_id = caller trace,
    request_id = caller request,
    workflow_id = caller workflow,

    connection_id = optional current routing affinity,
)
```

For fresh delegation:

```text
retry_of_execution_id = null
base_execution_id = null
base_checkpoint_id = null
```

by existing context defaults.

The existing cancellation event reference is retained so C does not invent a second cancellation domain.

### Important B dependency

C3 consumes the typed fields introduced by B:

```text
CapabilityExecutionContext.task_id
CapabilityExecutionContext.branch_id
CapabilityExecutionContext.correlation_id
CapabilityExecutionContext.trace_id
```

Therefore `R3_C1_C3_v1.patch` must be applied only after `R3_B1_B3_v1.patch`.

## 7. Invocation ownership remains E1

C3 does not modify `CapabilityRuntime` or `CapabilityInvocation`.

The B contract remains:

```text
CapabilityInvocation I1.execution_id = E1
```

The child driver receives I1 and creates E2 independently.

Thus:

```text
E1 != E2
I1 is not an execution ID
I1 remains owned by E1
E2.causation_id = I1
```

## 8. Agent driver construction/wiring paths

Audit found two production construction paths beyond direct tests.

### HTTP registered Agent

`se/src/transport/gateway/api/v1/capability_router.py`

Pass the container's canonical factory into `AgentCapabilityDriver`.

### Lazy built-in Agent

`se/src/runtimes/capability/local_support_loader.py`

`LazyAgentCapabilityDriver` creates the real `AgentCapabilityDriver` on first use.

It passes:

```text
container.agent_execution_id_factory
```

when available.

The fallback remains source-compatible for isolated test containers that predate the new dependency.

## 9. Existing regression requiring semantic update

`se/tests/architecture/test_unified_capability_drivers.py`

An existing test explicitly asserts the old incorrect behavior:

```text
child execution_id == parent execution_id
```

C must update that test to assert:

```text
child E2 != parent E1
child.parent_execution_id = E1
child.causation_id = I1
```

Leaving this test unchanged would make the full suite fail even when production C3 is correct.

## 10. Focused C test included

New:

`se/tests/architecture/test_r3_c1_c3_execution_id_authority.py`

Covers:

1. deterministic injectable ID factory;
2. MultiAgentCoordinator root E1 allocation and exact pass-through to executor;
3. WorkflowRuntime root Agent allocation through injected factory;
4. AgentCapabilityDriver E1→I1→E2 child creation;
5. task/branch/correlation/trace/request/workflow/session propagation;
6. cancellation-event propagation;
7. fresh delegation retry/base lineage stays null;
8. in-memory CapabilityRuntime proof that I1 remains owned by E1 while child execution is E2.

This is deliberately not D2: it does not claim the final real-SQLite nested durable graph or restart reconstruction gate.

## 11. Files in C patch

1. ADD `se/src/runtimes/agent/ids.py`
2. MODIFY `se/src/runtimes/workflow/runtime.py`
3. MODIFY `se/src/runtimes/agent/coordinator.py`
4. MODIFY `se/src/runtimes/capability/drivers/agent_driver.py`
5. MODIFY `se/src/runtimes/capability/local_support_loader.py`
6. MODIFY `se/src/application/container.py`
7. MODIFY `se/src/main.py`
8. MODIFY `se/src/transport/gateway/api/v1/capability_router.py`
9. MODIFY `se/tests/architecture/test_unified_capability_drivers.py`
10. ADD `se/tests/architecture/test_r3_c1_c3_execution_id_authority.py`

No SQL migration is required by C.

## 12. Explicit non-changes

C does not implement:

- retry execution creation;
- fork execution creation;
- TaskBranch;
- ResumeClaim;
- checkpoint redesign;
- TaskBudget;
- child fan-out budgeting;
- complete cancellation-tree semantics;
- D real-SQLite nested graph exit gate.

## 13. Static artifact validation

Generated patch:

`R3_C1_C3_v1.patch`

Static validation performed:

```text
git apply --stat      PASS
git apply --numstat   PASS
new ids.py compile    PASS
new focused test AST/py_compile PASS
```

Patch stats:

```text
10 files changed
393 insertions
13 deletions
```

## 14. Required post-apply gate

Run first:

```powershell
py -m pytest -v se/tests/architecture/test_r3_c1_c3_execution_id_authority.py
py -m pytest -q se/tests/architecture/test_unified_capability_drivers.py
```

Then C-adjacent regression:

```powershell
py -m pytest -q `
  se/tests/architecture/test_builtin_agent_support.py `
  se/tests/architecture/test_phase4_execution.py `
  se/tests/architecture/test_phase4_multi_agent.py `
  se/tests/architecture/test_r3_b1_b3_capability_lineage.py `
  se/tests/architecture/test_r3_a1_a3_representation.py `
  se/tests/architecture/test_roadmap_r0_r2.py
```

Then full:

```powershell
py -m pytest -q se/tests tools cl/tests
```

Do not mark R3 complete after C alone. If C is green, the next phase is D1→D3, whose job is to prove the final durable E1→I1→E2 graph with real SQLite/restart and final regression/realtime gates.
