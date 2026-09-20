# R4 — Active Budget / Wait TTL / Deadline Hierarchy
## Exact Call-Site Audit + Contract Freeze Candidate

**Repository:** `boxs-51/assistant`  
**Audited HEAD:** `782a3fe64ad740875a4489d2676d29b01cb6178c`  
**R3 status:** COMPLETE by local exit-gate evidence; completion record patch produced separately.  
**R4 implementation status:** NOT STARTED.  
**Scope of this document:** architecture audit / contract freeze candidate only. No R4 code is included.

---

# 1. Executive summary

Current code has one execution-wide monotonic deadline:

```text
context.deadline = started_monotonic + limits.timeout_seconds
```

and operation-specific timeout caps for inference/tool execution.

That is insufficient for R4 because:

1. WAITING does not freeze and persist remaining active execution budget.
2. Resume reconstructs a fresh full timeout from `limits.timeout_seconds`.
3. `wait_expires_at` does not exist in domain/SQL/runtime.
4. `iteration_timeout_seconds` is declared but not enforced.
5. normal TOOL timeout is currently applied to nested AGENT/LONG_RUNNING capability calls.
6. `AgentCapabilityDriver` gives child Agent a fresh `AgentExecutionLimits()` budget rather than a cap derived from parent remaining budget.

Therefore R4 requires real semantic changes, not only new fields.

---

# 2. Frozen R4 target from roadmap

The roadmap requires separate clocks:

```text
Active execution budget:
    runs while CREATED/RUNNING
    freezes while WAITING
    stops at terminal state

Wait TTL:
    runs only while WAITING
    stops on resume/terminal

Operation timeout:
    applies to currently running context/inference/tool operation

Parent/upper deadline:
    always bounds descendants/operations
```

Durable state must persist:

```text
remaining_active_budget_seconds
wait_expires_at
```

Never persist a raw `time.monotonic()` timestamp across restart.

On resume:

```text
runtime_deadline_monotonic =
time.monotonic() + remaining_active_budget_seconds
```

---

# 3. Exact current call-site findings

## 3.1 `AgentExecutionContext` currently owns one absolute process-local deadline

File:

```text
se/src/runtimes/agent/contracts/context.py
```

Current behavior:

```text
started = time.monotonic()
deadline = started + limits.timeout_seconds

remaining_seconds =
deadline - time.monotonic()
```

Problems:

- no durable `remaining_active_budget_seconds`;
- no distinction between first start and resumed execution;
- no iteration deadline;
- no wait TTL;
- `create()` always starts a fresh full execution timeout unless future caller manually changes `deadline`.

Severity: **P0 for R4**.

---

## 3.2 Resume resets active budget

File:

```text
se/src/runtimes/agent/persistence.py
DurableAgentStore.resume_execution()
```

Current reconstruction loads:

```text
limits = context_state["limits"]
```

then calls:

```text
AgentExecutionContext.create(... limits=restored_limits ...)
```

`create()` therefore sets:

```text
deadline = now_monotonic + limits.timeout_seconds
```

Example:

```text
initial active budget 60s
RUNNING consumed 20s
WAITING
expected persisted remaining = 40s

current resume:
new deadline = now + 60s
```

This grants 20 seconds back after every wait/resume.

Severity: **P0**.

Frozen requirement:

```text
resume must reconstruct the persisted remaining budget,
not reset the configured maximum.
```

---

## 3.3 WAITING transition does not freeze active budget

File:

```text
se/src/runtimes/agent/runtime.py
AgentRuntime._finish_durable_execution()
```

Current WAITING CAS persists:

```text
state
wait_reason
result
error
completed_at=null
```

It does not persist:

```text
remaining_active_budget_seconds
wait_expires_at
```

The disconnect path returns `AgentLoopState.WAITING`, but budget state is not captured at the state transition.

Severity: **P0**.

R4 must compute remaining budget at the durable RUNNING→WAITING authority boundary.

---

## 3.4 `wait_expires_at` does not exist

Audited:

```text
AgentExecution domain schema
AgentExecutionRecord SQL model
AgentExecutionContext
DurableAgentStore
AgentRuntime
AgentContinuationService
```

No execution-level `wait_expires_at` was found.

Severity: **P0**.

R4 must add durable wall-clock expiry.

Recommended SQL:

```text
remaining_active_budget_seconds FLOAT NOT NULL/nullable during migration
wait_expires_at DATETIME(timezone=True) NULL
```

Recommended index:

```text
ix_agent_executions_wait_expires_at
```

because a sweeper / scheduler / recovery process will eventually need:

```text
WHERE state='WAITING'
AND wait_expires_at <= now()
```

---

# 4. Wait TTL policy contract

R4 should introduce one execution wait policy authority.

Suggested contract:

```text
ExecutionWaitPolicy.wait_ttl_seconds(
    reason: AgentExecutionWaitReason,
    context/execution policy inputs
) -> float | None
```

Meaning:

```text
None = no automatic expiry under current policy
positive value = expiry wall-clock TTL
```

Recommended starting policy classes from roadmap:

```text
CONNECTION
    configured hours/day window

HUMAN_APPROVAL
    configured longer TTL

EXPLICIT_PAUSE
    configured / possibly unlimited

DEPENDENCY
    dependency policy

RECOVERY
    short recovery TTL

RETRY_BACKOFF
    durable scheduled retry time + guard window
```

R4 should not implement full retry scheduler or HITL subsystem; only execution-level expiry representation and enforcement.

---

# 5. WAITING lifecycle rules

## 5.1 Enter WAITING

At RUNNING@N:

```text
remaining =
max(0, current_runtime_deadline_monotonic - time.monotonic())

wait_expires_at =
wall_clock_now + wait_policy(reason)
```

One durable CAS should persist:

```text
RUNNING@N
→ WAITING@(N+1)

wait_reason
remaining_active_budget_seconds
wait_expires_at
```

R7 later extends this same transaction with immutable checkpoint ownership.

R4 must not create a competing checkpoint protocol.

## 5.2 Resume

Before WAITING→RUNNING:

```text
if wait_expires_at != null and wall_clock_now >= wait_expires_at:
    WAITING -> TIMEOUT
    resume denied
```

Otherwise:

```text
remaining > 0 required

runtime deadline =
time.monotonic() + remaining
```

Then durable CAS:

```text
WAITING@N
→ RUNNING@(N+1)

wait_reason = null
wait_expires_at = null
```

`remaining_active_budget_seconds` remains the durable source of truth for restart/recovery.

## 5.3 Terminal

Terminal execution must not retain active wait semantics:

```text
wait_reason = null
wait_expires_at = null
```

The remaining budget may be retained for audit or set to zero depending on frozen schema semantics, but it must never allow resurrection.

Recommendation:

```text
retain final remaining_active_budget_seconds for observability
```

because terminal-state guards already prevent resume.

---

# 6. Current iteration timeout is dead configuration

Schema:

```text
AgentExecutionLimits.iteration_timeout_seconds = 20.0
```

No production use of this field was found.

Current `AgentRuntime` starts an iteration and then:

- context build timeout = execution remaining;
- inference timeout = min(execution remaining, inference timeout);
- tool batch timeout = execution remaining;
- tool adapter timeout = min(execution remaining, tool timeout).

Thus:

```text
iteration_timeout_seconds
```

does not bound the iteration.

Severity: **P0 for Deadline Hierarchy**.

---

# 7. Required iteration budget contract

For each iteration:

```text
iteration_budget_seconds =
min(
    execution_remaining_seconds,
    limits.iteration_timeout_seconds
)
```

Runtime should establish process-local:

```text
iteration_deadline_monotonic =
time.monotonic() + iteration_budget_seconds
```

Do not persist raw iteration monotonic deadline.

All synchronous work in the iteration is bounded by:

```text
iteration_remaining_seconds =
min(
    execution_remaining_seconds,
    iteration_deadline - monotonic_now
)
```

This must bound:

```text
context build
inference
tool batch
nested synchronous capability/Agent call
```

If iteration deadline is exhausted:

```text
iteration -> TIMEOUT/FAILED according to existing loop contract
execution timeout classification must remain deterministic
```

Recommended error classification should distinguish:

```text
AGENT_EXECUTION_TIMEOUT
AGENT_ITERATION_TIMEOUT
CAPABILITY_TIMEOUT
```

if the existing public error model supports it without breaking compatibility.

---

# 8. Inference timeout hierarchy

Current code already partially enforces:

```text
min(execution_remaining, inference_timeout_seconds)
```

R4 target:

```text
effective_inference_timeout =
min(
    execution_remaining,
    iteration_remaining,
    inference_timeout_seconds
)
```

This is a local runtime timeout only.

Provider retry/fallback still belongs to Provider Runtime; provider retries may not exceed this supplied operation budget.

---

# 9. TOOL timeout hierarchy

Current file:

```text
se/src/runtimes/agent/adapters/tool.py
```

Current behavior for every capability:

```text
timeout =
min(
    execution_remaining,
    tool_timeout_seconds
)
```

The adapter then passes that as:

```text
CapabilityRuntime.execute_capability(timeout_seconds=timeout)
```

This is valid only for normal one-shot TOOL-like work.

R4 target for normal one-shot capability:

```text
effective_timeout =
min(
    execution_remaining,
    iteration_remaining,
    tool_timeout_seconds
)
```

---

# 10. P0 — nested AGENT/LONG_RUNNING is incorrectly subject to tool timeout

Current `CapabilityToolExecutionAdapter` resolves the capability definition, but does not use kind/execution mode when calculating timeout.

Therefore an Agent capability receives:

```text
tool_timeout_seconds
```

before `AgentCapabilityDriver` runs.

Roadmap explicitly requires:

```text
Nested AGENT/LONG_RUNNING must not use tool timeout.
```

Severity: **P0**.

Required split:

```text
if capability is normal ONE_SHOT TOOL:
    cap by tool_timeout_seconds

elif capability is AGENT or LONG_RUNNING:
    do not apply one-shot tool timeout
    cap by parent execution/iteration/delegation budget
```

Recommended decision:

```text
execution_mode == LONG_RUNNING
```

is the semantic trigger, with `kind == AGENT` as an additional guard.

Do not hardcode only `CapabilityKind.AGENT`, because future LONG_RUNNING implementations may not be TOOL semantics.

---

# 11. P0 — child Agent gets fresh unlimited execution budget

Current:

```text
se/src/runtimes/capability/drivers/agent_driver.py
```

creates child context with:

```text
limits=AgentExecutionLimits()
```

Thus a parent with 5 seconds left can spawn a child with a new default 60 seconds.

This violates:

```text
child operations cannot exceed parent remaining budget
```

Severity: **P0**.

R4 must propagate typed budget information through the capability boundary.

Do not infer this from:

```text
metadata
timeout string
connection_id
parent execution ID
```

---

# 12. Proposed typed capability budget propagation

Extend `CapabilityExecutionContext` with explicit ephemeral budget fields:

```text
caller_execution_remaining_seconds: float | null
caller_iteration_remaining_seconds: float | null
```

Alternative naming is acceptable, but semantics must be typed and unambiguous.

Agent tool adapter supplies them from the current Agent context / iteration budget.

For direct HTTP/MCP/non-Agent calls:

```text
caller_execution_remaining_seconds = null
caller_iteration_remaining_seconds = null
```

`AgentCapabilityDriver` derives child active budget:

```text
child_budget =
min_non_null(
    configured delegated Agent limit,
    caller_execution_remaining_seconds,
    caller_iteration_remaining_seconds   # current synchronous delegation
)
```

For R4 before TaskBudget exists:

```text
task_remaining_budget
```

is absent; R5 introduces that shared task-wide constraint.

---

# 13. Important contract ambiguity to freeze

Roadmap section 28 says child Agent budget is bounded by parent execution remaining.

Deadline hierarchy section 26 says every operation is also bounded by iteration budget.

Current child Agent execution is synchronous:

```text
parent iteration
  waits for
child Agent capability result
```

Therefore R4 should freeze:

```text
synchronous child Agent budget
<= parent iteration remaining
<= parent execution remaining
```

Future asynchronous ownership transfer may relax the iteration constraint, but that belongs to later async ownership/task phases.

This distinction must be explicit in R4 contract tests.

---

# 14. Context build timeout

Current:

```text
AgentRuntime._await_contextual(
    context_builder.build(...),
    timeout_seconds=context.remaining_seconds
)
```

Target:

```text
timeout_seconds =
min(
    execution_remaining,
    iteration_remaining
)
```

No separate context-build configured timeout currently exists.

R4 does not need to invent one unless required.

---

# 15. Tool batch timeout

Current parent batch wrapper uses:

```text
timeout_seconds=context.remaining_seconds
```

Target:

```text
timeout_seconds =
min(
    execution_remaining,
    iteration_remaining
)
```

Each individual normal TOOL then applies its own:

```text
tool_timeout_seconds
```

inside that batch envelope.

Nested LONG_RUNNING Agent bypasses one-shot tool timeout but remains bounded by parent execution + parent iteration remaining.

---

# 16. Resumed pending tool calls

Current `_execute_resumed_tool_calls()` also wraps the resumed pending batch with:

```text
context.remaining_seconds
```

After R4 reconstruction this becomes valid only if:

```text
context.remaining_seconds
```

was rebuilt from persisted `remaining_active_budget_seconds`.

It still also needs a new iteration budget for the resumed iteration.

Do not allow resume to bypass `iteration_timeout_seconds`.

---

# 17. SQL/domain changes recommended

## AgentExecution domain

Add:

```text
remaining_active_budget_seconds: float | null
wait_expires_at: datetime | null
```

For new executions:

```text
remaining_active_budget_seconds = limits.timeout_seconds
wait_expires_at = null
```

## SQL

Add migration after current R3 head:

```text
10a_r4_active_budget_wait_ttl
```

Columns:

```text
remaining_active_budget_seconds FLOAT
wait_expires_at DATETIME(timezone=True)
```

Index:

```text
ix_agent_executions_wait_expires_at
```

Potential optional composite index later:

```text
(state, wait_expires_at)
```

but only add if SQLite/query plan evidence justifies it.

## Context

Process-local:

```text
active_deadline_monotonic
iteration_deadline_monotonic
```

Durable input:

```text
remaining_active_budget_seconds
```

Never persist process-local monotonic absolute values.

---

# 18. Clock authority contract

Injectable clock is strongly recommended for deterministic tests.

Conceptual:

```text
ExecutionClock:
    monotonic() -> float
    now_utc() -> datetime
```

Use:

```text
monotonic()
    active execution elapsed time
    iteration elapsed time
    operation timeout

now_utc()
    wait_expires_at
    created_at/updated_at/completed_at
```

Do not mix them.

Do not reconstruct remaining active budget from wall-clock RUNNING timestamps.

---

# 19. Active budget mutation authority

Recommended single authority:

```text
AgentRuntime / AgentExecutionContext runtime budget helper
```

Durable mutation occurs only through AgentRuntime lifecycle CAS.

Do not allow:

```text
CapabilityRuntime
AgentContinuationService
transport reconnect handler
client
```

to independently modify remaining execution budget.

Continuation/checkpoint services may consume/read the value later, but R4 authority remains Agent execution lifecycle.

---

# 20. Wait expiry authority

R4 needs a deterministic function for stale WAITING execution.

At minimum:

```text
resume path:
    refuse expired wait
    CAS WAITING -> TIMEOUT
```

A background sweeper is useful but not mandatory for correctness if every resume/read path checks expiry.

Recommended phase split:

```text
R4-A:
    on-resume expiry enforcement

R4-B optional:
    background expiration sweeper / maintenance job
```

Do not make correctness depend solely on a best-effort background task.

---

# 21. R4 proposed implementation phases

## R4-A1 — contract freeze

Define:

```text
remaining_active_budget_seconds
wait_expires_at
clock authority
wait TTL policy
iteration deadline semantics
LONG_RUNNING timeout semantics
child synchronous budget semantics
```

No code.

## R4-A2 — domain + SQL representation

Modify:

```text
se/src/domain/schemas/agent_execution.py
se/src/infrastructure/storage/models/sql/agent/execution.py
migration 10a
```

Add migration/round-trip tests.

## R4-B1 — execution budget context

Modify:

```text
se/src/runtimes/agent/contracts/context.py
```

Separate:

```text
configured max timeout
durable remaining budget
process-local active deadline
iteration deadline
```

Add deterministic clock injection or helper.

## R4-B2 — WAITING freeze / resume restore

Modify:

```text
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/persistence.py
```

Freeze remaining budget at WAITING CAS.

Restore active deadline from persisted remaining duration.

Reject expired wait.

## R4-B3 — wait TTL policy

Add execution wait policy abstraction and default reason mapping.

Do not mix R7 ResumeClaim TTL with R4 wait TTL.

## R4-C1 — iteration hierarchy

Enforce:

```text
iteration =
min(execution remaining, iteration timeout)
```

Use it for:

```text
context build
inference
tool batch
resumed tool batch
```

## R4-C2 — operation hierarchy

Normal TOOL:

```text
min(execution, iteration, tool timeout)
```

Inference:

```text
min(execution, iteration, inference timeout)
```

## R4-C3 — LONG_RUNNING/nested Agent budget

Stop applying one-shot tool timeout to LONG_RUNNING Agent capability.

Propagate typed caller remaining budgets.

Child synchronous E2 budget:

```text
<= parent execution remaining
<= parent iteration remaining
```

R5 later adds shared TaskBudget as another upper bound.

## R4-D — proof / regressions

Real durable tests plus existing WS/resume regressions.

---

# 22. Required R4 tests

At minimum:

## Active budget freezes

```text
configured = 60
run 20
WAITING
persist remaining = 40

wall-clock WAIT 10 minutes

resume
active remaining ~= 40
not 60
not negative from 10-minute wait
```

Use fake/injected clock, not real sleeps.

## Multiple WAITING cycles

```text
60
run 10 -> wait => 50
resume
run 15 -> wait => 35
resume
remaining => 35
```

No budget regeneration.

## Restart

Persist WAITING state and reconstruct in a fresh store/runtime.

Budget remains identical.

## Wait TTL

```text
WAITING(CONNECTION)
wait_expires_at = T

resume at T-epsilon -> allowed
resume at T or later -> TIMEOUT / denied
```

## No raw monotonic persistence

Verify SQL/context_state has remaining duration, not an absolute process monotonic timestamp.

## Iteration timeout

Execution has 60s, iteration timeout 20s.

A context builder/inference/tool batch that exceeds 20s cannot consume 60s.

## Inference timeout

```text
min(execution, iteration, inference configured)
```

## One-shot TOOL timeout

```text
min(execution, iteration, tool configured)
```

## LONG_RUNNING Agent not killed by tool timeout

Example:

```text
parent execution remaining 40
parent iteration remaining 18
tool_timeout = 2

nested Agent effective budget = 18
not 2
```

## Child never exceeds parent

```text
parent remaining = 5
default child configured = 60
child effective active budget <= 5
```

## Direct root Agent

A direct/root Agent without caller parent uses its own configured execution budget.

## Wait TTL vs active budget

Ten minutes waiting must not reduce active remaining seconds.

## Terminal resurrection

Expired wait timeout remains terminal and cannot resume.

---

# 23. R4 regression gates

Must include:

```text
R0-R3 completion suites
R2 CAS/resume race
R3 nested durable lineage
capability invocation lifecycle
Agent runtime/adapters/tool coordinator
Phase 6.9 connection/reconnect
Phase 6.10 real WS Agent loop
Phase 6.11 context assembly
full se/tests + tools + cl/tests
```

Critical non-regression:

```text
resume keeps same execution_id
R4 does not create retry E3
R4 does not create TaskBranch
R4 does not implement shared TaskBudget
R4 does not change capability ownership I1=E1
connection_id stays routing affinity
WAITING still requires wait_reason
terminal state cannot resurrect
```

---

# 24. Findings summary

## P0

1. Resume resets full execution timeout instead of restoring remaining active budget.
2. WAITING transition does not persist/freeze remaining active budget.
3. No `wait_expires_at` representation or enforcement.
4. `iteration_timeout_seconds` is not enforced anywhere.
5. Nested AGENT/LONG_RUNNING is incorrectly constrained by `tool_timeout_seconds`.
6. Child Agent receives a fresh default execution budget and can exceed parent remaining time.

## P1

1. No explicit clock abstraction, making active-budget/wait-TTL tests difficult and error-prone.
2. No typed budget propagation through `CapabilityExecutionContext`.
3. No wait-reason TTL policy authority.
4. Deadline error taxonomy does not distinguish execution/iteration/operation expiry cleanly.
5. R4/R7 boundary must be frozen so R4 wait TTL is not confused with future ResumeClaim TTL.
6. Synchronous child Agent must explicitly be bounded by parent iteration remaining; roadmap wording should state this.

---

# 25. Recommended next action

Do **not** implement R4 from the roadmap prose alone.

First produce:

```text
R4_ACTIVE_BUDGET_WAIT_TTL_CONTRACT_FREEZE.md
```

freezing:

- durable fields;
- clock authority;
- WAITING freeze/resume arithmetic;
- wait expiry CAS behavior;
- iteration deadline;
- effective operation timeout formulas;
- LONG_RUNNING exception to tool timeout;
- child synchronous budget propagation;
- exact R4/R5/R7 boundaries.

Only after that contract review should R4-A2/B/C patches be written.
