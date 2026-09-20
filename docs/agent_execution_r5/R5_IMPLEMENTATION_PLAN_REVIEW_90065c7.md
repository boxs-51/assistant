# R5 IMPLEMENTATION PLAN — SENIOR ARCHITECT REVIEW BEFORE CODE

**Repository:** `boxs-51/assistant`  
**Reviewed HEAD:** `90065c730c33ab3261063183570278d4ea13a424`  
**Reviewed artifact:** `R5_IMPLEMENTATION_PLAN.md`  
**Review date:** 2026-09-20  
**Decision:** `APPROVE R5-A1→A3 WITH CONTRACT CORRECTIONS; DO NOT START A4/R5-B UNTIL P0 ITEMS BELOW ARE RESOLVED`

---

## 1. Executive review

The overall roadmap direction is correct:

- Async ownership must be repaired before durable TaskBudget accounting is trusted.
- `AgentExecutionSupervisor` must remain process-local and must not become durable execution authority.
- Parent and child Agent cancellation scopes must be distinct.
- TaskBudget must be durable, shared by child/retry/fork/resume, and protected by DB transaction/CAS.
- WAITING must release active concurrency capacity and RESUME must reacquire it without increasing `used_executions`.

However, the plan still contains several P0/P1 contract contradictions. In particular, the A3 supervisor API as written cannot safely implement the resume reservation lifecycle, and the R5-B legacy migration policy would reset budget accounting for pre-R5 tasks.

The A1→A3 patch accompanying this review deliberately fixes only the process-local ownership layer. It does **not** claim that R5-A is complete.

---

# 2. P0 FINDINGS

## P0-01 — Supervisor reservation API is internally inconsistent

The plan proposes:

```python
def reserve(...)
def start_reserved(...)
```

while also requiring one `asyncio.Lock` for registry consistency.

A synchronous method cannot acquire `asyncio.Lock` without violating the async execution model.

More importantly, the resume flow explicitly says:

```text
reserve
→ durable claim
→ if durable claim fails: release ownership token
```

but the proposed public API contains no reservation-release operation.

### Required correction

Use async mutation APIs:

```python
async def reserve(...)
async def release_reserved(...)
async def start_reserved(...)
```

The A1→A3 patch implements this corrected contract.

---

## P0-02 — A1 test scope requires an A4 production fix

The A1 test list includes:

```text
child execution cancellation event is not parent alias
```

but the production file that currently aliases the event is:

```text
se/src/runtimes/capability/drivers/agent_driver.py
```

and the plan schedules that migration under R5-A4.

Therefore an honest A1→A3 patch cannot both:

1. leave A4 untouched, and
2. make an AgentCapabilityDriver non-alias test green.

### Required correction

For A1→A3:

- test that the Supervisor rejects two live/reserved executions sharing one event;
- defer the concrete AgentCapabilityDriver child-event construction test to A4.

This keeps A1→A3 independently green while making A4 fail closed until it creates a distinct child event.

---

## P0-03 — Resume `claim → ACK → start` can strand durable RUNNING state

The plan proposes:

```text
supervisor.reserve(E1)
→ claim_resume(E1) CAS
→ confirm_merge
→ execution.resume.accepted ACK
→ supervisor.start_reserved(E1)
```

If any of these occurs after the durable claim succeeds:

```text
ACK send fails
start_reserved fails
process crashes
```

the process-local token can be released, but the durable execution has already been claimed RUNNING.

A process-local reservation cannot repair that durable state.

### Required correction

This is a durable resume-claim problem, not a Supervisor problem.

Before R5-A5 is called complete, freeze one of:

- R7 durable claim lease / claim expiry;
- an explicit compensating CAS back to WAITING when safe;
- or a documented temporary recovery contract proving how claimed-but-not-started executions are recovered.

Do not state that `release_reserved()` alone solves this race.

---

## P0-04 — `AgentRuntime.execute()` has a cancellation window before its `try`

Current shape:

```python
revision = await self._begin_durable_execution(context)
try:
    result = await self._execute_loop(context)
```

Cancellation while `_begin_durable_execution()` is creating/claiming durable state is outside the execution cancellation handler.

A commit can therefore become durable while the owner is cancelled before `revision` returns to the protected region.

### Required correction

Before Supervisor migration is considered production-complete, `AgentRuntime.execute()` must define cancellation semantics for the complete durable begin/claim boundary.

Possible implementation must preserve AgentRuntime as durable state authority; Supervisor must not patch the DB directly.

This is intentionally **not** changed in A1→A3 because it requires a dedicated durable lifecycle regression test and should be reviewed together with A4/A5 call-site wiring.

---

## P0-05 — Legacy TaskBudget initialization can reset budget

The plan currently allows:

```text
existing task without budget row
→ ensure_budget(task_id, configured_policy)
→ create zeroed budget row
```

For a nonterminal Task that already executed work before R5, this creates:

```text
used_executions = 0
used_tool_calls = 0
used_inference_calls = 0
...
```

even though durable work already occurred.

That violates the frozen rule:

```text
retry/fork/resume/child/restart must never reset TaskBudget
```

### Required correction

For pre-R5 nonterminal tasks, choose one explicit migration rule:

1. reconstruct trustworthy counters from durable history; or
2. mark legacy budget state and fail closed for new growth until migrated; or
3. explicitly grandfather the task under a separately named legacy policy with documented limits.

Do not silently create a fresh zero-use budget.

---

## P0-06 — Active-slot accounting must be transactionally coupled to execution state

The plan uses wording such as:

```text
same transaction where practical
```

for WAITING/terminal release.

That is too weak.

These must be one atomic durable transition:

```text
RUNNING → WAITING
+
active_executions -= 1
```

and:

```text
WAITING → RUNNING
+
active_executions += 1
```

Otherwise duplicate handlers/crashes can create negative counters, double releases, or leaked active slots.

### Required correction

R5-C must require a shared UoW/transaction or an equivalent idempotent transition record. “Where practical” should be removed from the contract.

---

## P0-07 — Budget reservations lack durable idempotency identity

Revision CAS prevents two simultaneous stale writers, but it does not by itself prevent:

```text
reservation commits
→ caller loses acknowledgement / crashes
→ same logical operation is retried
→ reservation charged again
```

This applies to:

- new execution reservation;
- tool-call reservation;
- inference reservation;
- resume active-slot reacquisition.

### Required correction

Each reservation type needs a durable logical identity or a transactionally unique durable anchor:

```text
execution_id
tool_call_id / invocation_id
logical inference request_id or equivalent
execution revision/state transition for resume/release
```

A retry of the same logical admission must be idempotent.

---

# 3. P1 FINDINGS

## P1-01 — EventDispatcher ownership cannot remain optional for the final R5-A exit gate

`EventDispatcher.start()` creates dispatch worker tasks without retaining them.

If the R5-A exit condition remains:

```text
no orphan async task
no Task exception was never retrieved
```

worker tracking/draining is not merely cosmetic.

It may be implemented in A5 instead of A1→A3, but the plan should not call it optional when closing the integrated exit gate.

---

## P1-02 — Shutdown requires a quiesce phase, not only `supervisor.shutdown()`

Current proposed order can allow EventDispatcher/API paths to attempt new Agent work while Supervisor is already closing.

Recommended lifecycle:

```text
1. reject/quiesce new Agent starts
2. stop or quiesce Agent-producing ingress/dispatch workers
3. cancel + drain Supervisor live Agent graph
4. drain event workers
5. close runtimes/MCP/http/storage
```

A two-phase Supervisor (`quiesce` then `shutdown/drain`) may be cleaner when A5 is implemented.

---

## P1-03 — Cancellation cooperation is an unstated requirement

`cancel() + gather()` can wait forever if an internal coroutine catches `CancelledError` and never terminates.

R5 should freeze:

```text
all owned async Agent/provider/capability coroutines must be cancellation-cooperative
```

Blocking synchronous/native work must live behind a separately owned boundary with its own termination semantics.

Do not solve this by abandoning pending Tasks after an arbitrary timeout; that would reintroduce the orphan-task bug R5 exists to remove.

---

## P1-04 — `max_parallel_agents` semantics are invented by the plan

The plan defines:

```text
active_parallel_agents = delegated child Agents only
```

but the roadmap only establishes `max_parallel_agents`; it does not prove that root Agent executions are excluded.

Before schema freeze choose explicitly:

- all active Agent executions in the Task; or
- delegated child Agents only.

If child-only is intended, rename the counter/limit to make that contract obvious.

---

## P1-05 — Cost type is not frozen

The plan allows:

```text
decimal/float
```

for `max_total_cost_usd` / `used_cost_usd`.

Durable accounting must choose one representation before migration, for example:

```text
NUMERIC(p,s) + Decimal
```

with explicit rounding rules.

Do not mix binary float policy comparisons with SQL decimal accounting.

---

## P1-06 — Task revision CAS can be bypassed by existing unconditional updates

Adding:

```text
agent_tasks.revision
compare_and_set_task(...)
```

is not enough while existing status-changing paths continue to call unconditional `update_task()`.

Once cancellation/resolution correctness depends on Task revision, all conflicting Task state transitions must participate in the revision protocol or be proven non-conflicting.

---

## P1-07 — TaskBudget limits need immutable policy provenance

`ensure_budget(task_id, configured_policy)` must not allow server config changes after restart to silently replace Task-level limits.

Recommended durable fields:

```text
policy_version / policy_fingerprint
created limits
```

Once a TaskBudget exists, its limits should be immutable unless a separate explicit administrative mutation contract is introduced.

---

## P1-08 — DB invariants should backstop service validation

Service validation is necessary but not sufficient for durable counters.

Where portable across supported DBs, migration should add/check invariants such as:

```text
revision >= 0
all used/active counters >= 0
active_executions <= used_executions
CLOSED ↔ closed_at semantics
positive required limits
```

If SQLite compatibility prevents a particular check, document which invariant remains application-enforced.

---

# 4. A1→A3 PATCH REVIEW DECISION

The attached patch is intentionally limited to:

```text
R5-A1 regression tests
R5-A2 low-level owned-child cleanup
R5-A3 AgentExecutionSupervisor implementation
```

It does **not** modify:

```text
ApplicationContainer
main/bootstrap
WorkflowRuntime
AgentCapabilityDriver
events_router resume
MultiAgentCoordinator cancellation
EventDispatcher
TaskBudget
```

Therefore the correct post-patch status is:

```text
R5-A1 COMPLETE
R5-A2 COMPLETE
R5-A3 COMPLETE
R5-A4 PENDING
R5-A5 PENDING
R5-A6 PENDING
R5 overall NOT COMPLETE
```

---

# 5. Supervisor contract implemented by the patch

The A3 implementation freezes these process-local rules:

```text
one live/reserved owner per execution_id per process
explicit reservation release
reservation token is never durable
distinct cancellation_event per live/reserved execution
parent cancellation cascades downward
child cancellation never mutates parent event
Task cancellation fans out only to matching task_id scopes
caller cancellation cancels + drains supervised runtime
shutdown rejects new reservations and drains owned tasks
Supervisor never writes durable AgentExecution state
```

The Supervisor uses an `asyncio.Lock` for registry mutations only. Read snapshots are process-local/event-loop-local and are not a distributed synchronization mechanism.

---

# 6. Validation performed on the patch artifact

The generated patch was checked as follows:

```text
git apply --numstat              PASS
git apply --check               PASS on reconstructed exact target hunks
python -m py_compile            PASS for new Supervisor + both new test files
process-local Supervisor smoke  PASS
    child-only cancellation isolation
    parent cancellation
    task cancellation
    alias rejection
    stale reservation rejection
    shutdown/closed-state behavior
```

A full repository pytest run cannot be executed in this environment because the repository is not locally available and direct network clone is unavailable. The patch is therefore syntax/hunk/contract validated, but still requires the repository's normal focused and full pytest gates after application.

Recommended focused gate after apply:

```powershell
py -m pytest -q `
  se/tests/architecture/test_r5_a_async_ownership.py `
  se/tests/architecture/test_r5_a_agent_execution_supervisor.py `
  se/tests/architecture/test_phase5_adapters.py `
  se/tests/architecture/test_phase5_runtime.py `
  se/tests/architecture/test_phase5_tool_execution_coordinator.py `
  se/tests/architecture/test_r4_exit_gate.py
```

Then:

```powershell
py -m pytest -q se/tests tools cl/tests
```

---

# 7. Required next review before A4

Do not start A4 until these are frozen:

```text
A4-1 unique child cancellation-event construction in AgentCapabilityDriver
A4-2 AgentRuntime durable-begin cancellation semantics
A4-3 exact Supervisor injection ownership (single instance per app process)
A4-4 Workflow root and multi-agent executor migration
A4-5 resume claimed-but-not-started failure contract
A4-6 coordinator background runner cancel+drain semantics
A4-7 shutdown quiesce order
```

Only after those are settled should `AgentExecutionSupervisor` be wired into production call-sites.
