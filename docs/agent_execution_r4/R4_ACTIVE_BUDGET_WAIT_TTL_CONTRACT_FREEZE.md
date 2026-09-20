# R4 — Active Budget / Wait TTL / Deadline Hierarchy
## Contract Freeze v1

**Repository:** `boxs-51/assistant`  
**Baseline:** `5619e99830c92ceadebae1cfa3df028624437995`  
**Prerequisite:** R3 Execution Lineage COMPLETE  
**Document status:** **FROZEN — REVIEW COMPLETE / R4-A1 MAY START**  
**Scope:** R4 only — active execution budget, WAITING TTL, deadline hierarchy, nested LONG_RUNNING Agent budget propagation.

---

# 1. Purpose

R4 separates execution lifetime into independent time domains.

The current runtime has one process-local execution deadline:

```text
deadline = time.monotonic() + limits.timeout_seconds
```

That model is insufficient for durable WAITING/resume because:

```text
RUNNING time
WAITING wall-clock time
iteration timeout
inference timeout
tool timeout
nested Agent lifetime
```

do not share the same semantics.

R4 freezes the contract before implementation so later patches cannot accidentally:

```text
reset execution budget on resume
consume active budget while WAITING
treat wait TTL as execution budget
let iteration work exceed execution budget
kill LONG_RUNNING Agent with one-shot tool timeout
let child Agent exceed parent remaining time
persist process-local monotonic timestamps
mix R4 wait TTL with R7 ResumeClaim TTL
```

---

# 2. R4 non-goals

R4 does **not** implement:

```text
TaskBudget
task-wide token/cost/tool/inference accounting
delegation depth / recursion cycle prevention
task-wide cancellation tree
retry scheduler
retry execution E3
TaskBranch persistence
FORK
ResumeClaim
checkpoint-directed resume protocol
invocation reconciliation
side-effect replay authority
lease/owner crash recovery
```

Ownership remains:

```text
R4  execution-local active budget / wait TTL / deadline hierarchy
R5  TaskBudget / cancellation tree / depth / fan-out
R6  reconciliation / idempotency
R7  checkpoint + ResumeClaim + durable resume protocol
R8+ branch/fork/retry orchestration
```

---

# 3. Frozen vocabulary

## 3.1 Configured execution budget

```text
limits.timeout_seconds
```

Meaning:

> Maximum active runtime duration initially granted to one AgentExecution before parent/task/delegation caps are applied.

It is configuration, not durable remaining state.

It must never be reused as the resumed remaining budget after WAITING.

---

## 3.2 Remaining active budget

Canonical durable field:

```text
remaining_active_budget_seconds
```

Meaning:

> Amount of execution-active time still available to the same AgentExecution.

This value decreases only while execution ownership is actively RUNNING.

It does not decrease while durable state is WAITING.

---

## 3.3 Active deadline

Process-local field:

```text
active_deadline_monotonic
```

Meaning:

```text
time.monotonic() + remaining_active_budget_seconds
```

It is reconstructed whenever runtime claims active ownership.

It must never be persisted.

---

## 3.4 Wait TTL

Canonical durable field:

```text
wait_expires_at
```

Meaning:

> UTC wall-clock timestamp after which one durable WAITING execution is no longer resumable under the current wait policy.

It is independent from active execution budget.

---

## 3.5 Iteration deadline

Process-local field:

```text
iteration_deadline_monotonic
```

Meaning:

```text
time.monotonic() + min(
    execution_remaining,
    configured_iteration_timeout
)
```

It exists only while one Agent iteration is active.

It must never be persisted as a raw monotonic timestamp.

---

## 3.6 Operation timeout

One operation-local duration derived from the current hierarchy.

Examples:

```text
context build
provider inference
one-shot tool invocation
nested synchronous capability call
```

An operation timeout is not durable execution state.

---

# 4. Clock authority

R4 freezes two clock domains.

## Monotonic clock

Use for elapsed active runtime:

```text
execution active budget
iteration budget
inference timeout
tool timeout
nested synchronous operation timeout
```

Conceptual API:

```text
ExecutionClock.monotonic() -> float
```

## UTC wall clock

Use for durable calendar expiry:

```text
wait_expires_at
created_at
updated_at
completed_at
```

Conceptual API:

```text
ExecutionClock.now_utc() -> datetime
```

Frozen rule:

```text
raw monotonic timestamps MUST NOT be persisted
```

A restart must reconstruct active deadlines from a persisted duration:

```text
active_deadline_monotonic =
clock.monotonic() + remaining_active_budget_seconds
```

---

# 5. Canonical AgentExecution durable fields

R4 adds:

```text
remaining_active_budget_seconds: float | null
wait_expires_at: datetime | null
```

Existing R3 lineage remains unchanged.

Target semantic contract:

```text
AgentExecution:
    execution_id
    session_id
    task_id
    branch_id
    agent_id

    parent_execution_id
    retry_of_execution_id
    base_execution_id
    base_checkpoint_id

    correlation_id

    state
    wait_reason
    revision

    remaining_active_budget_seconds
    wait_expires_at

    request
    result
    error

    created_at
    started_at
    updated_at
    completed_at
```

---

# 6. SQL contract

R4 migration target:

```text
10a_r4_active_budget_wait_ttl
```

Add to `agent_executions`:

```text
remaining_active_budget_seconds FLOAT NULL
wait_expires_at DATETIME(timezone=True) NULL
```

Add index:

```text
ix_agent_executions_wait_expires_at
```

Recommended later query:

```text
WHERE state = 'WAITING'
  AND wait_expires_at IS NOT NULL
  AND wait_expires_at <= :now
```

A composite `(state, wait_expires_at)` index is not frozen into R4 v1 unless query-plan evidence requires it.

---

# 7. Initial execution budget

For a root execution with no upper parent/task cap:

```text
initial_remaining =
limits.timeout_seconds
```

Then:

```text
active_deadline_monotonic =
clock.monotonic() + initial_remaining
```

Durable creation should persist:

```text
remaining_active_budget_seconds = initial_remaining
wait_expires_at = null
```

A root execution must not receive additional time simply because runtime restarts.

---

# 8. Active budget consumption

While execution is actively RUNNING:

```text
remaining =
max(
    0,
    active_deadline_monotonic - clock.monotonic()
)
```

That value is the authoritative remaining duration when execution ownership is released.

Frozen rule:

```text
CREATED/RUNNING consumes active budget
WAITING does not consume active budget
terminal states do not resume consumption
```

---

# 9. Entering WAITING

The RUNNING → WAITING lifecycle boundary is the R4 budget-freeze authority.

Given:

```text
RUNNING@revision=N
```

compute:

```text
remaining =
max(
    0,
    active_deadline_monotonic - clock.monotonic()
)
```

and:

```text
wait_expires_at =
wait_policy.expiry_for(
    wait_reason,
    clock.now_utc()
)
```

Then durable CAS:

```text
RUNNING@N
→
WAITING@(N+1)
```

must persist together:

```text
state = WAITING
wait_reason = required non-null reason
remaining_active_budget_seconds = remaining
wait_expires_at = computed UTC timestamp or null by policy
completed_at = null
```

If:

```text
remaining <= 0
```

execution must not enter resumable WAITING.

It transitions to timeout semantics instead.

---

# 10. WAITING does not spend active budget

Frozen invariant:

```text
remaining_active_budget_seconds
```

must remain constant while execution stays WAITING.

Example:

```text
configured budget = 60s

RUNNING consumes 20s
remaining = 40s

WAITING for 10 minutes

resume
remaining active budget = 40s
```

Not:

```text
50 minutes negative
60 seconds reset
```

---

# 11. Wait TTL policy

R4 introduces one policy authority.

Conceptual contract:

```text
ExecutionWaitPolicy.wait_ttl_seconds(
    reason: AgentExecutionWaitReason,
    execution/context
) -> float | None
```

Semantics:

```text
positive float
    automatic expiry after this wall-clock duration

None
    no automatic expiry under current configured policy
```

R4 freezes the authority, not exact product defaults.

Recommended policy categories:

```text
CONNECTION
    configurable hours/day-scale TTL

HUMAN_APPROVAL
    configurable long TTL

EXPLICIT_PAUSE
    configurable; may be unlimited

DEPENDENCY
    dependency-specific TTL

RECOVERY
    short recovery window

RETRY_BACKOFF
    durable scheduled retry time + guard window
```

Exact numeric defaults belong to configuration, not the contract.

---

# 12. Wait expiry semantics

Before any WAITING execution may resume:

```text
if wait_expires_at is not null
and clock.now_utc() >= wait_expires_at:
    execution is expired
```

Frozen state transition:

```text
WAITING
→
TIMEOUT
```

Resume is denied.

The timeout mutation must use revision/CAS.

Terminal execution cannot be resurrected.

Correctness must not depend only on a background sweeper.

A future sweeper may proactively expire rows, but the resume path must always verify TTL itself.

---

# 13. Resume semantics

Resume preserves execution identity:

```text
WAITING E1
→
RUNNING E1
```

Before claim:

```text
state == WAITING
revision matches
wait TTL not expired
remaining_active_budget_seconds > 0
```

After successful claim:

```text
active_deadline_monotonic =
clock.monotonic()
+
remaining_active_budget_seconds
```

Durable state becomes:

```text
state = RUNNING
wait_reason = null
wait_expires_at = null
```

The configured:

```text
limits.timeout_seconds
```

must not overwrite persisted remaining budget.

---

# 14. Resume budget source of truth

Frozen priority:

```text
persisted remaining_active_budget_seconds
```

is the authoritative source for a resumed execution.

Not:

```text
limits.timeout_seconds
started_at
updated_at
wall-clock time since creation
wall-clock time spent WAITING
```

Compatibility migration rule:

For legacy rows with:

```text
remaining_active_budget_seconds = null
```

the frozen policy is **fail-closed**.

For a legacy `WAITING` execution:

```text
remaining_active_budget_seconds = null
```

means:

```text
budget provenance is unknown
resume MUST NOT mint a fresh budget
resume MUST NOT fall back to limits.timeout_seconds
resume MUST NOT estimate from created_at/updated_at/started_at
```

The row remains representable and readable, but a future R4-B2 resume path must
reject it with an explicit compatibility/recovery error until an operator or
dedicated migration/recovery path supplies a trustworthy remaining duration.

New R4 executions must always persist a real non-null remaining value before
they become durably resumable.

---

# 15. Terminal semantics

For:

```text
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

persist:

```text
wait_reason = null
wait_expires_at = null
```

Recommendation:

```text
remaining_active_budget_seconds
```

is retained at final observed value for observability.

It is not reusable because terminal resurrection is prohibited.

---

# 16. Deadline hierarchy

R4 freezes:

```text
Task budget                # future R5 upper bound
    ↓
Execution active budget
    ↓
Iteration budget
    ↓
Operation budget
```

In R4, TaskBudget is not implemented.

Therefore current hierarchy is:

```text
Execution active budget
    ↓
Iteration budget
    ↓
Operation budget
```

Every lower layer must be less than or equal to every active upper bound.

---

# 17. Iteration budget

Existing configuration:

```text
limits.iteration_timeout_seconds
```

becomes active runtime policy.

At the start of one iteration:

```text
iteration_budget =
min(
    execution_remaining,
    limits.iteration_timeout_seconds
)
```

Then:

```text
iteration_deadline_monotonic =
clock.monotonic() + iteration_budget
```

Expose:

```text
iteration_remaining_seconds =
max(
    0,
    min(
        execution_remaining,
        iteration_deadline_monotonic - clock.monotonic()
    )
)
```

All synchronous work belonging to the iteration must remain inside this deadline.

---

# 18. Context-build timeout

Current context build has no independent configured timeout.

R4 freezes:

```text
effective_context_build_timeout =
min(
    execution_remaining,
    iteration_remaining
)
```

R4 does not introduce an additional context-build timeout setting.

---

# 19. Inference timeout

Effective inference timeout:

```text
effective_inference_timeout =
min(
    execution_remaining,
    iteration_remaining,
    limits.inference_timeout_seconds
)
```

Provider Runtime may retry/fallback internally only while respecting the timeout budget supplied by AgentRuntime.

Provider retries do not receive a fresh operation deadline.

---

# 20. Normal TOOL timeout

For one-shot TOOL semantics:

```text
effective_tool_timeout =
min(
    execution_remaining,
    iteration_remaining,
    limits.tool_timeout_seconds
)
```

This applies to normal short-lived TOOL execution.

The one-shot tool timeout is not a general capability timeout for every capability kind.

---

# 21. LONG_RUNNING exemption

Frozen rule:

```text
CapabilityExecutionMode.LONG_RUNNING
```

must not be capped by:

```text
limits.tool_timeout_seconds
```

Nested Agent capabilities are LONG_RUNNING and therefore do not inherit one-shot TOOL timeout semantics.

Additional guard:

```text
CapabilityKind.AGENT
```

must also bypass one-shot tool timeout.

Recommended predicate:

```text
is_long_running =
    definition.execution_mode == LONG_RUNNING
    or definition.kind == AGENT
```

If true:

```text
tool_timeout_seconds does not apply
```

The call remains bounded by parent execution and iteration budgets.

---

# 22. Capability budget propagation

R4 must propagate caller timing authority through typed fields.

Recommended ephemeral fields on `CapabilityExecutionContext`:

```text
caller_execution_remaining_seconds: float | null
caller_iteration_remaining_seconds: float | null
```

For Agent-owned tool/capability calls:

```text
caller_execution_remaining_seconds =
    parent execution remaining

caller_iteration_remaining_seconds =
    parent iteration remaining
```

For direct HTTP/MCP/non-Agent calls:

```text
caller_execution_remaining_seconds = null
caller_iteration_remaining_seconds = null
```

Budget provenance must not live only in generic metadata.

---

# 23. Synchronous child Agent budget

R4 freezes the current nested Agent call as synchronous:

```text
parent iteration waits for child Agent result
```

Therefore child E2 cannot outlive either:

```text
parent execution remaining
parent iteration remaining
```

For current R4:

```text
child_initial_budget =
min_non_null(
    configured child/delegated Agent budget,
    caller_execution_remaining_seconds,
    caller_iteration_remaining_seconds
)
```

If no dedicated delegated-Agent configured budget exists yet:

```text
configured child budget =
child limits.timeout_seconds
```

Therefore:

```text
child_initial_budget =
min(
    child limits.timeout_seconds,
    parent execution remaining,
    parent iteration remaining
)
```

This is the frozen R4 v1 rule.

---

# 24. Future asynchronous child ownership

If a future phase allows child Agent execution to detach from the parent iteration and continue asynchronously, then:

```text
parent iteration remaining
```

may cease to be an upper bound after ownership transfer.

That is explicitly **not R4 v1 behavior**.

R4 v1 supports the repository's current synchronous nested Agent path only.

---

# 25. Child active budget may never expand

Frozen invariant:

```text
child active budget
<= parent execution remaining at delegation
```

and for synchronous R4:

```text
child active budget
<= parent iteration remaining at delegation
```

A child created with default:

```text
AgentExecutionLimits(timeout_seconds=60)
```

cannot receive 60 seconds if the parent has only 5 seconds remaining.

---

# 26. Direct/root Agent budget

A root Agent with no Agent parent derives budget from its own configured limit:

```text
initial_remaining =
limits.timeout_seconds
```

A direct AGENT capability that R3 treats as root E2 likewise uses its own configured execution budget unless another explicit external upper bound exists.

It must not fabricate a parent timing budget from synthetic capability execution IDs.

---

# 27. CapabilityRuntime timeout meaning

`CapabilityRuntime.execute_capability(timeout_seconds=...)` remains an operation envelope.

For normal TOOL:

```text
timeout_seconds =
effective_tool_timeout
```

For nested LONG_RUNNING Agent:

```text
timeout_seconds =
min(
    parent execution remaining,
    parent iteration remaining
)
```

It must not receive:

```text
tool_timeout_seconds
```

as the controlling cap for LONG_RUNNING Agent execution.

---

# 28. Tool batch deadline

The parent tool batch must be bounded by:

```text
min(
    execution_remaining,
    iteration_remaining
)
```

Each one-shot TOOL may then apply a smaller tool-specific timeout.

One LONG_RUNNING child in the batch cannot escape the parent iteration deadline.

---

# 29. Resumed pending tool calls

Resumed pending tool calls must run under reconstructed active budget.

Frozen requirements:

```text
resume active deadline
    derives from persisted remaining budget

resumed iteration budget
    derives from execution remaining + iteration timeout

resumed one-shot tool
    derives from execution + iteration + tool timeout

resumed LONG_RUNNING Agent
    derives from execution + iteration
```

Resume must not bypass iteration timeout enforcement.

---

# 30. Short retry backoff

Short in-process provider/tool retry backoff does not create durable WAITING.

Example:

```text
sleep 1.5s during RUNNING
```

This remains active runtime and consumes active budget.

Use:

```text
WAITING(RETRY_BACKOFF)
```

only when runtime ownership is durably released for a scheduled retry.

R4 introduces wait TTL semantics for such WAITING rows but does not implement retry scheduling.

---

# 31. R4 / R7 TTL separation

R4 wait TTL:

```text
wait_expires_at
```

belongs to the WAITING execution itself.

R7 ResumeClaim TTL belongs to a future claim object:

```text
claim_expires_at
```

They are independent.

Example:

```text
execution WAITING valid until 18:00

resume claim acquired at 17:30
claim valid only until 17:31
```

Claim expiry does not automatically mean execution wait TTL expired.

Execution wait TTL expiry invalidates future resume claims.

---

# 32. R4 / R7 atomicity boundary

R4 freezes lifecycle fields:

```text
remaining_active_budget_seconds
wait_expires_at
```

R7 later expands the RUNNING→WAITING atomic transaction to include:

```text
immutable checkpoint
current_checkpoint_id
pending invocation durability
resume protocol state
```

R4 must not introduce a separate checkpoint authority.

R7 must reuse R4 budget/wait fields as part of the larger atomic transaction.

---

# 33. R4 / R5 boundary

R4 execution budget is execution-local.

R5 TaskBudget will later add shared constraints such as:

```text
max_total_executions
max_active_executions
max_total_tool_calls
max_total_tokens
max_total_cost
max_delegation_depth
```

R4 child budget formula therefore does **not** yet include TaskBudget.

Future R5 hierarchy becomes:

```text
TaskBudget/time upper bound
    ↓
Execution
    ↓
Iteration
    ↓
Operation
```

R4 must leave a clean extension point for this upper bound.

---

# 34. Cancellation interaction

Cancellation is not a time budget.

Frozen rule:

```text
cancellation_event
```

and deadlines are independent stop conditions.

Effective execution permission requires both:

```text
not cancelled
remaining time > 0
```

A cancelled execution cannot be resumed merely because active budget remains.

R5 owns complete cancellation-tree propagation.

---

# 35. Error classification

R4 should preserve deterministic timeout origin.

Recommended internal error categories:

```text
AGENT_EXECUTION_TIMEOUT
AGENT_ITERATION_TIMEOUT
CAPABILITY_TIMEOUT
WAIT_TTL_EXPIRED
```

Public compatibility may continue mapping them into existing canonical error DTOs.

Frozen requirement:

> Runtime must be able to identify which deadline caused termination; do not collapse all timeout sources before observability/persistence.

---

# 36. Durable mutation authority

Only Agent execution lifecycle authority may persist:

```text
remaining_active_budget_seconds
wait_expires_at
```

Primary authority:

```text
AgentRuntime
```

`DurableAgentStore` persists/reconstructs values but does not invent them.

Must not independently mutate execution budget:

```text
CapabilityRuntime
AgentContinuationService
WebSocket transport
client
provider
```

---

# 37. Recommended clock abstraction

R4 should introduce an injectable clock.

Conceptual:

```python
class ExecutionClock(Protocol):
    def monotonic(self) -> float: ...
    def now_utc(self) -> datetime: ...
```

Default:

```text
monotonic -> time.monotonic
now_utc   -> datetime.now(timezone.utc)
```

Tests use deterministic fake clock.

No real sleep should be required for R4 budget/TTL contract tests.

---

# 38. Runtime helper contract

Recommended execution context helpers:

```text
remaining_active_seconds
begin_iteration()
iteration_remaining_seconds
remaining_for_inference()
remaining_for_tool()
remaining_for_long_running()
freeze_active_budget()
restore_active_budget()
```

Exact method names are not frozen.

Semantics are frozen.

Avoid scattering raw `min(...)` formulas throughout adapters and runtimes without one canonical helper layer.

---

# 39. State invariants

## CREATED

```text
wait_reason = null
wait_expires_at = null
remaining_active_budget_seconds > 0
```

## RUNNING

```text
wait_reason = null
wait_expires_at = null
active_deadline_monotonic exists process-locally
```

## WAITING

```text
wait_reason != null
remaining_active_budget_seconds >= 0
active deadline is not running
wait_expires_at = timestamp or null according to policy
```

## Terminal

```text
wait_reason = null
wait_expires_at = null
cannot resume
```

---

# 40. Resume invariants

Resume may succeed only when:

```text
state == WAITING
revision matches
wait TTL not expired
remaining_active_budget_seconds > 0
identity/authorization checks pass
```

Successful resume:

```text
same execution_id
same lineage
same configured limits
same remaining active budget
new process-local monotonic deadline
```

Resume must not modify:

```text
parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
task_id
branch_id
correlation_id
```

---

# 41. Migration compatibility

R4 migration must not reinterpret R3 lineage fields.

For existing rows:

```text
RUNNING legacy rows
WAITING legacy rows
terminal legacy rows
```

the migration/read path must define compatibility explicitly.

Recommended migration defaults:

```text
remaining_active_budget_seconds = NULL
wait_expires_at = NULL
```

Then application read compatibility handles legacy nulls.

Do not backfill a fake precise remaining value from wall-clock timestamps.

---

# 42. Required R4 architecture tests

At minimum:

```text
active budget freezes during WAITING
active budget does not regenerate on resume
multiple WAITING cycles do not regenerate budget
restart preserves exact remaining duration
wait TTL allows resume before expiry
wait TTL rejects resume at/after expiry
expired WAITING CASes to TIMEOUT
raw monotonic timestamp is never persisted
iteration timeout is enforced
context build obeys iteration deadline
inference obeys execution+iteration+inference hierarchy
one-shot TOOL obeys execution+iteration+tool hierarchy
LONG_RUNNING Agent ignores one-shot tool timeout
child Agent budget never exceeds parent execution remaining
synchronous child never exceeds parent iteration remaining
direct/root Agent uses own configured budget
terminal execution cannot resurrect
connection_id does not affect timing ancestry
R3 E1→I1→E2 ownership remains unchanged
```

---

# 43. Required durable integration tests

Real SQLite test:

```text
create E1 with 60s
fake monotonic advances 20s
E1 enters WAITING
persist remaining = 40s

advance wall clock by 10 minutes
restart store/runtime
resume E1
remaining active = 40s

advance active monotonic by 10s
WAITING again
persist remaining = 30s
```

No real sleeping.

Second durable test:

```text
WAITING with wait_expires_at=T
restart
resume at T-epsilon -> allowed
resume at T -> TIMEOUT
```

Third durable nested test:

```text
parent execution remaining = 12s
parent iteration remaining = 7s
tool timeout = 2s
child Agent configured timeout = 60s

expected nested child active budget = 7s
not 2s
not 12s
not 60s
```

---

# 44. Required regression gates

R4 exit gate must include:

```text
R0-R3 completion tests
R2 durable CAS/resume race tests
R3 nested real-SQLite lineage
CapabilityInvocation lifecycle
AgentRuntime tests
Agent adapters
tool execution coordinator
workflow composition
Phase 6.9 routing/reconnect
Phase 6.9 real TCP WebSocket
Phase 6.10 true WebSocket Agent/client loop
Phase 6.11 context assembly
offline v1 E2E
full se/tests + tools + cl/tests
```

---

# 45. Prohibited implementation patterns

Reject an R4 patch containing:

```text
resume deadline = now + limits.timeout_seconds

remaining active budget derived from wall-clock WAITING duration

raw time.monotonic() persisted to SQL

WAITING consumes active budget by default

wait TTL stored only in metadata

iteration_timeout_seconds still unused

LONG_RUNNING Agent capped by tool_timeout_seconds

child Agent gets fresh default 60s when parent has less remaining

budget provenance inferred from connection_id

budget provenance inferred from execution-id prefix

R4 wait_expires_at reused as R7 ResumeClaim expiry

TaskBudget implementation hidden inside R4

retry execution creation hidden inside timeout handling

new execution_id created on resume
```

---

# 46. Exact implementation ownership map

## Domain

```text
se/src/domain/schemas/agent_execution.py
```

Add durable R4 fields.

## SQL

```text
se/src/infrastructure/storage/models/sql/agent/execution.py
se/src/infrastructure/storage/migrations/sql/versions/10a_r4_active_budget_wait_ttl.py
```

## Agent context / clocks

```text
se/src/runtimes/agent/contracts/context.py
```

Implement active/iteration timing semantics and clock integration.

## Lifecycle authority

```text
se/src/runtimes/agent/runtime.py
```

Own:

```text
initial budget
RUNNING→WAITING freeze
wait expiry check
resume active-deadline reconstruction
iteration deadline creation
terminal cleanup
```

## Persistence

```text
se/src/runtimes/agent/persistence.py
```

Round-trip durable R4 fields.

## Capability boundary

```text
se/src/runtimes/capability/contracts/context.py
se/src/runtimes/agent/adapters/tool.py
```

Carry typed caller execution/iteration remaining budgets.

## Child Agent

```text
se/src/runtimes/capability/drivers/agent_driver.py
```

Derive bounded child execution budget.

---

# 47. Proposed implementation phases

## R4-A1 — representation

```text
domain fields
SQL fields
migration
round-trip tests
```

## R4-A2 — clock and execution-budget context

```text
ExecutionClock
remaining active helper
active deadline reconstruction
iteration deadline helper
```

## R4-B1 — WAITING freeze

```text
RUNNING→WAITING budget persistence
wait policy
wait_expires_at
```

## R4-B2 — resume

```text
TTL validation
remaining-budget reconstruction
WAITING→RUNNING CAS
```

## R4-C1 — iteration hierarchy

```text
context build
inference
tool batch
resumed tool batch
```

## R4-C2 — capability hierarchy

```text
normal TOOL timeout
LONG_RUNNING exemption
typed caller budgets
```

## R4-C3 — nested Agent budget

```text
child <= parent execution remaining
child <= parent iteration remaining
```

## R4-D — durable proof and regressions

No R5/R7 behavior.

---

# 48. Frozen decisions

The following decisions are frozen by this contract candidate:

```text
D1
WAITING wall-clock duration does not consume active execution budget.

D2
remaining_active_budget_seconds is durable; monotonic deadlines are not.

D3
wait_expires_at uses UTC wall clock.

D4
Resume reconstructs deadline from persisted remaining duration.

D5
Resume never resets budget from limits.timeout_seconds.

D6
iteration_timeout_seconds becomes an enforced upper bound.

D7
Inference timeout = min(execution, iteration, inference configured).

D8
One-shot TOOL timeout = min(execution, iteration, tool configured).

D9
LONG_RUNNING / AGENT does not use one-shot tool timeout.

D10
Current nested Agent delegation is synchronous.

D11
Synchronous child budget <= parent execution remaining.

D12
Synchronous child budget <= parent iteration remaining.

D13
R4 wait TTL is independent from R7 ResumeClaim TTL.

D14
R4 does not implement TaskBudget.

D15
AgentRuntime remains durable timing-state authority.
```

---

# 49. Frozen legacy WAITING compatibility policy

The repository owner decision for R4 v1 is:

```text
legacy WAITING
+
remaining_active_budget_seconds = NULL
=
FAIL CLOSED
```

Required behavior for the future R4-B2 resume implementation:

```text
do not claim RUNNING
do not reset from limits.timeout_seconds
do not infer elapsed active time from wall-clock timestamps
do not create a new execution_id
```

The execution remains durable and inspectable in `WAITING`, but automatic
resume is rejected until an explicit recovery/migration path establishes a
trusted remaining active duration.

This is intentionally stricter than a bounded fallback because the repository
cannot reconstruct historical active-vs-WAITING elapsed time exactly from the
existing pre-R4 schema.

---

# 50. Contract Freeze result

Review result:

```text
Active-budget duration persistence       FROZEN
UTC wait TTL semantics                   FROZEN
monotonic-vs-wall-clock authority        FROZEN
WAITING freeze arithmetic                FROZEN
resume reconstruction arithmetic         FROZEN
iteration deadline                       FROZEN
inference/tool timeout hierarchy         FROZEN
LONG_RUNNING one-shot timeout exemption  FROZEN
sync child <= parent execution budget    FROZEN
sync child <= parent iteration budget    FROZEN
R4/R5 boundary                           FROZEN
R4/R7 TTL boundary                       FROZEN
legacy WAITING NULL policy               FAIL-CLOSED / FROZEN
```

Production implementation may now begin with:

```text
R4-A1 — domain + SQL + migration representation only
```

R4-A1 must not implement runtime budget arithmetic, wait expiry enforcement,
iteration deadlines, LONG_RUNNING timeout behavior, or child budget
propagation. Those remain B/C work after A1 representation passes its exit
gate.
