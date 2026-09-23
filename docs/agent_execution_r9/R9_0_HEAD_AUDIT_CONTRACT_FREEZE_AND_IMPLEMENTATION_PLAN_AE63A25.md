# AE-R9 — HEAD AUDIT, CONTRACT FREEZE & IMPLEMENTATION PLAN

**Repository:** `boxs-51/assistant`  
**Canonical baseline:** `main @ ae63a25a7d077dc0de1322525e70f2e74107895f`  
**Work branch:** `work/ae-r9-a-ae63a25`  
**Issue:** #10 — `[CHECKPOINT] AE-R8 CLOSED -> AE-R9 boundary @ ae63a25`  
**Parent phase:** AE-R8 CLOSED / GREEN  
**Status:** **EXECUTABLE CONTRACT FREEZE — AE-R9 MAY IMPLEMENT STAGED WORK ONLY**

---

## 1. Roadmap authority

AE-R9 is exactly:

~~~text
Phase R9 — Retry + Branch Resolution + Task Aggregation

retry-as-new-execution in same branch
ADOPT
SUPERSEDE
DISCARD
AGGREGATE execution
Task completion CAS
branch result authority
~~~

Required proof:

~~~text
retry E1 -> E2 same branch
terminal execution never resurrects
simultaneous branch completion
one ADOPT winner
explicit aggregate
one branch failure does not fail Task
completed Task invalidates outstanding ResumeClaims
~~~

Exit gate:

~~~text
Task resolution is deterministic under races.
~~~

---

## 2. Baseline / merge gate

The PRE-R9 merge-first gate is now satisfied for the Agent Execution track.

Canonical AE baseline:

~~~text
main @ ae63a25a7d077dc0de1322525e70f2e74107895f
~~~

This HEAD contains the merged AE-R8 implementation through PR #9 and the
repository namespace registry.

The following branches remain separate roadmap tracks and are not AE-R9 input:

~~~text
tools-v1-contract-freeze          -> TV1-T8 / PTC track
feature/central-asset-storage-f1  -> CAS track, CAS-F5+ paused
~~~

AE-R9 MUST NOT absorb those scopes.

The abandoned PRE-R9 branch:

~~~text
work/r9-a-3b833607
~~~

is documentation-only and is not an implementation base.

---

## 3. Existing R8-ready authority

The merged baseline already provides:

### TaskBranch

~~~text
agent_task_branches

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
~~~

Allowed stored resolution values already include:

~~~text
OPEN
ADOPTED
SUPERSEDED
DISCARDED
CANCELLED
~~~

R8 only exercised OPEN/CANCELLED.

### Branch CAS

The repository already permits CAS mutation only of:

~~~text
current_execution_id
resolution_state
~~~

AE-R9 keeps immutable branch lineage immutable.

### TaskBudget

Existing shared counters include:

~~~text
used_executions
active_executions
active_branches
active_parallel_agents
~~~

Existing NEW_EXECUTION reservation logic is atomic with execution insertion.

### R8 fork receipt

~~~text
agent_task_fork_admissions
~~~

already proves the repository pattern for durable logical-request
idempotency.

### ResumeClaim

R7 already has:

~~~text
CREATED
CONSUMED
REJECTED
EXPIRED
~~~

plus per-claim CAS.

### R8 multibranch task reconciliation

`reconcile_multibranch_task_activity_in_uow()` intentionally handles only:

~~~text
any OPEN head CREATED/RUNNING -> Task RUNNING
else any OPEN head WAITING    -> Task WAITING
else                           -> leave Task non-terminal
~~~

The all-terminal OPEN-branch case is intentionally delegated to AE-R9.

---

## 4. Re-audit findings on merged HEAD

### P0-R9-1 — no durable RETRY logical-request identity

Current code can mint a new execution ID, but it has no durable receipt for:

~~~text
retry_request_id
same logical request -> same retry execution
same request id + semantic drift -> conflict
~~~

A transport retry could otherwise consume TaskBudget twice.

### P0-R9-2 — no atomic same-branch RETRY admission

Generic NEW_EXECUTION admission is insufficient.

AE-R9 requires one transaction binding:

~~~text
Task
TaskBudget
OPEN TaskBranch
source terminal AgentExecution
RetryAdmission receipt
new AgentExecution
TaskBranch.current_execution_id
NEW_EXECUTION reservation
~~~

A crash or race must not leave budget, branch-head, receipt and execution
authority out of sync.

### P0-R9-3 — RETRY source semantics are not enforced

Frozen initial rule:

~~~text
source execution belongs to same task + same branch
source execution is the branch current_execution_id
source execution is terminal:
    FAILED
    TIMEOUT
    CANCELLED only when Task itself is not CANCELLED and policy explicitly permits
COMPLETED is not retryable by default
WAITING/RUNNING/CREATED are never retried as a new execution
branch resolution_state == OPEN
Task is non-terminal
TaskBudget is OPEN
~~~

AE-R9 does not resurrect the source execution.

For the initial implementation, automatic policy selection is non-scope.
A caller explicitly requests RETRY.

### P0-R9-4 — branch resolution state exists but has no authority service

There is no durable operation defining:

~~~text
OPEN -> DISCARDED
OPEN -> ADOPTED
OPEN -> SUPERSEDED
~~~

with counter/accounting and race behavior.

Resolved branches are monotonic:

~~~text
ADOPTED/SUPERSEDED/DISCARDED/CANCELLED -> OPEN  forbidden
resolved -> different resolved state    forbidden
~~~

### P0-R9-5 — ADOPT is not atomic with Task terminal authority

The winning operation MUST commit together:

~~~text
lock Task
lock TaskBudget
lock all TaskBranch rows in deterministic branch_id order
validate selected branch + selected current execution
selected branch -> ADOPTED
all other OPEN branches -> SUPERSEDED
active_branches -> 0
Task -> COMPLETED with adopted execution result
TaskBudget -> CLOSED
outstanding CREATED ResumeClaims for this Task -> REJECTED
one commit
~~~

There must never be a durable state in which the Task is COMPLETED while
an OPEN branch or resumable CREATED claim remains authoritative.

### P0-R9-6 — no branch result authority

ADOPT requires:

~~~text
branch == OPEN
branch.current_execution_id != null
current execution belongs to same task/branch
current execution state == COMPLETED
result is durable
~~~

FAILED/TIMEOUT/CANCELLED/WAITING/RUNNING heads cannot be adopted.

Late loser execution completion cannot rewrite the selected Task result.

### P0-R9-7 — DISCARD accounting is missing

Standalone DISCARD:

~~~text
OPEN -> DISCARDED
active_branches - 1 exactly once
Task remains non-terminal
Task output unchanged
~~~

Initial fail-closed rule:

~~~text
do not DISCARD the final OPEN branch
~~~

unless the same transaction is part of a separately frozen terminal resolution.

### P0-R9-8 — completed Task does not invalidate outstanding ResumeClaims

R7 provides per-claim CAS but there is no task-scoped atomic rejection primitive.

AE-R9 needs repository support to select/lock CREATED claims by Task and reject
them in the ADOPT winner transaction.

Rejection code:

~~~text
TASK_RESOLVED
~~~

### P0-R9-9 — AGGREGATE has no durable provenance

AGGREGATE must be explicit.

A future AE-R9-F aggregate receipt must bind:

~~~text
aggregate_request_id
task_id
target branch
aggregate execution_id
ordered source branch snapshots
ordered source execution snapshots
result fingerprints
plan/runtime-seed fingerprint
~~~

Aggregate completion does NOT itself complete the Task.
A later ADOPT is still required.

### P1-R9-1 — current branch CAS is too generic for resolution invariants

`compare_and_set_task_branch()` is a useful low-level primitive but must not
become the public resolution API.

AE-R9 services must validate source state and Task/TaskBudget authority before
using it.

### P1-R9-2 — generic NEW_EXECUTION admission does not prove retry lineage

AE-R9 RETRY needs a specialized primitive rather than calling
`reserve_new_execution()` followed by a separate branch-head CAS.

---

## 5. Canonical RETRY identity

Add immutable receipt:

~~~text
agent_task_retry_admissions
~~~

Initial fields:

~~~text
task_id
retry_request_id

plan_fingerprint

branch_id
source_execution_id
source_checkpoint_id nullable

execution_id

created_by
created_at
~~~

Primary logical identity:

~~~text
(task_id, retry_request_id)
~~~

Uniqueness:

~~~text
execution_id UNIQUE
~~~

Replay rules:

~~~text
same task_id + retry_request_id + same fingerprint
    -> return same execution

same task_id + retry_request_id + different fingerprint
    -> RETRY_REQUEST_CONFLICT
~~~

The retry receipt is immutable after commit.

---

## 6. RETRY lineage / execution contract

For source:

~~~text
Task T1
Branch B1
Execution E1 terminal
~~~

retry produces:

~~~text
Task T1
Branch B1
Execution E2 NEW
~~~

Lineage:

~~~text
E2.task_id = T1
E2.branch_id = B1
E2.retry_of_execution_id = E1
E2.parent_execution_id = E1.parent_execution_id
E2.base_execution_id = E1.base_execution_id
E2.base_checkpoint_id = explicit retry checkpoint when supplied,
                        otherwise lineage-compatible inherited value
~~~

`parent_execution_id` remains delegation lineage only.

The branch itself is not recreated and `active_branches` is unchanged.

Initial retry state after atomic admission:

~~~text
RUNNING revision=1
~~~

matching existing TaskBudget execution admission semantics.

Runtime activation/handoff is AE-R9-C.

---

## 7. Retry from checkpoint rule

The roadmap permits RETRY_FROM_CHECKPOINT.

Initial AE-R9 contract:

1. `source_checkpoint_id` is optional.
2. If supplied, it MUST:
   - belong to `source_execution_id`;
   - match task_id and branch_id;
   - be normalized/durable;
   - pass the same side-effect safety rules needed to reconstruct committed context;
   - not be stale relative to the selected source execution/branch policy.
3. If absent, AE-R9-C will derive the retry seed from branch/execution durable
   context without inventing a new checkpoint.
4. RETRY_FROM_CHECKPOINT never mutates the checkpoint.

No provider retry semantics are implied.

---

## 8. Branch resolution state machine

~~~text
OPEN
 | \
 |  \
 |   +--> DISCARDED
 +------> ADOPTED
 +------> SUPERSEDED

CANCELLED remains a Task-cancellation outcome owned by the existing cancellation path.
~~~

No resolved state has an outgoing transition.

### DISCARD

~~~text
OPEN branch
-> DISCARDED
-> active_branches - 1 exactly once
-> no Task output
-> no Task completion
~~~

### ADOPT

~~~text
selected OPEN branch + COMPLETED current execution
-> selected branch ADOPTED
-> all other OPEN branches SUPERSEDED
-> active_branches 0
-> Task COMPLETED
-> Task.output = selected durable execution result
-> TaskBudget CLOSED
-> outstanding CREATED ResumeClaims rejected
~~~

### SUPERSEDE

Public standalone SUPERSEDE is not required initially.
It is produced atomically by ADOPT for loser OPEN branches.

---

## 9. Task aggregation rules after AE-R9

Canonical derived state:

~~~text
if Task CANCELLED:
    CANCELLED

elif Task COMPLETED by ADOPT:
    COMPLETED

elif any OPEN branch current execution CREATED/RUNNING:
    RUNNING

elif any OPEN branch current execution WAITING:
    WAITING

elif OPEN branches remain and caller may explicitly RETRY:
    keep Task non-terminal pending policy/user action

elif no legal OPEN branch remains:
    do not invent Task FAILED automatically in initial R9
    explicit failure policy remains fail-closed until frozen
~~~

One branch failure never directly fails the Task.

---

## 10. ResumeClaim invalidation

ADOPT must atomically reject every outstanding:

~~~text
AgentResumeClaim.state == CREATED
whose execution.task_id == resolved Task
~~~

with:

~~~text
state = REJECTED
revision += 1
rejection_code = TASK_RESOLVED
rejected_at = now
~~~

CONSUMED/REJECTED/EXPIRED claims are immutable history.

Task cancellation keeps its existing authority and may later share a helper,
but AE-R9 must not regress R7 semantics.

---

## 11. Lock order

All AE-R9 multi-row writes preserve:

~~~text
1. AgentTask
2. TaskBudget
3. TaskBranch rows sorted by branch_id
4. AgentExecution rows
5. Retry/Aggregate admission receipt
6. ResumeClaim rows
7. CAS mutations
8. one commit
~~~

Do not acquire these in reverse order in a competing path.

SQLite tests rely on transaction/CAS fences where `FOR UPDATE` is not strong.

---

## 12. Accounting

### RETRY

~~~text
used_executions + 1
active_executions + 1
active_parallel_agents + 1 only when delegated
active_branches unchanged
~~~

### terminal execution completion

Existing release execution semantics remain authoritative.

### DISCARD

~~~text
active_branches - 1
~~~

exactly once.

### ADOPT

~~~text
all remaining OPEN branches resolved
active_branches -> 0
TaskBudget -> CLOSED
~~~

No counter may go negative.

---

## 13. Error taxonomy

Internal stable codes to preserve through the later public control plane:

~~~text
RETRY_REQUEST_CONFLICT
RETRY_SOURCE_NOT_TERMINAL
RETRY_SOURCE_NOT_CURRENT
RETRY_BRANCH_NOT_OPEN
RETRY_TASK_TERMINAL
RETRY_BUDGET_EXCEEDED
RETRY_CHECKPOINT_CONFLICT

BRANCH_NOT_OPEN
BRANCH_RESULT_NOT_COMPLETED
BRANCH_DISCARD_LAST_OPEN_FORBIDDEN
BRANCH_RESOLUTION_CONFLICT

TASK_ALREADY_RESOLVED
TASK_RESOLUTION_CONFLICT
AGGREGATE_REQUEST_CONFLICT
AGGREGATE_INPUT_CONFLICT
~~~

AE-R9-G will map these to the public error envelope.

---

## 14. Non-scope boundaries

### AE-R10

Do not add provider retry/fallback policy, retry hints, model fallback or provider budgets.

### AE-R11

Only correctness-critical indexes/tables required by AE-R9 may be added.
No general persistence optimization.

### AE-R12

Do not add:

~~~text
owner_instance_id
execution leases
stale RUNNING scanning
distributed takeover
distributed immediate cancellation
~~~

### AE-R14

Targeted R9 race tests are required.
General production fault injection remains later.

### TV1 / PTC / CAS

No edits to the independent tool/provider contract roadmap or CAS-F5+ work.

---

## 15. Implementation sequence

### AE-R9-A — representation + durable admission contracts

Add:

~~~text
Retry contract dataclasses / fingerprint
agent_task_retry_admissions model
15a migration
repository save/get primitives
task-scoped CREATED ResumeClaim query/lock primitive
representation + migration tests
~~~

No runtime RETRY yet.

### AE-R9-B — atomic RETRY planning/admission

Add one UoW operation that:

~~~text
validates Task/TaskBudget/Branch/source execution
validates optional checkpoint
replays/conflicts retry_request_id
increments TaskBudget execution counters
inserts E2
moves branch.current_execution_id E1 -> E2 by CAS
writes RetryAdmission
writes NEW_EXECUTION reservation
commits once
~~~

No execution activation before commit.

### AE-R9-C — RETRY activation/runtime handoff

Add runtime seed + activation path and restart-safe replay.

Prove:

~~~text
same branch
new execution
source never resurrected
same retry request never starts two executions
~~~

### AE-R9-D — DISCARD

Atomic:

~~~text
OPEN -> DISCARDED
active_branches - 1
last OPEN branch fail-closed
idempotent replay
~~~

### AE-R9-E — ADOPT / Task completion

Atomic winner:

~~~text
COMPLETED branch head
one ADOPT winner
other OPEN -> SUPERSEDED
Task COMPLETED
TaskBudget CLOSED
Task output frozen
CREATED ResumeClaims rejected
~~~

### AE-R9-F — explicit AGGREGATE

Add durable aggregate provenance + aggregate execution admission.

Aggregation never implicitly adopts.

### AE-R9-G — public control plane

Wire explicit endpoints/commands only after storage/runtime invariants are green.

Preserve stable public error taxonomy.

### AE-R9-H — race matrix / exit gate

Required races:

~~~text
same retry request vs same retry request
same retry request semantic drift
retry vs Task cancel
retry vs branch discard
retry vs adopt
two adopts different branches
adopt vs late loser completion
discard vs adopt same branch
resume claim consume vs adopt
aggregate vs source branch mutation
server restart after committed retry before activation
~~~

Final gates:

~~~text
AE-R9 targeted tests
R8 exit gates
Architecture Baseline
Phase 5 Exit Gates
~~~

---

## 16. Freeze decision

The merged AE-R8 baseline is suitable for AE-R9.

AE-R9-A is authorized to begin on:

~~~text
work/ae-r9-a-ae63a25
base ae63a25a7d077dc0de1322525e70f2e74107895f
~~~

Production authority remains staged. No AE-R9-B/C/D/E/F/G behavior may be
wired by AE-R9-A.

