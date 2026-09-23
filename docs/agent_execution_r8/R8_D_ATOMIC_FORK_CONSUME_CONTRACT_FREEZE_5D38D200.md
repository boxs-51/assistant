# R8-D — EXACT ATOMIC FORK CONSUME CONTRACT FREEZE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Audited HEAD:** `5d38d200383646f1131a00acb3632922c82c144d`  
**R8-C code baseline:** `d2282284e5384b4915bc02a8d9d61077b9a605da`  
**Parent contract:** `docs/agent_execution_r8/R8_C_EXACT_BOUNDARY_CONTRACT_FREEZE_832A8816.md`  
**Roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Scope:** R8-D only — atomic durable FORK consume  
**Status:** **CONTRACT FROZEN / NOT IMPLEMENTED**

---

# 1. Exact R8-C → R8-D boundary

R8-C proves that one source state is safe to fork and returns a read-only
`ForkPlan`.

R8-D is the first phase allowed to convert that proof into durable FORK
authority.

R8-D owns:

```text
write-side checkpoint branch-lineage hardening

durable fork_request_id dedupe authority

exact in-transaction revalidation of the R8-C plan

one TaskBudget CAS

optional Task WAITING -> RUNNING CAS

one new TaskBranch
one new TaskBranchContext
one new RUNNING AgentExecution

one BRANCH reservation
one NEW_EXECUTION reservation

one immutable FORK admission receipt
```

R8-D does **not** own:

```text
branch-aware ContextAssembler
runtime transcript seeding
execution runner scheduling
HTTP FORK endpoint
branch list/read endpoints
branch result resolution
ADOPT/SUPERSEDE/DISCARD/AGGREGATE
retry-as-new-execution
multi-branch Task completion policy
```

Those remain R8-E+ / R9.

---

# 2. Baseline facts confirmed on HEAD 5d38d200

## 2.1 R8-C ForkPlan is proof only

The current plan binds:

```text
Task revision
Branch revision
Execution revision
checkpoint identity/iteration
TaskBudget revision/policy
base transcript
side-effect snapshot
overlay messages
principal
```

and explicitly excludes:

```text
fork_request_id
new branch_id
new execution_id
timestamps
connection/client/process identity
```

Therefore R8-D requires its own durable request-id authority.

## 2.2 Existing TaskBudget ledger cannot replay output identities

`agent_task_budget_reservations` stores only:

```text
task_id
kind
reservation_key
payload_fingerprint
created_at
```

It does not persist:

```text
new_branch_id
new_execution_id
```

Therefore BRANCH + NEW_EXECUTION reservations alone cannot implement:

```text
same fork_request_id
→ same branch/execution after lost response
```

without deterministic output IDs.

R8-D must not overload the reservation table as the sole FORK request receipt.

## 2.3 Existing R8-B root admission is the atomic accounting precedent

R8-B already proves the required pattern:

```text
TaskBudget CAS
+ AgentExecution
+ TaskBranch
+ BranchContext
+ BRANCH ledger
+ NEW_EXECUTION ledger
+ COMMIT
```

R8-D extends that pattern with:

```text
source-plan revalidation
+ durable fork_request_id receipt
+ sibling branch lineage
```

## 2.4 Current WAITING write path still misses branch equality

`stage_waiting_checkpoint()` validates:

```text
execution_id
execution_revision
session_id
task_id
wait_reason
```

but still does not enforce:

```text
checkpoint.branch_id == execution.branch_id
```

`verify_committed_waiting_checkpoint()` also does not verify the complete
session/task/branch snapshot.

This must be hardened before FORK consume is enabled.

---

# 3. P0 findings

## P0-R8D-1 — fork_request_id requires an immutable durable receipt

The request identity contract frozen by R8-C is:

```text
(task_id, fork_request_id)
→ plan_fingerprint
→ one committed branch/execution pair
```

A new immutable receipt is required.

The receipt represents only a **committed** FORK.

There is no PENDING state because the receipt is inserted in the same database
transaction as every other FORK write.

If the transaction rolls back, the receipt does not exist.

---

## P0-R8D-2 — same request replay must run before source revalidation

After a successful FORK, the original source may later:

```text
resume
gain a newer checkpoint
change Branch.current_execution_id in R9
consume more TaskBudget
```

A lost-response retry of the already-committed request must still return the
same FORK result.

Therefore transaction order is:

```text
validate in-memory plan fingerprint

load fork admission receipt FIRST

if receipt exists:
    semantic replay
    return same branch/execution
    do not require source to still be forkable

else:
    perform fresh R8-C revalidation
    consume new FORK
```

This is identical in spirit to R7 same-request semantic replay.

---

## P0-R8D-3 — same request + different semantics is a hard conflict

If:

```text
(task_id, fork_request_id)
```

already exists but:

```text
receipt.plan_fingerprint != plan.plan_fingerprint
```

R8-D returns:

```text
FORK_REQUEST_SEMANTIC_CONFLICT
```

and performs zero writes.

Changing only `fork_request_id` does not change plan semantics.

Reusing one request ID for different semantics is forbidden.

---

## P0-R8D-4 — source revalidation must occur inside the consume UoW

Calling `AgentForkPlanningService.build_fork_plan()` again is not sufficient
because R8-C opens independent read UoWs.

R8-D requires transaction-scoped revalidation using the same UoW that will
perform the writes.

There must be no gap:

```text
revalidate
COMMIT/close read transaction
↓ race
open write transaction
consume
```

All consume fences and writes share one transaction.

---

## P0-R8D-5 — Task WAITING + new RUNNING branch requires Task RUNNING

R8-C permits source Task:

```text
RUNNING
or
WAITING
```

R8-D creates:

```text
new AgentExecution.state = RUNNING
```

The R8 aggregate-state rule is:

```text
any current execution CREATED/RUNNING
→ Task RUNNING
```

Therefore when source Task is WAITING, R8-D must atomically CAS:

```text
Task WAITING@expected_revision
→ RUNNING@(expected_revision + 1)

wait_reasons = []
```

inside the FORK transaction.

If Task is already RUNNING, its row is validated but not unnecessarily
revision-bumped.

---

## P0-R8D-6 — one TaskBudget revision change only

R8-D must not call standalone:

```text
reserve_branch_slot()
then
reserve_new_execution()
```

The FORK consumes one accounting unit.

One TaskBudget CAS performs:

```text
active_branches + 1
used_executions + 1
active_executions + 1
```

and, when the forked execution preserves delegated ancestry:

```text
active_parallel_agents + 1
```

all under one revision:

```text
N -> N + 1
```

---

## P0-R8D-7 — delegated FORK preserves delegation ancestry but creates a new Branch

Canonical FORK lineage remains separate from delegation lineage.

For source execution E1:

```text
E1.parent_execution_id = P1
```

the forked E2 uses:

```text
E2.parent_execution_id = P1
E2.retry_of_execution_id = NULL

E2.base_execution_id = E1
E2.base_checkpoint_id = C1

E2.branch_id = NEW branch B2

B2.parent_branch_id = E1.branch_id
B2.base_execution_id = E1
B2.base_checkpoint_id = C1
```

Forbidden:

```text
E2.parent_execution_id = E1
```

because that would encode FORK as delegation.

A delegated source causes E2 to remain an active delegated Agent execution, so
R8-D must enforce/charge `max_parallel_agents`.

The parent execution may belong to the source Branch.

That cross-branch parent reference is valid only because the fork preserves the
pre-existing delegation edge; it does not create a new delegation edge.

---

## P0-R8D-8 — source execution budget/transport affinity must not be reset blindly

The forked execution is a new execution, but it is cloned from a durable safe
point.

Initial R8-D freezes:

```text
request
    = JSON-safe copy of source_execution.request

remaining_active_budget_seconds
    = source_execution.remaining_active_budget_seconds

bound_client_id
    = NULL

bound_connection_id
    = NULL

wait_expires_at
    = NULL
```

Transport affinity is never inherited into the new Branch.

R8-D does not create branch runtime/context state beyond durable lineage and
the required request snapshot.

R8-E owns branch-base transcript/runtime context seeding.

---

# 4. R8-D0 — write-side checkpoint branch-lineage hardening

Before atomic FORK consume is enabled, change
`stage_waiting_checkpoint()` to require:

```text
checkpoint.execution_id == execution.id
checkpoint.execution_revision == source_revision + 1
checkpoint.session_id == execution.session_id
checkpoint.task_id == execution.task_id
checkpoint.branch_id == execution.branch_id
```

Exact equality applies even when values are NULL.

For task-scoped normalized R8 executions:

```text
task_id != NULL
→ branch_id MUST be non-NULL
```

Also harden `verify_committed_waiting_checkpoint()`:

```text
checkpoint.execution_id == execution.id
checkpoint.execution_revision == target revision
checkpoint.session_id == execution.session_id
checkpoint.task_id == execution.task_id
checkpoint.branch_id == execution.branch_id
```

This is write-side prevention only.

No historical checkpoint row is rewritten.

R8-C already fails closed on historical mismatches.

---

# 5. Durable fork admission schema

Add migration:

```text
14c_r8_fork_admission

down_revision:
14b_r8_root_branch_accounting
```

Add table:

```text
agent_task_fork_admissions
```

Columns:

```text
task_id                VARCHAR(255) NOT NULL
fork_request_id        VARCHAR(255) NOT NULL

plan_fingerprint       VARCHAR(64) NOT NULL

source_branch_id       VARCHAR(255) NOT NULL
source_execution_id    VARCHAR(255) NOT NULL
source_checkpoint_id   VARCHAR(255) NOT NULL

branch_id               VARCHAR(255) NOT NULL
execution_id            VARCHAR(255) NOT NULL

created_by              VARCHAR(255) NOT NULL
created_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

Primary key:

```text
(task_id, fork_request_id)
```

Foreign keys:

```text
task_id
→ agent_tasks.id
ON DELETE CASCADE

source_branch_id
→ agent_task_branches.branch_id
ON DELETE RESTRICT

source_execution_id
→ agent_executions.id
ON DELETE RESTRICT

source_checkpoint_id
→ agent_execution_checkpoints.checkpoint_id
ON DELETE RESTRICT

branch_id
→ agent_task_branches.branch_id
ON DELETE RESTRICT

execution_id
→ agent_executions.id
ON DELETE RESTRICT
```

Unique:

```text
branch_id
execution_id
```

The receipt is immutable.

It has:

```text
no state
no revision
no updated_at
```

because a visible row means the entire FORK transaction committed.

---

# 6. Why a dedicated receipt is required

Do not add a new TaskBudget reservation kind merely to represent
`fork_request_id`.

Existing reservations remain resource-accounting ledgers:

```text
BRANCH
NEW_EXECUTION
```

The fork admission receipt owns request-level idempotency and replay output.

This separation makes authority explicit:

```text
ForkAdmission
    = request dedupe + committed result identity

BRANCH reservation
    = branch capacity accounting identity

NEW_EXECUTION reservation
    = execution accounting identity
```

---

# 7. ForkAdmission result contract

Add immutable result conceptually:

```text
ForkAdmission:
    task_id
    fork_request_id
    plan_fingerprint

    branch_id
    branch_revision

    execution_id
    execution_revision

    task_revision
    task_budget_revision
```

Initial successful consume returns:

```text
branch_revision = 0
execution_revision = 1
```

`task_revision` is:

```text
expected_task_revision
when source Task was already RUNNING

expected_task_revision + 1
when source Task WAITING -> RUNNING was consumed
```

`task_budget_revision` is always:

```text
expected_task_budget_revision + 1
```

The durable receipt stores output identities, not mutable current revisions.

Replay may return current row revisions separately if needed, but must not
rewrite the receipt.

---

# 8. Public consume API

Conceptual service API:

```text
consume_fork_plan(plan: ForkPlan) -> ForkAdmission
```

Caller does not supply:

```text
new_branch_id
new_execution_id
```

R8-D allocates both server-side.

Reason:

- output identity is a durable server authority;
- same request replay is resolved by the admission receipt;
- callers cannot select/collide with existing Branch/Execution identities.

For one local consume call, candidate IDs are allocated once before the
internal conflict-retry loop.

If a later external retry allocates different candidates, an existing receipt
wins and the candidates are discarded.

---

# 9. In-memory plan integrity fence

Before opening/consuming writes require:

```text
fork_plan_fingerprint(plan)
==
plan.plan_fingerprint
```

Else reject:

```text
FORK_PLAN_FINGERPRINT_INVALID
```

R8-D never trusts a caller-mutated ForkPlan.

---

# 10. Idempotent replay path

Inside the consume UoW, first load:

```text
ForkAdmission(task_id, fork_request_id)
```

If found:

require:

```text
receipt.plan_fingerprint == plan.plan_fingerprint
receipt.source_branch_id == plan.source_branch_id
receipt.source_execution_id == plan.source_execution_id
receipt.source_checkpoint_id == plan.source_checkpoint_id
receipt.created_by == plan.target_user_id
```

Then require committed result rows still exist and match receipt:

```text
TaskBranch(receipt.branch_id)
AgentTaskBranchContext(receipt.branch_id)
AgentExecution(receipt.execution_id)
BRANCH reservation
NEW_EXECUTION reservation
```

Validate their immutable fork lineage.

If valid:

```text
return same branch_id + execution_id
zero counter changes
zero source revalidation
zero new writes
```

If the receipt exists but committed result rows are missing/conflicting:

```text
FORK_ADMISSION_CORRUPT
```

R8-D does not repair silently.

---

# 11. Fresh consume — exact in-transaction revalidation

If no receipt exists, revalidate every R8-C fence in the same UoW.

## Task

Require:

```text
task.id == plan.task_id
task.revision == plan.expected_task_revision
task.created_by == plan.target_user_id
task.session_id == plan.session_id
task.assigned_agent_id == plan.source_agent_id
task.status IN {RUNNING, WAITING}
```

## Source Branch

Require:

```text
branch.branch_id == plan.source_branch_id
branch.task_id == plan.task_id
branch.revision == plan.expected_branch_revision
branch.resolution_state == OPEN
branch.current_execution_id == plan.source_execution_id
```

## Source Execution

Require:

```text
execution.id == plan.source_execution_id
execution.revision == plan.expected_execution_revision
execution.task_id == plan.task_id
execution.branch_id == plan.source_branch_id
execution.session_id == plan.session_id
execution.agent_id == plan.source_agent_id
execution.state == WAITING
execution.current_checkpoint_id == plan.source_checkpoint_id
```

## Source Checkpoint

Require:

```text
checkpoint.checkpoint_id == plan.source_checkpoint_id
checkpoint.execution_id == plan.source_execution_id
checkpoint.execution_revision == plan.expected_execution_revision
checkpoint.session_id == plan.session_id
checkpoint.task_id == plan.task_id
checkpoint.branch_id == plan.source_branch_id
checkpoint.iteration == plan.checkpoint_iteration
checkpoint.transcript_snapshot IS NOT NULL
```

## Pending snapshots

Require:

```text
count == 0
```

## Live side effects

Re-read every source-execution CapabilityInvocation and every required
AgentToolResult using the same transaction.

Recompute:

```text
ForkSideEffectSnapshot tuple
side_effect_fingerprint
```

Require exact equality with the plan.

No:

```text
reconciliation
replay
PROVISIONAL promotion
network call
```

## Transcript

Strictly reconstruct the fork-safe transcript in the same UoW.

Require:

```text
base_transcript_fingerprint == plan.base_transcript_fingerprint
exact canonical transcript == plan.base_transcript
```

Every terminal side effect still appears exactly once.

---

# 12. TaskBudget exact revalidation

Load TaskBudget in the same UoW.

Require:

```text
budget.state == OPEN
budget.revision == plan.expected_task_budget_revision
budget.policy_fingerprint == plan.budget_policy_fingerprint
```

Capacity:

```text
active_branches < max_active_branches
used_executions < max_total_executions
active_executions < max_active_executions
```

If the source execution is delegated:

```text
source_execution.parent_execution_id != NULL
```

also require:

```text
active_parallel_agents < max_parallel_agents
```

No capacity is reserved before this transaction.

---

# 13. Source delegation ancestry validation

R8-D must walk the source execution's existing `parent_execution_id` ancestry
inside the consume transaction.

Require:

```text
no cycle

every ancestor exists

every ancestor belongs to the same Task

source delegation lineage is internally consistent
```

R8-D creates no new delegation edge.

The new forked execution copies:

```text
parent_execution_id = source_execution.parent_execution_id
```

and therefore has the same delegation depth as the source execution.

Do not require the copied parent to belong to the NEW branch.

It predates the branch fork.

---

# 14. New Branch contract

New TaskBranch:

```text
branch_id
    = server-allocated new identity

task_id
    = plan.task_id

parent_branch_id
    = plan.source_branch_id

base_execution_id
    = plan.source_execution_id

base_checkpoint_id
    = plan.source_checkpoint_id

current_execution_id
    = new execution_id

resolution_state
    = OPEN

revision
    = 0

created_by
    = plan.target_user_id

reason
    = R8_FORK
```

The user-facing free-form `reason` from the future API is not part of R8-D.

If later introduced as durable semantic metadata, it must be bound into the
semantic fingerprint first.

---

# 15. New BranchContext contract

Insert exactly one:

```text
branch_id = new branch
revision = 0
overlay_messages = plan.overlay_messages
```

R8-D persists the overlay.

R8-D does not consume it in context assembly.

R8-E owns:

```text
base checkpoint transcript load
+
BranchContext overlay
+
runtime/context seed
```

Base transcript is not copied into BranchContext.

---

# 16. New AgentExecution contract

Insert:

```text
id
    = server-allocated new execution identity

session_id
    = plan.session_id

agent_id
    = plan.source_agent_id

task_id
    = plan.task_id

branch_id
    = new branch_id

parent_execution_id
    = source_execution.parent_execution_id

retry_of_execution_id
    = NULL

base_execution_id
    = plan.source_execution_id

base_checkpoint_id
    = plan.source_checkpoint_id

correlation_id
    = plan.correlation_id

state
    = RUNNING

wait_reason
    = NULL

revision
    = 1

current_checkpoint_id
    = NULL

bound_client_id
    = NULL

bound_connection_id
    = NULL

remaining_active_budget_seconds
    = source_execution.remaining_active_budget_seconds

wait_expires_at
    = NULL

request
    = JSON-safe copy of source_execution.request

result
    = NULL

error
    = NULL

started_at
    = now UTC
```

R8-D does not persist the source checkpoint transcript into:

```text
AgentExecution.transcript
AgentExecution.context_state
R7 resume_transcript
```

R8-E owns branch-runtime context seeding.

---

# 17. Task status CAS

If Task is already:

```text
RUNNING@plan.expected_task_revision
```

validate only; do not bump revision.

If Task is:

```text
WAITING@plan.expected_task_revision
```

CAS in the same UoW:

```text
WAITING
→ RUNNING

wait_reasons
→ []

revision
→ expected + 1
```

If the Task changed concurrently:

```text
FORK_TASK_CONFLICT
```

and roll back everything.

Terminal Task states always reject.

---

# 18. Single TaskBudget CAS

Exactly one TaskBudget CAS is allowed.

Base mutation:

```text
active_branches
    + 1

used_executions
    + 1

active_executions
    + 1
```

If:

```text
source_execution.parent_execution_id != NULL
```

also:

```text
active_parallel_agents
    + 1
```

No other TaskBudget revision mutation occurs during FORK consume.

The CAS predicate includes:

```text
task_id
revision == plan.expected_task_budget_revision
```

The state/capacity checks occur before the CAS and database constraints remain
the final backstop.

---

# 19. BRANCH reservation contract

Use the existing kind:

```text
BRANCH
```

Key:

```text
reservation_key = new_branch_id
```

Canonical payload:

```text
{
  "fork_request_id": plan.fork_request_id,
  "plan_fingerprint": plan.plan_fingerprint,
  "parent_branch_id": plan.source_branch_id,
  "base_execution_id": plan.source_execution_id,
  "base_checkpoint_id": plan.source_checkpoint_id
}
```

Persist the existing canonical reservation fingerprint.

This reservation accounts one durable Branch slot.

It is not the fork-request replay receipt.

---

# 20. NEW_EXECUTION reservation contract

Use the existing kind:

```text
NEW_EXECUTION
```

Key:

```text
reservation_key = new_execution_id
```

Canonical payload:

```text
{
  "fork_request_id": plan.fork_request_id,
  "plan_fingerprint": plan.plan_fingerprint,
  "execution_id": new_execution_id,
  "execution_values": <canonical JSON-safe new execution values>,
  "delegated": source_execution.parent_execution_id != NULL
}
```

No separate NEW_EXECUTION transaction is allowed.

---

# 21. Atomic transaction ordering

For a fresh request:

```text
0. verify in-memory plan fingerprint

BEGIN UoW

1. load fork admission receipt
   if existing:
       semantic replay or semantic conflict
       STOP

2. revalidate Task/Branch/Execution/Checkpoint

3. require zero pending checkpoint rows

4. recompute side-effect snapshot/fingerprint

5. strictly recompute committed base transcript/fingerprint

6. revalidate TaskBudget revision/policy/capacity

7. validate source delegation ancestry

8. allocate/retain candidate branch_id + execution_id

9. if Task WAITING:
       CAS Task -> RUNNING

10. single TaskBudget CAS

11. INSERT AgentExecution RUNNING@1
    # execution.branch_id remains a soft reference,
    # so this can precede TaskBranch insert

12. INSERT TaskBranch
    # current_execution_id FK now resolves

13. INSERT TaskBranchContext

14. INSERT BRANCH reservation

15. INSERT NEW_EXECUTION reservation

16. INSERT immutable ForkAdmission receipt
    # output FKs now resolve

COMMIT
```

Failure at any point rolls back:

```text
Task CAS
TaskBudget CAS
Execution
Branch
BranchContext
both reservations
receipt
```

No intermediate authority is visible.

---

# 22. Why receipt insert is last

The receipt means:

```text
the FORK is fully committed semantically
```

Therefore it is staged after all resource/output rows in the transaction.

Because commit is atomic, external readers never observe the intermediate
ordering.

If receipt uniqueness loses a same-request race, the whole losing transaction
rolls back and retries by reading the winner receipt.

---

# 23. Same-request concurrency

Two workers receive:

```text
same task_id
same fork_request_id
same plan_fingerprint
```

Both may allocate different candidate IDs.

Exactly one transaction may commit.

The loser may fail first on:

```text
TaskBudget CAS
fork receipt unique key
branch/execution uniqueness
database lock serialization
```

After rollback/retry:

```text
load receipt
→ verify same plan fingerprint
→ return winner branch/execution
```

Final durable state:

```text
1 ForkAdmission
1 new Branch
1 BranchContext
1 new Execution
1 BRANCH reservation
1 NEW_EXECUTION reservation
1 TaskBudget charge
```

---

# 24. Different-request final-capacity race

Two valid plans use different:

```text
fork_request_id
```

when only one branch/execution capacity slot remains.

Both read the same expected TaskBudget revision.

Exactly one single TaskBudget CAS wins.

Loser retries and finds:

```text
no matching receipt
+
new budget revision/capacity
```

then returns:

```text
FORK_BRANCH_CAPACITY_UNAVAILABLE
or
FORK_EXECUTION_CAPACITY_UNAVAILABLE
or
FORK_PLAN_STALE
```

depending on which frozen fence changed first.

It must not create partial output rows.

---

# 25. Crash semantics

## Crash before COMMIT

Database rollback leaves:

```text
no receipt
no branch
no branch context
no execution
no reservations
no counter charge
no Task status change
```

Retry performs a fresh consume.

## COMMIT succeeds, response is lost

Receipt exists.

Retry:

```text
same request + same plan
→ semantic replay
→ same branch/execution
```

No source revalidation or second budget charge occurs.

## Receipt/result corruption

If receipt exists but its Branch/Execution/ledger rows are missing or have
conflicting immutable lineage:

```text
FORK_ADMISSION_CORRUPT
```

No auto-repair in R8-D.

---

# 26. Source immutability

Fresh FORK consume must not mutate:

```text
source TaskBranch
source AgentExecution
source checkpoint
source checkpoint pending rows
source CapabilityInvocations
source AgentToolResults
source transcript
source ResumeClaims
```

Only shared durable authorities that may change are:

```text
AgentTask
    WAITING -> RUNNING only when required

TaskBudget
    one accounting CAS
```

The source Branch revision remains unchanged.

---

# 27. Stable R8-D errors

Add stable consume errors conceptually:

```text
ForkConsumeError
ForkConsumeRejected
ForkConsumeDeferred
ForkConsumeConflict
```

Required codes:

```text
FORK_PLAN_FINGERPRINT_INVALID

FORK_REQUEST_SEMANTIC_CONFLICT
FORK_ADMISSION_CORRUPT

FORK_TASK_CONFLICT
FORK_BRANCH_CONFLICT
FORK_EXECUTION_CONFLICT
FORK_CHECKPOINT_CONFLICT

FORK_PENDING_INVOCATIONS
FORK_SIDE_EFFECT_CHANGED
FORK_TRANSCRIPT_CHANGED

FORK_TASK_BUDGET_REQUIRED
FORK_TASK_BUDGET_CLOSED
FORK_TASK_BUDGET_STALE
FORK_TASK_BUDGET_POLICY_CHANGED

FORK_BRANCH_CAPACITY_UNAVAILABLE
FORK_EXECUTION_CAPACITY_UNAVAILABLE
FORK_PARALLEL_AGENT_CAPACITY_UNAVAILABLE

FORK_OUTPUT_ID_CONFLICT
FORK_CONSUME_CONFLICT
```

R8-D performs bounded local database conflict retries only.

It performs no network retry/reconciliation.

---

# 28. Exact implementation blast radius

Expected R8-D implementation scope:

```text
ADD
se/src/infrastructure/storage/migrations/sql/versions/
    14c_r8_fork_admission.py

ADD
se/src/infrastructure/storage/models/sql/agent/fork_admission.py

UPDATE
se/src/infrastructure/storage/models/sql/agent/__init__.py

UPDATE
se/src/runtimes/agent/contracts/fork.py
    # ForkAdmission result only

UPDATE
se/src/infrastructure/storage/repositories/agent.py
    # save/get fork admission
    # read-only helpers only as required by consume transaction

UPDATE
se/src/runtimes/agent/waiting_checkpoint.py
    # exact checkpoint branch-lineage hardening

UPDATE
se/src/runtimes/agent/fork_planning.py
    # transaction-scoped shared verification helpers only

UPDATE
se/src/runtimes/agent/task_budget.py
    # atomic consume_fork_plan transaction
```

Potential persistence changes are allowed only for shared pure/read helpers
needed by transaction-scoped verification.

---

# 29. Explicit non-scope files

R8-D must not wire:

```text
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/coordinator.py
se/src/main.py

se/src/transport/gateway/api/v1/multi_agent_router.py
se/src/transport/gateway/api/v1/events_router.py

ContextBuilder / ContextAssembler
CL
```

No runner is started by R8-D.

No public FORK API is exposed by R8-D.

---

# 30. Mandatory regression matrix

## Checkpoint write-side hardening

Prove:

```text
matching task/branch/session checkpoint commits

checkpoint.branch_id != execution.branch_id
→ rollback/reject

task-scoped execution with branch_id NULL
→ reject

committed checkpoint verification checks task/branch/session
```

## Admission migration

Prove:

```text
14b -> 14c -> 14b
one Alembic head
receipt table exact PK/FKs/unique constraints
no historical synthetic fork receipts
```

## Fresh consume

Prove:

```text
one new Branch
one BranchContext
one RUNNING Execution
one BRANCH reservation
one NEW_EXECUTION reservation
one ForkAdmission receipt

active_branches +1
used_executions +1
active_executions +1

TaskBudget revision +1 exactly once
```

## Task WAITING source

Prove:

```text
Task WAITING
→ same transaction
→ Task RUNNING
→ wait_reasons []
→ Task revision +1
```

No Task mutation when it was already RUNNING.

## Delegated source

Prove:

```text
source.parent_execution_id = P

fork.parent_execution_id = P
fork.parent_execution_id != source_execution_id

new Branch differs from source Branch

active_parallel_agents +1
max_parallel_agents enforced
```

## Lineage

Prove:

```text
B2.parent_branch_id = B1
B2.base_execution_id = E1
B2.base_checkpoint_id = C1

E2.base_execution_id = E1
E2.base_checkpoint_id = C1
E2.retry_of_execution_id = NULL
```

## Full R8-C revalidation

Change each after planning but before consume:

```text
Task revision/status
Branch revision/state/current_execution_id
Execution revision/state/checkpoint pointer
checkpoint task/branch/session/iteration
pending checkpoint rows
CapabilityInvocation revision/outcome
COMMITTED tool result content
base transcript
TaskBudget revision
TaskBudget policy
```

Each must fail closed before any durable FORK write.

## Same request replay

```text
same task_id
same fork_request_id
same plan_fingerprint
→ same branch/execution
→ no second budget charge
```

Test again after source execution is no longer forkable.

Replay must still return the committed result.

## Same request semantic mismatch

```text
same request ID
different plan fingerprint
→ FORK_REQUEST_SEMANTIC_CONFLICT
→ zero writes
```

## Same request concurrent race

Use real SQL concurrency.

Prove:

```text
one receipt
one Branch
one Execution
one budget charge
all callers observe same winner IDs
```

## Different requests at final capacity

Prove:

```text
one TaskBudget CAS winner
one Branch
one Execution
one receipt
loser has zero partial rows
```

## Rollback injection

Inject failure after each conceptual write:

```text
Task CAS
TaskBudget CAS
Execution insert
Branch insert
BranchContext insert
BRANCH ledger insert
NEW_EXECUTION ledger insert
ForkAdmission insert
```

After failure:

```text
all new rows absent
Task/TaskBudget restored
source rows unchanged
```

## Response-loss replay

Simulate committed transaction followed by caller-side exception.

Retry same request and prove semantic replay.

## Source immutability

Snapshot before/after:

```text
source Branch revision/current_execution_id
source Execution revision/state/checkpoint
source checkpoint
pending rows
CapabilityInvocations
AgentToolResults
ResumeClaims
```

All remain unchanged.

---

# 31. R8-D invariants

```text
R8D-I01
One committed fork_request_id maps to exactly one branch/execution pair.

R8D-I02
ForkAdmission is immutable committed-result authority.

R8D-I03
Same request + same plan replays without source revalidation.

R8D-I04
Same request + different plan fails closed.

R8D-I05
Fresh consume revalidates all R8-C fences in one write transaction.

R8D-I06
Checkpoint branch identity is enforced at write time before FORK consume.

R8D-I07
FORK creates a new Branch and a new Execution.

R8D-I08
FORK never reuses the source execution ID.

R8D-I09
FORK lineage uses parent_branch/base_execution/base_checkpoint.

R8D-I10
parent_execution_id remains delegation lineage only.

R8D-I11
retry_of_execution_id is NULL for FORK.

R8D-I12
Source Branch/Execution/Checkpoint remain immutable.

R8D-I13
BranchContext stores only fork-local overlay.

R8D-I14
TaskBudget changes exactly one revision per fresh FORK.

R8D-I15
BRANCH and NEW_EXECUTION ledgers commit with the FORK outputs.

R8D-I16
Delegated FORK consumes active_parallel_agents capacity.

R8D-I17
Task WAITING becomes RUNNING atomically when a new RUNNING branch is created.

R8D-I18
No transport affinity is inherited by the new Branch execution.

R8D-I19
No R7 ResumeClaim is reused as FORK authority.

R8D-I20
R8-D creates no runner and exposes no public FORK endpoint.
```

---

# 32. Stop conditions

Stop implementation and return to contract review if R8-D would require:

```text
reuse source execution_id

encode fork as parent_execution_id = source execution

create Branch and Execution in separate transactions

reserve branch/execution capacity before the atomic consume transaction

reconcile/replay a remote invocation

promote PROVISIONAL tool result

mutate source checkpoint

mutate source Branch.current_execution_id

store base transcript in R7 resume_transcript

let same fork_request_id create a second output pair

use TaskBudgetReservation as the only request replay authority

start AgentRuntime before the consume transaction commits
```

---

# 33. Frozen implementation order

When implementation is authorized:

```text
R8-D0
checkpoint write-side branch-lineage hardening

R8-D1
14c fork-admission migration + ORM/repository

R8-D2
ForkAdmission contract + plan integrity fence

R8-D3
transaction-scoped R8-C revalidator

R8-D4
atomic Task/TaskBudget/Execution/Branch/Context/ledger/receipt consume

R8-D5
same-request semantic replay + conflict/race handling

R8-D6
rollback/race/idempotency/source-immutability regression matrix

R8-D7
full CI + completion document
```

R8-E context isolation/runtime seeding must not be included in R8-D.

---

# 34. Final freeze

```text
R8-A
CLOSED / GREEN

R8-B
CLOSED / GREEN

R8-C
CLOSED / GREEN

R8-D
CONTRACT FROZEN
NOT IMPLEMENTED

R8-E+
NOT IMPLEMENTED

F5
PAUSED UNTIL R14+
```
