# R3 B1→B3 — Exact Call-Site Audit

**Repository:** `boxs-51/assistant`  
**Pinned baseline:** `d9f0cb389a9def61db8b56dc0b52f6c2960d27d7`  
**Baseline message:** `cap nhat R3 trien khai tu A1 den A3`  
**Prerequisite status:** R3-A1→A3 COMPLETE (`3/3` focused, `9/9` R0–R2, `474` full regression)  
**Scope:** R3-B1 → R3-B3 only  
**Excluded:** C1/C2/C3 execution-ID authority and nested child E2 behavior; all D integration gates.

---

## 1. Executive decision

R3-B can land independently on `d9f0cb3`.

The exact call-site graph is:

```text
AgentExecutionContext E1
    |
    v
CapabilityToolExecutionAdapter
    |
    | execution_id = E1
    | invocation_id = I1
    | task/branch/correlation/trace typed
    v
CapabilityRuntime.execute_capability()
    |
    +-- CapabilityExecutionContext
    |      execution_id = E1
    |      invocation_id = I1
    |      task_id
    |      branch_id
    |      correlation_id
    |      trace_id
    |
    +-- CapabilityInvocation I1
           execution_id = E1
           correlation_id = C1
           trace_id = TR1
```

B does not create E2. `AgentCapabilityDriver` remains unchanged until C3.

---

## 2. B1 — `CapabilityExecutionContext`

### MODIFY
`se/src/runtimes/capability/contracts/context.py`

Current first-class identifiers:

- `execution_id`
- `invocation_id`
- `request_id`
- `session_id`
- `connection_id`
- `workflow_id`

Add nullable typed fields:

- `task_id`
- `branch_id`
- `correlation_id`
- `trace_id`

`CapabilityExecutionContext.create()` accepts and retains all four.

### Exact constructor call-sites audited

Production direct `CapabilityExecutionContext.create()` callers:

1. `se/src/runtimes/capability/runtime.py`
2. `se/src/tool/_mcp/executor.py`

The legacy MCP executor supplies none of the new fields, so all four remain null and source compatibility is preserved.

No non-Agent caller is forced to fabricate task/branch lineage.

---

## 3. B2 — CapabilityRuntime caller lineage + invocation ownership

### MODIFY
`se/src/runtimes/capability/runtime.py`

`execute_capability()` adds optional:

```text
task_id
branch_id
correlation_id
trace_id
```

Typed context is populated directly.

### Compatibility fallback

For `correlation_id` and `trace_id` only:

```text
explicit typed argument
    wins over
legacy metadata value
```

This preserves old call-sites that still send:

```text
metadata["correlation_id"]
metadata["trace_id"]
```

while making typed fields authoritative for new Agent-owned calls.

`task_id` and `branch_id` do not use generic metadata fallback. They are execution semantics and become available only when a typed caller supplies them.

### CapabilityInvocation ownership

No contract, SQL model or repository changes are required.

Already first-class and durable today:

```text
CapabilityInvocation.execution_id
CapabilityInvocation.correlation_id
CapabilityInvocation.trace_id
```

Already present in:

- `se/src/runtimes/capability/contracts/invocation.py`
- `se/src/infrastructure/storage/models/sql/capability/invocation.py`
- `se/src/infrastructure/storage/repositories/capability_invocations.py`

The existing SQL store serializes the invocation model generically.

Patch changes the assignment source to:

```text
execution_id = context.execution_id
correlation_id = context.correlation_id
trace_id = context.trace_id
```

Therefore:

```text
I1.execution_id = E1
```

remains frozen and no future child E2 can accidentally become invocation owner through B.

### Command event boundary

`CapabilityRuntime._handle_execute_command()` forwards optional:

- `task_id`
- `branch_id`
- `correlation_id`
- `trace_id`

from the event payload.

This keeps the event-command path capable of using the same typed runtime API.

---

## 4. B1/B2 — Remote client trace consumer

### MODIFY
`se/src/runtimes/capability/drivers/remote_client_driver.py`

Exact current issue:

```text
RealtimeEnvelope.trace_id = context.metadata["trace_id"]
```

After B1, that would ignore the new typed `context.trace_id`.

Patch uses:

```text
context.trace_id or legacy metadata fallback
```

Typed trace wins; old direct contexts remain compatible.

No RealtimeEnvelope schema change is made.

No task/branch/correlation fields are added to the WS protocol in B.

---

## 5. B3 — Agent tool adapter

### MODIFY
`se/src/runtimes/agent/adapters/tool.py`

The canonical Agent tool call now passes:

```text
execution_id = context.execution_id       # E1
invocation_id = request.invocation_id     # I1

task_id = context.task_id
branch_id = context.branch_id
correlation_id = context.correlation_id
trace_id = context.trace_id

request_id
session_id
connection_id
workflow_id
```

Existing cancellation/deadline/routing/metadata behavior remains unchanged.

Critical invariant:

```text
CapabilityRuntime still sees E1 as execution owner.
```

No child Agent ID is allocated here.

---

## 6. B3 — Declarative composition

### MODIFY
`se/src/runtimes/capability/composition.py`

Each composed step forwards:

- caller `execution_id` E1;
- `task_id`;
- `branch_id`;
- `correlation_id`;
- `trace_id`;
- existing request/session/workflow/deadline/cancellation metadata.

The driver intentionally does **not** pass an `invocation_id` for child steps. Existing CapabilityRuntime behavior allocates a fresh invocation ID per composed step.

`connection_id` is intentionally not added by this patch. It is routing affinity, not lineage, and changing composition-wide routing semantics is outside B.

---

## 7. B3 — Executable skill

### MODIFY
`se/src/runtimes/capability/drivers/skill_driver.py`

Executable skill remains inside caller execution E1:

```text
InferenceRequest.execution_id = CapabilityExecutionContext.execution_id
```

No AgentExecution child is created.

For observability, the skill's inference metadata now exposes non-null typed:

- `task_id`
- `branch_id`
- `correlation_id`
- `trace_id`

and keeps `invocation_id`.

Typed values override any same-name legacy diagnostic metadata.

---

## 8. Explicitly verified / unchanged

### KEEP — `CapabilityInvocation`
No new task/branch fields or SQL columns.

### KEEP — `SqlCapabilityInvocationStore`
Generic model serialization already persists E1/correlation/trace.

### KEEP — HTTP `CapabilityExecutionRequest`
HTTP/direct capability calls are non-Agent by default and remain valid with null lineage. No client is forced to fabricate Agent ancestry.

### KEEP — DirectChat
No new Agent lineage arguments are required.

### KEEP — legacy MCP executor
New context fields default null.

### KEEP — `AgentCapabilityDriver`
Still contains the known R3 blocker:

```text
child Agent execution_id = caller context.execution_id
```

and still consumes correlation from metadata.

This is intentional in B. C3 owns the fix:

```text
E1 -> I1 -> NEW E2
E2.parent_execution_id = E1
E2.causation_id = I1
```

No C behavior is included in this patch.

---

## 9. Focused test evidence included

New:

`se/tests/architecture/test_r3_b1_b3_capability_lineage.py`

The test module proves:

1. Agent-owned CapabilityExecutionContext carries typed task/branch/correlation/trace.
2. Direct/non-Agent context can keep those fields null.
3. CapabilityRuntime preserves `I1.execution_id == E1`.
4. Explicit typed correlation/trace win over conflicting legacy metadata.
5. Legacy metadata fallback still works when typed values are absent.
6. CapabilityToolExecutionAdapter propagates E1/I1 + task/branch/correlation/trace.
7. Declarative composition propagates caller lineage and lets runtime allocate per-step invocation IDs.
8. RemoteClientDriver prefers typed trace over legacy metadata.
9. Executable skill keeps `execution_id == E1` and exposes typed diagnostics.

Existing regression suites remain the compatibility gate.

---

## 10. Patch files

Production:

1. `se/src/runtimes/capability/contracts/context.py`
2. `se/src/runtimes/capability/runtime.py`
3. `se/src/runtimes/capability/drivers/remote_client_driver.py`
4. `se/src/runtimes/agent/adapters/tool.py`
5. `se/src/runtimes/capability/composition.py`
6. `se/src/runtimes/capability/drivers/skill_driver.py`

Tests:

7. `se/tests/architecture/test_r3_b1_b3_capability_lineage.py`

No migration is required.

---

## 11. Pre-apply validation

Performed on generated artifact:

```text
git apply --stat      PASS
git apply --numstat   PASS
new focused test AST  PASS
```

Patch statistics:

```text
7 files changed
453 insertions
4 deletions
```

The large insertion count is primarily the standalone focused B exit-gate test file.

Repository tests were not executed in this environment.

---

## 12. Required post-apply gate

Run in this order:

```powershell
py -m pytest -v se/tests/architecture/test_r3_b1_b3_capability_lineage.py

py -m pytest -q `
  se/tests/architecture/test_phase3.py `
  se/tests/architecture/test_phase5_adapters.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py `
  se/tests/architecture/test_phase6_4_remote_client_driver.py `
  se/tests/architecture/test_phase6_7_workflow_composition.py

py -m pytest -q se/tests/architecture/test_r3_a1_a3_representation.py
py -m pytest -q se/tests/architecture/test_roadmap_r0_r2.py

py -m pytest -q se/tests tools cl/tests
```

B1→B3 is complete only if the focused B gate and full regression are green.

Do not start C1→C3 before that gate passes.
