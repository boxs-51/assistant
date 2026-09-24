# AE-R11-F1B — Task-Lineage Hardening

Status: **P1 correctness repair candidate / non-destructive**

Authority:
- Issue #31
- blocker: `P1-R11-F1B-TASK-LINEAGE-1`
- exact parent: `c52fa42d61dcab3b4a7871f93942fd52bed6accd`
- repair branch: `work/ae-r11-f1b-task-lineage-c52fa42d`

## Problem

`AgentTaskRecord.parent_task_id` is the durable semantic edge for SUBTASK lineage.
It is intentionally represented as an indexed string rather than a SQL foreign key.
The landed F1-B dry-run already protects reverse semantic references for executions,
branches, checkpoints, admissions and capability invocations, but did not scan for
another Task whose `parent_task_id` names the candidate Task.

That gap is unsafe for the first destructive F1-C stage: a terminal parent could be
classified CANDIDATE while a retained child Task still names it as its parent,
leaving dangling delegation/subtask provenance after collection.

## Repair

Before any candidate classification, the task-scoped F1-B builder performs a
read-only reverse scan:

```text
other AgentTaskRecord.parent_task_id == candidate task_id
    -> external_child_task_reference
    -> FAIL_CLOSED for the entire task-scoped report
```

The rule is intentionally conservative. F1-B does not attempt multi-Task joint
collection and does not infer that an external child is collectible merely because
it is terminal.

## Boundaries

This repair:
- adds no DELETE/UPDATE/INSERT path;
- changes no Task schema or migration;
- does not alter TaskBudget replay semantics;
- does not change transcript reachability;
- does not acquire CAS, CTX, R6 ledger, or R12 recovery authority;
- does not release R11-F1-C destructive work by itself.

## Evidence

Architecture regression freezes the semantic field and fail-closed reason code.
Integration regression creates a policy-eligible terminal parent plus an external
child Task that points to it and proves the parent report is FAIL_CLOSED with zero
candidates.

Exit requires fresh exact-head Linux + Windows Architecture and independent Issue
#31 audit. F1-C remains blocked until this repair lands, canonical-main post-merge
CI is GREEN, and the F1-C claim is refreshed again from that exact main.
