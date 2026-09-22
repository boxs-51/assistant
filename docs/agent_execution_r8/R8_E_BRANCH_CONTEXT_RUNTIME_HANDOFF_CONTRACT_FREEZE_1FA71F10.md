# R8-E — EXACT BRANCH CONTEXT + RUNTIME HANDOFF BOUNDARY FREEZE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Audited HEAD:** `1fa71f1001972565c2bc07efeb6e846ccbdf3ae7`  
**R8-D contract:** `25475c32ca9532bf70221db4cac56774b5b89350`  
**R8-D0→D6 code baseline:** `1fa71f1001972565c2bc07efeb6e846ccbdf3ae7`  
**Roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Scope:** R8-E branch context isolation + exact E→F runtime-handoff boundary  
**Status:** **CONTRACT FROZEN / NOT IMPLEMENTED**

---

# 1. Phase boundary

The existing R8 roadmap already separates:

```text
R8-E
branch context isolation
runtime transcript seed

R8-F
execution-scoped runner ownership
FORK API
branch read surfaces
minimal branch-aware Task activity
Task cancellation fan-out
```

Therefore this freeze does not move runner ownership into R8-E.

R8-E owns:

```text
restart-safe FORK runtime-seed representation
exact branch-base reconstruction
branch-local overlay validation
AgentExecutionContext branch_base_transcript
explicit-vs-session history semantics
AgentRuntime transcript seed selection
no Session-history bleed after FORK
read-only ForkExecutionBootstrap
```

R8-E does **not** own:

```text
supervisor.reserve/start_reserved wiring
durable execution activation CAS
HTTP FORK endpoint
coordinator runner creation
branch list/get API
Task cancel fan-out
Task completion policy
ADOPT/SUPERSEDE/DISCARD/AGGREGATE
stale RUNNING recovery lease
```

The exact R8-E → R8-F handoff is frozen in this document so R8-F cannot
invent a second execution-authority model later.

---

# 2. Baseline facts on HEAD 1fa71f10

R8-D already commits:

```text
ForkAdmission receipt
TaskBranch B2
TaskBranchContext B2
AgentExecution E2 RUNNING@1
BRANCH reservation
NEW_EXECUTION reservation
```

E2 is:

```text
new execution_id
new branch_id
base_execution_id = source E1
base_checkpoint_id = source C1
retry_of_execution_id = NULL

state = RUNNING
revision = 1
current_checkpoint_id = NULL

bound_client_id = NULL
bound_connection_id = NULL

request = source request snapshot
remaining_active_budget_seconds = source durable remaining budget
context_state = NULL
```

R8-D creates durable execution authority but deliberately starts no process-local
runner.

---

# 3. Existing R8 contract that remains authoritative

The original R8 audit already freezes:

```text
RESUME:
    resume_transcript

FORK:
    branch_base_transcript

ordinary root execution:
    session/context history
```

and:

```text
FORK branch context
=
source checkpoint committed transcript
+
branch-local overlay
```

not:

```text
latest mutable Session history
```

R8-E must implement exactly that distinction.

It must not overload R7 `resume_transcript`.

---

# 4. P0-R8E-1 — explicit empty branch history currently leaks Session history

Current `ContextBuilderAdapter.build()` uses:

```python
if request.prior_messages:
    history = prior_messages
else:
    history = loaded.session.messages
```

Therefore:

```text
explicit branch history = []
```

is indistinguishable from:

```text
no explicit history supplied
```

and falls back to mutable Session history.

That violates branch isolation.

R8-E must introduce an explicit history-source contract.

Frozen contract:

```text
AgentContextHistoryMode.AUTO
AgentContextHistoryMode.EXPLICIT
```

Semantics:

```text
AUTO:
    non-empty prior_messages -> prior_messages
    otherwise -> canonical Session history

EXPLICIT:
    prior_messages is authoritative even when empty
    Session messages MUST NOT be merged
```

Default remains `AUTO` for compatibility.

FORK always uses `EXPLICIT`.

R7 resume should also be treated as explicit history when a resume revision is
present, even if its transcript is empty.

---

# 5. P0-R8E-2 — E2 has no restart-safe runtime-context seed

Normal Agent execution admission persists `context_state` including:

```text
request_id
workflow_id
metadata
causation_id
trace_id
connection_id
limits
```

R8-D E2 currently has:

```text
context_state = NULL
```

A server restart after FORK consume therefore cannot reconstruct exact:

```text
AgentExecutionLimits
workflow_id
request_id
trace_id
causation_id
semantic metadata
```

from E2 alone.

R8-E must not silently create:

```text
AgentExecutionLimits()
```

because that could mint a new execution budget/policy different from the source
safe point.

---

# 6. P0-R8E-3 — reading source context_state later is not a frozen fork point

Source E1 `context_state` is mutable execution state.

Runtime checkpoint writes can merge new values into it, including:

```text
metadata
connection_id
limits
request/trace/workflow context
```

B1/E1 may resume after B2 was forked.

Therefore R8-E must not reconstruct B2 by reading current E1 `context_state`
after the fork transaction.

The runtime seed must be captured and fingerprinted at FORK planning/consume
time.

---

# 7. E0 prerequisite — ForkRuntimeSeed becomes part of frozen FORK semantics

Before R8-E context reconstruction is enabled, extend the read-only FORK proof
with an immutable runtime seed.

Conceptual contract:

```text
ForkRuntimeSeed:
    version = 1

    request_id
    workflow_id
    metadata
    causation_id
    trace_id

    limits

    request_fingerprint
    remaining_active_budget_seconds

    checkpoint_iteration
    base_transcript_fingerprint
    side_effect_fingerprint

    branch_context_revision
    overlay_fingerprint
```

The seed must be JSON-safe and canonically hashed:

```text
runtime_seed_fingerprint
    = SHA256(canonical_json(ForkRuntimeSeed))
```

`ForkPlan` must bind:

```text
runtime_seed
runtime_seed_fingerprint
```

and `fork_plan_fingerprint()` must include both.

This closes a TOCTOU hole where context/runtime metadata could change while the
existing R8-C plan fingerprint remains unchanged.

---

# 8. Exact runtime seed derivation

R8-C planning derives the runtime seed from the same source execution/checkpoint
safe point used for the FORK plan.

Require:

```text
source execution context_state is a mapping
context_state.limits exists
AgentExecutionLimits validates successfully
source remaining_active_budget_seconds is finite and >= 0
checkpoint remaining_active_budget_seconds matches source execution
```

For request/trace values:

```text
checkpoint metadata request_id
checkpoint metadata trace_id

must not conflict with
source context_state request_id/trace_id
```

When both are present and differ:

```text
FORK_RUNTIME_CONTEXT_CONFLICT
```

Missing non-essential values may remain NULL.

Missing/invalid `limits` is fail-closed:

```text
FORK_RUNTIME_CONTEXT_INCOMPLETE
```

No default execution limits are minted.

---

# 9. Transport affinity is deliberately stripped from ForkRuntimeSeed

FORK creates a new Branch and a new execution.

It does not inherit source transport affinity.

The runtime seed must not carry these as active routing authority:

```text
client_id
connection_id
origin_client_id
origin_connection_id
routing_connection_id
```

Any such keys in source metadata are removed from the fork runtime metadata.

E2 remains:

```text
bound_client_id = NULL
bound_connection_id = NULL

AgentExecutionContext.connection_id = NULL
```

A future remote capability routing decision must acquire its own valid
connection authority.

---

# 10. Durable runtime-seed evidence belongs to immutable ForkAdmission

The existing receipt stores only:

```text
plan_fingerprint
source IDs
output branch_id
output execution_id
principal
```

The plan fingerprint is not reversible.

After restart, R8-E needs immutable evidence for the exact runtime seed it must
reconstruct.

Add a forward migration after 14c:

```text
14d_r8_fork_runtime_seed
down_revision = 14c_r8_fork_admission
```

Extend `agent_task_fork_admissions` with:

```text
runtime_seed_json         JSON nullable
runtime_seed_fingerprint  VARCHAR(64) nullable
```

They are application-immutable.

Why nullable:

```text
no synthetic backfill
no invented historical fork semantics
old receipt without seed -> not runnable through R8-E
```

Fresh R8-E-capable FORK consumes must write both fields in the same R8-D atomic
transaction.

A receipt missing either field yields:

```text
FORK_RUNTIME_SEED_MISSING
```

R8-E does not repair it from current source state.

---

# 11. Overlay fingerprint

Add a canonical:

```text
fork_overlay_fingerprint(overlay_messages)
```

The immutable seed stores:

```text
branch_context_revision = 0
overlay_fingerprint
```

At reconstruction:

```text
BranchContext.revision == seed.branch_context_revision
fork_overlay_fingerprint(BranchContext.overlay_messages)
    == seed.overlay_fingerprint
```

Otherwise:

```text
FORK_BRANCH_CONTEXT_CHANGED
```

This prevents a mutable BranchContext row from silently changing the semantics
of a committed ForkAdmission.

---

# 12. P0-R8E-4 — initial overlay role policy must be fail-closed

Current R8-C overlay normalization accepts any syntactically valid
`InferenceMessage.role`.

That allows an overlay to contain:

```text
system
tool
assistant
```

Initial R8 semantics do not have authority for those roles:

- `system` is owned by canonical system-prompt assembly;
- `tool` would permit synthetic committed-result injection;
- `assistant` would inject model-authored history without a durable execution
  provenance.

Freeze initial R8 overlay policy:

```text
overlay message role MUST be "user"
```

Anything else:

```text
FORK_OVERLAY_ROLE_INVALID
```

Future relaxation requires a separate contract review.

R9 aggregation must not tunnel accepted branch results through fake tool/user
overlay messages.

---

# 13. Read-only ForkExecutionBootstrap contract

R8-E prepares process-local runtime state but acquires no execution ownership.

Conceptual result:

```text
ForkExecutionBootstrap:
    execution_id
    expected_execution_revision = 1

    task_id
    branch_id

    fork_request_id
    plan_fingerprint
    runtime_seed_fingerprint
    branch_context_revision

    context: AgentExecutionContext
```

The bootstrap is read-only process-local data.

It is not:

```text
a lease
a ResumeClaim
a durable runner claim
a second ForkAdmission
```

---

# 14. Read-only reconstruction API

Conceptual durable-store API:

```text
prepare_fork_execution_context(
    execution_id,
    *,
    identity,
    agent,
    clock=None,
) -> ForkExecutionBootstrap
```

R8-E does not require the original in-memory `ForkPlan`.

That is mandatory for restart safety.

---

# 15. Exact reconstruction authority

Within one read UoW, load:

```text
E2 AgentExecution

ForkAdmission by execution_id

Task T1

TaskBranch B2

TaskBranchContext B2

base checkpoint C1

base checkpoint iteration
checkpoint-bounded COMMITTED tool projections
```

Add repository primitive:

```text
get_task_fork_admission_by_execution(execution_id)
```

because `execution_id` is already unique in the receipt table.

---

# 16. E2 pre-activation fence

R8-E reconstructs only an unactivated fork output:

```text
E2.state == RUNNING
E2.revision == 1
E2.current_checkpoint_id IS NULL

E2.task_id != NULL
E2.branch_id != NULL

E2.base_execution_id != NULL
E2.base_checkpoint_id != NULL

E2.retry_of_execution_id IS NULL

E2.bound_client_id IS NULL
E2.bound_connection_id IS NULL
```

If E2 is already revision > 1:

```text
FORK_EXECUTION_ALREADY_ACTIVATED
```

R8-E does not restart it.

Stale RUNNING recovery belongs R12.

---

# 17. Receipt / Branch / Execution lineage validation

Require:

```text
receipt.execution_id == E2.id
receipt.branch_id == E2.branch_id
receipt.task_id == E2.task_id
receipt.created_by == identity.user_id

B2.branch_id == receipt.branch_id
B2.task_id == receipt.task_id
B2.resolution_state == OPEN
B2.current_execution_id == E2.id

B2.parent_branch_id == receipt.source_branch_id
B2.base_execution_id == receipt.source_execution_id
B2.base_checkpoint_id == receipt.source_checkpoint_id

E2.base_execution_id == receipt.source_execution_id
E2.base_checkpoint_id == receipt.source_checkpoint_id
```

Task requires:

```text
Task.id == receipt.task_id
Task.created_by == identity.user_id
Task.session_id == E2.session_id
Task.status IN {RUNNING, WAITING}
```

R8-E must not require the source Branch's current execution to still be E1.

R8-E must not require source TaskBudget revision to remain the old planned
revision.

Those are consume-time fences, not immutable branch-base identity.

---

# 18. Source progress after FORK must not change B2 reconstruction

After R8-D commits, source B1/E1 may:

```text
resume
advance to later iterations
create later tool calls
create later checkpoints
eventually be retried in R9
```

B2 reconstruction is permanently checkpoint-directed:

```text
receipt.source_checkpoint_id
    -> exact historical checkpoint C1
    -> C1 iteration
    -> C1 transcript / C1 active-batch COMMITTED projections
```

Forbidden:

```text
scan latest E1 transcript

scan all current E1 side effects and append them

read E1.current_checkpoint_id as B2 base

read latest Session messages as B2 base
```

This is the core branch-isolation fence.

---

# 19. Base transcript reconstruction

R8-E reuses the strict R8-C checkpoint semantics, but performs reconstruction
inside the same read UoW as receipt/Branch/Context validation.

Base conversation:

```text
base =
strict checkpoint-bounded COMMITTED transcript(C1)
```

Require:

```text
fork_transcript_fingerprint(base)
    == runtime_seed.base_transcript_fingerprint
```

No live-source global invocation scan is performed at R8-E startup.

R8-D's committed receipt already proves that the FORK safe-side-effect check
won at consume time.

R8-E only revalidates the durable projections necessary to reconstruct the
historical checkpoint transcript.

---

# 20. Exact branch-base composition

Initial R8-E branch seed:

```text
branch_base_transcript
    =
    base checkpoint transcript
    +
    BranchContext.overlay_messages
```

Order is exact and deterministic:

```text
all base messages
then
all branch-local overlay messages in stored order
```

No dedupe by text/content.

No Session message merge.

No sibling BranchContext merge.

No accepted upstream branch result in R8-E.

R9 owns explicit branch aggregation.

---

# 21. System-prompt semantics

Historical `system` messages may exist inside the checkpoint transcript and
remain part of the historical fingerprint.

However the canonical `DefaultAgentContextAssembler` owns the current system
prompt and already excludes `role == "system"` from conversation input before
prepending the canonical prompt.

R8-E preserves that architecture.

Therefore:

```text
checkpoint system message
    = historical evidence

current SystemPromptProvider output
    = active system-policy authority
```

Branch overlay cannot introduce a system message.

---

# 22. AgentExecutionContext branch seed

Add:

```text
branch_base_transcript: list[dict[str, Any]] | None = None
branch_runtime_seed_fingerprint: str | None = None
```

Semantics:

```text
None
    no FORK seed

[]
    explicit empty FORK history
```

That distinction is mandatory.

Do not reuse:

```text
resume_transcript
resume_revision
resume_pending_tool_calls
```

for FORK.

---

# 23. Mutually exclusive seed states

Invalid:

```text
branch_base_transcript is not None
AND
resume_revision is not None
```

or:

```text
branch_base_transcript is not None
AND
resume_pending_tool_calls is non-empty
```

Fail:

```text
EXECUTION_CONTEXT_SEED_CONFLICT
```

One AgentRuntime invocation is exactly one of:

```text
ordinary root/delegated new execution
R7 RESUME
R8 FORK execution
```

never multiple simultaneously.

---

# 24. Reconstructed context fields

R8-E builds E2 context from durable E2 + immutable runtime seed:

```text
execution_id = E2.id
agent_id = E2.agent_id
session_id = E2.session_id
task_id = E2.task_id
branch_id = E2.branch_id

parent_execution_id = E2.parent_execution_id
retry_of_execution_id = NULL
base_execution_id = E2.base_execution_id
base_checkpoint_id = E2.base_checkpoint_id

correlation_id = E2.correlation_id

identity = authenticated identity
agent = current registry AgentDefinition matching E2.agent_id

input = E2.request

request_id = runtime seed request_id
workflow_id = runtime seed workflow_id
metadata = runtime seed sanitized metadata
causation_id = runtime seed causation_id
trace_id = runtime seed trace_id

limits = runtime seed limits

connection_id = NULL
remaining_active_budget_seconds = E2 durable remaining budget
wait_expires_at = NULL

branch_base_transcript = reconstructed base + overlay
branch_runtime_seed_fingerprint = receipt seed fingerprint
```

Require E2 request fingerprint and remaining budget to match the seed.

---

# 25. New execution-local counters are not inherited from E1

E2 is a new AgentExecution.

Initialize:

```text
iteration = 0
tool_calls_used = 0
retry_attempts_used = 0
usage = zero InferenceUsage
```

Do not inherit E1's:

```text
iteration count
model token usage
tool-call count
retry attempts
pending tool batch
```

TaskBudget global counters remain shared and were already charged by R8-D.

---

# 26. Active budget remains frozen during read-only reconstruction

R8-E context construction uses:

```text
activate_budget = False
```

Reason:

```text
reconstruction
local supervisor reservation
distributed activation CAS
```

must not consume process-monotonic execution time before runtime ownership is
established.

R8-F restores the active deadline only after it wins durable activation.

---

# 27. Runtime transcript seed selection

R8-E may modify `AgentRuntime._execute_loop()` only to select the correct
already-prepared seed.

Frozen selection:

```text
if branch_base_transcript is not None:
    transcript = branch_base_transcript
    history_mode = EXPLICIT

elif resume_revision is not None:
    transcript = resume_transcript
    history_mode = EXPLICIT

else:
    transcript = []
    history_mode = AUTO
```

R8-E does not call `_begin_durable_execution()`.

R8-E does not start the loop itself.

---

# 28. ContextBuilder explicit-history behavior

`AgentContextRequest` adds:

```text
history_mode = AUTO | EXPLICIT
```

Default:

```text
AUTO
```

In `EXPLICIT` mode:

```text
history = prior_messages
```

even when empty.

The adapter may still resolve:

```text
current canonical system prompt
capabilities
skills
constraints
```

but must not import Session conversation messages.

Thus "shared context" in initial R8-E does not mean mutable shared conversation
history.

---

# 29. Restart contract

After process restart, with only durable SQL + authenticated identity + current
Agent registry, R8-E must reproduce the same:

```text
branch lineage
input
limits
semantic metadata
base transcript
overlay
runtime seed fingerprint
```

without:

```text
original ForkPlan in RAM
MultiAgentCoordinator._tasks
MultiAgentCoordinator._sessions
old cancellation_event
old connection_id
old client_id
```

A new process-local cancellation event is correct.

---

# 30. R8-E does not mutate durable execution authority

`prepare_fork_execution_context()` is read-only.

It does not:

```text
increment E2 revision
change E2 state
change TaskBudget
change Task
change Branch
change BranchContext
write a checkpoint
start inference
dispatch a tool
reserve supervisor ownership
```

That keeps context proof separate from execution authority.

---

# 31. P0-R8E/F-1 — supervisor ownership alone is not a distributed start fence

`AgentExecutionSupervisor` is process-local.

Two workers can both observe the same committed ForkAdmission.

Without a durable start fence both could execute:

```text
AgentRuntime.execute(E2, durable_revision=1)
```

and perform duplicate inference/tool effects before either finish CAS loses.

R8-F must therefore use a durable one-time execution activation CAS.

---

# 32. Exact E→F distributed activation contract

R8-F handoff must be:

```text
R8-E prepare_fork_execution_context(E2)
    -> read-only ForkExecutionBootstrap

AgentExecutionSupervisor.reserve(context)
    -> local duplicate fence

durable activate_fork_execution(
    E2 RUNNING@1,
    runtime_seed_fingerprint
)
    -> CAS RUNNING@1 -> RUNNING@2
    -> distributed duplicate fence

AgentExecutionSupervisor.start_reserved(...)
    -> process-local owned task

restore active budget
    -> activation barrier

AgentRuntime.execute(
    context,
    durable_revision=2
)
```

Forbidden:

```text
AgentRuntime._begin_durable_execution(E2)
```

because E2 was already admitted and charged atomically by R8-D.

---

# 33. Activation CAS semantics

Conceptual R8-F persistence primitive:

```text
activate_fork_execution(
    execution_id,
    *,
    expected_revision=1,
    runtime_seed_fingerprint,
) -> activated_revision=2
```

Before CAS require:

```text
E2.state == RUNNING
E2.revision == 1
E2.current_checkpoint_id IS NULL
E2 is the receipt output execution
B2.current_execution_id == E2.id
receipt runtime seed fingerprint matches bootstrap
```

CAS:

```text
RUNNING@1
→
RUNNING@2
```

No TaskBudget counter changes.

No new NEW_EXECUTION reservation.

No ResumeClaim.

No Task state transition.

Revision 2 means this committed fork output has consumed its one-time runtime
activation authority.

---

# 34. Why TaskBudget reservation is not the activation claim

Existing TaskBudget reservation kinds are resource accounting:

```text
NEW_EXECUTION
RESUME_EXECUTION
RELEASE_EXECUTION
TOOL_CALL
INFERENCE
USAGE
BRANCH
RELEASE_BRANCH
```

Runtime activation does not consume another execution or branch slot.

Do not add/use a fake `FORK_START` accounting reservation.

The AgentExecution revision CAS is the correct distributed start authority.

---

# 35. R8-F handoff race matrix frozen now

## Two workers, same committed fork

Both reconstruct the same seed.

Both may locally reserve in different processes.

Exactly one wins:

```text
RUNNING@1 -> RUNNING@2
```

Loser:

```text
release local supervisor reservation
do not start runtime
do not dispatch inference/tool
return already-activated/conflict semantics
```

## Same process duplicate

Supervisor reservation rejects the duplicate before durable activation where
possible.

## Durable CAS wins, local start fails

The winner owns `RUNNING@2`.

It must not simply retry `start_reserved` from another request.

While the process is alive, fail closed through an execution-revision-owned
failure/recovery path that releases:

```text
active_executions
active_parallel_agents when delegated
```

exactly once.

Do not release `active_branches` merely because runtime handoff failed.

B2 remains an OPEN durable branch until a later branch-resolution/retry policy
acts.

R9 owns branch resolution semantics.

## Process dies after activation CAS

```text
E2 remains stale RUNNING@2
```

Do not invent a lease in R8.

R12 owns stale RUNNING detection, execution leases and RECOVERY.

---

# 36. R8-D commit before R8-F activation

The window:

```text
R8-D COMMIT
E2 = RUNNING@1
process exits before R8-F activation
```

is restart-safe.

A future R8-F request may reconstruct E2 and attempt the one-time
`RUNNING@1 -> RUNNING@2` activation.

No second branch/execution/TaskBudget charge occurs because the existing
ForkAdmission is replay authority.

---

# 37. Branch/runtime failure does not terminalize Task in R8-E/F

A single branch execution failure cannot directly decide the multi-branch Task
result.

Therefore R8-E/F must not do:

```text
E2 FAILED
→ Task FAILED
```

or:

```text
E2 COMPLETED
→ Task COMPLETED
```

once multiple branches exist.

Minimal branch-aware Task activity belongs R8-F.

Final branch-result authority remains R9.

---

# 38. Stable R8-E errors

Freeze these context/bootstrap errors:

```text
FORK_RUNTIME_SEED_MISSING
FORK_RUNTIME_SEED_INVALID
FORK_RUNTIME_CONTEXT_INCOMPLETE
FORK_RUNTIME_CONTEXT_CONFLICT

FORK_EXECUTION_NOT_FOUND
FORK_EXECUTION_LINEAGE_CONFLICT
FORK_EXECUTION_ALREADY_ACTIVATED

FORK_ADMISSION_NOT_FOUND
FORK_ADMISSION_CORRUPT

FORK_BRANCH_NOT_FOUND
FORK_BRANCH_NOT_OPEN
FORK_BRANCH_LINEAGE_CONFLICT

FORK_BRANCH_CONTEXT_MISSING
FORK_BRANCH_CONTEXT_CHANGED
FORK_OVERLAY_ROLE_INVALID

FORK_BASE_CHECKPOINT_MISSING
FORK_BASE_CHECKPOINT_CONFLICT
FORK_BASE_TRANSCRIPT_CHANGED

FORK_FOREIGN_PRINCIPAL

EXECUTION_CONTEXT_SEED_CONFLICT
```

R8-E errors are read-only failures.

They do not repair durable state.

---

# 39. Expected R8-E implementation blast radius

## Representation / immutable seed

```text
ADD
se/src/infrastructure/storage/migrations/sql/versions/
    14d_r8_fork_runtime_seed.py

UPDATE
se/src/infrastructure/storage/models/sql/agent/fork_admission.py

UPDATE
se/src/runtimes/agent/contracts/fork.py

UPDATE
se/src/runtimes/agent/fork_planning.py

UPDATE
se/src/runtimes/agent/task_budget.py
    # only to persist the frozen runtime seed in the existing
    # atomic R8-D consume transaction
```

## Reconstruction

```text
UPDATE
se/src/infrastructure/storage/repositories/agent.py
    # lookup ForkAdmission by execution_id

UPDATE
se/src/runtimes/agent/persistence.py
    # read-only prepare_fork_execution_context
    # same-UoW checkpoint-bound reconstruction
```

## Runtime context isolation

```text
UPDATE
se/src/runtimes/agent/contracts/context.py
    # branch_base_transcript + seed fingerprint

UPDATE
se/src/runtimes/agent/contracts/context_builder.py
    # AUTO / EXPLICIT history mode

UPDATE
se/src/runtimes/agent/adapters/context.py
    # explicit empty history must not fall back to Session

UPDATE
se/src/runtimes/agent/runtime.py
    # transcript seed selection only
```

Tests may be added under architecture/integration R8-E coverage.

---

# 40. Explicit R8-E non-scope files

R8-E must not wire:

```text
se/src/runtimes/agent/coordinator.py
se/src/runtimes/agent/supervisor.py
se/src/main.py

se/src/transport/gateway/api/v1/multi_agent_router.py
se/src/transport/gateway/api/v1/events_router.py

se/src/runtimes/capability/drivers/agent_driver.py

cl/
```

No public FORK endpoint.

No process-local execution task is started.

No R9 resolution semantics.

---

# 41. R8-F boundary files anticipated, but not part of R8-E

The later R8-F patch may touch:

```text
Agent durable persistence / TaskBudget service
    # one-time RUNNING@1 -> RUNNING@2 activation primitive
    # fail-closed handoff accounting

AgentExecutionSupervisor call-site
MultiAgentCoordinator
multi_agent_router
main/container wiring

branch get/list surfaces
Task cancel fan-out
minimal branch-aware Task activity
```

R8-F must consume the exact R8-E bootstrap contract rather than rebuilding
branch context independently.

---

# 42. Mandatory R8-E regression matrix

## Explicit empty history

```text
base transcript = []
overlay = []

Session has messages

FORK first inference
→ Session messages absent
→ canonical system prompt may still be present
```

## Session bleed

After fork:

```text
append message S2 to Session
```

B2 reconstruction stays:

```text
C1 + B2 overlay
```

S2 is absent.

## Sibling isolation

Fork B2 and B3 from the same C1:

```text
B2 overlay = [u2]
B3 overlay = [u3]
```

B2 sees:

```text
C1 + u2
```

B3 sees:

```text
C1 + u3
```

Neither sees the sibling overlay.

## Source progresses after fork

After B2 commit:

```text
E1 resumes
creates later iteration/messages/tool results/checkpoint
```

B2 reconstruction remains exactly C1 + B2 overlay.

## Restart reconstruction

Drop all process-local objects.

Recreate only:

```text
UoW/store
authenticated Identity
Agent registry definition
```

B2 bootstrap is identical.

## Runtime seed mismatch

Mutate/corrupt:

```text
receipt runtime_seed_json
seed fingerprint
E2 request
E2 remaining budget
BranchContext revision
BranchContext overlay
base checkpoint transcript
```

Each fails closed before runtime start.

## Overlay roles

Reject:

```text
system
assistant
tool
```

Accept initial R8:

```text
user
```

## Seed mutual exclusion

Reject context containing both:

```text
branch_base_transcript
resume_revision/resume seed
```

## Execution-local accounting reset

Fork bootstrap has:

```text
iteration = 0
usage = 0
tool_calls_used = 0
retry_attempts_used = 0
```

even if E1 has non-zero values.

## Transport isolation

E1 source may have:

```text
client_id
connection_id
```

B2 context has neither as active transport authority.

---

# 43. Mandatory E→F handoff tests to reserve for R8-F

Do not implement in R8-E, but R8-F must prove:

```text
two processes race same E2
→ one RUNNING@1 -> RUNNING@2 winner
→ one runtime task/effect stream

same-process duplicate
→ supervisor rejects duplicate

activation CAS loss
→ local reservation released
→ zero inference/tool dispatch

activation win + start_reserved failure
→ no blind reactivation
→ execution capacity released exactly once through fail-closed path

process death before activation
→ E2 remains RUNNING@1 and may be activated later

process death after activation
→ E2 remains stale RUNNING@2
→ no duplicate restart in R8
→ R12 recovery owns it
```

---

# 44. R8-E invariants

```text
R8E-I01
FORK context never uses R7 resume_transcript.

R8E-I02
branch_base_transcript None and [] have different semantics.

R8E-I03
Explicit FORK history never falls back to Session messages.

R8E-I04
Branch base is checkpoint-directed, never latest-source-directed.

R8E-I05
Sibling overlays are never merged implicitly.

R8E-I06
Initial R8 overlay contains user messages only.

R8E-I07
Base checkpoint transcript precedes branch overlay.

R8E-I08
Accepted upstream branch results are absent until explicit R9 aggregation.

R8E-I09
Runtime seed is immutable durable ForkAdmission evidence.

R8E-I10
Current source context_state is never used to reconstruct an already-committed fork.

R8E-I11
No default AgentExecutionLimits are minted for a missing fork seed.

R8E-I12
Transport affinity is not inherited across FORK.

R8E-I13
Fork execution-local usage/tool/retry counters start at zero.

R8E-I14
R8-E reconstruction is read-only.

R8E-I15
R8-E starts no AgentRuntime task.

R8E-I16
E2 must be RUNNING@1 for pre-activation bootstrap.

R8E-I17
R8-F must use one durable execution-revision CAS before starting E2.

R8E-I18
Only the RUNNING@1 -> RUNNING@2 CAS winner may dispatch inference/tools.

R8E-I19
R8-F does not reuse ResumeClaim as FORK authority.

R8E-I20
Execution lease/stale RUNNING recovery remains R12.
```

---

# 45. Stop conditions

Stop implementation and return to contract review if R8-E would require:

```text
load latest Session messages into a forked branch

read current source transcript instead of base checkpoint

read current source context_state as the fork runtime seed

put fork history into resume_transcript

copy pending source invocation into E2

allow tool/system messages in initial branch overlay

mint default limits because durable fork seed is missing

inherit source client/connection affinity

start AgentRuntime from R8-E

call _begin_durable_execution(E2)

start E2 on two workers without a durable CAS

reuse R7 ResumeClaim as FORK start authority

introduce execution lease before R12

terminalize Task because one fork branch completes
```

---

# 46. Frozen implementation order

When R8-E implementation is authorized:

```text
R8-E0
ForkRuntimeSeed contract
+ runtime-seed/overlay fingerprints
+ 14d receipt seed columns
+ extend ForkPlan semantic fingerprint
+ D3 same-UoW runtime-seed revalidation
+ D4 atomic receipt seed persistence

R8-E1
repository lookup by fork execution
+ read-only ForkExecutionBootstrap contracts

R8-E2
same-UoW branch/receipt/checkpoint/context reconstruction
+ exact checkpoint-bound transcript
+ restart-safe context creation

R8-E3
AgentExecutionContext.branch_base_transcript
+ branch seed mutual-exclusion validation

R8-E4
AgentContextRequest AUTO/EXPLICIT mode
+ ContextBuilder no-Session-fallback semantics

R8-E5
AgentRuntime transcript seed selection
+ no begin/start wiring

R8-E6
branch isolation / restart / source-progress / empty-history regression matrix

R8-E7
full CI + completion document
```

R8-F is a separate patch series.

Before coding R8-F, audit/freeze:

```text
RUNNING@1 -> RUNNING@2 activation CAS
supervisor reserve/start ordering
activation failure cleanup
execution-scoped runners
branch-aware Task activity
FORK HTTP/read surfaces
Task cancellation fan-out
```

---

# 47. Status after this freeze

```text
R8-A
CLOSED / GREEN

R8-B
CLOSED / GREEN

R8-C
CLOSED / GREEN

R8-D0→D6
IMPLEMENTED / GREEN

R8-D7
NOT DONE

R8-E
CONTRACT FROZEN
NOT IMPLEMENTED

R8-F
NOT IMPLEMENTED

R8-G
NOT IMPLEMENTED

F5
PAUSED UNTIL R14+
```
