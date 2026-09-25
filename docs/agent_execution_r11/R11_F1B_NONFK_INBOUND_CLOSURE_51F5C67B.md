# R11-F1-B non-FK inbound closure hardening

Status: refreshed repair candidate for `P1-R11-F1B-NONFK-INBOUND-CLOSURE-2`.

Exact baseline:

```text
canonical main = 51f5c67bdbd3e503b13542c7c82980b87f420d5f
post-PR#75 Architecture #1218 = GREEN/GREEN
repair branch = work/ae-r11-f1b-nonfk-inbound-closure-200aa3dc
branch name is historical; branch content is re-anchored to main@51f5c67b
```

## Problem

R11-F1-B classifies a task-scoped graph only after reverse-scanning durable
inbound references that can keep candidate rows live. Three semantic SQL
columns are not foreign-key protected and were not covered by the prior
reverse closure:

- `AgentExecutionRecord.branch_id`;
- `AgentExecutionRecord.current_checkpoint_id`;
- `AgentExecutionCheckpointRecord.parent_checkpoint_id`.

Because these are plain String references, a retained row outside the candidate
set can name a candidate Branch or Checkpoint without the database preventing
deletion of the target.

## Repair

F1-B fails closed when any of these exact inbound shapes exist:

```text
external AgentExecution.branch_id
    -> candidate AgentTaskBranch.branch_id

external AgentExecution.current_checkpoint_id
    -> candidate AgentExecutionCheckpoint.checkpoint_id

external AgentExecutionCheckpoint.parent_checkpoint_id
    -> candidate AgentExecutionCheckpoint.checkpoint_id
```

Frozen reason codes:

- `external_execution_branch_reference`;
- `external_execution_current_checkpoint_reference`;
- `external_checkpoint_parent_reference`.

The external checkpoint scan excludes the candidate checkpoint set by
checkpoint_id, so an internal parent/child checkpoint edge does not create a
false external blocker.

## Required semantics

Any one of the three references makes the whole report:

```text
classification = FAIL_CLOSED
reason_code = INCOMPLETE_OR_INCONSISTENT_GRAPH
has_candidates = false
```

The fingerprint remains deterministic because the final problem source is the
sorted, deduplicated set of reason strings.

The builder remains read-only. This repair adds only SELECT-based reverse
closure; it does not insert, update, delete, flush, commit, or rollback.

## Regression evidence

Three independent integration regressions seed an otherwise policy-eligible
terminal candidate and then add exactly one retained external reference:

1. an external Execution whose `branch_id` names a candidate Branch;
2. an external Execution whose `current_checkpoint_id` names a candidate
   Checkpoint;
3. an external Checkpoint whose `parent_checkpoint_id` names a candidate
   Checkpoint.

Each regression requires whole-report FAIL_CLOSED, zero candidates, the exact
reason code, and identical before/after durable row counts.

Architecture evidence freezes the three field names and reason codes while
retaining the read-only/no-destructive-SQL contract.

## Refresh evidence

The previous candidate was built from
`200aa3dcce3701b620e510a2a618ebccaddb6dff`. Canonical main advanced seven
commits through CTX-F4B and CAS-F5-0 to
`51f5c67bdbd3e503b13542c7c82980b87f420d5f`.

The drift is path-disjoint from this repair. The branch was nevertheless
re-anchored exactly as required by Issue #31's refresh-on-drift rule, and this
document now names the current integration baseline. Fresh exact-head CI and
independent audit are required again; historical #1213 success is not current
integration authority.

## Relationship to F1-C semantic-writer serialization

This repair is **Repair A** and must land before the concurrent-writer repair.

`P1-R11-F1C-SEMANTIC-EDGE-RACE-1` remains independently open. Draft PR #76
is parked as Repair B until this static closure repair lands and canonical main
returns GREEN/GREEN. Repair B must then be refreshed/re-anchored from that exact
post-Repair-A baseline before its own merge gate.

## Authority boundaries

- R11-F1-C destructive implementation remains HOLD.
- No DELETE authority is introduced.
- No schema migration is introduced.
- ClientInvocationLedger remains R6-owned.
- No R12 lease/recovery/stale-RUNNING semantics.
- CAS hard-deny remains unchanged: no FileAsset/FileBlob/FileReference/
  FileProviderBinding lifecycle, READY release, ObjectStorage deletion or
  reconciliation, provider hydration/F5, or CAS physical GC.
- R11-G/H remain CLOSED.
- Merge authorization = NONE.
