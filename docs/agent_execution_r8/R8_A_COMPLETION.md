# R8-A COMPLETION — NORMALIZED TASKBRANCH PERSISTENCE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**R8-A code baseline:** `bb20cd8a7a52792aab40d187c0a528578f9da5ac`  
**Contract freeze:** `docs/agent_execution_r8/R8_A_EXACT_BOUNDARY_CONTRACT_FREEZE_92857439.md`  
**Date:** 2026-09-22  
**Status:** **R8-A CLOSED / GREEN**

---

## Delivered

R8-A adds durable normalized representation for:

```text
Task
  1 -> N TaskBranch

TaskBranch
  1 -> N AgentExecution
```

Implemented:

- `BranchResolutionState`, `TaskBranch`, `TaskBranchContext`;
- `agent_task_branches`;
- `agent_task_branch_contexts`;
- migration `14a_r8_task_branch` from `13b_r7_pending_snapshot`;
- historical Case A/B/C backfill;
- Case D/ambiguous history fail-closed;
- AgentExecution + normalized checkpoint branch-lineage backfill together;
- conservative `current_execution_id` inference using exactly one top-level execution;
- TaskBranch revision CAS with immutable lineage protection;
- BranchContext revision CAS with JSON-safe overlay persistence;
- downgrade that removes only synthesized compatibility branch IDs.

No FORK runtime was implemented.

---

## Historical backfill contract proven

```text
A: Task with no executions
   -> no fake branch

B: all execution.branch_id NULL
   -> deterministic compatibility root
   -> execution branch backfill
   -> checkpoint branch backfill

C: exactly one existing branch ID
   -> preserve existing ID
   -> normalize NULL siblings

D: >1 distinct branch IDs for one Task
   -> fail closed
```

Additional fail-closed cases:

```text
task_id NULL + branch_id non-NULL
same branch ID reused across Tasks
checkpoint/execution task or branch mismatch
```

---

## CAS contract proven

TaskBranch immutable after creation:

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

R8-A CAS mutable fields:

```text
current_execution_id
resolution_state
```

BranchContext mutable field:

```text
overlay_messages
```

Every successful CAS increments revision exactly once.

Stale CAS loses without overwriting the winner.

---

## Scope proof

R8-A did not modify:

```text
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/task_budget.py
se/src/runtimes/agent/coordinator.py
se/src/main.py
multi_agent_router.py
CL
R7 ResumeClaim/resume runtime
```

Therefore R8-A does not expose FORK behavior.

---

## Exit-gate evidence

On exact code baseline:

```text
bb20cd8a7a52792aab40d187c0a528578f9da5ac
```

Phase 5 consolidated:

```text
40 passed
SUCCESS
```

Architecture Baseline — Linux:

```text
801 passed
1 skipped
26 warnings
SUCCESS
```

Architecture Baseline — Windows client:

```text
68 passed
SUCCESS
```

Pre-R8 baseline was 791 Linux tests; R8-A contributes 10 new passing tests.

---

## Frozen next boundary

Next phase:

```text
R8-B
root-branch accounting
+ atomic branch-aware first-execution admission
```

R8-B must not implement general FORK consume.

General FORK remains deferred to R8-C/R8-D after branch accounting and safe-checkpoint planning are separately green.

---

## Final status

```text
R7:
CLOSED

R8-A:
CLOSED / GREEN

R8-B:
NOT IMPLEMENTED

R8-C:
NOT IMPLEMENTED

R8-D FORK:
NOT IMPLEMENTED

F5:
PAUSED UNTIL R14+
```
