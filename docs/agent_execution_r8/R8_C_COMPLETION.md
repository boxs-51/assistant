# R8-C COMPLETION — READ-ONLY FORK-SAFE PLANNING

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**Contract freeze:** `02b6891dcdef0b1767f1c98bdfefe74e31f231cc`  
**R8-C code baseline:** `d2282284e5384b4915bc02a8d9d61077b9a605da`  
**Date:** 2026-09-22  
**Status:** **R8-C CLOSED / GREEN**

---

## Delivered

R8-C implements only read-only FORK-safe planning:

```text
ForkPlan / ForkSideEffectSnapshot contracts
canonical semantic fingerprints
read-only CapabilityInvocation scan by execution
strict already-COMMITTED tool projection readers
strict checkpoint transcript reconstruction
exact Task/Branch/Execution/Checkpoint lineage validation
pending invocation rejection
live side-effect safety validation
TaskBudget read-only capacity preflight
overlay normalization
read-only regression matrix
```

No R8-D consume authority exists.

---

## Read-only authority

`AgentForkPlanningService` performs:

```text
durable reads
+ deterministic validation
+ canonical reconstruction
+ deterministic hashing
```

It does not:

```text
create TaskBranch
create AgentExecution
reserve TaskBudget
create BRANCH reservation
create NEW_EXECUTION reservation
materialize legacy checkpoints
reconcile remote invocations
dispatch/replay capabilities
promote PROVISIONAL tool results
mutate source checkpoint/execution/branch
```

---

## Exact source lineage

A valid initial source is:

```text
Task RUNNING/WAITING
→ OPEN TaskBranch
→ TaskBranch.current_execution_id
→ WAITING AgentExecution
→ AgentExecution.current_checkpoint_id
→ exact normalized checkpoint
```

Planner requires exact agreement for:

```text
task ownership
task/session identity
assigned agent
branch ownership
branch current execution
execution task/branch/session
execution revision
checkpoint execution/revision/session/task/branch
checkpoint pointer
```

Source execution must be the current Branch head.

---

## Pending invocation rule

Any normalized checkpoint pending row rejects FORK:

```text
NOT_DISPATCHED
IN_FLIGHT
OUTCOME_UNKNOWN
TERMINAL_COMMITTED
```

All are rejected because the historical checkpoint was cut with a pending batch.

No remote reconciliation occurs during planning.

---

## Live side-effect scan

R8-C adds the read-only repository primitive:

```text
CapabilityInvocationRepository.list_records_for_execution(execution_id)
```

Canonical ordering:

```text
invocation_id ASC
```

Every source invocation must already be terminal.

Remote invocation additionally requires:

```text
remote_outcome_state == TERMINAL_COMMITTED
```

Unsafe states/outcomes reject without repair or replay.

---

## Strict committed projection

Every terminal source invocation requires:

```text
tool_call_id
+
matching AgentToolResult
+
commit_state == COMMITTED
```

Identity must agree on:

```text
execution_id
invocation_id
tool_call_id
capability_id
```

Remote terminal projection must also agree with terminal invocation output/error semantics.

PROVISIONAL never enters ForkPlan.

The R7 `load_committed_tool_result()` helper is intentionally not used because it can promote a PROVISIONAL row.

R8-C uses a separate non-mutating reader.

---

## Strict transcript reconstruction

R8-C adds:

```text
load_fork_safe_checkpoint_transcript()
```

It requires:

```text
normalized checkpoint
inline transcript_snapshot
zero checkpoint pending invocation rows
checkpoint iteration
COMMITTED durable result for every source tool projection
exact result/invocation authority agreement
```

Active iteration tool messages are rebuilt in:

```text
AgentIteration.tool_call_ids
```

order.

This preserves canonical parallel-tool ordering.

A terminal side effect must appear exactly once in the resulting fork base transcript.

---

## Fingerprints

R8-C provides:

```text
committed_result_fingerprint
fork_transcript_fingerprint
fork_side_effect_fingerprint
fork_plan_fingerprint
```

Canonical serialization uses deterministic JSON.

The semantic plan fingerprint binds:

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

It intentionally excludes:

```text
fork_request_id
new branch ID
new execution ID
timestamps
connection/client/process identity
```

Thus different request IDs for identical semantics produce the same semantic fingerprint.

---

## TaskBudget behavior

Planner only reads and captures:

```text
TaskBudget revision
policy fingerprint
active_branches
used_executions
active_executions
limits
```

Capacity exhaustion produces `ForkPlanDeferred`.

No counter or reservation is changed.

R8-D must revalidate the captured revision and capacity atomically before consume.

---

## Hardening added before final gate

Before final exit, the R8-C patch was additionally hardened to:

```text
require source execution agent_id == Task.assigned_agent_id

translate invalid/missing normalized current checkpoint
into stable FORK rejection semantics

bind strict transcript tool projections to
CapabilityInvocation durable authority

require every terminal source side effect
to appear exactly once in the fork base transcript

cover all pending remote outcome snapshot states

cover non-remote terminal effects with committed projections

cover full semantic fingerprint revision/policy fences
```

These changes remained fully within R8-C read-only scope.

---

## Initial CI regression

The first full-suite candidate `147726f8` had:

```text
846 passed
3 failed
1 skipped
```

All three failures came from one test helper constructing `ForkPlan` with duplicate `plan_fingerprint` arguments.

No production planner/persistence test failed.

The fixture was corrected and further R8-C hardening/tests were applied before the final candidate.

---

## Exact final exit gate

Code baseline:

```text
d2282284e5384b4915bc02a8d9d61077b9a605da
```

Phase 5 exit gates:

```text
40 passed
SUCCESS
```

Architecture Baseline — Linux:

```text
854 passed
1 skipped
36 warnings
SUCCESS
```

Architecture Baseline — Windows client:

```text
68 passed
SUCCESS
```

Architecture Baseline overall:

```text
SUCCESS
```

---

## Scope proof

Production files changed from the R8-C freeze:

```text
se/src/runtimes/agent/contracts/fork.py
se/src/runtimes/agent/contracts/__init__.py
se/src/runtimes/agent/fork_planning.py
se/src/runtimes/agent/persistence.py
se/src/infrastructure/storage/repositories/capability_invocations.py
```

Tests:

```text
se/tests/architecture/test_r8_c_fork_contracts.py
se/tests/architecture/test_r8_c_fork_planning.py
se/tests/integration/test_r8_c_read_only_persistence.py
```

No R8-C migration was added.

No changes occurred in:

```text
task_budget.py
runtime.py
coordinator.py
waiting_checkpoint.py
TaskBranch model
Execution model
Checkpoint model
main.py
multi_agent_router.py
events_router.py
cl/
R7 ResumeClaim semantics
R7 PendingResumeTicket semantics
```

---

## Closed invariants

```text
R8C-I01 ForkPlan owns no durable execution authority.
R8C-I02 Planner performs no durable mutation.
R8C-I03 Planner performs no network reconciliation.
R8C-I04 Only normalized checkpoints are forkable.
R8C-I05 Source Branch is OPEN and exact Task owner.
R8C-I06 Source execution is Branch.current_execution_id.
R8C-I07 Initial source execution is exact current WAITING checkpoint.
R8C-I08 Task/Branch/Execution/Checkpoint lineage matches exactly.
R8C-I09 Any checkpoint pending invocation row blocks FORK.
R8C-I10 Every live source invocation is terminal.
R8C-I11 Remote invocation requires TERMINAL_COMMITTED.
R8C-I12 Every terminal effect has a matching COMMITTED projection.
R8C-I13 PROVISIONAL never enters ForkPlan.
R8C-I14 Parallel tool ordering follows iteration.tool_call_ids.
R8C-I15 Every terminal side effect appears exactly once in base transcript.
R8C-I16 side_effect_watermark alone never authorizes FORK.
R8C-I17 TaskBudget is read-only preflight only.
R8C-I18 R8-D must revalidate expected revisions/fingerprints.
R8C-I19 fork_request_id is idempotency identity, not semantic content.
R8C-I20 R8-C chooses no new Branch or Execution identity.
```

---

## Next boundary

```text
R8-A  CLOSED / GREEN
R8-B  CLOSED / GREEN
R8-C  CLOSED / GREEN

R8-D  NOT IMPLEMENTED
R8-E+ NOT IMPLEMENTED

F5    PAUSED UNTIL R14+
```

Before implementing R8-D, audit must freeze the exact atomic FORK consume transaction, including write-side checkpoint branch-lineage hardening, request-id dedupe authority, revalidation of every R8-C fence, TaskBudget CAS, Branch/Context/Execution inserts, and race/crash rollback semantics.
