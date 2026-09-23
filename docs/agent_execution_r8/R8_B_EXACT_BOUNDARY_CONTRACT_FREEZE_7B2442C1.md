# R8-B — EXACT BOUNDARY AUDIT & CONTRACT FREEZE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Audited HEAD:** `7b2442c1c9608575c7452dc00a083f32cebfbee7`  
**R8-A code baseline:** `bb20cd8a7a52792aab40d187c0a528578f9da5ac`  
**Roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Scope:** R8-B only — root-branch accounting + atomic first root execution admission  
**Status:** **CONTRACT FROZEN / NOT IMPLEMENTED**

---

# 1. R8-B exact boundary

R8-A established durable normalized representation:

```text
Task
  1 -> N TaskBranch

TaskBranch
  1 -> N AgentExecution
```

R8-B now establishes the first runtime/accounting authority for the single root branch that exists before general FORK is enabled.

R8-B owns:

```text
historical root-branch TaskBudget reconciliation
root branch reservation accounting
atomic first root execution admission
root branch.current_execution_id establishment
root branch_id propagation into AgentExecutionContext after durable admission
delegated child verification against the already-owned branch
```

R8-B does not own:

```text
general FORK
fork checkpoint planning
new sibling branch creation
branch-local transcript isolation
retry as new execution
ADOPT / SUPERSEDE / DISCARD / AGGREGATE
multi-branch Task completion semantics
execution-scoped background runner conversion
HTTP branch/fork APIs
```

Those remain R8-C+ / R9.

---

# 2. Baseline facts confirmed on HEAD 7b2442c1

## 2.1 R8-A normalized persistence exists

Tables:

```text
agent_task_branches
agent_task_branch_contexts
```

TaskBranch already owns:

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
```

BranchContext already owns:

```text
branch_id
revision
overlay_messages
```

## 2.2 R8-A deliberately did not mutate TaskBudget

R8-A migration does not change:

```text
TaskBudget.active_branches
TaskBudget reservations
```

Therefore historical tasks normalized by R8-A may now have:

```text
one TaskBranch row
but
active_branches == 0
and
no BRANCH reservation
```

This representation is safe for R8-A, but it is not sufficient before R8-D FORK starts consuming `max_active_branches`.

## 2.3 R5 TaskBudget already has branch reservation vocabulary

Existing reservation kinds:

```text
BRANCH
RELEASE_BRANCH
```

Existing `reserve_branch_slot()` semantics:

```text
TaskBudget OPEN
active_branches < max_active_branches
→ active_branches + 1
→ BRANCH reservation
```

Existing reservation primary identity:

```text
(task_id, kind, reservation_key)
```

Existing reservation fingerprint:

```text
sha256(
  canonical-json({
    "kind": kind,
    "reservation_key": key,
    "payload": payload
  })
)
```

with:

```text
sort_keys = true
separators = (",", ":")
default = str
```

## 2.4 NEW_EXECUTION is already atomic

Current `reserve_new_execution()` atomically commits:

```text
TaskBudget CAS:
    used_executions + 1
    active_executions + 1
    optional active_parallel_agents + 1

AgentExecution INSERT

NEW_EXECUTION reservation
```

in one UoW.

But it does not create/account TaskBranch.

## 2.5 First execution is persisted inside AgentRuntime

Current path:

```text
MultiAgentCoordinator
    Task ASSIGNED -> RUNNING CAS
        ↓
executor
        ↓
AgentRuntime._begin_durable_execution()
        ↓
TaskBudgetService.start_task_scoped_execution()
        ↓
reserve_new_execution()
```

This means R8-B must integrate at the TaskBudget/AgentRuntime admission boundary.

The coordinator is not the durable AgentExecution insert authority.

---

# 3. P0 findings

## P0-R8B-1 — R8-A historical branch rows are not TaskBudget-accounted

A normalized historical root branch can exist while:

```text
active_branches = 0
```

If R8-D later evaluates:

```text
active_branches < max_active_branches
```

without reconciling the historical root, it can permit:

```text
root branch
+
max_active_branches additional forks
```

instead of counting root as one slot.

R8-B must normalize historical accounting before FORK exists.

---

## P0-R8B-2 — root admission cannot be implemented by sequential existing calls

This is forbidden:

```text
reserve_branch_slot()
COMMIT

reserve_new_execution()
COMMIT

create TaskBranch
COMMIT
```

Crash/race can produce:

```text
branch slot without branch
branch without execution
execution without branch reservation
double active_branches accounting
```

R8-B requires one transaction.

---

## P0-R8B-3 — first runtime context currently starts with branch_id=NULL

`main.py` creates the root AgentExecutionContext without a branch ID.

`AgentRuntime._begin_durable_execution()` currently copies:

```text
context.branch_id
→ AgentExecution.branch_id
```

Therefore simply inserting a TaskBranch in SQL is insufficient.

After root admission wins, AgentRuntime must receive the authoritative durable `branch_id` and assign:

```text
context.branch_id = admitted.branch_id
```

before `_execute_loop()`.

This is safe because:

```text
AgentRuntime.execute()
→ durable begin
→ _execute_loop()
→ EXECUTION_STARTED event
→ tool/inference/checkpoint work
```

So branch identity can become authoritative before execution events/tool lineage are emitted.

---

## P0-R8B-4 — one root branch must consume exactly one BRANCH reservation

The first root execution currently consumes:

```text
NEW_EXECUTION
```

only.

R8-B must atomically consume:

```text
BRANCH       key = branch_id
NEW_EXECUTION key = execution_id
```

The same root branch must never consume BRANCH twice.

---

## P0-R8B-5 — delegated child Agent execution must not consume another branch slot

R5 child Agent executions inherit:

```text
task_id
branch_id
parent_execution_id
```

from their caller.

They are new executions in the same Branch.

Therefore:

```text
delegated child
→ NEW_EXECUTION accounting
→ no BRANCH increment
→ no TaskBranch.current_execution_id change
```

R8-B must explicitly preserve this rule.

---

## P0-R8B-6 — a new top-level execution on an already-existing root Branch is not R8-B

Once a root branch already has execution history, a second top-level execution in that same Branch is:

```text
RETRY / new attempt
```

which belongs to R9.

Therefore R8-B must fail closed if asked to admit a new top-level execution for a Task whose root Branch already exists from prior runtime execution.

Historical branch normalization is not permission to mint another top-level execution.

---

# 4. Existing R5 Task start CAS remains authoritative

R5-E explicitly froze:

```text
AgentTask ASSIGNED
    CAS
AgentTask RUNNING
```

before invoking AgentRuntime.

R8-B does not fold the AgentTask start CAS into the branch/execution transaction.

Reason:

- R5-E already owns cancellation-vs-start Task authority;
- R8-B owns branch/execution admission after the Task RUNNING winner exists;
- changing Task start semantics would expand R8-B into unrelated R5/R12 lifecycle recovery.

R8-B therefore requires:

```text
Task.status == RUNNING
TaskBudget.state == OPEN
```

for a new first root execution admission.

The existing crash window:

```text
Task RUNNING
→ process loss before AgentRuntime admission
```

is not redefined as FORK semantics.

General stale-process recovery remains a later roadmap concern, especially R12.

---

# 5. Canonical root branch identity

New tasks that have no R8-A historical branch use a deterministic root ID:

```text
r8_root_<sha256(task_id)[0:40]>
```

Properties:

```text
stable across local retry
stable across concurrent first-start attempts
does not depend on execution_id
does not depend on connection_id
does not depend on process identity
```

Historical R8-A branches keep their existing IDs:

```text
r8_legacy_<...>
or
pre-existing R3 branch_id
```

R8-B never renames them.

---

# 6. Historical root accounting normalization

R8-B adds a data-only migration:

```text
revision:
14b_r8_root_branch_accounting

down_revision:
14a_r8_task_branch
```

No new tables or columns are required.

---

# 7. Historical accounting invariant

For every Task that has a TaskBudget:

```text
expected active_branches
=
number of TaskBranch rows whose resolution_state == OPEN
```

At R8-B entry, R8-A guarantees this is at most one branch per historical Task.

Therefore expected historical values are:

```text
0 or 1
```

A Task with no TaskBudget remains under the existing R5 legacy quarantine.

R8-B must not fabricate a TaskBudget whose historical usage cannot be reconstructed.

---

# 8. Historical migration cases

## HB-A — no TaskBranch, TaskBudget exists

Required:

```text
active_branches == 0
no BRANCH reservation
```

If not, fail closed.

No row is created.

---

## HB-B — one OPEN TaskBranch, TaskBudget exists, no accounting yet

Expected pre-state:

```text
active_branches == 0
no BRANCH reservation for branch_id
```

Migration atomically writes:

```text
active_branches = 1
TaskBudget.revision += 1

BRANCH reservation:
    task_id = branch.task_id
    kind = BRANCH
    reservation_key = branch.branch_id
    payload = {}
    payload_fingerprint = canonical R5 fingerprint
```

No execution counter changes.

---

## HB-C — one OPEN TaskBranch already correctly accounted

Allowed idempotent historical state:

```text
active_branches == 1

matching BRANCH reservation exists
key = branch_id
fingerprint matches payload {}
```

Migration leaves it unchanged.

---

## HB-D — CANCELLED/non-OPEN historical Branch

R8-A may have:

```text
resolution_state = CANCELLED
```

For such a Task:

```text
expected active_branches = 0
```

R8-B does not invent a historical BRANCH/RELEASE_BRANCH lifecycle that was never durably recorded.

No BRANCH reservation is synthesized for a non-OPEN historical branch.

---

## HB-E — branch exists but TaskBudget is missing

Preserve existing R5 behavior:

```text
no fake budget
no BRANCH reservation
no counter reconstruction
```

Future runtime access remains fail-closed through:

```text
TASK_BUDGET_LEGACY_UNINITIALIZED
```

---

# 9. Historical migration fail-closed cases

Migration must abort if any of these are found:

```text
Task has >1 TaskBranch before R8-D exists

Task has zero OPEN branches
but active_branches != 0

Task has one OPEN branch
but active_branches not in {0, 1}

active_branches == 0
but matching BRANCH reservation already exists

active_branches == 1
but matching BRANCH reservation is missing

BRANCH reservation exists with wrong reservation_key

BRANCH reservation exists with wrong payload_fingerprint

BRANCH reservation exists for a Task with no normalized OPEN branch

TaskBranch.task_id differs from reservation Task
```

R8-B migration never silently rewrites an unknown ledger.

---

# 10. BRANCH reservation identity

Canonical root reservation:

```text
kind:
BRANCH

reservation_key:
branch_id

payload:
{}
```

Fingerprint must be byte-for-byte compatible with existing:

```text
_reservation_fingerprint(
    TaskBudgetReservationKind.BRANCH,
    branch_id,
    {},
)
```

This preserves compatibility with the existing R5 reservation table and future R8-D sibling branch admission.

---

# 11. Root execution admission result

Introduce an explicit immutable result such as:

```text
RootExecutionAdmission
    branch_id
    branch_revision
    execution_revision
```

Initial successful root admission returns:

```text
branch_revision = 0
execution_revision = 1
```

This is not a FORK plan/result.

It is only durable first-root admission authority.

---

# 12. Atomic first root execution contract

Conceptual API:

```text
start_root_task_scoped_execution(
    task_id,
    execution_id,
    execution_values,
)
→ RootExecutionAdmission
```

Caller must not supply branch authority.

Required input shape:

```text
execution_values.task_id == task_id
execution_values.id == execution_id

parent_execution_id == NULL
retry_of_execution_id == NULL
base_execution_id == NULL
base_checkpoint_id == NULL

branch_id == NULL
state == RUNNING
revision == 1
```

R8-B computes the root branch ID.

---

# 13. Atomic root transaction

One UoW must perform all of:

```text
load AgentTask
require Task.status == RUNNING

load TaskBudget
require TaskBudget.state == OPEN

require no prior Task execution history
require no normalized TaskBranch

compute deterministic root branch_id

verify no BRANCH reservation
verify no NEW_EXECUTION reservation for conflicting semantic identity

check:
    active_branches < max_active_branches
    used_executions < max_total_executions
    active_executions < max_active_executions

CAS TaskBudget once:
    active_branches + 1
    used_executions + 1
    active_executions + 1

persist AgentExecution:
    branch_id = root branch
    state = RUNNING
    revision = 1

persist TaskBranch:
    branch_id = root branch
    task_id = task
    parent_branch_id = NULL
    base_execution_id = NULL
    base_checkpoint_id = NULL
    current_execution_id = execution_id
    resolution_state = OPEN
    revision = 0
    created_by = AgentTask.created_by
    reason = R8_ROOT_FIRST_EXECUTION

persist TaskBranchContext:
    branch_id = root branch
    revision = 0
    overlay_messages = []

persist BRANCH reservation:
    key = branch_id
    payload = {}

persist NEW_EXECUTION reservation:
    key = execution_id
    payload = existing R5 NEW_EXECUTION semantic payload
    but with the admitted branch_id inside execution_values

COMMIT
```

No partial state may become visible.

---

# 14. SQL insertion ordering

Because:

```text
TaskBranch.current_execution_id
→ AgentExecution.id
```

but:

```text
AgentExecution.branch_id
```

is intentionally still a soft reference after R8-A, safe insertion order is:

```text
TaskBudget CAS
→ AgentExecution INSERT with branch_id
→ TaskBranch INSERT with current_execution_id
→ BranchContext INSERT
→ BRANCH reservation
→ NEW_EXECUTION reservation
→ COMMIT
```

All remain inside one transaction.

No intermediate row is externally observable.

---

# 15. Root idempotency contract

## Same execution_id + same semantic payload retry

If the transaction previously committed but caller lost the response:

```text
BRANCH reservation exists
NEW_EXECUTION reservation exists
TaskBranch exists
AgentExecution exists
```

R8-B verifies all semantic fingerprints/identity and returns the same:

```text
branch_id
execution_revision
```

No counter increments.

---

## Concurrent different execution_ids

Two callers may race first root admission.

They target the same deterministic:

```text
branch_id
BRANCH reservation key
```

Exactly one may win.

Loser must observe:

```text
root already admitted by another execution
```

and fail with a durable conflict.

Loser must not:

```text
create second top-level execution
consume second NEW_EXECUTION slot
consume second branch slot
replace current_execution_id
```

---

# 16. TaskBudget CAS rule

Root admission modifies one TaskBudget revision exactly once.

Forbidden:

```text
CAS active_branches +1
then
CAS used_executions/active_executions +1
```

Required single CAS:

```text
revision N
→
revision N+1

active_branches     + 1
used_executions     + 1
active_executions   + 1
```

This is the accounting unit of first-root admission.

---

# 17. Existing reserve_branch_slot() contract

Existing:

```text
reserve_branch_slot()
release_branch_slot()
```

remain valid low-level R5 primitives.

But R8-B root admission must not call them as standalone transactions.

They may later be reused internally only if a caller supplies the same UoW/transaction boundary.

The public semantic root admission authority is the combined R8-B transaction.

---

# 18. Delegated child execution contract

A delegated child is:

```text
same Task
same Branch
new AgentExecution
parent_execution_id != NULL
```

R8-B must require:

```text
execution_values.branch_id IS NOT NULL

TaskBranch(branch_id) exists
TaskBranch.task_id == task_id
TaskBranch.resolution_state == OPEN

parent AgentExecution exists
parent.task_id == task_id
parent.branch_id == branch_id
```

Then existing NEW_EXECUTION accounting applies:

```text
used_executions +1
active_executions +1
active_parallel_agents +1
```

But:

```text
active_branches unchanged
TaskBranch.current_execution_id unchanged
no BRANCH reservation
```

R8-B does not change delegation ancestry semantics.

---

# 19. Top-level branch-aware admission fail-closed rules

After R8-B:

```text
parent_execution_id == NULL
AND existing TaskBranch exists
AND execution_id is new
```

must fail closed.

It is not first-root admission anymore.

It may represent:

```text
same-branch RETRY
or
future FORK-derived execution
```

Both are outside R8-B.

Error should clearly point to a later branch lifecycle boundary instead of silently treating it as first execution.

---

# 20. AgentRuntime integration

Only one narrow R8-B runtime integration is allowed.

Current root path starts with:

```text
context.branch_id == NULL
```

For a new task-scoped top-level execution:

```text
AgentRuntime._begin_durable_execution()
→ start_root_task_scoped_execution()
→ admission.branch_id
→ context.branch_id = admission.branch_id
→ return execution_revision
```

This assignment must happen before:

```text
_execute_loop()
EXECUTION_STARTED
context build
capability invocation
checkpoint creation
```

so every downstream lineage surface sees the durable branch.

---

# 21. Existing R7 resume path is not root admission

If `AgentExecution` already exists in durable storage:

```text
AgentRuntime._begin_durable_execution()
→ existing WAITING path
```

R8-B must not create/reserve a Branch during R7 resume.

The existing execution already owns:

```text
task_id
branch_id
```

R8-B migration is responsible for historical branch budget accounting before runtime.

RESUME remains:

```text
same execution
same branch
no BRANCH increment
```

---

# 22. Historical current_execution_id

R8-A already established:

```text
current_execution_id
only when exactly one top-level historical execution is provable
```

R8-B must not guess missing historical branch heads.

If a historical branch has:

```text
current_execution_id == NULL
```

because multiple top-level executions existed before normalized Branch authority, it remains unresolved.

R8-B may account the branch slot, but may not select a winner.

R9 resolution/retry policy must not inherit a fabricated branch head.

---

# 23. Expected implementation blast radius

R8-B implementation should be limited to:

```text
ADD
se/src/infrastructure/storage/migrations/sql/versions/14b_r8_root_branch_accounting.py

UPDATE
se/src/runtimes/agent/task_budget.py

UPDATE
se/src/runtimes/agent/runtime.py
```

Repository changes are optional and allowed only if a small transaction helper is necessary:

```text
OPTIONAL UPDATE
se/src/infrastructure/storage/repositories/agent.py
```

Expected new tests:

```text
ADD
se/tests/integration/test_r8_b_root_branch_accounting_migration.py

ADD
se/tests/integration/test_r8_b_atomic_root_admission.py

ADD
se/tests/architecture/test_r8_b_runtime_branch_authority.py
```

Existing R5/R7 tests will require expectation updates where first root admission now consumes a branch slot.

---

# 24. Explicit files that R8-B must not modify

Do not modify:

```text
se/src/runtimes/agent/coordinator.py
se/src/main.py
se/src/runtimes/agent/contracts/context.py
se/src/runtimes/agent/adapters/context.py
se/src/runtimes/agent/fork_planning.py
se/src/transport/gateway/api/v1/multi_agent_router.py
CL
R7 ResumeClaim/PendingResumeTicket/events_router
```

No FORK endpoint or branch context consumption belongs in R8-B.

---

# 25. R8-B mandatory regression matrix

## Historical migration

Prove:

```text
14a -> 14b -> 14a

one Alembic head

no branch:
    active_branches remains 0

one OPEN historical branch:
    0 -> 1 exactly once
    BRANCH reservation created
    TaskBudget revision +1

already-correct historical accounting:
    idempotent/no mutation

CANCELLED branch:
    active_branches remains 0
    no fake lifecycle ledger

branch with no TaskBudget:
    no fake TaskBudget

counter/reservation disagreement:
    migration fails closed

wrong BRANCH fingerprint:
    migration fails closed
```

## First root admission

Prove:

```text
Task RUNNING + pristine budget
→ one root branch
→ one BranchContext
→ one AgentExecution
→ one BRANCH reservation
→ one NEW_EXECUTION reservation
→ active_branches == 1
→ used_executions == 1
→ active_executions == 1
```

## Atomic rollback

Inject failure after each conceptual write:

```text
budget CAS
execution insert
branch insert
context insert
BRANCH ledger insert
NEW_EXECUTION ledger insert
```

After rollback:

```text
no counter leak
no branch leak
no execution leak
no partial reservation
```

## Retry/idempotency

```text
same execution retry
→ same branch
→ same counters
→ same two reservations

different execution race
→ one winner
→ one root branch
→ one NEW_EXECUTION winner
```

## Limits

```text
max_active_branches reached
→ no execution created

max_total_executions reached
→ no branch created

max_active_executions reached
→ no branch created
```

## Runtime propagation

```text
root context starts branch_id=NULL
durable admission wins
context.branch_id == durable TaskBranch.branch_id
EXECUTION_STARTED/tool/checkpoint lineage sees same branch
```

## Delegation

```text
child execution
→ same branch
→ no active_branches increment
→ branch.current_execution_id unchanged
```

## R7

```text
WAITING/resume
→ same execution
→ same branch
→ no BRANCH re-reservation
```

Then run full:

```text
python -m pytest -q
```

---

# 26. R8-B invariants

```text
R8B-I01
Every newly admitted Task root execution belongs to a durable TaskBranch.

R8B-I02
Root branch counts exactly once against active_branches.

R8B-I03
Root BRANCH accounting and first NEW_EXECUTION accounting commit atomically.

R8B-I04
One root admission increments TaskBudget revision exactly once.

R8B-I05
BRANCH reservation key is branch_id.

R8B-I06
NEW_EXECUTION reservation key remains execution_id.

R8B-I07
Same root retry is idempotent.

R8B-I08
Different concurrent root execution IDs cannot both win.

R8B-I09
Delegated child execution does not consume a Branch slot.

R8B-I10
Delegated child cannot cross Task/Branch lineage.

R8B-I11
TaskBranch.current_execution_id is root execution authority, not child execution authority.

R8B-I12
R7 RESUME never reserves a new Branch.

R8B-I13
Historical branch accounting is reconciled before general FORK.

R8B-I14
Budgetless legacy Task history remains quarantined, not guessed.

R8B-I15
R8-B never creates a sibling branch.

R8B-I16
R8-B never starts a second top-level execution in an existing branch.

R8B-I17
AgentRuntime receives branch_id from durable admission before loop/event/tool/checkpoint work.

R8B-I18
Task start CAS remains R5-E authority and is not replaced by R8-B.

R8B-I19
BranchContext remains empty/unconsumed in R8-B.

R8B-I20
FORK authority does not exist after R8-B.
```

---

# 27. Stop conditions

Stop implementation and return to contract review if R8-B would require:

```text
creating a second sibling TaskBranch

copying a checkpoint transcript into BranchContext

base_checkpoint_id for a root branch

changing parent_execution_id semantics

reusing an old execution_id

new HTTP endpoints

ADOPT/SUPERSEDE/DISCARD/AGGREGATE

Task completion aggregation across branches

turning historical ambiguous current_execution_id into a guessed winner

sequential BRANCH then NEW_EXECUTION commits
```

Any of these crosses into R8-C/D/E/F or R9.

---

# 28. Frozen implementation order

When R8-B implementation begins:

```text
R8-B1
historical accounting migration 14b

R8-B2
RootExecutionAdmission contract/helper

R8-B3
atomic root admission transaction

R8-B4
delegated branch validation

R8-B5
AgentRuntime branch_id handoff

R8-B6
historical/accounting/race/rollback regression tests

R8-B7
full CI + completion document
```

Only after R8-B is GREEN should R8-C begin read-only FORK-safe planning.

---

# 29. Final freeze

```text
R8-A:
CLOSED / GREEN

R8-B:
CONTRACT FROZEN
NOT IMPLEMENTED

R8-C:
NOT IMPLEMENTED

R8-D FORK:
NOT IMPLEMENTED

F5:
PAUSED UNTIL R14+
```
