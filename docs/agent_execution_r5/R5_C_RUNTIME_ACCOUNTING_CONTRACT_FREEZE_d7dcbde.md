# R5-C Live TaskBudget Accounting — Exact Call-site Audit & Contract Freeze

**Repository:** `boxs-51/assistant`  
**Baseline:** `d7dcbde5177fad17274b6049d80052bbb2e17b17`  
**Status:** PRE-IMPLEMENTATION / CONTRACT FREEZE  
**Prerequisites:** R5-A COMPLETE, R5-B COMPLETE

## 1. Objective

R5-C is the first phase allowed to make TaskBudget authoritative for live
Agent execution.

The core invariant is:

```text
TaskBudget mutation and the durable AgentExecution transition it represents
must either commit together or not commit at all.
```

No process-local `finally` counter repair is accepted as a substitute.

---

## 2. Exact baseline findings

### P0-C1 — New execution still bypasses TaskBudget

`AgentRuntime._begin_durable_execution()` currently performs:

```text
save_execution(CREATED)
compare_and_set_execution(CREATED -> RUNNING)
```

as separate durable-store transactions.

For `context.task_id != None`, this bypasses the R5-B atomic primitive:

```text
TaskBudget used_executions + active_executions
+ AgentExecution INSERT
+ reservation ledger
```

Required R5-C change:

```text
new task-scoped execution
    -> TaskBudget transaction owns AgentExecution INSERT + initial RUNNING state

taskless root execution
    -> existing AgentRuntime path remains valid
```

R5-C must not double-create a task-scoped AgentExecution.

### P0-C2 — Resume CAS and active-slot reacquire are separate

Current resume:

```text
WAITING -> RUNNING execution CAS
```

does not mutate TaskBudget.

The R5-B `reserve_resume_slot()` primitive is intentionally not safe enough by
itself for production wiring.

Required transaction:

```text
WHERE AgentExecution = WAITING @ expected_revision
AND TaskBudget has capacity

AgentExecution:
    WAITING -> RUNNING
    revision += 1

TaskBudget:
    active_executions += 1
    active_parallel_agents += 1 if delegated

reservation:
    (task_id, RESUME_EXECUTION, execution_id:source_revision)

COMMIT
```

A stale execution revision or exhausted budget must roll the whole transaction
back.

### P0-C3 — WAITING / terminal transition does not release active capacity

`AgentRuntime._finish_durable_execution()` currently performs only the
AgentExecution CAS.

Required transaction for:

```text
RUNNING -> WAITING
RUNNING -> COMPLETED
RUNNING -> FAILED
RUNNING -> CANCELLED
RUNNING -> TIMEOUT
```

is:

```text
AgentExecution transition CAS
+
TaskBudget active_executions -= 1
+
active_parallel_agents -= 1 for delegated execution
+
durable release reservation keyed by target execution revision
```

exactly once.

### P0-C4 — cancellation/failure compensation also bypasses TaskBudget

These call-sites currently CAS execution state directly:

```text
_cancel_durable_revision()
execute() exception handler
resume active-budget exhaustion
resume wait-TTL expiry
cancel_claimed_execution()
```

Once task-scoped active slots are live, every RUNNING -> terminal path must use
the same accounting-aware transition primitive.

A separate cleanup path would create counter leaks/double-release races.

### P0-C5 — no authoritative TaskBudget policy source exists

`TaskBudgetLimits` has required fields and no defaults.

Current project state has no:

```text
ConfigSchema.agent/task_budget settings
AgentDefinition task-wide budget
AgentTaskCreateRequest task-wide budget
```

`AgentExecutionLimits` is execution-local and cannot legitimately be used to
derive:

```text
max_total_executions
max_active_branches
max_total_inference_calls
max_delegation_depth
```

Therefore R5-C must not invent numeric defaults inside AgentRuntime or
MultiAgentCoordinator.

Frozen decision:

```text
TaskBudget policy source = application configuration
```

Add a dedicated config section:

```text
agent:
  task_budget:
    policy_version
    deny_recursive_agent_cycle
    max_total_executions
    max_active_executions
    max_active_branches
    max_parallel_agents
    max_total_tool_calls
    max_total_inference_calls
    max_total_tokens
    max_total_cost_usd
    max_delegation_depth
```

The values are application policy and must be explicit in `default.yaml`, not
derived from `AgentExecutionLimits`.

Per-request budget override is NOT introduced in R5-C. That requires a
separate authorization/admin contract.

### P0-C6 — multi-agent Task creation does not create a budget

`MultiAgentCoordinator.create_task_async()` currently persists only
`agent_tasks`.

Required R5-C transaction:

```text
AgentTask INSERT
+ TaskBudget INSERT from application policy
= one transaction
```

Do not:

```text
save_task()
commit
ensure_budget()
commit
```

because a crash between those commits creates a fresh nonterminal Task without
its mandatory budget.

R5-C needs a durable-store/repository primitive for Task + TaskBudget creation
in one UoW.

### P0-C7 — inference is not reserved before provider dispatch

Current AgentRuntime creates:

```text
request_id = inf_<uuid>
response = await inference.complete(...)
```

without task-wide accounting.

Required order for task-scoped execution:

```text
reserve_inference(task_id, request_id)
-> provider dispatch
-> response
-> account_usage(task_id, usage_key=request_id, ...)
```

The same `request_id` is the durable idempotency key.

A retry of the same logical inference request must not consume
`used_inference_calls` twice.

### P0-C8 — tool-call accounting is still execution-local only

`CapabilityToolExecutionAdapter.execute()` currently calls:

```text
context.reserve_tool_call()
```

which protects only `AgentExecutionLimits.max_tool_calls`.

R5-C must preserve that execution-local limit AND add task-wide durable
reservation.

Canonical task-wide identity:

```text
tool_call_id
```

because it is persisted and stable across WAITING/resume.

Do not use the transient retry attempt number as a new TaskBudget tool call.

Required order:

```text
persist logical tool call
reserve TaskBudget tool-call key
dispatch capability
```

For a resumed pending tool call, the same `tool_call_id` reservation is
idempotent.

### P1-C1 — actual provider cost is not currently populated

`InferenceUsage` contains:

```text
estimated_cost_usd: float
```

but `ProviderInferenceAdapter` currently populates token counts only.

R5-C may account:

```text
tokens = response.usage.total_tokens
cost = response.usage.estimated_cost_usd
```

but cost will remain zero until provider adapters provide a trustworthy value.

R5-C must not fabricate provider cost.

The durable TaskBudget representation remains Decimal; float is converted only
at the boundary through the R5-B normalization function.

### P1-C2 — coordinator still uses unconditional update_task()

R5-B introduced AgentTask revision/CAS, but MultiAgentCoordinator still writes:

```text
durable_store.update_task(...)
```

R5-C does not need to rewrite every Task lifecycle mutation unless it conflicts
with TaskBudget transaction correctness.

However, any new Task + TaskBudget create path must be atomic.

Full Task lifecycle CAS authority remains R5-E.

### P1-C3 — taskless roots remain valid, taskless delegation does not

R5-C rule:

```text
Workflow/direct-chat root with task_id=None:
    may execute without TaskBudget

Agent capability delegation with task_id=None:
    do not create a pseudo task
    R5-D will fail it as TASK_BUDGET_REQUIRED
```

R5-C should not silently synthesize a non-durable Task identity.

---

## 3. Required production architecture

### 3.1 Application dependencies

Add to `ApplicationContainer`:

```text
task_budget_service
task_budget_policy
```

Bootstrap one process-wide `TaskBudgetService` backed by the same
`uow_factory` as Agent persistence.

### 3.2 Configuration

Add immutable Pydantic configuration models under infrastructure config:

```text
AgentSettings
TaskBudgetSettings
```

and explicit values to `se/config/default.yaml`.

Bootstrap converts settings to:

```text
TaskBudgetLimits
TaskBudgetPolicy
```

once.

### 3.3 Coordinator creation boundary

`MultiAgentCoordinator` receives the TaskBudget service/policy.

`create_task_async()` must call one atomic persistence method:

```text
create_task_with_budget(task_values, budget_values)
```

No two-commit sequence.

### 3.4 Durable transition authority

Do not spread TaskBudget logic across routers/adapters.

Add atomic methods at the durable-store/service boundary, conceptually:

```text
start_task_scoped_execution(...)
resume_task_scoped_execution(...)
finish_task_scoped_execution(...)
```

Each method owns one UoW and mutates both AgentExecution and TaskBudget.

`AgentRuntime` remains the state-machine authority and invokes those methods.

### 3.5 Inference accounting

AgentRuntime owns inference logical identity and therefore owns:

```text
reserve inference
dispatch
account actual usage
```

ProviderInferenceAdapter remains provider translation only.

### 3.6 Tool accounting

AgentRuntime owns persisted logical tool-call IDs.

TaskBudget tool reservation therefore belongs immediately before dispatch from
AgentRuntime, not inside CapabilityRuntime.

Execution-local retry/tool limits remain in the existing adapter/coordinator.

---

## 4. Exact call-site plan

### UPDATE `se/src/infrastructure/config/schemas.py`

Add TaskBudget application policy schema.

### UPDATE `se/config/default.yaml`

Add explicit task-wide policy values.

### UPDATE `se/src/application/container.py`

Add:

```text
task_budget_service
task_budget_policy
```

### UPDATE `se/src/runtimes/agent/task_budget.py`

Add atomic UoW primitives for:

```text
create Task + TaskBudget
new execution admission + RUNNING
WAITING resume + slot reacquire
RUNNING finish + slot release
```

Existing standalone R5-B primitives remain useful for tests/admin operations
but production AgentRuntime uses coupled transitions.

### UPDATE `se/src/infrastructure/storage/repositories/agent.py`

Add repository helpers needed by the atomic operations.

No internal commit is allowed inside repository methods.

### UPDATE `se/src/runtimes/agent/persistence.py`

Expose transaction-safe Task/TaskBudget/Execution methods if AgentRuntime
continues to resolve through DurableAgentStore.

### UPDATE `se/src/runtimes/agent/coordinator.py`

Inject task budget service/policy and atomically create Task + TaskBudget.

Do not use `ensure_budget()` after task commit.

### UPDATE `se/src/main.py`

Construct policy/service once and inject into:

```text
ApplicationContainer
MultiAgentCoordinator
AgentRuntime
```

### UPDATE `se/src/runtimes/agent/runtime.py`

For `context.task_id is not None`:

```text
new execution:
    use atomic TaskBudget admission/start

resume:
    use atomic execution CAS + slot reacquire

finish/cancel/fail/timeout:
    use atomic execution CAS + slot release

before inference:
    reserve inference request_id

after inference:
    account actual usage by request_id

before logical tool dispatch:
    reserve tool_call_id(s)
```

For `task_id is None`, keep existing execution lifecycle semantics.

### NO R5-C production change to `AgentCapabilityDriver`

It already propagates:

```text
task_id
parent_execution_id
```

R5-D will enforce missing task scope for nested Agent delegation.

### NO R5-C production change to provider retry/fallback policy

R10 owns provider-internal retry semantics. R5-C only ensures one logical
Agent inference request consumes one TaskBudget inference slot.

---

## 5. Atomic transition keys

Use deterministic ledger keys:

```text
NEW_EXECUTION:
    execution_id

RESUME_EXECUTION:
    execution_id:source_revision

RELEASE_EXECUTION:
    execution_id:target_revision

INFERENCE:
    inference request_id

USAGE:
    inference request_id

TOOL_CALL:
    tool_call_id
```

`target_revision` means the revision produced by the durable execution
transition that released the active slot.

---

## 6. Failure semantics

### Budget exceeded before new execution

```text
no AgentExecution row
no active slot
TASK_BUDGET_EXCEEDED
```

### Budget exceeded on resume

```text
execution remains WAITING
no active slot acquired
TASK_BUDGET_EXCEEDED
```

### Crash/failure during finish transaction

```text
either RUNNING + active slot remain
or terminal/WAITING + slot released
never mixed durable state
```

R7/R12 still own stale-RUNNING recovery after whole-process failure.

### Inference reservation succeeds, provider call fails

`used_inference_calls` remains consumed.

Reason: the logical inference attempt was admitted and dispatched. Replaying the
same request_id is idempotent; a genuinely new request_id is a new inference
attempt.

### Tool reservation succeeds, dispatch fails

`used_tool_calls` remains consumed for that logical tool call.

Retry attempts of the same logical call do not consume another TaskBudget tool
slot.

---

## 7. Test plan

### C1 configuration / DI

Prove:

```text
policy parses from config
policy fingerprint stable
same singleton service injected
no derivation from AgentExecutionLimits
```

### C2 atomic task creation

Real SQLite:

```text
Task + TaskBudget commit together
forced budget insert failure rolls Task back
```

### C3 execution admission

Real SQLite:

```text
new task execution consumes used_executions + active_executions once
duplicate execution_id is idempotent/conflict-safe
budget exhaustion creates no AgentExecution
taskless execution keeps legacy path
```

### C4 WAITING / resume / terminal

Real SQLite:

```text
RUNNING -> WAITING releases slot atomically
WAITING -> RUNNING reacquires slot atomically
resume does not increment used_executions
terminal releases slot exactly once
stale execution revision changes neither side
```

### C5 inference accounting

Prove:

```text
reservation happens before provider dispatch
same request_id not double charged
actual token usage charged once
failed provider dispatch keeps inference reservation
```

### C6 tool accounting

Prove:

```text
one logical tool_call_id consumes one task-wide slot
resume replay uses same slot
tool retries do not multiply task-wide tool usage
execution-local max_tool_calls still enforced
```

### C7 integrated regression

Include R5-A, R5-B, R4 resume/WAITING, nested Agent lineage and full suite.

---

## 8. Exit gate

R5-C may be marked COMPLETE only when:

```text
no task-scoped execution state can diverge from active TaskBudget capacity
new execution admission is atomic
resume slot reacquire is atomic
WAITING/terminal release is atomic
inference and tool logical identities are durable/idempotent
task creation always gets its budget in the same commit
taskless root compatibility remains intact
full suite passes
```

After R5-C:

```text
R5-A COMPLETE
R5-B COMPLETE
R5-C COMPLETE
R5-D PENDING
R5-E PENDING
R5 overall NOT COMPLETE
```
