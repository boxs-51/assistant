# R8 — HEAD AUDIT, CONTRACT FREEZE CANDIDATE & IMPLEMENTATION PLAN

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Baseline:** `a55e4fd2a20ddccbd227e770d26fae72e33bc88e`  
**Parent phase:** R7 CLOSED  
**Roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Date:** 2026-09-22  
**Status:** **AUDIT / CONTRACT FREEZE CANDIDATE — NO R8 IMPLEMENTATION YET**

---

## 1. R8 roadmap contract

R8 is:

```text
Normalized TaskBranch Persistence
+ Branch Context Isolation
+ safe FORK
```

Roadmap exit gate:

```text
Fork is safe, isolated and durable.
```

Required R8 evidence:

```text
two branches run independently
branch-local message isolation
fork from unsafe checkpoint rejected
unknown side-effect blocks fork
TaskBudget limits enforced
original branch remains immutable after fork
```

R9 remains separate:

```text
retry as new execution in same branch
ADOPT
SUPERSEDE
DISCARD
AGGREGATE
Task completion CAS
branch result authority
```

R8 must not prematurely implement R9 resolution policy.

---

# 2. Baseline evidence

R8 starts from:

```text
main @ a55e4fd2a20ddccbd227e770d26fae72e33bc88e
```

Exact baseline CI:

```text
Architecture Baseline:
    Linux:  791 passed, 1 skipped, 14 warnings
    Windows client: 68 passed

Phase 5 consolidated exit gates:
    SUCCESS
```

Final R7 has already closed reconnect/resume/restart/lost-ACK authority.

Therefore the roadmap stop condition:

```text
Do not implement general Task FORK before R6 and R7 pass.
```

is satisfied.

---

# 3. Existing R8-ready representation already present

R3/R5/R7 intentionally pre-positioned several R8 fields.

## AgentExecution already contains

```text
task_id
branch_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
```

## AgentExecutionContext already contains

```text
task_id
branch_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
```

## Normalized checkpoint already contains

```text
task_id
branch_id
parent_checkpoint_id
transcript_snapshot
transcript_ref
side_effect_watermark
```

## TaskBudget already contains

```text
max_active_branches
active_branches
```

and reservation kinds:

```text
BRANCH
RELEASE_BRANCH
```

with service primitives:

```text
reserve_branch_slot()
release_branch_slot()
```

These are representation/preparation only.

They are not a normalized TaskBranch authority.

---

# 4. Exact missing authority

There is currently no:

```text
TaskBranch domain model
agent_task_branches table
TaskBranch repository
TaskBranch CAS
branch current-execution authority
branch context persistence
fork service
fork HTTP contract
branch-aware runner ownership
```

`AgentExecution.branch_id` is currently only an optional string.

It is not backed by a durable TaskBranch row.

---

# 5. P0/P1 findings

## P0-R8-1 — no normalized TaskBranch authority

Roadmap requires:

```text
Task 1:N Branch
Branch 1:N Execution
```

Current DB only stores optional `AgentExecution.branch_id`.

A string field cannot own:

```text
parent branch lineage
base checkpoint
current execution
resolution state
revision/CAS
```

R8 must introduce normalized persistence first.

---

## P0-R8-2 — branch budget reservation is not atomic with branch/execution creation

Current:

```text
reserve_branch_slot()
```

only increments:

```text
TaskBudget.active_branches
```

If R8 does:

```text
reserve branch slot
COMMIT
↓
create branch
COMMIT
↓
create execution
```

then crash/race can leak branch capacity or create a branch with no execution.

R8 must introduce one atomic transaction for FORK authority:

```text
TaskBudget CAS
+ BRANCH reservation
+ NEW_EXECUTION reservation
+ TaskBranch insert
+ branch context insert
+ AgentExecution insert
+ TaskBranch.current_execution_id
```

all-or-nothing.

---

## P0-R8-3 — coordinator owns only one background runner per Task

Current process-local ownership is keyed by:

```text
_running_tasks[task_id]
```

and `start_task()` rejects a second runner for the same Task.

R8 requires:

```text
one Task
→ multiple branches
→ multiple independent executions
```

Runner ownership must become execution-scoped.

Task cancellation still fans out to all owned executions.

---

## P0-R8-4 — current Task terminalization assumes one execution owns Task outcome

`MultiAgentCoordinator.execute_task()` currently maps one execution outcome directly to:

```text
Task COMPLETED
Task FAILED
Task WAITING
Task CANCELLED
```

That assumption is invalid once a Task has multiple branches.

R8 must introduce minimal branch-aware Task activity semantics sufficient to keep branches independent.

R9 still owns final branch resolution/ADOPT/AGGREGATE.

Frozen R8 rule:

```text
A single branch completion/failure must not terminalize a multi-branch Task.
```

Legacy/single-root-branch behavior may remain source-compatible until a Task actually forks.

---

## P0-R8-5 — checkpoint lineage is not fully enforced at checkpoint creation

R7 resume later verifies:

```text
checkpoint.branch_id == execution.branch_id
```

but `stage_waiting_checkpoint()` currently validates:

```text
session_id
task_id
```

and does not reject a mismatched `branch_id`.

Before R8 FORK may trust checkpoint branch identity:

```text
stage_waiting_checkpoint()
```

must also require exact branch equality.

---

## P0-R8-6 — no explicit FORK-safe checkpoint classification

R7 checkpoint means RESUME-safe.

R8 needs an additional FORK-safe predicate.

Initial R8 rule:

```text
FORK source must be a normalized checkpoint
with exact task/branch/execution identity
and zero unresolved pending invocation rows.
```

The initial R8 implementation deliberately rejects a checkpoint that still owns a pending remote invocation.

This includes:

```text
IN_FLIGHT
OUTCOME_UNKNOWN
NOT_DISPATCHED pending invocation
```

Reason:

A new branch is a new execution. It must not inherit a pending side-effect operation whose execution ownership belongs to the source execution.

Committed results already reconstructed into the safe transcript are allowed.

This is intentionally conservative.

Future relaxation requires a separate contract review.

---

## P0-R8-7 — branch-local context isolation does not exist

Current first-iteration context falls back to session conversation history.

Two branches therefore cannot safely diverge unless the fork execution starts from a branch-specific transcript.

R8 must ensure:

```text
fork branch context
=
source checkpoint committed transcript
+ branch-local overlay
```

and not:

```text
latest mutable Session history
```

after the fork point.

---

## P0-R8-8 — R7 resume transcript state must not be overloaded for FORK

`AgentExecutionContext.resume_transcript` is R7 continuation state.

FORK is not RESUME.

R8 must not stuff fork transcript state into `resume_transcript`.

Introduce a distinct branch initialization surface, for example:

```text
branch_base_transcript
```

Runtime transcript seed rule:

```text
RESUME:
    resume_transcript

FORK:
    branch_base_transcript

ordinary root execution:
    session/context history
```

Simultaneous RESUME + FORK seed data is invalid.

---

## P0-R8-9 — fork winner needs durable branch CAS/idempotency

Two workers may race the same fork request.

R8 requires one durable fork identity and one winner.

A replay of the same logical fork request must return the same created branch/execution rather than consuming another TaskBudget slot.

---

## P1-R8-1 — branch query surfaces are absent

R8 should expose read-only branch discovery:

```text
get branch
list branches for task
```

This is needed for tests, UI polling and later R9 resolution.

---

## P1-R8-2 — existing task history needs compatibility materialization

Historical Task-scoped executions may have:

```text
branch_id = NULL
```

or legacy non-null branch IDs without a TaskBranch row.

R8 migration/compatibility must not silently invent conflicting branch lineage.

See migration rules below.

---

# 6. Frozen TaskBranch domain

Add:

```text
BranchResolutionState:
    OPEN
    ADOPTED
    SUPERSEDED
    DISCARDED
    CANCELLED
```

R8 itself creates and operates only:

```text
OPEN
CANCELLED
```

R9 owns:

```text
ADOPTED
SUPERSEDED
DISCARDED
```

except historical compatibility materialization may represent already-terminal legacy Tasks conservatively.

Canonical TaskBranch:

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

No execution runtime state is persisted on the branch.

Display runtime state is derived from `current_execution_id`.

---

# 7. R8 persistence

## 7.1 Migration

Proposed revision:

```text
14a_r8_task_branch
```

Parent:

```text
13b_r7_pending_snapshot
```

This is correct on `main`.

Central Asset Storage migrations remain on the paused feature branch and are not part of R8.

---

## 7.2 agent_task_branches

Columns:

```text
branch_id              PK
task_id                FK agent_tasks.id CASCADE
parent_branch_id       FK agent_task_branches.branch_id SET NULL

base_execution_id      FK agent_executions.id SET NULL
base_checkpoint_id     FK agent_execution_checkpoints.checkpoint_id RESTRICT

current_execution_id   FK agent_executions.id SET NULL

resolution_state       NOT NULL
revision               NOT NULL

created_by             NOT NULL
reason                 NULL

created_at
updated_at
```

Constraints:

```text
revision >= 0

resolution_state IN (
    OPEN,
    ADOPTED,
    SUPERSEDED,
    DISCARDED,
    CANCELLED
)
```

Indexes:

```text
(task_id, resolution_state)
parent_branch_id
current_execution_id
base_checkpoint_id
```

No 1:1 uniqueness between branch and execution.

A Branch is explicitly 1:N executions over time.

---

## 7.3 Branch context

Add one branch context authority:

```text
agent_task_branch_contexts
```

Initial contract:

```text
branch_id          PK / FK branch CASCADE
revision           >= 0
overlay_messages   JSON NOT NULL default []
created_at
updated_at
```

Base transcript authority is not duplicated.

It is resolved through:

```text
TaskBranch.base_checkpoint_id
→ AgentExecutionCheckpoint
→ committed safe transcript
```

Root branch:

```text
base_checkpoint_id = NULL
overlay_messages = []
```

Fork branch:

```text
base_checkpoint_id = source checkpoint
overlay_messages = fork-local instructions/messages
```

R11 may later replace/optimize storage strategy.

---

# 8. Historical compatibility

R8 migration must inspect existing Task execution history.

For each Task:

### Case A — no execution history

No branch row is required during migration.

Root branch will be created atomically when the first Task execution is admitted.

### Case B — existing executions all have branch_id NULL

Create one normalized root branch.

Assign all historical Task executions to that root branch.

### Case C — exactly one existing non-null branch_id

Create one normalized branch using that existing identifier.

Assign any NULL execution rows for that Task to the same branch.

### Case D — more than one distinct pre-R8 non-null branch_id

Migration must fail closed with diagnostics.

Before R8, multiple normalized branches had no durable authority, so parent/base/current semantics cannot be inferred safely.

Do not silently merge them.

---

# 9. Root branch accounting

The root branch counts toward:

```text
TaskBudget.active_branches
```

Otherwise:

```text
max_active_branches = 1
```

would incorrectly permit:

```text
root + one fork
```

R8 must therefore account the root branch exactly once.

For a new Task, the preferred atomic admission is:

```text
first Task execution
→ create root TaskBranch
→ reserve root branch slot
→ reserve execution
→ insert execution
→ bind branch.current_execution_id
```

in one UoW.

---

# 10. Atomic FORK contract

Conceptual API:

```text
fork_task_branch(
    task_id,
    source_branch_id,
    source_execution_id,
    source_checkpoint_id,
    fork_request_id,
    new_branch_id,
    new_execution_id,
    overlay_messages,
    identity,
)
```

One transaction must:

```text
load AgentTask + TaskBudget
verify TaskBudget OPEN

load source TaskBranch
verify branch OPEN
verify task_id

load source AgentExecution
verify task_id/branch_id

load source checkpoint
verify:
    execution_id
    execution_revision
    task_id
    branch_id
    source checkpoint identity

verify FORK-safe:
    zero pending invocation snapshots
    reconstructable committed transcript

check/replay fork reservation identity

verify:
    active_branches < max_active_branches
    used_executions < max_total_executions
    active_executions < max_active_executions

insert new TaskBranch
insert new BranchContext
insert new RUNNING AgentExecution

increment:
    active_branches + 1
    used_executions + 1
    active_executions + 1

persist BRANCH reservation
persist NEW_EXECUTION reservation

set new branch.current_execution_id

COMMIT
```

Failure anywhere rolls back everything.

---

# 11. Fork lineage

New execution:

```text
task_id
    = source.task_id

branch_id
    = NEW branch

retry_of_execution_id
    = NULL

base_execution_id
    = source execution

base_checkpoint_id
    = source checkpoint

correlation_id
    = source correlation tree
```

`parent_execution_id` remains delegation lineage only.

If the source execution itself is a delegated child, the forked execution preserves that delegation ancestry rather than using the source execution as `parent_execution_id`.

The source execution is referenced through:

```text
base_execution_id
```

---

# 12. Original branch immutability

FORK must not mutate:

```text
source TaskBranch
source AgentExecution
source Checkpoint
source transcript
source ResumeClaim
source CapabilityInvocation
```

The only shared mutation is TaskBudget accounting.

The new branch references immutable origin identity.

---

# 13. Branch context assembly

For a forked execution, build the initial transcript from:

```text
load_committed_checkpoint_transcript(
    source_execution_id,
    source_checkpoint_id,
    active_tool_call_ids=()
)
+
branch.overlay_messages
```

The result enters:

```text
AgentExecutionContext.branch_base_transcript
```

Then AgentRuntime owns the new execution transcript independently.

After execution starts:

```text
Branch B1 transcript mutation
does not mutate
Branch B2 base/overlay
```

`ContextBuilderAdapter` must not reload newer global Session history on the first fork iteration when `branch_base_transcript` is present.

---

# 14. Minimal R8 Task activity policy

R8 does not implement final result resolution.

Rules:

### Single branch / legacy task

Existing task completion behavior remains compatible.

### Multi-branch task

One branch terminal outcome cannot terminalize the Task while another OPEN branch has a non-terminal execution.

Minimal derived activity:

```text
any current execution CREATED/RUNNING
→ Task RUNNING

else any current execution WAITING
→ Task WAITING

else
→ leave Task non-terminal for R9 branch resolution
```

R9 replaces this temporary multi-branch unresolved state with deterministic ADOPT/SUPERSEDE/DISCARD/AGGREGATE semantics.

---

# 15. Runner ownership

Replace process-local single runner assumption:

```text
_running_tasks[task_id]
```

with execution-scoped ownership.

Conceptually:

```text
_running_executions[execution_id] -> asyncio.Task
_task_execution_ids[task_id] -> set[execution_id]
```

Task cancellation:

```text
cancel Task
→ cancel/gather all local execution runners
→ supervisor.cancel_task(task_id)
→ durable TaskBudget close/CAS
```

No child task may become unowned.

---

# 16. Transport/API surface

Add request DTO:

```text
AgentTaskForkRequest:
    source_branch_id
    source_execution_id
    source_checkpoint_id
    fork_request_id
    overlay_messages
    reason
```

Recommended endpoints:

```text
POST /v1/multi-agent/tasks/{task_id}/fork
GET  /v1/multi-agent/tasks/{task_id}/branches
GET  /v1/multi-agent/branches/{branch_id}
```

HTTP is only a control-plane facade.

Branch/FORK authority remains in durable Task/Branch runtime services.

---

# 17. Exact expected implementation blast radius

## Representation / migration

```text
ADD
se/src/infrastructure/storage/migrations/sql/versions/14a_r8_task_branch.py

ADD
se/src/infrastructure/storage/models/sql/agent/task_branch.py

UPDATE
se/src/infrastructure/storage/models/sql/agent/__init__.py

UPDATE
se/src/domain/schemas/multi_agent.py
```

## Persistence

```text
UPDATE
se/src/infrastructure/storage/repositories/agent.py

UPDATE
se/src/runtimes/agent/persistence.py
```

## TaskBudget / branch authority

```text
UPDATE
se/src/runtimes/agent/task_budget.py
```

Do not implement FORK by sequentially calling the existing standalone branch/execution reservation methods.

Add one atomic R8 transaction boundary instead.

## Checkpoint safety

```text
UPDATE
se/src/runtimes/agent/waiting_checkpoint.py

ADD
se/src/runtimes/agent/fork_planning.py
or equivalent read-only eligibility service
```

## Runtime/context

```text
UPDATE
se/src/runtimes/agent/contracts/context.py

UPDATE
se/src/runtimes/agent/runtime.py

UPDATE
se/src/runtimes/agent/adapters/context.py

UPDATE
se/src/runtimes/agent/coordinator.py

UPDATE
se/src/main.py
```

The DefaultAgentContextAssembler semantic prompt/capability resolver itself should remain mostly unchanged; branch isolation is primarily a transcript-source authority problem.

## Transport

```text
UPDATE
se/src/transport/gateway/api/v1/multi_agent_router.py
```

## Tests

New R8 suites should be isolated under:

```text
se/tests/architecture/
se/tests/integration/
se/tests/e2e/
```

---

# 18. R8 implementation split

## R8-A — normalized persistence

Implement only:

```text
TaskBranch domain
branch resolution enum
SQL TaskBranch
SQL BranchContext
migration/backfill
repository CRUD/CAS
```

No FORK execution yet.

Exit:

```text
single Alembic head
historical compatibility proven
Branch 1:N Execution representable
```

---

## R8-B — branch-aware TaskBudget authority

Implement:

```text
root branch accounting
atomic root execution admission
branch reservation idempotency
TaskBranch current_execution CAS
```

Exit:

```text
branch slot cannot leak
max_active_branches enforced
root branch counts exactly once
```

---

## R8-C — FORK-safe planning

Implement read-only planner:

```text
source task/branch/execution/checkpoint validation
checkpoint branch-lineage validation
FORK-safe pending invocation check
stable fork semantic fingerprint
```

No mutation yet.

Exit:

```text
unsafe/stale/foreign checkpoint fails closed
unknown side-effect cannot fork
```

---

## R8-D — atomic FORK consume

Implement:

```text
one transaction:
    TaskBudget
    branch reservation
    execution reservation
    TaskBranch
    BranchContext
    AgentExecution
```

Duplicate same fork request:

```text
→ semantic replay
→ same branch/execution
```

Competing different requests at final capacity:

```text
→ one winner
```

---

## R8-E — branch context isolation

Implement:

```text
committed base transcript load
branch overlay persistence
branch_base_transcript context field
runtime transcript seed
no Session-history bleed after fork
```

---

## R8-F — coordinator + runner ownership + HTTP

Implement:

```text
execution-scoped runners
fork task API
branch list/get
minimal branch-aware Task activity
Task cancel fan-out
```

No R9 ADOPT/AGGREGATE.

---

## R8-G — exit gate

Required targeted tests:

```text
migration/backfill
TaskBranch CAS
branch slot race
atomic fork race
two branches independent
branch transcript isolation
source branch immutable
unsafe checkpoint rejected
OUTCOME_UNKNOWN rejected
TaskBudget max_active_branches
Task cancel with two branch runners
R7 resume on source branch still works
server restart preserves forked branch rows
```

Then:

```text
python -m pytest -q
```

---

# 19. R8 invariants

```text
R8-I01
TaskBranch is durable authority; AgentExecution.branch_id is a reference.

R8-I02
Branch is 1:N Execution.

R8-I03
FORK creates new branch + new execution.

R8-I04
FORK never reuses source execution_id.

R8-I05
FORK never uses parent_execution_id as fork lineage.

R8-I06
source TaskBranch/Execution/Checkpoint remain immutable.

R8-I07
TaskBudget branch + execution accounting commits atomically with fork creation.

R8-I08
same fork request is idempotent.

R8-I09
different concurrent fork requests cannot oversubscribe TaskBudget.

R8-I10
fork source checkpoint must match exact task/branch/execution lineage.

R8-I11
pending unresolved invocation blocks FORK in initial R8.

R8-I12
branch context starts from committed checkpoint transcript only.

R8-I13
branch-local overlay cannot leak into sibling branch.

R8-I14
FORK transcript state is not stored in R7 resume_transcript.

R8-I15
one branch terminal result does not terminalize a multi-branch Task.

R8-I16
R9 owns ADOPT/SUPERSEDE/DISCARD/AGGREGATE semantics.

R8-I17
Task cancel still fans out across every active branch execution.

R8-I18
R7 RESUME remains same execution, same branch.

R8-I19
R8 FORK remains new execution, new branch.

R8-I20
TaskBranch runtime state is derived, not independently duplicated.
```

---

# 20. Explicit non-scope

Do not implement in R8:

```text
retry scheduler                  # R9
ADOPT                            # R9
SUPERSEDE                        # R9
DISCARD                          # R9
AGGREGATE                        # R9
final Task result authority      # R9
provider retry/fallback          # R10
checkpoint storage optimization  # R11
stale RUNNING execution lease    # R12
legacy compatibility cleanup     # R13
full production fault matrix     # R14
Central Asset Storage F5         # paused until R14+
```

---

# 21. Stop conditions

Stop R8 and return to contract review if implementation requires any of:

```text
reuse execution_id for FORK

change parent_execution_id to encode fork

copy unresolved remote invocation into a new execution

mutate source checkpoint

mutate source branch transcript

increment TaskBudget in a separate transaction from fork creation

allow two branches to read each other's overlay implicitly

close a multi-branch Task because one branch completed

reuse R7 ResumeClaim as FORK authority
```

---

# 22. Recommended next action

The R8 contract should be implemented in order:

```text
R8-A → R8-B → R8-C → R8-D → R8-E → R8-F → R8-G
```

First patch should be **R8-A only**:

```text
TaskBranch representation
migration
historical compatibility
repository CAS
tests
```

No FORK runtime should be wired before R8-A persistence is green.
