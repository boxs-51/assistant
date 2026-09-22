# R8-A — EXACT BOUNDARY AUDIT & CONTRACT FREEZE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Audited HEAD:** `92857439c004992a07f240e6efd8c6c801def634`  
**Parent roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Scope:** R8-A only — normalized TaskBranch representation, migration/backfill, repository CAS, tests  
**Explicit non-scope:** R8-B+, FORK planning/consume, runtime/context wiring, coordinator/HTTP changes  
**Status:** FROZEN FOR IMPLEMENTATION

---

## 1. Exact R8-A boundary

R8-A introduces durable representation only:

```text
Task
  1 -> N TaskBranch

TaskBranch
  1 -> N AgentExecution
```

It does not create a new branch at runtime and does not expose FORK.

Allowed production files:

```text
se/src/domain/schemas/multi_agent.py
se/src/infrastructure/storage/models/sql/agent/task_branch.py
se/src/infrastructure/storage/models/sql/agent/__init__.py
se/src/infrastructure/storage/migrations/sql/versions/14a_r8_task_branch.py
se/src/infrastructure/storage/repositories/agent.py
se/src/runtimes/agent/persistence.py
```

Allowed tests:

```text
se/tests/architecture/test_r8_a_task_branch_representation.py
se/tests/integration/test_r8_a_task_branch_migration.py
se/tests/integration/test_r8_a_task_branch_repository.py
```

R8-A must not modify:

```text
AgentRuntime
AgentExecutionContext
TaskBudgetService
MultiAgentCoordinator
ContextBuilder/ContextAssembler
gateway routers
R7 resume/claim logic
CL
```

---

## 2. Existing baseline facts

The current repository already has:

```text
AgentExecution.task_id
AgentExecution.branch_id
AgentExecution.base_execution_id
AgentExecution.base_checkpoint_id

AgentExecutionCheckpoint.task_id
AgentExecutionCheckpoint.branch_id

TaskBudget.max_active_branches
TaskBudget.active_branches
TaskBudget BRANCH/RELEASE_BRANCH reservation kinds
```

But there is no normalized branch authority and no branch CAS.

R3/R5/R7 representation must remain source-compatible.

---

## 3. Newly discovered migration invariants

### R8-A-P0-1 — checkpoint lineage must be backfilled with execution lineage

R7 resume compares normalized checkpoint branch identity with execution/plan branch identity.

Therefore migration may not do only:

```text
agent_executions.branch_id = synthesized branch
```

while leaving:

```text
agent_execution_checkpoints.branch_id = NULL
```

R8-A backfill must update both in the same Alembic transaction.

### R8-A-P0-2 — non-task execution cannot own a branch

Preflight rejects:

```text
agent_executions.task_id IS NULL
AND branch_id IS NOT NULL
```

A TaskBranch cannot be materialized without a Task.

### R8-A-P0-3 — one pre-R8 branch identifier cannot belong to multiple Tasks

Preflight rejects any non-null `branch_id` mapped to more than one distinct `task_id`.

### R8-A-P0-4 — multiple pre-R8 branch IDs for one Task are ambiguous

Before R8 there was no durable TaskBranch authority.

If one Task already contains more than one distinct non-null branch ID, migration cannot safely infer parent/base/current branch semantics.

Migration fails closed.

### R8-A-P0-5 — normalized checkpoint pre-state must already agree with execution lineage

For every task-scoped normalized checkpoint present before R8-A:

```text
checkpoint.task_id == execution.task_id
checkpoint.branch_id == execution.branch_id
```

where NULL equals NULL.

Any mismatch is existing durable corruption and migration fails closed instead of silently rewriting authority.

The only rewrite permitted is the Case-B synthesized root branch, where both execution and checkpoint branch IDs were NULL before the migration.

---

# 4. Frozen schema — agent_task_branches

Table:

```text
agent_task_branches
```

Columns:

```text
branch_id             VARCHAR(255) PRIMARY KEY
task_id               VARCHAR(255) NOT NULL
parent_branch_id      VARCHAR(255) NULL

base_execution_id     VARCHAR(255) NULL
base_checkpoint_id    VARCHAR(255) NULL

current_execution_id  VARCHAR(255) NULL

resolution_state      VARCHAR(16) NOT NULL DEFAULT 'OPEN'
revision              INTEGER NOT NULL DEFAULT 0

created_by            VARCHAR(255) NOT NULL
reason                TEXT NULL

created_at            TIMESTAMP WITH TIME ZONE NOT NULL
updated_at            TIMESTAMP WITH TIME ZONE NOT NULL
```

Foreign keys:

```text
task_id
  -> agent_tasks.id
  ON DELETE CASCADE

parent_branch_id
  -> agent_task_branches.branch_id
  ON DELETE RESTRICT

base_execution_id
  -> agent_executions.id
  ON DELETE RESTRICT

base_checkpoint_id
  -> agent_execution_checkpoints.checkpoint_id
  ON DELETE RESTRICT

current_execution_id
  -> agent_executions.id
  ON DELETE SET NULL
```

Checks:

```text
revision >= 0

resolution_state IN (
    'OPEN',
    'ADOPTED',
    'SUPERSEDED',
    'DISCARDED',
    'CANCELLED'
)

parent_branch_id IS NULL OR parent_branch_id <> branch_id

root/fork origin shape:
(
  parent_branch_id IS NULL
  AND base_execution_id IS NULL
  AND base_checkpoint_id IS NULL
)
OR
(
  parent_branch_id IS NOT NULL
  AND base_execution_id IS NOT NULL
  AND base_checkpoint_id IS NOT NULL
)
```

Indexes:

```text
ix_agent_task_branches_task_resolution
    (task_id, resolution_state)

ix_agent_task_branches_parent_branch_id
ix_agent_task_branches_current_execution_id
ix_agent_task_branches_base_checkpoint_id
```

R8-A does not retrofit a foreign key onto historical
`agent_executions.branch_id` or `agent_execution_checkpoints.branch_id`.

Reason:

- existing R3 lineage fields are soft references;
- adding cyclic retroactive FKs is not required for R8-A representation;
- R8-B atomic admission will validate branch existence/task identity transactionally;
- R8-A must minimize blast radius to persistence representation only.

---

# 5. Frozen schema — agent_task_branch_contexts

Table:

```text
agent_task_branch_contexts
```

Columns:

```text
branch_id          VARCHAR(255) PRIMARY KEY
revision           INTEGER NOT NULL DEFAULT 0
overlay_messages   JSON NOT NULL DEFAULT []
created_at         TIMESTAMP WITH TIME ZONE NOT NULL
updated_at         TIMESTAMP WITH TIME ZONE NOT NULL
```

Foreign key:

```text
branch_id
  -> agent_task_branches.branch_id
  ON DELETE CASCADE
```

Check:

```text
revision >= 0
```

R8-A persists the overlay container but does not consume it in context assembly.

Base transcript is not copied into this table.

---

# 6. BranchResolutionState

Canonical enum:

```text
OPEN
ADOPTED
SUPERSEDED
DISCARDED
CANCELLED
```

R8-A only represents these states.

R8-B/R8-C/R8-D do not gain permission to invent R9 result-resolution behavior merely because the enum exists.

---

# 7. Historical backfill cases A→D

## Case A — Task has no execution history

```text
agent_tasks row exists
agent_executions count = 0
```

Action:

```text
create no branch
create no branch context
change no TaskBudget counter
```

The root branch will later be created by R8-B atomic first-execution admission.

---

## Case B — all Task executions have branch_id = NULL

Create one deterministic compatibility root branch.

ID:

```text
r8_legacy_<sha256(task_id)[0:40]>
```

Properties:

```text
parent_branch_id = NULL
base_execution_id = NULL
base_checkpoint_id = NULL
resolution_state =
    CANCELLED if AgentTask.status == CANCELLED
    else OPEN
created_by = AgentTask.created_by
reason = R8_MIGRATION_SYNTHESIZED_ROOT
```

Then atomically backfill:

```text
all task AgentExecution.branch_id
all normalized checkpoint.branch_id for those executions
```

to the synthesized branch.

TaskBudget is not modified in R8-A.

R8-B owns root-branch accounting.

---

## Case C — exactly one existing non-null branch_id

Treat that identifier as the compatibility root branch.

Requirements:

```text
all non-null branch IDs for Task are identical
checkpoint pre-state agrees with execution pre-state
branch ID is not reused by another Task
```

Create normalized branch:

```text
branch_id = existing ID
parent/base lineage = NULL
reason = R8_MIGRATION_NORMALIZED_EXISTING_BRANCH
```

Any NULL execution branch IDs on the same Task are assigned to this branch, and their NULL checkpoint branch IDs are assigned to it.

Pre-existing non-null identifiers are preserved on downgrade.

---

## Case D — more than one distinct existing non-null branch_id for one Task

Fail migration:

```text
R8_BRANCH_BACKFILL_CONFLICT
```

No branch hierarchy is guessed.

No rows are silently merged.

---

# 8. current_execution_id backfill

Nested Agents inherit `branch_id`, so “latest execution in the branch” is not a safe branch-head rule.

R8-A may set `current_execution_id` only when historical data proves one unambiguous top-level execution:

```text
same task_id
same normalized branch_id
parent_execution_id IS NULL
count == 1
```

Otherwise:

```text
current_execution_id = NULL
```

NULL means:

```text
historical branch head was not provable at migration time
```

It is not permission to pick an arbitrary nested execution.

R8-B must establish current-execution authority before new branch-aware runtime admission uses such a branch.

---

# 9. Downgrade contract

Downgrade to `13b_r7_pending_snapshot` must preserve pre-R8 data.

For branches with:

```text
reason = R8_MIGRATION_SYNTHESIZED_ROOT
```

downgrade restores synthesized execution/checkpoint branch IDs to NULL.

For:

```text
reason = R8_MIGRATION_NORMALIZED_EXISTING_BRANCH
```

existing branch IDs remain unchanged.

Then drop:

```text
agent_task_branch_contexts
agent_task_branches
```

No TaskBudget counters are touched.

---

# 10. Repository contract

Add low-level repository methods:

```text
save_task_branch(values)
get_task_branch(branch_id)
list_task_branches(task_id)
compare_and_set_task_branch(branch_id, expected_revision, values)

save_task_branch_context(values)
get_task_branch_context(branch_id)
compare_and_set_task_branch_context(branch_id, expected_revision, overlay_messages)
```

## TaskBranch CAS

CAS predicate:

```text
branch_id == requested branch
revision == expected_revision
```

Success:

```text
revision = expected_revision + 1
```

R8-A mutable fields are only:

```text
current_execution_id
resolution_state
```

Immutable after creation:

```text
branch_id
task_id
parent_branch_id
base_execution_id
base_checkpoint_id
created_by
reason
created_at
```

Any attempt to mutate immutable lineage through this repository raises before SQL execution.

## BranchContext CAS

CAS predicate:

```text
branch_id
revision
```

Only:

```text
overlay_messages
```

is mutable.

Success increments revision by exactly one.

---

# 11. DurableAgentStore contract

Add:

```text
BranchConflictError
```

Store facade methods:

```text
save_task_branch
load_task_branch
list_task_branches
compare_and_set_task_branch

save_task_branch_context
load_task_branch_context
compare_and_set_task_branch_context
```

`overlay_messages` must pass through canonical JSON-safe normalization.

The store owns commit boundaries exactly as existing Task/Execution facades do.

---

# 12. R8-A mandatory tests

## Representation

Prove:

```text
BranchResolutionState values exact
TaskBranch shape
TaskBranchContext shape
SQL check constraints present
branch context defaults to []
```

## Migration

Prove:

```text
13b -> 14a -> 13b

exactly one Alembic head

Case A:
    no fake branch

Case B:
    deterministic root
    execution backfilled
    checkpoint backfilled
    current_execution inferred only when unambiguous

Case C:
    existing branch ID preserved
    NULL sibling execution/checkpoint normalized

Case D:
    explicit migration failure

task_id NULL + branch_id non-null:
    explicit migration failure

same branch ID reused across tasks:
    explicit migration failure

checkpoint/execution lineage mismatch:
    explicit migration failure

downgrade:
    synthesized IDs return to NULL
    pre-existing IDs remain
```

## Repository/CAS

Prove:

```text
save/get/list branch
branch CAS revision increments
stale branch CAS loses
immutable lineage mutation rejected

save/get branch context
overlay JSON round-trip
context CAS increments
stale context CAS loses
```

---

# 13. R8-A exit gate

R8-A is complete only if:

```text
migration head = 14a_r8_task_branch

historical R7 WAITING checkpoint remains branch-consistent

TaskBranch representation is durable

branch/context CAS is stale-write safe

no TaskBudget counters changed by migration

no FORK runtime exists

full repository pytest remains green
```

After R8-A closes, next phase is:

```text
R8-B — root-branch accounting + atomic branch-aware execution admission
```

not R8-D/FORK directly.
