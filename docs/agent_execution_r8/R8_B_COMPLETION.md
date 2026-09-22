# R8-B COMPLETION — ROOT BRANCH ACCOUNTING + ATOMIC FIRST EXECUTION ADMISSION

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Contract freeze:** `4f01ad88f38ab519735f15e4b8d063c2018fc7d2`  
**R8-B code baseline:** `afd19b4d82cd762e7e17efdc9b3192b2dba382ac`  
**Date:** 2026-09-22  
**Status:** **R8-B CLOSED / GREEN**

---

## 1. Delivered scope

R8-B implements exactly:

```text
historical root-branch TaskBudget reconciliation
+
atomic first root execution admission
+
delegated child branch validation
+
AgentRuntime durable branch handoff
+
regression coverage
```

R8-B does not implement:

```text
FORK planning
FORK consume
sibling branch creation
branch-local context consumption
retry-as-new-execution
ADOPT / SUPERSEDE / DISCARD / AGGREGATE
multi-branch Task completion policy
branch HTTP APIs
```

---

## 2. Migration — 14b_r8_root_branch_accounting

Migration chain:

```text
13b_r7_pending_snapshot
→ 14a_r8_task_branch
→ 14b_r8_root_branch_accounting
```

The migration is data-only.

It reconciles normalized R8-A TaskBranch rows with:

```text
TaskBudget.active_branches
TaskBudget BRANCH reservation ledger
```

Historical invariant:

```text
active_branches
=
number of normalized OPEN TaskBranch rows
```

At the R8-B boundary, R8-A guarantees at most one historical branch per Task.

### Supported states

```text
no branch + active_branches=0
→ unchanged

one OPEN branch + no accounting
→ active_branches 0→1
→ TaskBudget revision +1
→ canonical BRANCH reservation inserted

one OPEN branch + canonical accounting already present
→ unchanged/idempotent

non-OPEN branch
→ no live branch slot synthesized

TaskBranch with no TaskBudget
→ left in R5 legacy quarantine
```

### Fail-closed migration states

Migration rejects:

```text
more than one TaskBranch before R8-D

branchless Task with non-zero branch counter

branchless Task with BRANCH reservation

OPEN branch with inconsistent active_branches

counter says accounted but reservation is absent

reservation exists but counter says unaccounted

wrong BRANCH reservation key

wrong BRANCH reservation fingerprint

orphan BRANCH reservation

budgetless branch with unexplained BRANCH ledger
```

Canonical BRANCH reservation identity:

```text
kind = BRANCH
reservation_key = branch_id
payload = {}
```

Fingerprint is byte-for-byte compatible with the existing R5 reservation fingerprint function.

---

## 3. RootExecutionAdmission

R8-B adds immutable:

```text
RootExecutionAdmission
    task_id
    branch_id
    branch_revision
    execution_id
    execution_revision
```

New Tasks receive deterministic root IDs:

```text
r8_root_<sha256(task_id)[0:40]>
```

The identity is independent of:

```text
execution_id
connection_id
process identity
retry timing
```

---

## 4. Atomic first-root transaction

`start_root_task_scoped_execution()` requires:

```text
Task.status == RUNNING
TaskBudget.state == OPEN

parent_execution_id == NULL
retry_of_execution_id == NULL
base_execution_id == NULL
base_checkpoint_id == NULL
branch_id == NULL

execution state == RUNNING
execution revision == 1
```

One UoW owns all writes:

```text
TaskBudget CAS once:
    active_branches +1
    used_executions +1
    active_executions +1

AgentExecution INSERT
    branch_id = deterministic root

TaskBranch INSERT
    current_execution_id = root execution
    resolution_state = OPEN
    reason = R8_ROOT_FIRST_EXECUTION

TaskBranchContext INSERT
    overlay_messages = []

BRANCH reservation INSERT
    key = branch_id

NEW_EXECUTION reservation INSERT
    key = execution_id

COMMIT
```

No sequential branch/execution commits exist.

Rollback tests inject failure at each conceptual write boundary and prove no partial durable state remains.

---

## 5. Idempotency and race authority

Same:

```text
task_id
execution_id
semantic execution payload
```

after uncertain commit returns the same durable root admission without incrementing counters again.

Two different root `execution_id` values racing for the same Task target the same deterministic root branch.

Exactly one may win.

The loser cannot:

```text
create another root execution
consume another branch slot
change branch.current_execution_id
create a second NEW_EXECUTION authority
```

---

## 6. Delegated child branch authority

Delegation branch identity is not trusted from caller metadata.

`resolve_delegation_admission()` now derives the branch from durable parent execution lineage.

It verifies:

```text
every ancestor belongs to same task
every ancestor has normalized branch_id
every ancestor remains in the same branch
TaskBranch exists
TaskBranch.task_id matches
TaskBranch.resolution_state == OPEN
```

AgentRuntime then writes the derived durable branch into child context before execution insert.

A delegated child:

```text
gets NEW execution
stays in parent branch
increments used_executions
increments active_executions
increments active_parallel_agents
does NOT increment active_branches
does NOT change TaskBranch.current_execution_id
does NOT create BRANCH reservation
```

---

## 7. AgentRuntime branch handoff

Before R8-B root runtime context starts with:

```text
context.branch_id = NULL
```

For first root execution:

```text
AgentRuntime._begin_durable_execution()
→ start_root_task_scoped_execution()
→ RootExecutionAdmission.branch_id
→ context.branch_id = durable branch_id
```

This occurs before:

```text
_execute_loop()
EXECUTION_STARTED
inference
tool execution
checkpoint creation
```

so downstream runtime lineage sees the committed branch authority.

For delegated execution:

```text
durable parent lineage
→ DelegationAdmission.branch_id
→ context.branch_id
→ AgentExecution.branch_id
```

Caller-provided conflicting branch identity fails closed.

---

## 8. R7 compatibility

R7 WAITING/RESUME semantics remain:

```text
same execution
same branch
no new BRANCH reservation
no new used_executions charge
```

R7-F task-scoped test fixtures were updated to use the branch committed by R8-B root admission in:

```text
AgentExecution
normalized WAITING checkpoint
ResumePlan
recovery checkpoint lineage
```

No R7 validation was relaxed.

This fixed the temporary CI failures caused by old test fixtures that still assumed `branch_id=NULL`.

---

## 9. R5 compatibility

Old R5 test harnesses that bypassed the production coordinator were aligned with the already-frozen R5-E lifecycle:

```text
ASSIGNED/CREATED
→ Task RUNNING
→ durable root admission
```

Parallel-agent tests now create a normalized root branch before creating delegated children.

Existing low-level non-delegated reservation tests remain source-compatible where branch authority is not part of the behavior under test.

---

## 10. Regression incident and resolution

First R8-B full suite exposed 9 failures:

```text
R5 delegated harness missing branch_id
2x R5 runtime harnesses starting from ASSIGNED
4x R7-F fixtures carrying branch_id=NULL
R8-A test assuming 14a remained Alembic head
R8-B second-root conflict message mismatch
```

All were contract-alignment regressions.

Fixes preserved strict production validation rather than weakening R8-B/R7 invariants.

Final exact code baseline:

```text
afd19b4d82cd762e7e17efdc9b3192b2dba382ac
```

---

## 11. Exit-gate evidence

### Phase 5 consolidated

```text
40 passed in 1.36s
SUCCESS
```

### Architecture Baseline — Linux

```text
819 passed
1 skipped
36 warnings
SUCCESS
```

### Architecture Baseline — Windows client

```text
68 passed
SUCCESS
```

Previous R8-A baseline:

```text
801 passed
1 skipped
```

R8-B and its regression updates therefore add/activate 18 additional passing Linux tests relative to the R8-A exit baseline.

---

## 12. Scope proof

Production changes relative to the R8-B freeze are limited to:

```text
se/src/infrastructure/storage/migrations/sql/versions/
    14b_r8_root_branch_accounting.py

se/src/runtimes/agent/
    task_budget.py
    runtime.py
```

Test changes are limited to R8-B suites and R5/R7 compatibility harnesses.

No changes occurred in:

```text
coordinator.py
main.py
AgentExecutionContext schema
ContextAssembler
fork_planning.py
multi_agent_router.py
cl/
R7 events_router
ResumeClaim semantics
PendingResumeTicket semantics
```

---

## 13. Closed invariants

```text
R8B-I01
Every newly admitted Task root execution has a durable TaskBranch.

R8B-I02
Root branch consumes exactly one active_branches slot.

R8B-I03
Root BRANCH + NEW_EXECUTION accounting commits atomically.

R8B-I04
First root admission changes TaskBudget revision exactly once.

R8B-I05
Same root retry is idempotent.

R8B-I06
Competing root execution IDs have one durable winner.

R8B-I07
Delegated child remains in parent's durable branch.

R8B-I08
Delegated child consumes no additional branch slot.

R8B-I09
Delegated child cannot cross Task/Branch lineage.

R8B-I10
TaskBranch.current_execution_id stays root authority in R8-B.

R8B-I11
R7 RESUME never reserves a new branch.

R8B-I12
Historical R8-A root branches are accounted before general FORK.

R8B-I13
Budgetless legacy history remains quarantined.

R8B-I14
R8-B creates no sibling branch.

R8B-I15
R8-B creates no second top-level execution in an existing branch.

R8B-I16
Root durable branch_id reaches AgentRuntime before model/tool/checkpoint work.

R8B-I17
R5-E Task start CAS remains authoritative.

R8B-I18
BranchContext remains empty/unconsumed.

R8B-I19
No FORK authority exists after R8-B.
```

---

## 14. Frozen next boundary

```text
R8-A
CLOSED / GREEN

R8-B
CLOSED / GREEN

R8-C
NOT IMPLEMENTED

R8-D FORK
NOT IMPLEMENTED

F5
PAUSED UNTIL R14+
```

Next work must be:

```text
R8-C — read-only FORK-safe planning
```

R8-C must not mutate TaskBudget, TaskBranch, AgentExecution, checkpoints, or reservation ledgers.

Only after R8-C planning is separately frozen and green may R8-D consume FORK authority.
