# R8-F — EXECUTION ACTIVATION + CONTROL-PLANE BOUNDARY FREEZE

**Repository:** `boxs-51/assistant`  
**Canonical branch:** `r8-taskbranch-fork`  
**Audited HEAD:** `79a1cb3c8d91b4a29bbfbf5b1658048de061694a`  
**Parent completion:** `docs/agent_execution_r8/R8_E_COMPLETION.md`  
**Parent contract:** `docs/agent_execution_r8/R8_E_BRANCH_CONTEXT_RUNTIME_HANDOFF_CONTRACT_FREEZE_1FA71F10.md`  
**Roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Scope:** R8-F execution-scoped runner ownership, durable fork activation, public FORK/branch reads, minimal branch-aware Task activity, Task cancellation fan-out  
**Status:** **CONTRACT FROZEN / NOT IMPLEMENTED**

---

# 1. Phase boundary

R8-E is CLOSED/GREEN and produces a read-only:

```text
ForkExecutionBootstrap
    execution_id = E2
    expected_execution_revision = 1
    task_id
    branch_id
    fork_request_id
    plan_fingerprint
    runtime_seed_fingerprint
    branch_context_revision
    reconstructed AgentExecutionContext
```

E2 already exists durably as:

```text
state = RUNNING
revision = 1
current_checkpoint_id = NULL

TaskBudget:
    active_branches already charged
    used_executions already charged
    active_executions already charged

runner = NOT STARTED
active deadline = FROZEN
```

R8-F owns the boundary from this durable pre-activation authority to one
process-local runner.

R8-F does **not** create another AgentExecution.

R8-F does **not** reuse R7 ResumeClaim.

R8-F does **not** resolve branch results.

---

# 2. Canonical R8-F activation model

The one-time distributed activation fence is:

```text
E2 RUNNING@1
    -- durable activation winner -->
E2 RUNNING@2
```

Meaning:

```text
RUNNING@1
    = durable R8-D fork admission exists
    = execution capacity is charged
    = no process may perform inference/tools yet

RUNNING@2
    = one distributed activation CAS won
    = exactly one process may start E2
```

No other transition is execution authority.

A process-local supervisor reservation is necessary but is not durable authority.


After `AgentExecutionSupervisor.reserve(E2)` succeeds, every exit before a
durable activation WIN must execute idempotent `release_reserved(token)` in a
guaranteed error/finally path. This includes Task/Budget/Branch/seed rejection,
deadlock/retry exhaustion, and activation CAS loss.

After activation wins, durable cleanup responsibility transfers to the winner
path. `start_reserved()` consumes the token on success.

---

# 3. Canonical ordering

Fresh FORK execution startup MUST be ordered:

```text
1. durable replay-first check
2. if no replay:
       build read-only ForkPlan
3. consume ForkPlan atomically
4. load exact Agent definition
5. prepare read-only ForkExecutionBootstrap
6. AgentExecutionSupervisor.reserve(E2)
7. same-UoW durable activation revalidation
8. CAS E2 RUNNING@1 -> RUNNING@2
9. restore active execution budget
10. AgentExecutionSupervisor.start_reserved(...)
11. AgentRuntime.execute(
        context,
        durable_revision=2,
    )
```

Forbidden orderings:

```text
activate before local reserve
start runner before durable CAS
call AgentRuntime.execute(E2) with no durable_revision
call _begin_durable_execution(E2)
route E2 through coordinator.execute_task()
```

---

# 4. P0-R8F-1 — activation must fence more than execution revision

A generic:

```text
compare_and_set_execution(E2, 1, ...)
```

is insufficient.

The read-only R8-E bootstrap can race:

```text
Task cancellation
Branch resolution/cancellation
BranchContext mutation
ForkAdmission corruption/tamper
TaskBudget closure
```

The activation transaction MUST revalidate current durable authority in the
same UoW before the execution CAS.

Minimum activation fence:

## AgentTask

```text
task exists
task.id == bootstrap.task_id
task.session_id == context.session_id
task.created_by == identity.user_id
task.status is nonterminal
```

For the initial R8-F implementation the expected normal state is:

```text
RUNNING
```

A terminal Task rejects activation.

## TaskBudget

```text
budget exists
budget.state == OPEN
```

R8-F activation does not charge budget again.

## ForkAdmission

```text
receipt exists by execution_id
receipt.task_id == bootstrap.task_id
receipt.branch_id == bootstrap.branch_id
receipt.execution_id == bootstrap.execution_id
receipt.fork_request_id == bootstrap.fork_request_id
receipt.plan_fingerprint == bootstrap.plan_fingerprint
receipt.runtime_seed_fingerprint == bootstrap.runtime_seed_fingerprint
receipt.created_by == identity.user_id
```

## TaskBranch

```text
branch exists
branch.task_id == task_id
branch.resolution_state == OPEN
branch.current_execution_id == E2
branch.parent_branch_id == receipt.source_branch_id
branch.base_execution_id == receipt.source_execution_id
branch.base_checkpoint_id == receipt.source_checkpoint_id
```

## TaskBranchContext

```text
context row exists
revision == bootstrap.branch_context_revision
overlay fingerprint == immutable runtime-seed overlay fingerprint
```

## AgentExecution E2

```text
id == bootstrap.execution_id
state == RUNNING
revision == 1
current_checkpoint_id == NULL

task_id == receipt.task_id
branch_id == receipt.branch_id
base_execution_id == receipt.source_execution_id
base_checkpoint_id == receipt.source_checkpoint_id
retry_of_execution_id == NULL

bound_client_id == NULL
bound_connection_id == NULL

request fingerprint == immutable runtime-seed request fingerprint
remaining_active_budget_seconds == immutable runtime-seed remaining budget
```

Only after all fences pass may the transaction CAS:

```text
RUNNING@1 -> RUNNING@2
```

Recommended activation mutation:

```text
revision = 2
state = RUNNING
started_at = activation time
```

No Branch or TaskBudget counter changes occur in activation.

---

# 5. P0-R8F-2 — real SQL serialization / TOCTOU rule

The activation check cannot be:

```text
read Task
read Budget
read Branch
read BranchContext
later CAS only E2
```

even when those reads and the E2 CAS occur inside one transaction.

A concurrent Task cancellation mutates Task + TaskBudget, not E2. Without a
cross-row serialization fence, activation could validate OPEN authority and
then still commit E2@2 after cancellation commits.

R8-F therefore requires a real SQL concurrency fence.

Preferred lock order for activation:

```text
1. AgentTask FOR UPDATE
2. TaskBudget FOR UPDATE
3. TaskBranch FOR UPDATE
4. TaskBranchContext FOR UPDATE
5. ForkAdmission read
6. AgentExecution specialized activation CAS
```

Repository support should add:

```text
get_task_budget_for_update()
get_task_branch_for_update()
get_task_branch_context_for_update()
```

`get_task_for_update()` already exists.

The specialized activation CAS predicate must additionally require at minimum:

```text
execution_id == E2
revision == 1
state == RUNNING
current_checkpoint_id IS NULL
task_id == expected task
branch_id == expected branch
base_execution_id == expected source execution
base_checkpoint_id == expected source checkpoint
retry_of_execution_id IS NULL
```

Activation remains TaskBudget-accounting neutral. Do not bump TaskBudget
revision/counters merely to manufacture a fence.

Task cancellation of an unactivated fork must use the same Task/Budget
serialization order and race against activation on E2 revision 1.

PostgreSQL/MySQL rely on row locking plus the specialized CAS. SQLite does not
provide equivalent row-level FOR UPDATE semantics, so SQLite regression tests
must prove the equivalent one-winner behavior through transaction/CAS conflict
handling and must never be treated as proof that unlocked cross-row reads are
safe on other databases.

---

# 6. P0-R8F-2 — CAS loser owns no durable cleanup

Two workers can both:

```text
reconstruct E2@1
reserve E2 process-locally in different processes
```

Only one may win:

```text
E2@1 -> E2@2
```

CAS loser behavior is exactly:

```text
release its local supervisor reservation
dispatch zero inference
dispatch zero tools
perform zero execution cleanup mutation
perform zero TaskBudget decrement
return/observe the already-created fork identity
```

The loser MUST NOT cancel or fail E2.

E2@2 belongs to the activation winner.

---

# 7. P0-R8F-3 — activation winner + start failure cleanup

This is distinct from CAS loss.

If a process wins E2@2 but then cannot consume its reserved supervisor token,
for example:

```text
Task cancellation invalidated reservation
supervisor shutdown
reservation/token mismatch
unexpected local start failure
```

the winner owns a durable RUNNING execution with no runner.

It must fail closed exactly once.

Required cleanup:

```text
E2 RUNNING@2
    -> CANCELLED/FAILED @3

TaskBudget.active_executions -1
TaskBudget.active_parallel_agents -1 only if E2 preserves delegation ancestry
```

Do NOT:

```text
decrement active_branches
release the BRANCH reservation
delete ForkAdmission
delete BranchContext
resolve/discard/cancel B2 merely because local activation failed
terminalize Task
```

Branch result authority remains R9.

The existing task-scoped execution release transaction may be reused if it
preserves this exact accounting.

---

# 8. Crash window vs live caller cancellation

The unavoidable R8-F process-crash window is:

```text
durable E2@2 commit
PROCESS CRASH
before process-local runner start
```

R8-F MUST fail closed on restart:

```text
E2 revision > 1
=> never auto-start as a fresh fork
```

Do not invent a second activation. Stale RUNNING lease/recovery belongs R12.

However ordinary coroutine/request/shutdown cancellation while the process is
still alive is **not** this R12 crash boundary.

Durable activation must therefore be awaited through explicit owned-task
semantics:

```text
create activation child task
await asyncio.shield(child)

if outer caller is cancelled:
    gather child to a known outcome

    if THIS child won E2@1 -> E2@2:
        fail-close E2@2 through normal task-scoped execution release
        release any still-held local reservation
        re-raise CancelledError

    else:
        release only local reservation
        re-raise CancelledError
```

The caller cancellation must never be converted into a normal identity replay
response merely because a reload observes E2@2.

This separates:

```text
hard process death after commit -> R12 recovery boundary
live caller cancellation        -> R8-F must deterministically resolve ownership
```

---

# 9. P0-R8F-4 — active budget must be restored exactly once

R8-E bootstrap deliberately creates:

```text
context.deadline = None
context.remaining_active_budget_seconds = durable value
activate_budget = False
```

This means the active execution budget is frozen.

Because R8-F will call:

```text
AgentRuntime.execute(
    context,
    durable_revision=2,
)
```

`_begin_durable_execution()` is bypassed.

Therefore only the durable activation winner may call:

```text
context.restore_active_budget(
    context.remaining_active_budget_seconds
)
```

after activation authority is committed and before inference/tool work.

Invariant:

```text
CAS loser:
    no restore

activation winner:
    exactly one restore before runtime work
```

---

# 10. P0-R8F-5 — fork runners are execution-scoped

Current coordinator root/UI compatibility ownership:

```text
_running_tasks[task_id]
```

cannot be the fork exclusivity fence.

Two valid branches intentionally share one Task.

Canonical fork ownership:

```text
durable:
    execution_id + execution revision

process-local:
    AgentExecutionSupervisor keyed by execution_id
```

`MultiAgentCoordinator.start_task()` may remain a legacy/root UI wrapper.

Fork E2 MUST NOT be rejected because another execution of the same Task is
already running.

Task cancellation must still cancel every process-local execution whose
`task_id` matches.

---

# 11. P0-R8F-6 — fork E2 never passes through execute_task()

`MultiAgentCoordinator.execute_task()` currently maps one execution directly
to Task terminal state.

That is invalid once the Task has multiple branches.

Fork E2 must use a separate execution-scoped path:

```text
existing E2
-> activation
-> AgentRuntime.execute(E2, durable_revision=2)
-> settle E2 own WAITING/terminal state
-> minimal Task activity reconciliation
```

Forbidden:

```text
successful B2 -> Task COMPLETED
failed B2     -> Task FAILED
cancelled B2  -> Task CANCELLED
```

unless the Task itself was explicitly cancelled.

R9 owns final result authority.

---

# 12. Minimal multi-branch Task activity

R8-F adds only derived activity.

It does not choose a winning result.

For a nonterminal Task:

```text
if normalized branch count <= 1:
    preserve existing legacy/single-branch Task behavior

else if any OPEN branch current execution is CREATED or RUNNING:
    Task.status = RUNNING
    Task.wait_reasons = []

else if no OPEN branch current execution is RUNNING
     and at least one OPEN branch current execution is WAITING:
    Task.status = WAITING
    Task.wait_reasons = deterministic union of current WAITING reasons

else:
    leave Task nonterminal unchanged
    # R9 owns branch result resolution
```

Terminal Task:

```text
no activity reconciliation may resurrect it
```

A single branch COMPLETED/FAILED/CANCELLED never creates Task terminal result
authority.

Legacy/root wrappers are not exempt from this rule.

A source B1 may have entered `MultiAgentCoordinator.execute_task()` before
the Task forked. After AgentRuntime settles B1, the legacy wrapper still
attempts Task-level writes.

Therefore TaskBudget authority MUST fence those stale writes durably:

```text
legacy transition_task(... -> WAITING)
    lock Task -> Budget
    if normalized branch count > 1:
        rederive aggregate branch activity
        do not blindly force WAITING

legacy terminalize_task(... -> terminal)
    lock Task -> Budget
    if normalized branch count > 1:
        rederive aggregate branch activity
        keep TaskBudget OPEN
        do not terminalize Task
```

This check must occur inside the same durable transaction. A coordinator-only
branch-count check is forbidden because FORK could commit between the check and
Task mutation.

R8-D FORK consume and these legacy-write fences both lock AgentTask first, so
FORK-vs-terminalization has one serialized outcome:

```text
terminalization wins first:
    Task terminal / Budget CLOSED
    later FORK revalidation fails
    no ForkAdmission commits

FORK wins first:
    multiple branches exist
    later legacy terminal/write rederives aggregate nonterminal activity
```

Forbidden:

```text
ForkAdmission committed
AND Task terminalized by one branch's legacy wrapper
```

Explicit Task cancellation remains the dedicated `cancel_task()` authority.

---

# 13. Activity reconciliation integration

The activity rule should be a reusable durable service, not a router-only
heuristic.

It must be usable after:

```text
task-scoped execution RUNNING -> WAITING
task-scoped execution RUNNING -> terminal
R7 same-execution resume activation
fork execution lifecycle changes
```

This avoids a fork-specific UI patch that leaves the source R7 branch stale.

Reconciliation may use a separate retrying transaction because it is
derived Task activity, not execution authority, but any transaction that may
write aggregate Task activity MUST serialize on the AgentTask row first.

Canonical lock order:

```text
AgentTask FOR UPDATE
-> TaskBudget / execution authority work when applicable
-> read current OPEN branch heads
-> derive aggregate activity
-> Task CAS if a write is needed
```

This is required because R8-D FORK consume can add a new RUNNING branch while
Task is already RUNNING without bumping Task revision. A stale activity
snapshot must therefore conflict on the Task row, not rely only on revision
CAS.

R7 resume must preserve the same order:

```text
Task FOR UPDATE
-> prepare_resume_capacity_in_uow()
-> WAITING execution -> RUNNING CAS
-> aggregate activity rederive under the already-held Task lock
```

Do not acquire Task FOR UPDATE only after TaskBudget/execution writes; that
would invert the R8 activation/cancellation order and create a deadlock risk.

Required race outcomes:

```text
FORK vs standalone reconcile:
    reconcile first -> Task WAITING revision advances; stale ForkPlan cannot commit
    FORK first      -> reconcile sees new RUNNING branch; final Task RUNNING

R7 resume vs standalone reconcile:
    regardless of ordering, a resumed current branch RUNNING cannot coexist
    with final Task WAITING
```

It must always re-read current branch/execution state while holding this Task
serialization authority before writing.

---

# 14. P0-R8F-7 — public FORK must be durable replay-first

Same `fork_request_id` retry can arrive after:

```text
source execution advanced
source checkpoint is no longer current
source Task status changed RUNNING/WAITING
```

Therefore the HTTP/control-plane path MUST NOT begin by rebuilding a new
ForkPlan unconditionally.

Canonical request flow:

```text
existing = load ForkAdmission(task_id, fork_request_id)

if existing:
    verify replay semantics from immutable durable evidence

    if exact E2 is RUNNING@1 preactivation:
        reuse SAME admission / SAME E2
        -> prepare ForkExecutionBootstrap(E2)
        -> reserve
        -> activation CAS
        -> restore budget
        -> start runtime

    else:
        return same branch/execution identity + current status
        do not bootstrap/start again

else:
    build_fork_plan()
    consume_fork_plan()
    continue to bootstrap/activation
```

A race where another worker commits admission after the first replay read is
handled by R8-D consume idempotency.

This replay state machine is the supported recovery for crash-after-consume
but before activation. It never creates a second Branch or Execution.

---

# 15. Replay semantic verification

Replay-first must verify caller-supplied fork semantics without requiring the
source to remain forkable.

Initial R8-F replay identity:

```text
task_id
fork_request_id
source_branch_id
source_execution_id
source_checkpoint_id
target user
overlay messages
```

Use immutable evidence:

```text
ForkAdmission source IDs / created_by
ForkAdmission runtime_seed_fingerprint
runtime_seed.overlay_fingerprint
persisted BranchContext overlay
```

If same `fork_request_id` is reused with different semantics:

```text
FORK_REQUEST_SEMANTIC_CONFLICT
```

Replay lifecycle classification is fail-closed.

Valid shapes are exactly:

```text
PREACTIVATION
    RUNNING@1
    -> continue bootstrap/activation of SAME E2

IDENTITY_REPLAY
    RUNNING@2+
    WAITING@3+
    COMPLETED@2+
    FAILED@2+
    CANCELLED@2+
    TIMEOUT@2+
    -> return same durable identity/status only
```

The terminal `@2` case includes Task cancellation winning against dormant
preactivation E2.

Unexpected shapes such as:

```text
CREATED@1
WAITING@1
RUNNING@0
negative/invalid revision
unknown lifecycle state
```

must fail closed as `FORK_ADMISSION_CORRUPT` (or an equivalent frozen
activation conflict), not surface a successful replay.

---

# 16. Initial HTTP request contract

Add:

```text
AgentTaskForkRequest
    fork_request_id
    source_branch_id
    source_execution_id
    source_checkpoint_id
    overlay_messages
```

Initial R8-F should **not** add `reason` as a semantic request field because
the current R8-D/E immutable plan fingerprint does not bind a caller-provided
reason.

A future reason field requires a contract change that binds/persists it.

Overlay policy remains R8-E:

```text
initial fork overlay role == user only
```

---

# 17. HTTP surfaces

Add:

```text
POST /v1/multi-agent/tasks/{task_id}/fork
GET  /v1/multi-agent/tasks/{task_id}/branches
GET  /v1/multi-agent/branches/{branch_id}
```

HTTP is only a facade.

It does not own:

```text
fork plan semantics
fork consume transaction
activation authority
runner authority
branch result resolution
```

---

# 18. Durable authorization / restart rule

R8-F branch and fork surfaces must not require coordinator in-memory maps to
prove ownership.

Current:

```text
MultiAgentCoordinator._tasks
MultiAgentCoordinator._sessions
```

are process-local and empty after restart.

For R8-F durable surfaces:

```text
load AgentTask from durable store
verify task.created_by == identity.user_id
load Branch / ForkAdmission from durable store
verify branch.task_id / receipt.task_id
```

This makes branch discovery and fork replay restart-safe.

---

# 19. Branch read contract

Read-only TaskBranch output includes durable branch authority:

```text
branch_id
task_id
parent_branch_id
base_execution_id
base_checkpoint_id
current_execution_id
resolution_state
revision
created_by
reason
created_at
updated_at
```

No runtime state is persisted redundantly on TaskBranch.

If a view exposes current execution state, it must derive it from
`current_execution_id`.

---

# 20. P0-R8F-8 — Task cancellation ordering + dormant RUNNING@1 cleanup

R8-F keeps the safer existing R5 durable-first cancellation fence, but R8 adds
a special durable case: a fork may already have consumed execution capacity as
E2 RUNNING@1 while no local supervisor handle exists yet.

Canonical durable cancellation transaction:

```text
1. lock AgentTask
2. lock TaskBudget
3. Task -> CANCELLED
4. TaskBudget -> CLOSED
5. find exact preactivation fork executions for this Task:
       backed by ForkAdmission
       state == RUNNING
       revision == 1
       current_checkpoint_id IS NULL
       exact fork lineage
6. for each exact preactivation fork:
       CAS RUNNING@1 -> CANCELLED@2
       decrement active_executions exactly once
       decrement active_parallel_agents exactly once when delegated
7. COMMIT
```

Activation and cancellation therefore race on the same E2 revision:

```text
activation:   RUNNING@1 -> RUNNING@2
cancellation: RUNNING@1 -> CANCELLED@2
```

Exactly one wins.

After the durable cancellation transaction:

```text
8. cancel/gather root compatibility runner if any
9. AgentExecutionSupervisor.cancel_task(task_id)
10. drain all local execution tasks
```

Do not blindly cancel arbitrary RUNNING@1 executions. The discriminator is an
immutable ForkAdmission plus the exact R8 preactivation shape.

Do not release active_branches here. Branch resolution/accounting remains R9.

Why durable-first:

```text
Task terminal + budget CLOSED
=> fresh fork activation fails
=> R7 resume fails
=> new task-scoped work fails
```

If activation won before cancellation, E2 is RUNNING@2 and is no longer part
of dormant preactivation cleanup. Local supervisor cancellation applies on the
owning process; remote stale RUNNING ownership remains the R12 boundary.

Do not reverse this into local-cancel-first.

---

# 21. Cross-worker cancellation boundary

R8-F guarantees:

```text
durable Task cancellation blocks new work
all process-local branch executions are cancelled/drained
```

It does not invent an immediate cross-process cancellation channel.

A runner already executing on another server instance may observe cancellation
through subsequent durable TaskBudget/execution operations.

Distributed stale RUNNING lease/recovery belongs R12.

Do not add owner-instance leases in R8-F.

---

# 22. Branch resolution boundary

R8-F does not implement:

```text
ADOPT
SUPERSEDE
DISCARD
AGGREGATE
final Task result winner
retry-as-new-execution
```

Activation failure does not implicitly resolve B2.

Branch current execution remains E2.

R9 owns result resolution.

---

# 23. Existing contracts that must remain unchanged

## R7 RESUME

```text
same execution_id
same branch_id
ResumeClaim authority
```

R8-F must not route RESUME through FORK activation.

## R8 FORK

```text
new execution_id
new branch_id
ForkAdmission authority
```

R8-F must not mint ResumeClaim.

## Delegation

`parent_execution_id` remains only delegation lineage.

Fork E2 preserves source delegation ancestry and uses:

```text
base_execution_id
base_checkpoint_id
```

for fork origin.

---

# 24. Exact implementation blast radius

Expected R8-F production changes:

```text
UPDATE
se/src/domain/schemas/multi_agent.py

UPDATE
se/src/application/container.py

UPDATE
se/src/infrastructure/storage/repositories/agent.py

UPDATE
se/src/runtimes/agent/contracts/fork.py

UPDATE
se/src/runtimes/agent/persistence.py

UPDATE
se/src/runtimes/agent/task_budget.py

UPDATE
se/src/runtimes/agent/runtime.py

UPDATE
se/src/runtimes/agent/coordinator.py

UPDATE
se/src/main.py

UPDATE
se/src/transport/gateway/api/v1/multi_agent_router.py
```

Possible test-only additions under:

```text
se/tests/architecture/
se/tests/integration/
se/tests/e2e/
```

R8-F should not require changes to:

```text
cl/
se/src/transport/gateway/api/v1/events_router.py
se/src/runtimes/capability/drivers/agent_driver.py
provider runtime
R9 resolution models
```

If implementation requires those, stop and re-audit the boundary.

Exception:

A tiny R7 resume call-site hook is allowed only if needed to invoke the generic
multi-branch Task activity reconciler; it must not alter R7 resume authority.

---

# 25. R8-F implementation plan

## R8-F0 — contracts / DTOs

Add:

```text
ForkActivationResult
or equivalent immutable activation receipt

AgentTaskForkRequest
fork response/read view contract
```

Freeze error codes and replay semantics.

No runtime wiring yet.

---

## R8-F1 — replay-first durable read path

Add durable helper for:

```text
lookup ForkAdmission(task_id, fork_request_id)
verify owner/source/overlay semantics
load current branch/execution
return same fork identity
```

Tests:

```text
retry after source moves
retry after E2 activates
retry after E2 terminal
same request + different overlay rejected
same request + different source rejected
foreign principal rejected
```

---

## R8-F2 — atomic activation authority

Add same-UoW activation primitive.

Tests:

```text
fresh E2@1 -> E2@2
Task cancelled before activation rejects
TaskBudget closed rejects
Branch not OPEN rejects
Branch current execution mismatch rejects
BranchContext revision/overlay mismatch rejects
runtime seed mismatch rejects
two workers -> one activation winner
CAS loser performs zero cleanup mutation
```

---

## R8-F3 — supervisor handoff + budget restore

Wire:

```text
prepare bootstrap
reserve supervisor
activate durable
restore active budget
start_reserved
AgentRuntime.execute(... durable_revision=2)
```

Tests:

```text
inference cannot start before activation
active budget is running when inference begins
local reserve conflict starts no second runner
activation validation failure releases local reservation
activation loser releases local reservation and starts no runner
outer cancellation around activation commit deterministically resolves activation ownership
invalid replay lifecycle shape fails closed
start_reserved failure cancels/fails E2 and releases execution capacity once
branch capacity remains charged after activation failure
```

---

## R8-F4 — execution-scoped coordinator path

Add fork execution control-plane path that bypasses:

```text
_running_tasks[task_id] exclusivity
execute_task() Task terminalization
```

Use supervisor execution_id ownership.

Ensure detached owned tasks have explicit observation/drain ownership so task
exceptions are never abandoned.

Tests:

```text
B1 + B2 run concurrently under same Task
two fork branch executions can coexist when budget permits
Task-level root wrapper does not block B2
```

---

## R8-F5 — multi-branch Task activity

Add durable reconciler.

Tests:

```text
B1 RUNNING + B2 COMPLETED -> Task RUNNING
B1 WAITING + B2 COMPLETED -> Task WAITING
B1 WAITING + B2 FAILED -> Task WAITING
legacy source/root WAITING write cannot override a RUNNING sibling
legacy source/root terminal write cannot terminalize a forked Task
FORK consume vs legacy terminalize cannot commit fork + terminal Task
all current branch executions terminal -> Task stays nonterminal
Task CANCELLED is never resurrected
single-branch legacy completion behavior unchanged
```

Also prove R7 same-branch resume remains compatible.

---

## R8-F6 — HTTP + branch reads

Add public control-plane routes.

Tests:

```text
owner can fork
foreign user cannot fork/read branch
branch list deterministic
branch get durable/restart-safe
same fork request HTTP retry returns same branch/execution
HTTP retry after source progress does not rebuild stale plan
```

---

## R8-F7 — cancellation / last-mile regressions

Tests:

```text
Task cancel closes durable budget before local drain
Task cancel settles dormant exact fork E2 RUNNING@1 and releases active execution capacity
Task cancel blocks preactivation E2
Task cancel cancels two local branch runners
cancel vs activation RUNNING@1 race has exactly one durable winner
no branch runner survives local supervisor drain
R7 resume source branch still works
full targeted R8 A-F regressions
full CI
```

Then write R8-F completion.

---

# 26. Required R8-F invariants

```text
R8F-I01
RUNNING@1 is durable preactivation only.

R8F-I02
Only RUNNING@1 -> RUNNING@2 same-UoW activation CAS grants execution authority.

R8F-I03
Supervisor reservation is process-local and never replaces durable activation.

R8F-I04
CAS loser performs zero durable cleanup.

R8F-I05
Activation winner restores active budget before runtime work.

R8F-I06
AgentRuntime executes E2 with durable_revision=2 and never creates E2 again.

R8F-I07
Fork runner ownership is execution-scoped.

R8F-I08
Fork E2 never goes through coordinator.execute_task() Task terminalization.

R8F-I09
Same fork_request_id is replay-first and never creates/starts a second fork execution.

R8F-I10
Branch/FORK authorization uses durable Task ownership.

R8F-I11
One branch terminal result never terminalizes a multi-branch Task.

R8F-I11A
Legacy/source task wrappers cannot overwrite aggregate multi-branch activity after a FORK; WAITING and terminal writes are fenced inside TaskBudget authority.

R8F-I11B
FORK consume and legacy terminalization serialize on the Task row; a committed ForkAdmission and a one-branch terminal Task outcome cannot coexist from that race.

R8F-I11C
Every aggregate Task activity writer serializes on AgentTask before taking its branch snapshot; a committed RUNNING branch cannot coexist with a stale aggregate Task WAITING write.

R8F-I11D
R7 task-scoped resume preserves Task -> TaskBudget/execution -> activity lock order and cannot leave Task WAITING after the current branch resumes RUNNING.

R8F-I12
Task cancellation durably closes Task/TaskBudget before local runner drain.

R8F-I13
Task cancellation blocks fresh fork activation and R7 resume.

R8F-I14
Activation/start failure releases active execution capacity exactly once.

R8F-I15
Activation/start failure does not release active branch capacity or resolve B2.

R8F-I16
R7 RESUME and R8 FORK keep separate authority models.

R8F-I17
R12, not R8-F, owns stale RUNNING crash recovery.

R8F-I18
R9, not R8-F, owns branch result resolution.

R8F-I18A
Committed replay with exact E2 RUNNING@1 continues activation of the SAME E2; RUNNING@2+/WAITING/terminal replay is identity-only.

R8F-I18B
Every pre-activation failure after local reserve releases the supervisor reservation.

R8F-I18C
Live caller cancellation cannot strand this process's activation winner at RUNNING@2; activation is shielded/observed and a local WIN is fail-closed before CancelledError propagates.

R8F-I18D
Replay lifecycle classification is fail-closed; only RUNNING@1, RUNNING@2+, WAITING@3+, and terminal@2+ are valid R8-F shapes.

R8F-I19
Activation and Task cancellation serialize over Task/TaskBudget authority and race on E2 revision 1.

R8F-I20
Task cancellation settles exact dormant ForkAdmission-backed RUNNING@1 executions and releases their active execution capacity exactly once.

R8F-I21
Task cancellation does not release active branch capacity or invent branch resolution.
```

---

# 27. Required regressions before R8-F closure

At minimum:

```text
activation task-cancel SQL serialization race
dormant RUNNING@1 fork cancellation/accounting
activation two-worker race
activation loser zero-mutation
activation start failure accounting
outer-cancellation-around-activation ownership
invalid replay lifecycle rejection
active budget restore
replay after source movement
restart/retry after consume-before-activation activates the SAME E2 exactly once
replay after activation
two branch runners same Task
branch completion does not complete Task
branch failure does not fail Task
legacy root/source completion does not terminalize a forked Task
legacy root/source WAITING does not mask a RUNNING sibling
FORK consume vs legacy terminalization has no split-brain outcome
FORK consume vs standalone activity reconciliation has no RUNNING-branch/WAITING-Task split
R7 resume vs standalone activity reconciliation preserves final Task RUNNING
WAITING activity derivation
Task cancellation with two branch runners
restart-safe branch reads
foreign principal rejection
R7 resume regression
R8-C/D/E regression
full CI
```

---

# 28. Explicit non-scope

Do not implement in R8-F:

```text
retry-as-new-execution            # R9
ADOPT / SUPERSEDE / DISCARD       # R9
AGGREGATE                         # R9
Task final result authority       # R9
provider retry/fallback           # R10
checkpoint storage optimization   # R11
owner_instance_id / lease         # R12
stale RUNNING recovery scanner    # R12
legacy cleanup                    # R13
full fault-injection matrix       # R14
Central Asset Storage F5          # paused until R14+
```

---

# 29. Stop conditions

Stop implementation and return to contract review if R8-F would require:

```text
starting E2 without RUNNING@1 -> RUNNING@2 durable CAS

using only supervisor.is_running() as distributed authority

cleaning up E2 after losing the activation CAS

recharging NEW_EXECUTION or BRANCH during activation

routing fork E2 through execute_task()

terminalizing Task from one branch result

allowing a legacy/root execute_task wrapper to overwrite aggregate multi-branch activity after FORK

rebuilding ForkPlan before checking committed same-request replay

auto-starting an already RUNNING@2 execution after restart

reusing ResumeClaim for FORK

releasing branch capacity because local activation/start failed

leaving a ForkAdmission-backed dormant RUNNING@1 execution active after Task cancellation

claiming same-UoW validation is sufficient without a real cross-row SQL serialization fence

swallowing caller CancelledError into identity replay after an interrupted activation await

accepting arbitrary/invalid ForkAdmission execution state+revision shapes as successful replay
```

---

# 30. Exit boundary to R8-G

R8-F completion must leave:

```text
safe durable FORK
+ isolated branch transcript
+ exactly-one activation authority
+ independent branch runners
+ durable branch read/control plane
+ minimal nonterminal Task activity
+ Task cancellation fan-out
```

R8-G then owns only final R8 exit-gate proof / hardening.

No R9 result-resolution semantics may be pulled backward into R8-F.
