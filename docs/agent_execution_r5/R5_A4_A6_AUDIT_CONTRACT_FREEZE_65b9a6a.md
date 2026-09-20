# R5-A4→A6 AUDIT / CONTRACT FREEZE

**Repository:** `boxs-51/assistant`  
**Baseline:** `65b9a6afcf1ee9506ac4795e880aaaeae48f6fb6`  
**Commit:** `cap nhat R5 ,A1->A3`  
**Date:** 2026-09-20  
**Scope:** R5-A4 production wiring, R5-A5 shutdown/runner ownership, R5-A6 exit gate.  
**Out of scope:** TaskBudget, durable task CAS, R6 remote-result reconciliation, R7 distributed resume lease/checkpoint redesign.

## 1. Evidence from A1→A3

User-provided execution evidence after applying A1→A3:

- focused R5/R4/Phase5 gate: `62 passed in 9.70s`
- full `se/tests tools cl/tests`: `548 passed, 5 warnings in 61.99s`
- no pytest failures
- two post-suite Windows asyncio Proactor unraisable transport warnings remain

A1→A3 is therefore accepted as the implementation baseline for A4.

The Proactor warnings are not evidence that AgentExecutionSupervisor itself leaks a Task. They are resource/transport warnings emitted after the suite and must remain visible in A6 evidence until their allocation site is traced. R5-A6 must not claim they were fixed by unrelated Agent ownership changes.

---

# 2. P0 audit findings

## P0-A4-01 — nested Agent still aliases the parent cancellation event

Current `AgentCapabilityDriver.execute()` ends with:

```python
execution_context.cancellation_event = context.cancellation_event
result = await self._agent_runtime.execute(execution_context)
```

This contradicts the A3 supervisor invariant that each live AgentExecution owns a distinct process-local cancellation scope.

Frozen rule:

```text
E1 cancellation_event is never reused by E2.
E1 cancel => E2 via supervisor ancestry / caller cancellation propagation.
E2 cancel -X-> E1.
Sibling cancel -X-> sibling.
```

The A4 patch removes the alias and routes the child through the singleton supervisor.

---

## P0-A4-02 — production still bypasses AgentExecutionSupervisor

Current direct production calls exist in:

```text
WorkflowRuntime._execute_agent()
main.execute_registered_agent_task()
AgentCapabilityDriver.execute()
events_router resume background execution
```

A supervisor that exists but is bypassed does not establish ownership.

Frozen rule:

```text
Every production AgentRuntime.execute() must execute inside
AgentExecutionSupervisor ownership.
```

Exception:

```text
AgentRuntime.execute() itself remains directly unit-testable.
Compatibility-only test fakes may run without a container supervisor.
```

---

## P0-A4-03 — durable begin/claim cancellation window exists in two paths

Current `AgentRuntime.execute()` calls `_begin_durable_execution()` before its
`try/except asyncio.CancelledError`.

`claim_resume()` also directly awaits `_begin_durable_execution()`.

Therefore caller cancellation can arrive while:

```text
save CREATED
or
WAITING -> RUNNING CAS
```

is still in flight.

Frozen rule:

```text
_begin_durable_execution() is an owned child Task.
Outer cancellation does not abandon it.
The owner shields/drains the begin Task.
If begin completes with a RUNNING revision after caller cancellation,
AgentRuntime CASes that revision to CANCELLED before propagating cancellation.
```

This applies to both:

```text
new/root execution start
resume claim
```

Supervisor never performs this durable CAS; AgentRuntime remains durable state authority.

---

## P0-A4-04 — resume has an in-process claimed-but-not-started race

Current order:

```text
rehydrate
reconnect
claim WAITING -> RUNNING
confirm merge
ACK
raw create_task(AgentRuntime.execute)
```

If merge, ACK, task creation, request cancellation, or shutdown fails after the
durable claim, the durable row can remain RUNNING with no process owner.

Frozen R5-A rule:

```text
1. reserve execution_id in process-local supervisor
2. durable claim
3. confirm merge
4. send ACK
5. start_reserved AgentRuntime task
```

For **any process-local failure after durable claim and before successful
`start_reserved`**, R5-A uses fail-closed compensation:

```text
RUNNING -> CANCELLED
error = RESUME_ACTIVATION_FAILED
```

and releases the process-local reservation.

This intentionally does not promise distributed crash recovery. If the entire
process dies after the durable claim before compensation can run, R7 lease /
stale-RUNNING recovery remains the authority.

R7 may later replace fail-closed cancellation with a resumable claim lease.

---

## P0-A5-01 — MultiAgentCoordinator background runner is cancelled but not drained

Current `cancel_task()`:

```python
running.cancel()
return task
```

Current `start_task()` done callback removes the Task reference without
retrieving a possible late exception.

Frozen rule:

```text
production HTTP cancellation uses async cancel_task_and_wait()
runner.cancel()
supervisor.cancel_task(task_id)
await/gather runner
persist terminal task view
return only after process-local ownership is drained
```

The old synchronous `cancel_task()` remains only as a compatibility facade for
existing direct callers/tests until R5-E durable Task cancellation replaces it.

Coordinator also gains `shutdown()` to cancel/drain every control-plane runner.

---

## P0-A5-02 — EventDispatcher creates unowned workers

Current:

```python
asyncio.create_task(self._dispatch_event_task(...))
```

with no retained task registry.

This is directly incompatible with the R5-A exit condition:

```text
no unowned event worker capable of running Agent code after shutdown
```

Frozen rule:

```text
EventDispatcher retains every worker task.
Done callbacks remove and observe task exceptions.
quiesce stops queue intake first.
dispatcher.shutdown cancels/drains tracked workers.
```

---

# 3. P1 findings

## P1-A4-01 — Supervisor must be one process singleton

Do not construct one supervisor per Agent driver.

Frozen DI:

```text
ApplicationContainer.agent_execution_supervisor
    = exactly one AgentExecutionSupervisor per application process
```

It is created during bootstrap before production Agent drivers are materialized
and injected into:

```text
Workflow root Agent path
MultiAgent executor
dynamic AgentCapabilityDriver
lazy AgentCapabilityDriver
resume route
MultiAgentCoordinator cancellation
```

Test-only fallback containers may omit it for backward-compatible unit tests.

---

## P1-A5-01 — shutdown requires quiesce before drain

Frozen order:

```text
1. supervisor.quiesce()      # reject new Agent ownership
2. eventing_manager.quiesce()# stop event ingress + drain event workers
3. multi_agent_coordinator.shutdown()
4. supervisor.shutdown()     # cancel/drain remaining Agent graph
5. runtime_kernel.shutdown()
6. MCP / HTTP / storage shutdown
```

Capability/provider runtimes are intentionally kept alive while live Agent
tasks are being drained.

---

## P1-A4-02 — continuation can remain RUNNING after fail-closed activation failure

If `confirm_merge()` succeeded and the later ACK/start step fails, R5-A marks
the durable AgentExecution CANCELLED but does not redesign continuation
checkpoint state.

This is an explicitly accepted temporary asymmetry:

```text
durable AgentExecution terminal state is authoritative
stale continuation cleanup / claim lease belongs to R7
```

R5-A must not add an ad-hoc rollback checkpoint protocol that conflicts with
the already planned R7 redesign.

---

## P1-A6-01 — post-suite Proactor warnings are not an R5-A completion blocker by themselves

The observed warnings are:

```text
BaseSubprocessTransport.__del__
_ProactorBasePipeTransport.__del__
ValueError: I/O operation on closed pipe
```

They do not name AgentExecutionSupervisor or one of the newly owned Agent
tasks.

A6 therefore treats them as a separately tracked Windows subprocess/transport
resource issue unless tracemalloc demonstrates that an Agent-owned operation
created the leaked transport.

A6 does still require:

```text
no "Task exception was never retrieved"
no pending AgentExecutionSupervisor task after shutdown
no pending MultiAgentCoordinator runner after cancellation/shutdown
no pending EventDispatcher worker after quiesce/shutdown
```

---

# 4. A4 implementation contract

## A4.1 AgentCapabilityDriver

Required:

```text
child gets new AgentExecutionContext
child retains new default asyncio.Event
child never aliases CapabilityExecutionContext.cancellation_event
production child runs through singleton supervisor
```

## A4.2 root workflow and multi-agent call sites

Required pattern:

```python
await supervisor.run(
    context,
    lambda: agent_runtime.execute(context),
)
```

The supervisor owns process-local Task lifetime only.

## A4.3 durable begin

Required helper semantics:

```text
create begin Task
await shield(begin Task)
on outer CancelledError:
    gather begin Task to completion
    if it returned a durable RUNNING revision:
        CAS revision -> CANCELLED
    re-raise CancelledError
```

## A4.4 resume

Required:

```text
reserve local ownership before durable claim
claim before merge
merge before ACK
ACK before start_reserved (preserves R4 observable order)
start_reserved consumes reservation
```

Failure before `start_reserved` succeeds:

```text
release reservation
if claim already succeeded:
    AgentRuntime.cancel_claimed_execution(...)
re-raise original failure
```

Whole-process crash remains R7.

---

# 5. A5 implementation contract

## A5.1 coordinator

New APIs:

```python
async def cancel_task_and_wait(...)
async def shutdown(...)
```

Existing `cancel_task()` remains compatibility-only.

## A5.2 EventDispatcher

New ownership state:

```text
_active_tasks: set[asyncio.Task]
_closing: bool
```

New API:

```python
async def shutdown()
```

Every worker has a done observer.

## A5.3 EventingManager

New API:

```python
async def quiesce()
```

`shutdown()` becomes:

```text
quiesce
ws_manager.shutdown
```

## A5.4 Supervisor

New API:

```python
async def quiesce()
```

Quiesce rejects new reservations/admissions. It does not cancel live work, and an already-admitted reservation may still be consumed by start_reserved().
`shutdown()` remains the cancelling/draining terminal operation.

---

# 6. A6 regression / exit-gate contract

A6 focused tests must prove:

```text
1. AgentCapabilityDriver child event is distinct from parent capability event.
2. Supervisor is used for nested Agent production-style execution.
3. cancellation during durable begin does not strand RUNNING.
4. cancellation during claim_resume does not strand RUNNING.
5. resume race loser still never merges/ACKs.
6. successful resume preserves R4 claim -> merge -> ACK -> execute order.
7. post-claim process-local activation failure ends durable execution fail-closed.
8. coordinator cancel_task_and_wait drains background runner.
9. EventDispatcher shutdown drains worker tasks.
10. supervisor quiesce rejects new starts and shutdown empties registry.
11. prior R3/R4/Phase5 tests remain green.
```

Repository-wide gate:

```powershell
py -m pytest -q se/tests tools cl/tests
```

Expected completion status after patch + green gates:

```text
R5-A1 COMPLETE
R5-A2 COMPLETE
R5-A3 COMPLETE
R5-A4 COMPLETE
R5-A5 COMPLETE
R5-A6 COMPLETE

R5-A Async Ownership: COMPLETE
R5-B TaskBudget: NOT STARTED
R5 overall: NOT COMPLETE
```

---

# 7. Explicit non-goals preserved

This patch must not implement:

```text
TaskBudget schema/counters/CAS
durable Task cancellation tree
TaskBranch/FORK
retry scheduler
remote invocation reconciliation
ResumeClaim lease / stale RUNNING recovery
checkpoint rollback redesign
provider retry accounting
distributed Agent execution lease
```

Those remain in their roadmap phases.


---

# 8. Patch artifact validation

The A4→A6 patch artifact was validated before handoff against the repository's
actual custom patch format (`tools/patch_applier.py` at baseline `65b9a6a`).

Validated properties:

```text
patch parser model: compatible with repository patch_applier.py
actions: 16
duplicate action paths: 0
UPDATE actions: 14
ADD actions: 2
new Python test AST parse: PASS
new Python test py_compile: PASS
AgentCapabilityDriver positional execution_id_factory ABI: PRESERVED
MultiAgentCoordinator positional execution_id_factory ABI: PRESERVED
```

The repository patcher intentionally treats bare `@@` as context-hunk
separators, so standard unified-diff line coordinates are not required by this
repository workflow.

Because the GitHub connector exposes repository text but does not materialize
the whole repository into the execution container, the final authoritative
context-match gate remains:

```powershell
py tools/patch_applier.py --check docs/R5_A4_A6_ASYNC_OWNERSHIP_WIRING_v1.patch
```

A6 remains `PENDING TEST EVIDENCE` until that check and the focused/full pytest
gates run on the user's actual Windows working tree.
