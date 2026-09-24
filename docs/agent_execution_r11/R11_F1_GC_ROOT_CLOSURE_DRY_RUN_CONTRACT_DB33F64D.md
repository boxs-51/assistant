# AE-R11-F1-A — Deterministic GC Root-Closure / Dry-Run Contract

Status: **F1-A EVIDENCE-FIRST CANDIDATE — NO PRODUCTION DELETE**

Authority:
- Issue #31
- landed parent PR #63 audited HEAD: `a21be92cd74deb9b91bd3112b49f5eedb1bd946d`
- parent merge commit / canonical main: `8b4b87ff1d34e256bf3e6f2032f21b686fa43456`
- parent stage: R11-F0 MERGED / CLOSED / REFRESH FINAL GREEN
- integration: direct child of canonical main
- this F1-A stage remains evidence-first and authorizes no merge by itself.

## Objective

F1-A freezes the executable shape of a future collector before any destructive path exists. The collector must derive a deterministic closure from one consistent persistence view, explain every retained/candidate classification, and fail closed when the frozen F0 graph is incomplete or inconsistent.

## Root snapshot

A root snapshot is an immutable value assembled in one persistence view. Root families are processed in this fixed order:

1. Task / TaskBudget / TaskBudgetReservation replay authority;
2. AgentExecution state and current/base checkpoint authority;
3. Branch and ResumeClaim authority;
4. Fork/Retry/Aggregate admission provenance;
5. execution-side iteration/tool-call/COMMITTED tool-result evidence;
6. CapabilityInvocation semantic association by `execution_id` and retained attempts;
7. AgentExecution semantic lineage;
8. checkpoint pending-invocation watermark authority;
9. checkpoint transcript representation roots;
10. transcript representation/payload/chunk closure;
11. R6 ClientInvocationLedger terminal-vs-RUNNING retention authority;
12. separately policy-retained terminal history.

WAITING and RUNNING executions are retain-only in F1-A. Age never turns them into candidates.

## Deterministic closure

Traversal uses:
- stable root-family order above;
- stable identity ordering within each family;
- explicit edge labels;
- a visited set keyed by row kind + durable identity;
- a work queue whose ordering is deterministic;
- retain/fail-closed classification on cycles, dangling required authority, cross-Task ancestry, or unresolved semantic edges.

Semantic edges MUST be traversed explicitly:
- `CapabilityInvocation.execution_id -> AgentExecution.id`;
- `AgentExecution.parent_execution_id`;
- `AgentExecution.retry_of_execution_id`;
- `AgentExecution.base_execution_id`;
- `AgentExecution.base_checkpoint_id`.

F0 graph parity also requires explicit parent-to-child closure for checkpoint pending-invocation authority:
- retained `AgentExecutionCheckpointRecord.checkpoint_id` -> every attached `AgentCheckpointPendingInvocationRecord`;
- the concrete child FK is `AgentCheckpointPendingInvocationRecord.checkpoint_id -> AgentExecutionCheckpointRecord.checkpoint_id` with `ON DELETE CASCADE`;
- that CASCADE is physical ownership only and never collection/liveness authority.

SQL FK traversal is insufficient and CASCADE is never liveness authority.

## Dry-run classification

F1-A separates classification from deletion. A dry-run item records:

```text
row_kind
row_identity
classification = RETAIN | CANDIDATE | FAIL_CLOSED
reason_code
root_or_edge_source
```

Required reason codes include:
- `ACTIVE_EXECUTION`
- `REPLAY_IDEMPOTENCY_AUTHORITY`
- `CLAIM_OR_ADMISSION_PROVENANCE`
- `EXECUTION_REPLAY_EVIDENCE`
- `CAPABILITY_SIDE_EFFECT_AUTHORITY`
- `EXECUTION_LINEAGE`
- `CHECKPOINT_PENDING_INVOCATION_AUTHORITY`
- `TRANSCRIPT_REACHABILITY`
- `CLIENT_LEDGER_CRASH_EVIDENCE`
- `POLICY_RETAINED_HISTORY`
- `R6_TERMINAL_POLICY_ELIGIBLE`
- `UNREACHABLE_POLICY_ELIGIBLE`
- `INCOMPLETE_OR_INCONSISTENT_GRAPH`

The same persistence snapshot must produce byte-for-byte equivalent ordered classifications across repeated dry-runs.

## Candidate fence

A row is a CANDIDATE only if its row class has an explicit collection policy and the complete frozen closure does not reach it. A fully unreachable terminal fixture may be classified as a candidate; F1-A does not delete it.

The following remain RETAIN:
- WAITING/RUNNING/replayable executions;
- active/usable ResumeClaim authority;
- admission/branch provenance still required by retained history;
- TaskBudget reservations while logical replay/idempotency remains possible;
- COMMITTED tool-result and matching CapabilityInvocation side-effect authority;
- required parent/retry/base execution/checkpoint lineage;
- pending-invocation watermark rows attached to any retained checkpoint;
- transcript representation/payload/chunks reachable from any retained checkpoint.

A retained checkpoint therefore retains its complete `agent_checkpoint_pending_invocations` watermark set. F1-A may not classify those rows independently merely because the physical FK uses CASCADE.

This is production correctness authority, not a string-only inventory. `AgentResumePlanningService.build_resume_plan(...)` calls `load_checkpoint_pending_invocations(checkpoint_id)` and fails closed with `CHECKPOINT_PENDING_INVOCATIONS_MISSING` when a CONNECTION-WAITING checkpoint has lost its pending snapshots. FORK and RETRY planning also inspect the same pending-invocation watermark before accepting a source checkpoint.

Shared transcript storage remains retained while any live representation reaches it.

R6 ClientInvocationLedger remains a separate retention authority:
- RUNNING ledger rows preserve crash/ambiguity evidence and are RETAIN even when they are old;
- only TERMINAL ledger rows may become CANDIDATE under the already-frozen R6 terminal expiry/retention policy;
- F1-A does not redefine R6 invocation state, retry, TTL, or cleanup semantics;
- an age-only rule must never classify a RUNNING ledger row as collectible.

## Transaction / destructive safety

There is no destructive executor in F1-A. Future deletion must:
1. consume a proven classification produced from one consistent view;
2. revalidate authority in the deleting transaction;
3. preserve FK safety;
4. abort atomically on stale/inconsistent authority;
5. never leave a partially-invalid durable graph.

Repeated dry-run is idempotent and side-effect free.

## Hard non-scope

- no repository DELETE API or destructive migration;
- no age-only scan;
- no R12 leases, owner_instance_id, stale-RUNNING interpretation, WAITING(RECOVERY), or recovery coordinator;
- no CAS asset/blob/reference/object-storage lifecycle or GC;
- no CTX persisted index/loader/retention ownership;
- no Memory/Personalization lifecycle;
- no speculative E2 query/index optimization;
- no redefinition of R6 ClientInvocationLedger lifecycle/retry/TTL semantics;
- historical pre-integration fence: `no merge of PR #60 or PR #63`; both parents are now landed under explicit user authorization, and this historical text grants no authority to merge F1-A.

## F1-A exit evidence

Architecture evidence must freeze:
1. exact F0 semantic edge inventory;
2. fixed deterministic root/edge ordering and visited-set requirement;
3. explicit CapabilityInvocation semantic join;
4. explicit execution lineage traversal;
5. separation of classification from deletion;
6. retain-only WAITING/RUNNING behavior;
7. TaskBudget reservation, COMMITTED result/invocation, admission/claim, checkpoint pending-invocation, and transcript reachability fences;
8. checkpoint -> pending-invocation FK/CASCADE shape plus explicit F0->F1 graph parity and a real production consumer;
9. deterministic dry-run reason schema;
10. R6 ClientInvocationLedger terminal-vs-RUNNING retention fence;
11. no production DELETE/GC implementation in this slice.


## M2 post-F0 landing refresh

R11-E2 / PR #60 and R11-F0 / PR #63 have landed under the
SERIAL LAND-THEN-REFRESH integration discipline.

Canonical M2 base is now:

```text
8b4b87ff1d34e256bf3e6f2032f21b686fa43456
```

Historical F1-A parent provenance remains
`db33f64d25a98709ff487f4dcba3281074f62f0c`. The original pre-integration
fence text `no merge of PR #60 or PR #63` is retained above only as historical
evidence required by the frozen architecture regression; it is not a statement
of current repository state.

This refresh replays the historical F1-A architecture regression byte-for-byte
and updates only this contract's integration authority/history. The executable
scope remains evidence-only: no production DELETE/GC executor or repository
DELETE API is added.

All previous hard fences remain in force:
- WAITING/RUNNING remain retain-only;
- R6 ClientInvocationLedger RUNNING evidence is not age-collected;
- checkpoint pending-invocation graph parity remains required;
- no R12 recovery semantics;
- no CAS physical asset/blob/reference/object-storage lifecycle or GC;
- no CTX persisted retention/index ownership;
- no Memory/Personalization lifecycle;
- R11-F1-C remains RESERVED / NOT RELEASED;
- R11-G/H remain CLOSED.

Fresh exact-head Linux + Windows Architecture and refreshed independent Issue
#31 audit are required before any merge decision.
