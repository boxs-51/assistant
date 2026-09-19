# R3 A1→A3 — Exact Call-Site Audit (baseline 6099e6a)

**Repository:** `boxs-51/assistant`  
**Pinned baseline:** `6099e6ac0226beb14efbade00dc58e5045eee742`  
**Scope:** R3-A1 → R3-A3 only  
**Excluded:** all B/C/D behavior: Capability lineage propagation, AgentExecutionIdFactory, root ID migration, child E2 allocation, nested-Agent E2 execution, final R3 integration/WS gates.

## 1. Decision

R2.1 is closed by the user's local evidence:

- focused R2.1: 16 passed;
- full `se/tests + tools + cl/tests`: 471 passed;
- 0 failures/errors.

The exact production call-site audit confirms R3-A can land independently before B/C.

## 2. A1 — domain/context representation

### MODIFY — `se/src/domain/schemas/agent_execution.py`

Current `AgentExecution` has:

- `task_id`
- `parent_execution_id`
- `correlation_id`

R3-A1 adds nullable representation only:

- `branch_id`
- `retry_of_execution_id`
- `base_execution_id`
- `base_checkpoint_id`

No retry/fork/branch behavior is introduced.

Production constructor audit found one active `AgentExecution(...)` creator:

- `se/src/runtimes/agent/coordinator.py`

All new fields default to null, so that call-site remains source-compatible and is intentionally not modified in A.

### MODIFY — `se/src/runtimes/agent/contracts/context.py`

`AgentExecutionContext.create()` is the canonical production constructor. Active source call-sites:

- `se/src/main.py`
- `se/src/runtimes/workflow/runtime.py`
- `se/src/runtimes/capability/drivers/agent_driver.py`
- `se/src/runtimes/agent/persistence.py`

The four R3 lineage fields are added as optional typed fields and optional `create()` parameters.

Important boundary: A1 does **not** modify the callers above to supply new values. Supplying task/branch/correlation lineage through the capability path belongs to B, and allocating child E2 belongs to C.

No production direct positional `AgentExecutionContext(...)` construction was found, so adding optional fields does not create positional call-site breakage.

## 3. A2 — SQL/migration/durable round-trip

### MODIFY — `se/src/infrastructure/storage/models/sql/agent/execution.py`

Add nullable columns:

- `branch_id` — indexed
- `retry_of_execution_id` — indexed
- `base_execution_id` — indexed
- `base_checkpoint_id` — deliberately not indexed in R3

Retain existing indexed:

- `task_id`
- `parent_execution_id`

Do **not** add SQL columns for:

- `request_id`
- `workflow_id`
- `trace_id`
- `causation_id`

Those remain in `context_state` under the frozen R3 contract.

### ADD — `se/src/infrastructure/storage/migrations/sql/versions/9a_r3_execution_lineage.py`

Current migration head audited:

`8a_agent_execution_waiting_cas`
→ down revision `7a_connection_affinity`

New migration:

`9a_r3_execution_lineage`
→ down revision `8a_agent_execution_waiting_cas`

It adds only the four R3 lineage columns and the three approved indexes.

No TaskBranch, checkpoint, ResumeClaim, TaskBudget, retry scheduler, or fork persistence is created.

### KEEP — `se/src/infrastructure/storage/repositories/agent.py`

No repository change is needed.

Reason:

- `save_execution(values)` already constructs `AgentExecutionRecord(**values)`;
- `update_execution(execution_id, values)` already applies values generically via `setattr`;
- CAS update is also generic.

Adding repository-specific lineage mapping would duplicate logic and expand scope unnecessarily.

The pre-existing `update_tool_call()` signature defect remains out of scope.

### MODIFY — `se/src/runtimes/agent/runtime.py`

`_begin_durable_execution()` is the authoritative initial durable create path.

It must write:

- `branch_id`
- `retry_of_execution_id`
- `base_execution_id`
- `base_checkpoint_id`

together with existing `task_id` and `parent_execution_id`.

`_persist_execution_checkpoint()` already persists the correct non-column diagnostic context:

- `request_id`
- `workflow_id`
- `causation_id`
- `trace_id`

Therefore no new R3 lineage duplication is added to `context_state`.

### MODIFY — `se/src/runtimes/agent/persistence.py`

`DurableAgentStore.resume_execution()` is the restart/rehydration call-site.

It now reconstructs lineage from the durable execution row:

- `task_id`
- `branch_id`
- `parent_execution_id`
- `retry_of_execution_id`
- `base_execution_id`
- `base_checkpoint_id`

and continues to reconstruct:

- `request_id`
- `workflow_id`
- `causation_id`
- `trace_id`

from `context_state`.

For `parent_execution_id`, SQL is authoritative but the patch retains a legacy `context_state` fallback for older rows.

## 4. A3 — event correlation representation

### MODIFY — `se/src/runtimes/agent/contracts/events.py`

`CorrelationContext` gains optional:

- `task_id`
- `branch_id`

All existing consumers remain compatible because both are nullable.

### MODIFY — `se/src/runtimes/agent/runtime.py`

`AgentRuntime._publish()` now supplies:

- `task_id=context.task_id`
- `branch_id=context.branch_id`

Existing event correlation remains unchanged:

- `execution_id`
- `parent_execution_id`
- `correlation_id`
- `request_id`
- `causation_id`
- `trace_id`
- iteration/tool/invocation IDs

### KEEP — `se/src/runtimes/agent/events.py`

No adapter code change is required.

`EventBusAgentEventPublisher` already serializes the complete correlation model using:

`event.correlation.model_dump(mode="json")`

so the new optional fields flow automatically.

## 5. Explicit non-changes

The A1→A3 patch intentionally does **not** modify:

- `se/src/runtimes/capability/contracts/context.py`
- `se/src/runtimes/capability/runtime.py`
- `se/src/runtimes/agent/adapters/tool.py`
- `se/src/runtimes/capability/composition.py`
- `se/src/runtimes/capability/drivers/agent_driver.py`
- `se/src/runtimes/capability/local_support_loader.py`
- `se/src/transport/gateway/api/v1/capability_router.py`
- root execution-ID allocation in WorkflowRuntime/Coordinator
- connection/realtime/CL files

Therefore the known R3 blocker remains intentionally unfixed until C3:

`AgentCapabilityDriver` still reuses caller E1 as the child execution ID.

That is expected after A only.

## 6. Tests included in this patch

The patch adds one focused A-slice test module:

`se/tests/architecture/test_r3_a1_a3_representation.py`

It verifies only:

1. A1 domain/context fields remain independent;
2. A2 real SQLite/ORM save + DurableAgentStore rehydrate round-trips every R3 lineage dimension;
3. A3 AgentRuntime → EventBus correlation exposes task/branch plus existing correlation fields.

It does **not** test or implement:

- CapabilityExecutionContext propagation;
- CapabilityInvocation E1 ownership through the new typed path;
- AgentExecutionIdFactory;
- E1→I1→E2 child allocation;
- nested Agent durable integration;
- remote WebSocket regressions.

Those remain B/C/D.

## 7. Patch contents

Expected patch scope:

1. `se/src/domain/schemas/agent_execution.py`
2. `se/src/runtimes/agent/contracts/context.py`
3. `se/src/infrastructure/storage/models/sql/agent/execution.py`
4. `se/src/infrastructure/storage/migrations/sql/versions/9a_r3_execution_lineage.py`
5. `se/src/runtimes/agent/persistence.py`
6. `se/src/runtimes/agent/contracts/events.py`
7. `se/src/runtimes/agent/runtime.py`
8. `se/tests/architecture/test_r3_a1_a3_representation.py`

No other production file is required for A1→A3.

## 8. Pre-apply validation performed

- Baseline pinned to commit `6099e6a`.
- Migration chain audited; `8a_agent_execution_waiting_cas` is the parent used by v1.
- Unified diff parses with `git apply --stat`.
- Modified hunks were checked against a synthetic baseline reconstructed from their exact preimage with `git apply --check`.
- New migration and A-slice test source compile with Python AST/`py_compile`.
- Repository test suite was not rerun here because this environment does not have the user's working tree/dependencies.

## 9. Expected post-apply gate

Run:

```text
py -m pytest -v se/tests/architecture/test_r3_a1_a3_representation.py
py -m pytest -q se/tests/architecture/test_roadmap_r0_r2.py
py -m pytest -q se/tests tools cl/tests
```

Also run the project Alembic upgrade/downgrade check used by the repository against a disposable database.

A should not be marked complete until the new focused test and the existing full regression suite are green.
