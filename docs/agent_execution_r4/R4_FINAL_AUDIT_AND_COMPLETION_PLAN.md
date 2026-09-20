# R4 FINAL AUDIT — Active Budget / Wait TTL / Deadline Hierarchy

**Repository:** `boxs-51/assistant`  
**Audited HEAD:** `91ffb7052c5927a65c9b3bdd4cc847d50a64f29d`  
**HEAD message:** `cap nhat R4 B2`  
**Audit date:** 2026-09-20  
**Goal:** finish R4 in one implementation wave without entering R5 or R7.

---

## 1. Executive status

R4 is **not yet complete** on the audited HEAD.

Current phase state:

```text
R4 Contract Freeze                    FROZEN

R4-A1 Durable representation          COMPLETE
R4-A2 Clock / active-budget context   COMPLETE
R4-B1 RUNNING -> WAITING freeze       COMPLETE
R4-B2 Resume / expiry / restart       COMPLETE

R4-C1 Iteration deadline hierarchy    NOT IMPLEMENTED
R4-C2 Capability timeout hierarchy    NOT IMPLEMENTED
R4-C3 Nested Agent child budget       NOT IMPLEMENTED

R4-D  Exit gate / completion record   PENDING
```

User-provided evidence for B2:

```text
R4-B2 architecture       6 passed
R4-B2 restart SQLite     3 passed
targeted regression     57 passed
full suite             519 passed, 5 warnings
failures                 0
```

The Windows asyncio Proactor unraisable cleanup warnings occur after the suite
has completed and are not an R4 timing-semantic blocker.

---

## 2. Frozen R4 target

R4 owns:

```text
active execution budget
WAITING budget freeze
wait TTL
restart-safe remaining duration
resume reconstruction
iteration deadline
operation deadline hierarchy
normal TOOL timeout
LONG_RUNNING / AGENT timeout exemption
nested synchronous Agent budget inheritance
```

R4 does **not** own:

```text
TaskBudget                         -> R5
task-wide cancellation tree       -> R5
durable ResumeClaim lease         -> R7
checkpoint atomicity redesign     -> R7
claim_expires_at                  -> R7
branch/fork/retry scheduler       -> later roadmap phases
```

Deadline hierarchy in R4:

```text
Execution active budget
        ↓
Iteration budget
        ↓
Operation budget
```

R5 may later add:

```text
Task budget
    ↓
Execution active budget
```

---

# 3. Completed implementation

## 3.1 R4-A1 — durable representation

Implemented:

```text
AgentExecution.remaining_active_budget_seconds
AgentExecution.wait_expires_at

AgentExecutionRecord.remaining_active_budget_seconds
AgentExecutionRecord.wait_expires_at

Alembic:
9a_r3_execution_lineage
    ↓
10a_r4_active_budget_wait_ttl
```

`wait_expires_at` is indexed.

Legacy rows remain representable with:

```text
remaining_active_budget_seconds = NULL
wait_expires_at = NULL
```

---

## 3.2 R4-A2 — active clock model

Implemented:

```text
ExecutionClock.monotonic()
ExecutionClock.now_utc()
SystemExecutionClock
```

`AgentExecutionContext` now distinguishes:

```text
process-local monotonic deadline
durable remaining duration
UTC wait expiry
```

Implemented helpers:

```text
remaining_active_seconds
freeze_active_budget()
restore_active_budget()
```

Raw monotonic timestamps are not persisted.

Frozen legacy policy:

```text
legacy WAITING
+
remaining_active_budget_seconds = NULL
=
FAIL CLOSED
```

---

## 3.3 R4-B1 — WAITING freeze

Implemented canonical durable transition:

```text
RUNNING
   ↓ freeze active budget
WAITING
```

One CAS persists:

```text
state
wait_reason
remaining_active_budget_seconds
wait_expires_at
result/error
revision
```

If active budget is exhausted at the WAITING boundary:

```text
WAITING is rejected
→ TIMEOUT
```

`ConfiguredExecutionWaitPolicy` exists and does not invent product TTL values.

---

## 3.4 R4-B2 — resume / expiry / restart

Implemented restart reconstruction:

```text
persisted remaining duration
    ↓ rehydrate frozen
    ↓ durable WAITING claim
    ↓ restore monotonic deadline
```

Correct resume gate now checks:

```text
WAITING state
revision
legacy NULL
zero remaining budget
wait_expires_at
CAS ownership
```

Boundary:

```text
now < wait_expires_at  -> resume may proceed
now >= wait_expires_at -> WAITING -> TIMEOUT
```

WebSocket resume ordering now prevents ACK/merge before the durable claim
wins.

Two simultaneous resume attempts have one durable winner.

---

# 4. Remaining P0 — R4-C1 Iteration deadline

## Finding

`AgentExecutionLimits.iteration_timeout_seconds` exists but is unused by
production runtime.

Repository search finds it only in:

```text
se/src/domain/schemas/agent_execution.py
```

Current runtime behavior:

```text
context build:
    timeout = execution remaining

inference:
    min(execution remaining, inference_timeout_seconds)

tool batch:
    timeout = execution remaining
```

Missing:

```text
iteration_budget =
min(
    execution_remaining,
    iteration_timeout_seconds
)
```

Therefore one iteration can consume the entire execution budget.

### Required C1 contract

`AgentExecutionContext` needs process-local iteration deadline state:

```text
iteration_deadline_monotonic
```

Required helpers:

```text
begin_iteration_budget()
clear_iteration_budget()
remaining_iteration_seconds
remaining_for_operation(timeout)
```

Required effective timeouts:

```text
context build =
min(execution remaining, iteration remaining)

inference =
min(
    execution remaining,
    iteration remaining,
    inference_timeout_seconds
)

tool batch envelope =
min(execution remaining, iteration remaining)
```

### Resume pending tool calls

A resumed pending tool belongs to a previously-started logical iteration but
does not persist a raw iteration monotonic deadline across restart.

R4-C1 policy:

```text
resume pending tool batch
    -> allocate a new process-local iteration envelope
       bounded by:
       execution remaining
       configured iteration_timeout_seconds
```

This avoids persistence of monotonic iteration timestamps while still
enforcing the hierarchy after restart.

### WAITING

When active execution is frozen:

```text
iteration_deadline_monotonic = NULL
```

No iteration deadline survives durable suspension.

---

# 5. Remaining P0 — R4-C2 Capability timeout hierarchy

## Finding

`CapabilityToolExecutionAdapter` currently does:

```text
timeout =
context.remaining_for(tool_timeout_seconds)
```

That means:

```text
min(execution remaining, tool timeout)
```

but omits iteration remaining.

More importantly, this exact tool timeout is also applied to:

```text
AGENT
LONG_RUNNING
```

This violates the frozen R4 contract.

### Required normal TOOL contract

```text
effective_timeout =
min(
    execution remaining,
    iteration remaining,
    tool_timeout_seconds
)
```

### Required LONG_RUNNING / AGENT contract

Predicate:

```text
definition.execution_mode == LONG_RUNNING
OR
definition.kind == AGENT
```

Effective timeout:

```text
min(
    execution remaining,
    iteration remaining
)
```

Do **not** apply `tool_timeout_seconds`.

### Required typed propagation

`CapabilityExecutionContext` needs ephemeral caller timing provenance:

```text
caller_execution_remaining_seconds
caller_iteration_remaining_seconds
```

These are not durable fields.

Agent-owned invocation supplies them.

Direct HTTP/MCP invocation may leave them `NULL`.

This avoids making nested Agent budget depend on:

```text
connection_id
metadata string conventions
ID prefixes
```

---

# 6. Remaining P0 — R4-C3 Nested Agent budget

## Finding

`AgentCapabilityDriver` currently creates every child with:

```python
limits=AgentExecutionLimits()
```

Default:

```text
timeout_seconds = 60
```

Therefore:

```text
parent execution remaining = 12
parent iteration remaining = 7

nested child
→ currently may receive 60
```

This violates the parent upper bound.

### Current R4 boundary

TaskBudget does not exist yet, therefore R4 child budget is:

```text
child_budget =
min(
    child configured execution timeout,
    caller execution remaining,
    caller iteration remaining
)
```

Because nested Agent execution is currently synchronous inside the parent tool
batch, the child must be bounded by the parent **iteration** as well as the
parent execution.

Future async ownership transfer may change the iteration relationship, but
that is not R4 v1.

### Direct/root Agent capability

No Agent caller:

```text
caller_agent_execution_id = NULL
```

then:

```text
child uses its own configured budget
```

unless an explicit external invocation timeout provides a tighter upper bound.

---

# 7. R4 task-ownership audit

## ProviderInferenceAdapter

Current code owns:

```text
provider_task
cancellation_task
```

On timeout/cancel it performs:

```text
cancel
await asyncio.gather(..., return_exceptions=True)
```

No R4 patch required.

## AgentToolExecutionCoordinator

Current code owns tool tasks and cancellation cleanup tasks, with explicit
`gather()` cleanup.

No R4 deadline-specific ownership patch is required.

The existing Windows Proactor subprocess warnings are outside these Agent
timing contracts.

---

# 8. Terminal timing cleanup audit

B1/B2 already ensure WAITING timing state is cleared during resume/terminal
transitions where relevant.

R4-C1 must additionally ensure:

```text
durable WAITING
terminal result
```

does not retain a process-local iteration deadline.

No new SQL field is required.

---

# 9. Patch sequence

R4 completion should be implemented in this exact sequence:

```text
R4-C1
Iteration Deadline Hierarchy

    ↓

R4-C2
Capability Timeout Hierarchy
+ LONG_RUNNING / AGENT exemption
+ typed caller timing provenance

    ↓

R4-C3
Nested Agent child-budget inheritance

    ↓

R4-D
Integrated exit gate
+ completion documentation
```

Each patch is independently reviewable and should be applied only after the
previous patch.

---

# 10. R4-C1 exit criteria

Must prove:

```text
iteration_timeout_seconds is live runtime behavior
iteration <= execution remaining
context build <= iteration
inference <= iteration and inference config
tool batch <= iteration
resumed pending tool batch <= new process-local iteration envelope
WAITING clears iteration deadline
```

---

# 11. R4-C2 exit criteria

Must prove:

```text
normal TOOL:
min(exec, iteration, tool config)

AGENT:
min(exec, iteration)
not tool_timeout

LONG_RUNNING:
min(exec, iteration)
not tool_timeout

typed caller execution remaining propagated
typed caller iteration remaining propagated

direct non-Agent invocation remains compatible
```

---

# 12. R4-C3 exit criteria

Must prove:

```text
child execution_id remains E2
parent execution remains E1

child budget <= parent execution remaining
child budget <= parent iteration remaining
child budget <= child configured timeout

tool_timeout_seconds does not become child Agent budget

direct/root Agent still gets its own budget
```

R3 lineage invariants must remain unchanged.

---

# 13. R4-D required regression gate

Focused R4:

```text
A1 representation/migration
A2 active clock
B1 WAITING freeze
B2 resume/restart/race
C1 iteration
C2 capability timeout hierarchy
C3 child Agent budget
```

Cross-phase:

```text
R0-R3 lifecycle/CAS
R3 nested durable E1/I1/E2
Phase 5 Agent runtime/adapters/coordinator
Phase 6.9 continuation
real TCP WebSocket Agent/client loop
Phase 6.10 true WebSocket loop
Phase 6.11 context assembly
full server/tools/client suite
```

---

# 14. Final R4 definition of done

R4 may be marked COMPLETE only when all statements below are true:

```text
[ ] remaining active budget survives restart
[ ] WAITING does not consume active budget
[ ] legacy NULL cannot mint new budget
[ ] wait expiry is enforced at resume
[ ] resume race has one winner
[ ] iteration timeout is real
[ ] context build respects iteration budget
[ ] inference respects execution+iteration+operation budget
[ ] normal tool respects execution+iteration+tool timeout
[ ] LONG_RUNNING ignores one-shot tool timeout
[ ] nested Agent cannot exceed parent execution remaining
[ ] nested Agent cannot exceed parent iteration remaining
[ ] R3 E1/I1/E2 lineage remains intact
[ ] no raw monotonic value is persisted
[ ] full regression suite passes
```

On audited HEAD the first five groups are implemented. The remaining
implementation work is C1, C2, C3, then D exit-gate closure.
