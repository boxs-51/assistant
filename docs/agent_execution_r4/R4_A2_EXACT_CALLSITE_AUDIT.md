# R4-A2 — Exact Call-Site Audit + Patch Review

**Repository HEAD audited:** `5619e99830c92ceadebae1cfa3df028624437995`  
**Local prerequisite:** R4-A1 representation patch applied and green.  
**Scope:** Clock + AgentExecutionContext active-budget semantics only.

## 1. Production `AgentExecutionContext.create()` call-sites

Exactly four production factory call-sites were found:

```text
se/src/main.py
    MultiAgentCoordinator -> AgentRuntime root execution

se/src/runtimes/workflow/runtime.py
    chat/workflow root Agent execution

se/src/runtimes/capability/drivers/agent_driver.py
    nested child Agent E2

se/src/runtimes/agent/persistence.py
    durable execution reconstruction/resume
```

No direct `AgentExecutionContext(...)` constructor call was found under
`se/src`.

A2 deliberately changes none of these call-sites.

Fresh callers omit `remaining_active_budget_seconds` and therefore receive:

```text
limits.timeout_seconds
```

as their initial active budget.

The durable resume caller remains unchanged in A2. R4-B2 will explicitly pass
the persisted A1 SQL value and enforce WAITING TTL / legacy NULL fail-closed
before claiming RUNNING.

## 2. Test factory call-sites

Search by test subtree found 23 files using `AgentExecutionContext.create()`:

```text
se/tests/architecture/test_skill_context_integration.py
se/tests/architecture/test_phase6_9_connection_invariant.py
se/tests/architecture/test_phase5_7_exit_gate.py
se/tests/architecture/test_phase5_11_exit_gate.py
se/tests/architecture/test_roadmap_r0_r2.py
se/tests/architecture/test_phase5_contracts.py
se/tests/architecture/test_phase5_8_exit_gate.py
se/tests/architecture/test_phase5_9_exit_gate.py
se/tests/architecture/test_r2_1_stabilization.py
se/tests/architecture/test_phase5_10_exit_gate.py
se/tests/architecture/test_phase5_6_exit_gate.py
se/tests/architecture/test_r3_a1_a3_representation.py
se/tests/architecture/test_phase5_runtime.py
se/tests/architecture/test_phase5_adapters.py
se/tests/architecture/test_r2_1_stabilization_v2.py
se/tests/architecture/test_phase6_11_context_assembly.py
se/tests/architecture/test_phase6_9_continuation.py
se/tests/architecture/test_r3_b1_b3_capability_lineage.py
se/tests/architecture/test_phase5_tool_execution_coordinator.py
se/tests/e2e/test_phase6_9_agent_client_loop.py
se/tests/e2e/test_phase6_9_real_tcp_websocket.py
se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py
se/tests/integration/test_r3_nested_agent_durable_lineage.py
```

No direct `AgentExecutionContext(...)` test constructor was found.

Only two existing tests pass `now_monotonic`:

```text
test_phase5_contracts.py
test_phase5_adapters.py
```

A2 retains this factory argument for source compatibility.

No existing test directly accesses:

```text
context.deadline
context.remaining_seconds
context.remaining_for(...)
```

so A2 adds focused tests for those timing contracts rather than rewriting old
tests.

## 3. `remaining_seconds` consumers

AgentExecutionContext-specific production consumers are:

```text
se/src/runtimes/agent/runtime.py
    context-build / tool-batch envelope

se/src/runtimes/agent/tool_execution/coordinator.py
    task/cancellation wait envelope

se/src/runtimes/agent/contracts/context.py
    timed_out
```

Search also finds `remaining_seconds` on `CapabilityExecutionContext` in:

```text
CapabilityRuntime
remote_client_driver
skill_driver
composition
```

Those are a different context contract and are not modified by A2.

A2 preserves `AgentExecutionContext.remaining_seconds: float` as a
backward-compatible façade.

For explicit unknown legacy budget it returns `0.0`, intentionally failing
closed instead of becoming infinite or regenerating configured timeout.

## 4. `remaining_for()` consumers

Exactly two Agent production call-sites:

```text
se/src/runtimes/agent/runtime.py
    inference timeout

se/src/runtimes/agent/adapters/tool.py
    current one-shot tool timeout
```

A2 preserves the method signature and behavior:

```text
min(execution remaining, requested timeout)
```

Iteration hierarchy and LONG_RUNNING exemption are R4-C work and are not
implemented here.

## 5. A2 design

New clock contract:

```text
ExecutionClock.monotonic()
ExecutionClock.now_utc()
SystemExecutionClock
```

`AgentExecutionContext` gains:

```text
clock
remaining_active_budget_seconds
active_deadline_monotonic  # alias over legacy process-local `deadline`
active_budget_running
remaining_active_seconds
freeze_active_budget()
restore_active_budget()
```

Compatibility:

```text
deadline remains available
remaining_seconds remains float
remaining_for() unchanged
now_monotonic remains accepted
all existing create() args remain optional/source-compatible
```

## 6. Fresh vs persisted budget semantics

Factory argument uses three states:

```text
omitted
    fresh context
    -> configured limits.timeout_seconds

explicit float
    trusted persisted/delegated duration
    -> deadline = monotonic_now + supplied duration

explicit None
    unknown legacy budget
    -> no active deadline
    -> remaining_seconds = 0
    -> timed_out = True
    -> restore without trusted value raises UnknownActiveBudgetError
```

This is required to preserve the frozen legacy WAITING fail-closed contract.

## 7. Scope deliberately deferred

A2 does NOT modify:

```text
AgentRuntime lifecycle CAS
RUNNING -> WAITING freeze persistence
DurableAgentStore.resume_execution()
wait_expires_at enforcement
wait TTL policy
iteration deadline
inference hierarchy
tool hierarchy
LONG_RUNNING exemption
CapabilityExecutionContext timing propagation
child Agent budget propagation
```

These remain R4-B/C.

## 8. New deterministic tests

`test_r4_a2_execution_budget_context.py` proves:

```text
fresh configured budget uses injected clock
20s active use reduces 60 -> 40
freeze stores 40
600s inactive/wait time keeps 40
restore starts a new local deadline from 40
explicit persisted 12.5 caps configured 60
explicit unknown budget fails closed
trusted recovery value can restore unknown context
legacy now_monotonic parameter remains accepted
remaining_for() still clamps operation timeout
```

No sleeps are used.

## 9. Required post-apply gate

```powershell
py -m pytest -v se/tests/architecture/test_r4_a2_execution_budget_context.py

py -m pytest -q `
  se/tests/architecture/test_phase5_contracts.py `
  se/tests/architecture/test_phase5_adapters.py `
  se/tests/architecture/test_phase5_runtime.py `
  se/tests/architecture/test_phase5_tool_execution_coordinator.py `
  se/tests/architecture/test_roadmap_r0_r2.py `
  se/tests/architecture/test_r3_execution_lineage.py `
  se/tests/architecture/test_r4_a1_representation.py `
  se/tests/integration/test_r4_a1_migration.py

py -m pytest -q se/tests tools cl/tests
```

A2 is COMPLETE only after focused + targeted + full regression are green.
