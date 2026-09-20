# R5-B TaskBudget — Contract Freeze before Implementation

**Repository:** `boxs-51/assistant`  
**Baseline:** `1f6fd54a15872005a971927e8597f55c5a343f40`  
**Date:** 2026-09-20  
**Scope:** R5-B1→B4 TaskBudget core only.  
**Explicitly out of scope:** R5-C runtime accounting wiring, R5-D cycle/delegation policy integration, R5-E Task cancellation tree, R6 reconciliation, R7 resume lease, R8 branch execution.

## 1. Baseline audit

At the reviewed HEAD:

- `AgentTask` has no `revision`.
- `agent_tasks` has no `revision`.
- no `agent_task_budgets` table exists.
- no durable TaskBudget reservation/idempotency ledger exists.
- `AgentRepository.update_task()` remains unconditional.
- `AgentExecution` already has revision/CAS.
- current AgentRuntime transitions are not coupled to task-wide active-slot accounting.
- R5-A async ownership is already implemented in the baseline.

R5-B therefore introduces the durable representation and transaction primitives only. R5-C is the first phase allowed to wire those primitives into live Agent execution.

---

## 2. Corrected legacy-task rule — fail closed

The old pre-review plan allowed:

```text
existing task with no budget
→ ensure_budget()
→ zero counters
```

That is prohibited.

Frozen R5-B rule:

```text
budget row exists
    → use it; policy/limits are immutable

budget missing + Task has NO durable AgentExecution history
    → safe to initialize once with zero counters

budget missing + Task HAS durable AgentExecution history
    → LEGACY_UNINITIALIZED
    → fail closed for new TaskBudget-managed growth
    → never manufacture used_* = 0
```

Alembic never invents policy limits and never backfills budget rows.

This permits pristine pre-R5 tasks to be adopted safely while preventing historical usage reset.

---

## 3. Durable model

### 3.1 Task revision

Add:

```text
agent_tasks.revision INTEGER NOT NULL DEFAULT 0
AgentTask.revision: int = 0
```

R5-B adds `compare_and_set_task()`. Existing unconditional task mutation remains compatibility-only until R5-E; no new correctness-sensitive R5 code may use it.

### 3.2 TaskBudget state

```text
OPEN
CLOSED
```

`CLOSED` forbids new growth reservations. Release/accounting of already-admitted work may still complete idempotently.

### 3.3 Required limits

All required limits are positive:

```text
max_total_executions
max_active_executions
max_active_branches
max_parallel_agents
max_total_tool_calls
max_total_inference_calls
max_delegation_depth
```

Optional hard accounting caps:

```text
max_total_tokens: int | None
max_total_cost_usd: Decimal | None
```

### 3.4 `max_parallel_agents` semantics

Freeze the previously ambiguous contract as:

```text
active_parallel_agents
    = active delegated child Agent executions
      where parent_execution_id != NULL

root Agent execution
    consumes active_executions
    does NOT consume active_parallel_agents
```

This keeps `max_parallel_agents` distinct from `max_active_executions` and preserves the roadmap's fan-out purpose.

### 3.5 Cost representation

Durable cost uses:

```text
Python: Decimal
SQL:    NUMERIC(20, 8)
round:  ROUND_HALF_EVEN to 8 decimal places
```

No binary float is used for durable cost policy/accounting.

### 3.6 Policy provenance

Each budget persists immutable:

```text
policy_version
policy_fingerprint
deny_recursive_agent_cycle
```

The fingerprint is SHA-256 over canonical JSON containing:

```text
policy_version
deny_recursive_agent_cycle
all TaskBudget limits
```

A restart/config change must not silently mutate an existing Task's limits.

---

## 4. Durable reservation ledger

Revision CAS alone does not provide retry idempotency.

Add:

```text
agent_task_budget_reservations
```

Identity:

```text
(task_id, kind, reservation_key) PRIMARY KEY
```

Each row also stores:

```text
payload_fingerprint
created_at
```

Examples:

```text
new execution        reservation_key = execution_id
resume slot          key = execution_id:source_revision
release slot         key = execution_id:target_revision
tool calls           key = logical tool batch/call identity
inference            key = logical inference request_id
usage accounting     key = logical provider usage identity
branch slot          key = branch/fork logical identity
```

Retry with same key + same payload is idempotent.  
Same key + different payload is `TASK_BUDGET_CONFLICT`.

---

## 5. Atomicity contracts

### New execution

Required transaction:

```text
TaskBudget conditional CAS
+ AgentExecution INSERT
+ reservation-ledger INSERT
= one DB transaction
```

If AgentExecution insertion fails, the budget increment rolls back.

R5-B provides this primitive but does not replace `AgentRuntime._begin_durable_execution()` yet. R5-C performs that wiring.

### Resume

R5-B provides an idempotent active-slot primitive but it is **not production-safe by itself**.

R5-C must couple:

```text
WAITING -> RUNNING execution CAS
+ active slot reacquire
```

inside one durable transaction/UoW before using it.

### WAITING / terminal release

R5-B provides an idempotent release primitive keyed by execution transition identity.

R5-C must couple it with the execution state transition. Do not call it from arbitrary `finally`.

### Tool and inference reservation

R5-B provides durable logical-key reservations. R5-C calls them before actual new dispatches.

### Actual token/cost accounting

Accounting is monotonic and idempotent. Hard token/cost caps prevent *subsequent* inference after the accumulated actual value reaches/exceeds the cap. R5 does not claim zero one-call overshoot.

---

## 6. DB backstop invariants

Where SQLite supports them, migration adds checks for:

```text
revision >= 0
required limits > 0
used/active counters >= 0
active_executions <= used_executions
active_executions <= max_active_executions
active_parallel_agents <= active_executions
active_parallel_agents <= max_parallel_agents
active_branches <= max_active_branches
used_executions <= max_total_executions
used_tool_calls <= max_total_tool_calls
used_inference_calls <= max_total_inference_calls
used_tokens >= 0
used_cost_usd >= 0
OPEN  <=> closed_at IS NULL
CLOSED <=> closed_at IS NOT NULL
state IN ('OPEN', 'CLOSED')
```

Token/cost totals are intentionally not constrained to be <= optional caps because actual provider usage may overshoot a hard cap by the final admitted call.

---

## 7. R5-B1→B4 patch groups

### B1 — domain representation

```text
ADD    se/src/domain/schemas/task_budget.py
UPDATE se/src/domain/schemas/multi_agent.py
```

### B2 — SQL + migration

```text
ADD    se/src/infrastructure/storage/models/sql/agent/task_budget.py
UPDATE se/src/infrastructure/storage/models/sql/agent/__init__.py
UPDATE se/src/infrastructure/storage/models/sql/agent/task.py
ADD    se/src/infrastructure/storage/migrations/sql/versions/11a_r5_task_budget.py
ADD    se/tests/integration/test_r5_b_task_budget_migration.py
```

### B3 — repository/store CAS

```text
UPDATE se/src/infrastructure/storage/repositories/agent.py
UPDATE se/src/runtimes/agent/persistence.py
ADD    se/tests/architecture/test_r5_b_task_budget_cas.py
```

### B4 — TaskBudgetService

```text
ADD    se/src/runtimes/agent/task_budget.py
UPDATE se/src/runtimes/agent/__init__.py
ADD    se/tests/architecture/test_r5_b_task_budget_service.py
ADD    se/tests/architecture/test_r5_b_task_budget_domain.py
```

---

## 8. Stable error contracts

```text
TASK_BUDGET_REQUIRED
TASK_BUDGET_LEGACY_UNINITIALIZED
TASK_BUDGET_CLOSED
TASK_BUDGET_EXCEEDED
TASK_BUDGET_CONFLICT
DELEGATION_DEPTH_EXCEEDED
```

Cycle detection remains R5-D even though `deny_recursive_agent_cycle` is persisted now.

---

## 9. R5-B exit gate

B is complete only when tests prove:

```text
domain validators and Decimal normalization
AgentTask revision round-trip
migration has no fake budget backfill
legacy task with durable history fails closed
pristine task can initialize exactly one immutable budget
stale Task CAS rejected
stale TaskBudget CAS rejected
concurrent budget writers cannot exceed limits
same logical reservation does not charge twice
different payload under same logical key conflicts
new-execution reservation rolls back if AgentExecution insert fails
CLOSED rejects new growth
release remains idempotent
```

R5-B completion does not imply R5 overall completion.

After B:

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C PENDING
R5-D PENDING
R5-E PENDING
R5 overall NOT COMPLETE
```


---

## 10. Pre-apply artifact validation

The generated `R5_B1_B4_TASK_BUDGET_CORE_v1.patch` was validated before
handoff:

```text
custom patch actions: 15
UPDATE actions: 6
ADD actions: 9
duplicate action paths: 0

all 8 added Python files:
    AST parse PASS
    Python compile PASS

synchronous SQLite ORM schema smoke:
    create_all PASS
    TaskBudget CHECK constraints: 12
    reservation-ledger kind CHECK: PASS
    used_cost_usd type: NUMERIC(20, 8)

standalone SQLite Alembic-operation smoke:
    11a_r5_task_budget upgrade PASS
    agent_tasks.revision NOT NULL DEFAULT 0 PASS
    legacy Task automatic TaskBudget backfill count = 0 PASS
```

The authoritative repository-context and async integration gates remain the
user repository's `patch_applier.py --check` plus the focused/full pytest
commands from `R5_B_EXIT_GATE.md`.
