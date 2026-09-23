# R8-C — EXACT READ-ONLY FORK-SAFE PLANNING CONTRACT FREEZE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Audited HEAD:** `832a881695b5d38c14c05b0f2a7ea72317d8f1f3`  
**R8-B code baseline:** `afd19b4d82cd762e7e17efdc9b3192b2dba382ac`  
**Roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Scope:** R8-C only — read-only FORK-safe planning  
**Status:** **CONTRACT FROZEN / NOT IMPLEMENTED**

---

# 1. R8-C exact boundary

R8-C introduces a read-only planning authority for one future R8-D FORK.

R8-C proves:

```text
source Task
→ source TaskBranch
→ source AgentExecution
→ source normalized checkpoint
→ committed transcript
→ terminal side-effect authority
→ TaskBudget capacity snapshot
```

and returns an immutable `ForkPlan`.

R8-C does **not**:

```text
create TaskBranch
create AgentExecution
increment TaskBudget
create BRANCH reservation
create NEW_EXECUTION reservation
mutate source Branch
mutate source Execution
mutate checkpoint
consume a fork request
execute/replay/reconcile a capability
create an HTTP endpoint
start a runner
```

R8-D remains the first phase allowed to acquire durable FORK authority.

---

# 2. R8-C must be more read-only than R7-D

R7-D planning is not an acceptable implementation shortcut.

`AgentResumePlanningService` may:

```text
materialize a legacy checkpoint
reconcile a remote invocation over a connection
commit newly learned terminal R6 authority
promote committed tool-result projection
```

Those behaviors are correct for RESUME, but violate the R8-C boundary.

R8-C must not call:

```text
materialize_legacy_checkpoint()
reconcile_remote_invocation()
continue_invocation()
CapabilityRuntime dispatch
ResumeClaim creation/consume
```

R8-C planning is:

```text
durable reads
+ deterministic validation
+ deterministic hashing
only
```

No network call is permitted.

---

# 3. P0 findings on HEAD 832a8816

## P0-R8C-1 — checkpoint creation still does not enforce branch equality

`stage_waiting_checkpoint()` currently validates:

```text
execution_id
execution_revision
session_id
task_id
wait_reason
```

but does not validate:

```text
checkpoint.branch_id == execution.branch_id
```

R8-C therefore must independently require exact branch equality before trusting a checkpoint.

R8-C does not modify `stage_waiting_checkpoint()`.

Write-side branch hardening is a mandatory R8-D0 prerequisite before durable FORK consume is enabled.

---

## P0-R8C-2 — R7 pending snapshot rows are immutable checkpoint history

`agent_checkpoint_pending_invocations` means:

```text
this invocation was unresolved when this checkpoint was cut
```

A row does not disappear merely because R6 later reaches terminal authority.

Therefore R8-C must not interpret a later `TERMINAL_COMMITTED` state as permission to fork from a checkpoint that was originally cut mid-batch.

Initial R8 policy is deliberately conservative:

```text
ANY checkpoint pending-invocation row
→ FORK_PENDING_INVOCATIONS
→ reject
```

This includes checkpoint observations of:

```text
NOT_DISPATCHED
IN_FLIGHT
OUTCOME_UNKNOWN
TERMINAL_COMMITTED
NULL
```

if the invocation is still represented in the checkpoint pending table.

A later clean normalized checkpoint with no pending rows may become FORK-safe.

---

## P0-R8C-3 — live invocation authority must also be scanned

Checking only checkpoint pending rows is insufficient.

A capability invocation may exist for the source execution but be:

```text
missing from an old checkpoint snapshot
created after an unsafe snapshot
left in an unexpected lifecycle state
```

R8-C requires a new read-only repository query:

```text
list_records_for_execution(execution_id)
```

Every source-execution CapabilityInvocation must be classified.

No live invocation may be ignored.

---

## P0-R8C-4 — unknown/unfinished side effects block FORK

The following invocation lifecycle states are never FORK-safe:

```text
CREATED
DISPATCHING
RUNNING
WAITING
RETRYING
```

The following remote outcome states are never FORK-safe:

```text
NOT_DISPATCHED
IN_FLIGHT
OUTCOME_UNKNOWN
```

R8-C does not replay or reconcile them.

It rejects them.

For a REMOTE_CLIENT invocation, terminal lifecycle is safe only when:

```text
remote_outcome_state == TERMINAL_COMMITTED
```

For a non-remote invocation, `remote_outcome_state == NULL` is allowed only after terminal lifecycle is proven.

---

## P0-R8C-5 — committed model projection must be provable

A terminal invocation with `tool_call_id` is FORK-safe only when a matching:

```text
AgentToolResult.commit_state == COMMITTED
```

exists.

Exact identity must match:

```text
execution_id
invocation_id
tool_call_id
capability_id
```

For REMOTE_CLIENT authority, the committed result projection must also agree with the terminal CapabilityInvocation payload/error semantics.

A PROVISIONAL result is never FORK-safe.

Missing or conflicting committed projection rejects the plan.

---

## P0-R8C-6 — initial R8-C does not accept unprojectable capability effects

If a source-execution CapabilityInvocation has no `tool_call_id`, R8-C cannot prove how its effect is represented in the branch transcript.

Initial R8-C therefore rejects it:

```text
FORK_SIDE_EFFECT_PROJECTION_MISSING
```

This is intentionally conservative.

A later phase may add explicit non-tool side-effect projection authority.

---

## P0-R8C-7 — existing committed transcript loader is not strict enough for FORK

`load_committed_checkpoint_transcript()` is safe for R7 resume prefix reconstruction, but it may silently omit a tool message when its durable result is missing or PROVISIONAL.

R8-C may not silently shrink source context.

R8-C needs a strict read-only helper that:

```text
requires inline transcript_snapshot on the normalized checkpoint

validates every tool-role message against COMMITTED AgentToolResult

loads the source checkpoint iteration

requires every canonical iteration.tool_call_id to have a COMMITTED result
when checkpoint pending rows are empty

strips any active-batch tool projections from the raw snapshot

re-appends exact committed projections in canonical iteration order

rejects any missing/conflicting/provisional projection
```

The result is the exact fork base transcript.

---

## P0-R8C-8 — side_effect_watermark is not sufficient authority

The normalized checkpoint schema contains:

```text
side_effect_watermark
```

but current production paths do not maintain it as a complete canonical side-effect digest.

It is nullable and cannot prove FORK safety.

R8-C must derive its own side-effect snapshot from:

```text
CapabilityInvocation durable rows
+
COMMITTED AgentToolResult projections
```

The checkpoint watermark may be observed for diagnostics, but must not authorize FORK.

---

## P0-R8C-9 — source Branch head must be exact

R8-A/B established:

```text
TaskBranch.current_execution_id
```

as branch execution authority.

R8-C requires:

```text
source_branch.current_execution_id == source_execution_id
```

Forking from a stale execution inside the same branch is rejected.

This prevents future R9 retries from making an older execution silently forkable.

---

# 4. Source Task contract

Planner input includes:

```text
task_id
source_branch_id
source_execution_id
source_checkpoint_id
fork_request_id
overlay_messages
target_user_id
```

Load `AgentTask` and require:

```text
Task exists
Task.id == task_id
Task.created_by == target_user_id
Task.status IN {RUNNING, WAITING}
```

Reject:

```text
CREATED
ASSIGNED
COMPLETED
FAILED
CANCELLED
```

The planner captures:

```text
expected_task_revision
session_id
assigned_agent_id
```

R8-C does not CAS the Task.

---

# 5. Source TaskBranch contract

Load `TaskBranch(source_branch_id)`.

Require:

```text
branch exists
branch.task_id == task_id
branch.resolution_state == OPEN
branch.current_execution_id == source_execution_id
```

Capture:

```text
expected_branch_revision
parent_branch_id
base_execution_id
base_checkpoint_id
```

R8-C does not mutate Branch revision or current execution.

---

# 6. Source AgentExecution contract

Load `AgentExecution(source_execution_id)`.

Initial R8-C accepts only a stable normalized WAITING source:

```text
execution.task_id == task_id
execution.branch_id == source_branch_id
execution.session_id == task.session_id

execution.state == WAITING

execution.current_checkpoint_id == source_checkpoint_id
```

Capture:

```text
expected_execution_revision
agent_id
correlation_id
parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
```

Why WAITING-only initially:

```text
RUNNING can still create side effects after planning
terminal execution may point at a stale earlier checkpoint
WAITING + exact current normalized checkpoint gives a frozen durable source
```

R8-C may be broadened later only through a new contract audit.

---

# 7. Source normalized checkpoint contract

R8-C accepts only an already-normalized checkpoint.

It never materializes legacy representation.

Require:

```text
checkpoint exists
checkpoint.checkpoint_id == source_checkpoint_id

checkpoint.execution_id == source_execution_id
checkpoint.execution_revision == execution.revision

checkpoint.session_id == execution.session_id
checkpoint.task_id == task_id
checkpoint.branch_id == source_branch_id

execution.current_checkpoint_id == checkpoint.checkpoint_id
```

The source checkpoint need not be CONNECTION-specific.

FORK safety is based on durable lineage + side-effect cleanliness, not reconnect semantics.

Require reconstructable inline source for initial R8:

```text
checkpoint.transcript_snapshot IS NOT NULL
```

`transcript_ref`-only sources remain deferred to R11 storage optimization.

---

# 8. Pending checkpoint rule

Load:

```text
list_checkpoint_pending_invocations(source_checkpoint_id)
```

Initial R8-C rule:

```text
len(pending) MUST equal 0
```

Any row causes:

```text
FORK_PENDING_INVOCATIONS
```

No R6 reconciliation is attempted.

No idempotency class relaxes this rule.

Therefore even:

```text
IDEMPOTENT + NOT_DISPATCHED
DEDUPE + OUTCOME_UNKNOWN
later TERMINAL_COMMITTED row
```

does not make that same historical checkpoint FORK-safe.

The fork must target a later clean checkpoint.

---

# 9. Live CapabilityInvocation safety scan

Add a read-only transactional repository primitive:

```text
CapabilityInvocationRepository.list_records_for_execution(execution_id)
```

Canonical order:

```text
invocation_id ASC
```

Order is only for deterministic snapshot hashing; it is not execution order.

For every row:

## 9.1 lifecycle

Require state in:

```text
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

Otherwise reject:

```text
FORK_SIDE_EFFECT_UNRESOLVED
```

## 9.2 remote outcome

If:

```text
driver_kind == REMOTE_CLIENT
```

require:

```text
remote_outcome_state == TERMINAL_COMMITTED
```

Reject:

```text
NULL
NOT_DISPATCHED
IN_FLIGHT
OUTCOME_UNKNOWN
```

with:

```text
FORK_REMOTE_OUTCOME_UNSAFE
```

For non-remote terminal invocation:

```text
remote_outcome_state may be NULL
```

but IN_FLIGHT/OUTCOME_UNKNOWN/NOT_DISPATCHED remain invalid if present.

## 9.3 projection

Require:

```text
tool_call_id IS NOT NULL
```

and exact COMMITTED AgentToolResult.

No PROVISIONAL projection is accepted.

---

# 10. Strict fork transcript reconstruction

R8-C introduces a read-only store helper conceptually:

```text
load_fork_safe_checkpoint_transcript(
    execution_id,
    checkpoint_id,
)
```

Algorithm:

```text
load exact normalized checkpoint

require transcript_snapshot

load checkpoint iteration by checkpoint.iteration

load canonical iteration.tool_call_ids

require checkpoint pending rows == []

validate every raw historical tool message against a COMMITTED result

for each canonical iteration tool_call_id:
    require COMMITTED AgentToolResult
    verify identity

remove active-iteration tool messages from raw snapshot

append committed active-iteration tool messages
in iteration.tool_call_ids order

validate every output as InferenceMessage
return immutable tuple
```

This preserves parallel tool ordering.

It must never expose:

```text
PROVISIONAL result
missing result
unknown result
unordered active result
```

---

# 11. Side-effect snapshot

R8-C returns one immutable snapshot item per source CapabilityInvocation.

Conceptual contract:

```text
ForkSideEffectSnapshot:
    invocation_id
    invocation_revision
    capability_id
    capability_version
    request_fingerprint
    idempotency
    state
    remote_outcome_state
    tool_call_id
    committed_result_fingerprint
```

Sorted by:

```text
invocation_id ASC
```

`committed_result_fingerprint` hashes canonical JSON of:

```text
execution_id
invocation_id
tool_call_id
capability_id
success
output
error_code
error_message
retryable
attempt
```

This snapshot is not an execution authority.

R8-D must re-read and recompute it before consume.

---

# 12. Side-effect fingerprint

Compute:

```text
side_effect_fingerprint
=
SHA256(
  canonical JSON [
    ForkSideEffectSnapshot...
  ]
)
```

Canonical JSON:

```text
sort_keys = true
separators = (",", ":")
ensure_ascii = false
JSON-safe normalized values only
```

An execution with zero capability invocations has a deterministic hash of:

```text
[]
```

No timestamps are included.

---

# 13. TaskBudget read-only preflight

R8-B already made Branch accounting authoritative.

R8-C loads `TaskBudget` and requires:

```text
budget exists
budget.state == OPEN
```

Capture:

```text
expected_task_budget_revision
policy_fingerprint

used_executions
active_executions
active_branches

max_total_executions
max_active_executions
max_active_branches
```

Read-only preflight requires capacity for one new sibling execution + branch:

```text
used_executions < max_total_executions
active_executions < max_active_executions
active_branches < max_active_branches
```

Capacity failure is:

```text
ForkPlanDeferred
```

not durable denial.

R8-D must revalidate/CAS the exact budget revision.

R8-C never reserves capacity.

---

# 14. Overlay contract

`overlay_messages` is part of FORK semantics even though R8-E will later consume branch context.

R8-C normalizes it with the same JSON-safe serialization rules used by durable Agent state.

Requirements:

```text
list order preserved
each element is a message-shaped mapping
no unserializable runtime object
```

R8-C does not persist overlay messages.

The normalized overlay is carried by `ForkPlan` and bound into the semantic fingerprint.

---

# 15. ForkPlan contract

Add an immutable contract conceptually:

```text
ForkPlan:
    fork_request_id
    plan_fingerprint

    task_id
    expected_task_revision
    session_id

    source_branch_id
    expected_branch_revision

    source_execution_id
    expected_execution_revision
    source_agent_id
    correlation_id

    source_checkpoint_id
    checkpoint_iteration

    expected_task_budget_revision
    budget_policy_fingerprint

    base_transcript
    base_transcript_fingerprint

    side_effects
    side_effect_fingerprint

    overlay_messages

    target_user_id
```

R8-C does not choose:

```text
new branch_id
new execution_id
new TaskBranch revision
new AgentExecution revision
```

Those belong to R8-D atomic consume.

Initial R8-C forks with the same source `agent_id`.

Agent override is not part of R8-C.

---

# 16. Fork semantic fingerprint

Add:

```text
fork_plan_fingerprint(plan_or_mapping)
```

The fingerprint binds immutable FORK semantics.

Canonical payload includes:

```text
task_id
expected_task_revision
session_id

source_branch_id
expected_branch_revision

source_execution_id
expected_execution_revision
source_agent_id
correlation_id

source_checkpoint_id
checkpoint_iteration

expected_task_budget_revision
budget_policy_fingerprint

base_transcript
base_transcript_fingerprint

side_effects
side_effect_fingerprint

overlay_messages

target_user_id
```

It explicitly excludes:

```text
fork_request_id
new branch_id
new execution_id
created_at
updated_at
process ID
connection ID
client ID
wall-clock planning time
```

Reason:

`fork_request_id` is R8-D idempotency identity, not semantic content.

R8-D will bind:

```text
(task_id, fork_request_id)
→ fork_plan_fingerprint
```

and reject reuse of the same request ID with different semantics.

---

# 17. Transcript fingerprint

```text
base_transcript_fingerprint
=
SHA256(
    canonical JSON [
        InferenceMessage.model_dump(mode="json"),
        ...
    ]
)
```

The full transcript is still present in `ForkPlan`.

The separate hash exists so R8-D/R8-E can cheaply revalidate source content without trusting caller-provided transcript bytes.

---

# 18. Exact lineage validation order

Planner validation order is frozen:

```text
1. validate request identifiers / principal

2. load AgentTask
3. validate owner + non-terminal state

4. load TaskBranch
5. validate task ownership + OPEN
6. validate branch.current_execution_id

7. load AgentExecution
8. validate task/branch/session
9. validate WAITING
10. validate current_checkpoint_id

11. load normalized checkpoint
12. validate execution/revision/session/task/branch identity

13. reject checkpoint pending rows

14. scan every live CapabilityInvocation for source execution
15. reject unresolved/unknown remote authority
16. require COMMITTED projections

17. strictly reconstruct committed transcript

18. load TaskBudget
19. validate OPEN + read-only capacity

20. normalize overlay

21. compute transcript fingerprint
22. compute side-effect fingerprint
23. compute ForkPlan fingerprint

24. return immutable ForkPlan
```

No mutation step exists in this sequence.

---

# 19. Stable planner errors

R8-C should introduce:

```text
ForkPlanError
ForkPlanRejected
ForkPlanDeferred
```

Stable rejection codes:

```text
FORK_TASK_NOT_FOUND
FORK_FOREIGN_PRINCIPAL
FORK_TASK_STATE_INVALID

FORK_BRANCH_NOT_FOUND
FORK_BRANCH_NOT_OPEN
FORK_BRANCH_TASK_CONFLICT
FORK_BRANCH_CURRENT_EXECUTION_CONFLICT

FORK_EXECUTION_NOT_FOUND
FORK_SOURCE_NOT_WAITING
FORK_EXECUTION_LINEAGE_CONFLICT

FORK_CHECKPOINT_NOT_FOUND
FORK_STALE_CHECKPOINT
FORK_CHECKPOINT_LINEAGE_CONFLICT
FORK_CHECKPOINT_TRANSCRIPT_UNAVAILABLE

FORK_PENDING_INVOCATIONS

FORK_SIDE_EFFECT_UNRESOLVED
FORK_REMOTE_OUTCOME_UNSAFE
FORK_SIDE_EFFECT_PROJECTION_MISSING
FORK_COMMITTED_RESULT_MISSING
FORK_COMMITTED_RESULT_CONFLICT
FORK_TRANSCRIPT_UNSAFE

FORK_TASK_BUDGET_REQUIRED
FORK_TASK_BUDGET_CLOSED
```

Deferred codes:

```text
FORK_BRANCH_CAPACITY_UNAVAILABLE
FORK_EXECUTION_CAPACITY_UNAVAILABLE
```

R8-C does not retry internally.

---

# 20. No legacy materialization

If source execution lacks an exact normalized checkpoint:

```text
reject
```

R8-C does not call the R7-I legacy materializer.

Reason:

```text
planning must not mutate representation
legacy checkpoint materialization can write rows/pointers
R8-A/B already define normalized TaskBranch authority
```

General FORK therefore requires post-R7 normalized state.

---

# 21. No remote reconciliation

If a source invocation is:

```text
IN_FLIGHT
OUTCOME_UNKNOWN
NOT_DISPATCHED
```

R8-C does not ask a client or provider what happened.

It rejects the source checkpoint.

This intentionally differs from RESUME.

RESUME may repair an existing execution.

FORK must copy only a state whose side effects are already durably settled before planning begins.

---

# 22. Source immutability proof

R8-C tests must snapshot before/after:

```text
AgentTask.revision
TaskBranch.revision
TaskBranch.current_execution_id
TaskBranchContext.revision

AgentExecution.revision
AgentExecution.state
AgentExecution.current_checkpoint_id

TaskBudget.revision
TaskBudget counters

AgentExecutionCheckpoint row
CheckpointPendingInvocation rows

CapabilityInvocation revision/state/outcome

AgentToolResult commit_state/content

TaskBudgetReservation count
ResumeClaim count
```

Successful planning and rejected planning must leave every value unchanged.

No SQL UPDATE/INSERT/DELETE is allowed.

---

# 23. Exact expected implementation blast radius

R8-C implementation should be limited to:

```text
ADD
se/src/runtimes/agent/contracts/fork.py

ADD
se/src/runtimes/agent/fork_planning.py

UPDATE
se/src/runtimes/agent/contracts/__init__.py

UPDATE
se/src/infrastructure/storage/repositories/capability_invocations.py
    # read-only list_records_for_execution only

UPDATE
se/src/runtimes/agent/persistence.py
    # read-only strict transcript/result helpers only
```

Potential `AgentRepository` update is allowed only for a read-only query needed by strict transcript proof.

No migration is expected.

---

# 24. Explicit files R8-C must not modify

Do not modify:

```text
se/src/runtimes/agent/task_budget.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/coordinator.py
se/src/runtimes/agent/waiting_checkpoint.py

se/src/infrastructure/storage/models/sql/agent/task_branch.py
se/src/infrastructure/storage/models/sql/agent/execution.py
se/src/infrastructure/storage/models/sql/agent/checkpoint.py

se/src/main.py
se/src/transport/gateway/api/v1/multi_agent_router.py

se/src/transport/gateway/api/v1/events_router.py
R7 ResumeClaim code
R7 PendingResumeTicket code
cl/
```

No Alembic revision belongs in R8-C.

---

# 25. R8-C regression matrix

## Lineage

Prove rejection for:

```text
foreign Task
foreign principal
terminal Task

missing Branch
non-OPEN Branch
Branch belongs to another Task
source execution != branch.current_execution_id

execution task mismatch
execution branch mismatch
execution session mismatch
execution not WAITING

checkpoint not current
checkpoint execution mismatch
checkpoint revision mismatch
checkpoint task mismatch
checkpoint branch mismatch
checkpoint session mismatch
```

## Pending invocation

Prove:

```text
zero pending rows
→ may proceed

one pending NOT_DISPATCHED row
→ reject

one pending IN_FLIGHT row
→ reject

one pending OUTCOME_UNKNOWN row
→ reject

one pending TERMINAL_COMMITTED snapshot row
→ still reject historical checkpoint
```

## Live side effects

Prove rejection for live invocation:

```text
CREATED
DISPATCHING
RUNNING
WAITING
RETRYING

REMOTE_CLIENT + NULL outcome
REMOTE_CLIENT + NOT_DISPATCHED
REMOTE_CLIENT + IN_FLIGHT
REMOTE_CLIENT + OUTCOME_UNKNOWN

PROVISIONAL tool result
missing tool result
tool-result identity mismatch
terminal invocation without tool_call_id
```

Prove safe:

```text
terminal non-remote invocation
+ matching COMMITTED result

terminal REMOTE_CLIENT invocation
+ TERMINAL_COMMITTED
+ matching COMMITTED result
```

## Transcript

Prove:

```text
PROVISIONAL never enters ForkPlan

active parallel committed tool results preserve iteration.tool_call_ids order

missing committed active-batch result fails closed

raw tool message without COMMITTED authority fails closed

same durable source produces byte-stable transcript fingerprint
```

## Budget

Prove:

```text
missing TaskBudget rejects
CLOSED TaskBudget rejects

max_active_branches reached
→ deferred

max_total_executions reached
→ deferred

max_active_executions reached
→ deferred

planner does not reserve capacity
```

## Fingerprint

Prove:

```text
same semantic input
→ same fingerprint

dict key order changes
→ same fingerprint

fork_request_id changes
→ same semantic fingerprint

overlay order/content changes
→ different fingerprint

source branch revision changes
→ different fingerprint

source execution revision changes
→ different fingerprint

checkpoint changes
→ different fingerprint

transcript changes
→ different fingerprint

side-effect revision/result changes
→ different fingerprint

TaskBudget revision/policy changes
→ different fingerprint
```

## Read-only proof

Successful and failed planner calls:

```text
write zero durable rows
change zero revisions
change zero counters
create zero reservations
create zero ResumeClaims
perform zero capability dispatch/reconciliation
```

---

# 26. R8-C invariants

```text
R8C-I01
ForkPlan owns no durable execution authority.

R8C-I02
Planner performs no durable mutation.

R8C-I03
Planner performs no network reconciliation.

R8C-I04
Only normalized checkpoints are forkable.

R8C-I05
Source Branch must be OPEN.

R8C-I06
Source execution must be Branch.current_execution_id.

R8C-I07
Initial R8-C forks only from exact current WAITING checkpoint.

R8C-I08
Checkpoint task/branch/session/execution lineage must exactly match.

R8C-I09
Any checkpoint pending-invocation row blocks FORK.

R8C-I10
Any non-terminal source CapabilityInvocation blocks FORK.

R8C-I11
Remote source invocation requires TERMINAL_COMMITTED authority.

R8C-I12
Every source invocation requires a provable committed model projection.

R8C-I13
PROVISIONAL tool results never enter ForkPlan.

R8C-I14
Canonical parallel tool order is preserved.

R8C-I15
side_effect_watermark alone never authorizes FORK.

R8C-I16
TaskBudget is only preflighted/read in R8-C.

R8C-I17
R8-D must revalidate every expected revision/fingerprint before consume.

R8C-I18
fork_request_id is idempotency identity, not semantic fingerprint content.

R8C-I19
R8-C never chooses new branch_id or new execution_id.

R8C-I20
Source Task/Branch/Execution/Checkpoint remain immutable after planning.
```

---

# 27. Stop conditions

Stop implementation and return to contract review if R8-C would require:

```text
calling remote reconciliation

replaying NOT_DISPATCHED invocation

replaying an idempotent OUTCOME_UNKNOWN invocation

materializing a legacy checkpoint

writing side_effect_watermark

updating AgentExecution.current_checkpoint_id

changing TaskBranch.current_execution_id

reserving TaskBudget capacity

creating BRANCH reservation

creating NEW_EXECUTION reservation

creating new branch/execution IDs

dispatching a capability

using PROVISIONAL result in transcript
```

Those actions are outside read-only planning.

---

# 28. Frozen implementation split

When implementation is authorized:

```text
R8-C1
ForkPlan / ForkSideEffectSnapshot contracts
+ canonical fingerprint helpers

R8-C2
read-only CapabilityInvocation list-by-execution query

R8-C3
strict committed transcript + side-effect projection reader

R8-C4
Task/Branch/Execution/Checkpoint exact lineage planner

R8-C5
TaskBudget read-only capacity snapshot
+ overlay normalization

R8-C6
read-only / unsafe-side-effect / fingerprint regression matrix

R8-C7
full CI + completion document
```

No R8-D consume code may be included in those patches.

---

# 29. Frozen next boundary

```text
R8-A
CLOSED / GREEN

R8-B
CLOSED / GREEN

R8-C
CONTRACT FROZEN
NOT IMPLEMENTED

R8-D
FORK CONSUME NOT IMPLEMENTED

R8-E+
NOT IMPLEMENTED

F5
PAUSED UNTIL R14+
```

R8-D may begin only after R8-C implementation proves:

```text
safe source lineage
zero checkpoint pending invocations
zero unresolved side effects
strict committed transcript
stable semantic fingerprint
zero planner writes
```
