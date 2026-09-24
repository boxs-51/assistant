# AE-R11-F1-B — Non-Destructive Task-Scoped GC Dry-Run

Status: **IMPLEMENTATION CANDIDATE — READ ONLY — PRODUCTION DELETE CLOSED**

Authority:
- Issue #31
- landed parent PR #65 audited HEAD: `cfcc7a29379f06061a1c1520ffb3e2a717cf0b4f`
- parent merge commit / canonical main: `cab4615ae29c8ec5993d3ed9cfa37640976c8990`
- parent stage: R11-F1-A MERGED / CLOSED / REFRESH FINAL GREEN
- relationship: direct child of canonical main
- historical F1-A parent provenance: `98776b8038b4ffe0b31aaf616748bc38544f6979`

## Objective

F1-B turns the frozen F0/F1-A retention graph into a real read-only dry-run implementation.
It classifies one Agent Task at a time from one SQL unit-of-work and emits a stable ordered
`RETAIN | CANDIDATE | FAIL_CLOSED` report. It never deletes, mutates, migrates, schedules,
or background-scans persistence.

## Candidate boundary

A Task may produce task-owned CANDIDATE rows only when all of the following are true:

1. the caller explicitly supplies `policy_eligible_terminal=True`;
2. the Task is terminal;
3. its TaskBudget is CLOSED;
4. no active/nonterminal AgentExecution remains;
5. no CREATED ResumeClaim remains;
6. all associated CapabilityInvocations are terminal and have no IN_FLIGHT/OUTCOME_UNKNOWN
   remote outcome;
7. required AgentExecution lineage, checkpoint lineage, pending-invocation authority,
   admissions, tool/result/invocation links and transcript authority are complete;
8. no external AgentExecution, Branch, Fork/Retry/Aggregate admission semantic/FK edge points into the candidate Task graph, including authorities whose execution task_id is NULL;
9. every Agent-side pending/tool-call/tool-result reference agrees with the matching CapabilityInvocation execution_id, tool_call_id and capability_id;
10. every candidate-task CapabilityInvocation invocation_id has no inbound AgentToolCall, AgentToolResult, or checkpoint-pending semantic reference outside the candidate execution/checkpoint sets. These invocation_id columns are string authority, not FKs, so reverse scanning is mandatory.

Age is not an input to this decision.

If any required authority is missing/inconsistent, the report fails closed and emits no
CANDIDATE result.

## Frozen traversal

The implementation must preserve:

- Task -> TaskBudget / TaskBudgetReservation;
- Task -> Branch / BranchContext;
- Task -> Fork / Retry / Aggregate admission provenance;
- Task -> AgentExecution;
- AgentExecution semantic lineage:
  - parent_execution_id;
  - retry_of_execution_id;
  - base_execution_id;
  - base_checkpoint_id;
- Execution -> Checkpoint / Iteration / CapabilityInvocation / ResumeClaim;
- Checkpoint -> parent checkpoint;
- Checkpoint -> AgentCheckpointPendingInvocation;
- pending/tool-call/tool-result -> CapabilityInvocation authority;
- Iteration -> ToolCall / ToolResult;
- CapabilityInvocation -> attempts;
- checkpoint transcript_ref/version -> representation parent closure -> payload parent closure
  -> chunks.

Checkpoint pending rows retain their watermark role. FK CASCADE is physical ownership only,
never liveness authority.

## Shared transcript safety

F1-B is task-scoped and therefore does not attempt global transcript garbage collection.
Any transcript representation/payload/chunk reached from a Task checkpoint is always RETAIN,
including when all task-owned rows are policy-eligible CANDIDATE. This deliberately prevents
a task-scoped dry-run from claiming shared transcript storage.

## R6 boundary

ClientInvocationLedger remains a separate R6 client-side authority and is not collected by
this server-side dry-run.

Server CapabilityInvocation rows are fail-closed for candidate classification when:
- invocation state is nonterminal;
- any retained invocation attempt is nonterminal;
- remote_outcome_state is IN_FLIGHT or OUTCOME_UNKNOWN;
- Agent-side invocation identity disagrees on execution/tool-call/capability authority;
- an external ToolCall/ToolResult/checkpoint-pending row still references the candidate invocation_id; or
- terminal-task tool-result authority is not COMMITTED.

F1-B does not redefine R6 retry, reconciliation, TTL, ledger, or invocation lifecycle.

## Determinism

Rows use stable `row_kind + row_identity` keys. Roots and edges are processed in sorted order.
The report serializes ordered classifications and hashes them with SHA-256. Repeating the
same dry-run against the same persistence state must produce the same ordered items and
fingerprint.

## Hard non-scope

- no DELETE / bulk-delete / truncate;
- no repository delete method;
- no destructive migration;
- no background collector/scheduler;
- no age-only policy;
- no R12 owner_instance_id / lease / stale-RUNNING / WAITING(RECOVERY);
- no CAS asset/blob/reference/object-store GC;
- no CTX persisted index/retention ownership;
- no ClientInvocationLedger cleanup;
- historical pre-integration fence: `no merge of PR #60, #63 or #65`; those predecessors are now landed under explicit user authorization, and this historical text grants no authority to merge F1-B.

## Exit evidence

F1-B must prove:

1. active/replayable task graph is RETAIN;
2. explicitly policy-eligible, fully terminal, internally complete task-owned graph can be
   CANDIDATE without mutation;
3. missing/dangling/cyclic semantic authority, cross-Task inbound provenance and NULL-task inbound lineage are FAIL_CLOSED;
4. unsafe/mismatched CapabilityInvocation, external inbound invocation authority, or non-COMMITTED terminal tool-result authority is FAIL_CLOSED;
5. pending-invocation authority remains reachable;
6. transcript closure remains RETAIN even when task-owned rows are CANDIDATE;
7. repeated dry-runs are identical;
8. database row counts are unchanged.

Production destructive GC remains CLOSED after F1-B.


## M2 post-F1-A landing refresh

R11-E2 / PR #60, R11-F0 / PR #63, and R11-F1-A / PR #65 have landed
under the SERIAL LAND-THEN-REFRESH integration discipline.

Canonical M2 base is now:

```text
cab4615ae29c8ec5993d3ed9cfa37640976c8990
```

Historical F1-B provenance remains anchored to the previously FINAL-GREEN
candidate `a7d7e3fe9956d02cf6a3fb46fec1dbdaf780c30c` and its historical F1-A
parent `98776b8038b4ffe0b31aaf616748bc38544f6979`.

This refresh replays `gc_dry_run.py`, the architecture regression, and the
integration regression byte-for-byte. Only this contract updates landed-parent
and current-main integration authority.

The slice remains strictly non-destructive:
- no DELETE/bulk-delete/truncate;
- no repository delete API;
- no destructive migration;
- no background collector/scheduler;
- no R12 recovery semantics;
- no CAS FileAsset/FileBlob/FileReference/object-storage physical GC authority;
- no CTX persisted retention/index ownership;
- no ClientInvocationLedger cleanup;
- R11-F1-C remains RESERVED / NOT RELEASED;
- R11-G/H remain CLOSED.

Before any merge decision, require fresh exact-head Linux + Windows Architecture,
independent Issue #31 refreshed audit, and a fresh #31 <-> #68 boundary check
confirming F1-B dry-run does not cross into CAS physical lifecycle/GC authority.
