# AE-R11-F1-C — Transactional Task-Scoped GC Executor

Status: **CANONICAL INTEGRATION CANDIDATE — DESTRUCTIVE / FAIL-CLOSED**

Primary authority: Issue #31.

Historical stacked-development baseline:

```text
STACK_POLICY_ISSUE    = #85
STACK_POLICY_VERSION  = v1
STACK_MODE            = STACKED_DEVELOPMENT
STACK_DEPTH           = 2
CANONICAL_MAIN_FORK   = 628f61fd7894e1425daf9a989f4c4494f7492a94
STACK_PARENT_PR       = #83
STACK_PARENT_HEAD     = f06779c9ec557c7c32a4be58c71ddf3e01a233d7
PARENT_ARCHITECTURE   = #1275 GREEN/GREEN
CHILD_CLAIM           = R11-F1-C transactional task-scoped GC executor
CLOSED_AUTHORITIES    = R6 lifecycle; ClientInvocationLedger; R12 lease/recovery; CAS lifecycle/physical GC; CTX lifecycle
MERGE_ORDER           = #83 first; #82 must not merge before parent
INTEGRATION_STATUS    = NOT_CANONICAL / REQUIRES_POST-PARENT_REFRESH
branch                = work/ae-r11-f1-c-ccd6b64c
```


Canonical integration baseline:

```text
CANONICAL_MAIN_HEAD         = f58ce30b73488be4fb861cdcb3d613b509005c0e
CANONICAL_MAIN_ARCHITECTURE = #1298 GREEN/GREEN
UPSTREAM_RELEASE_PR         = #86 MERGED
UPSTREAM_RELEASE_HEAD       = 6905e613d229b9605a9483feb5598c4352854f14
PR82_REFRESH_BASE           = f58ce30b73488be4fb861cdcb3d613b509005c0e
INTEGRATION_STATUS          = CANONICAL_REFRESH / EXACT-MAIN CI + AUDIT REQUIRED
MERGE_AUTHORIZATION         = GRANTED CONDITIONALLY / USER
```

Historical origin:
- Repair B / PR #76 landed and post-merge Architecture #1240 was GREEN/GREEN.
- Repair C / PR #83 closes the absent invocation-id serialization gap at candidate level.
- This child is rebuilt from the exact independently FINAL-GREEN Repair-C parent HEAD before parent merge, as allowed by Issue #85 and the stricter Issue #31 local override.

This stage is the first destructive R11-F candidate. It does not broaden the
retention policy frozen by F0/F1-A/F1-B; it only executes a whole-Task
collection that a fresh in-transaction F1-B report proves collectible.

## 1. Authority sequence

The executor must perform this sequence in one UoW/transaction:

1. acquire the shared Task GC serialization fence on the candidate Task;
2. run a non-destructive F1-B probe to identify candidate
   CapabilityInvocation ids;
3. acquire each shared CapabilityInvocation GC serialization fence in
   deterministic id order;
4. rerun F1-B in the same transaction;
5. reject any probe/final graph or classification fingerprint drift;
6. when a caller supplies a prior dry-run fingerprint, require an exact match
   but never treat that stale external report as deletion authority;
7. delete only row kinds classified CANDIDATE by the fresh report;
8. verify exact rowcounts for every destructive step;
9. flush before commit so FK/constraint failures remain inside the transaction;
10. commit only after the entire delete set succeeds.

Any mismatch raises and rolls the UoW back. Partial collection is forbidden.

## 2. Frozen candidate scope

The destructive executor may remove only proven CANDIDATE rows from these
Agent/R6 persistence classes:

- Task;
- TaskBudget and TaskBudgetReservation;
- Task branch and branch context;
- execution;
- checkpoint and checkpoint pending invocation;
- resume claim;
- iteration;
- Agent tool call / tool result;
- CapabilityInvocation and invocation attempt;
- fork / retry / aggregate admission.

An unknown candidate row kind is a fail-closed error.

Task is physically deleted last.

Admission/provenance rows are removed before the branch/execution/checkpoint
rows they RESTRICT. Branch contexts are removed before branches. Branches are
deleted deterministic child-first so parent_branch_id RESTRICT remains a safety
fence rather than something the collector bypasses.

## 3. Serialization closure

Repair B established the writer side of the same authority used here:

- child Task parent_task_id writers share the Task GC fence;
- fresh AgentToolCall invocation_id writers share the CapabilityInvocation GC
  fence and reject an already-existing R6 invocation id;
- checkpoint parent writes are same-execution/session/task/branch only and must
  precede the child revision;
- normalized WAITING requires the canonical current checkpoint parent;
- legacy materialization applies the same lineage proof before transcript
  structural sharing.

F1-C therefore locks Task and candidate invocation authority before its final
F1-B revalidation.

## 4. Drift / rollback rules

The following all fail closed before commit:

- fresh F1-B returns FAIL_CLOSED;
- candidate Task count is not exactly one;
- an unopened candidate row kind appears;
- probe/final fingerprint differs;
- caller-provided fingerprint is stale;
- branch lineage cycles;
- pre-delete rowset counts drift;
- any DELETE rowcount differs from the fresh report;
- FK/constraint failure at flush;
- any other exception during the delete sequence.

The service explicitly rolls back on error. Integration evidence injects a
mid-delete failure and requires every task-owned row to remain present after
rollback.

Repeat collection after a successful commit is idempotent: an already-missing
Task returns an already-collected result and performs no mutation.

## 5. Transcript hard retain

Task-scoped F1-C does not import or delete:

- AgentTranscriptRepresentationRecord;
- AgentTranscriptPayloadNodeRecord;
- AgentTranscriptChunkRecord.

The F1-B transcript closure remains independently rooted. The frozen evidence phrase is: transcript representation/payload/chunks remain retained. Those transcript rows remain retained after the Task graph is collected.

## 6. Ownership boundaries

- ClientInvocationLedger remains R6-owned.
- CapabilityInvocation lifecycle remains R6-owned; R11 only deletes a proven
  terminal/unreachable invocation as part of the exact F1-B whole-Task graph.
- no R12 lease/recovery semantics;
- no CAS FileAsset/FileBlob/FileReference/FileProviderBinding lifecycle,
  READY release, ObjectStorage deletion/reconciliation, provider hydration, or
  CAS physical GC;
- no CTX Memory retention/lifecycle authority;
- no FK disabling, constraint bypass, or cross-owner cleanup.

If future evidence shows Agent deletion changes canonical CAS reference
lifetime, collection must stop before mutation until a separately audited
CAS-owned retain/release handoff exists.

## 7. Acceptance evidence

Architecture tests freeze:

- exact baseline and post-merge GREEN;
- Task fence before revalidation;
- invocation fences before final revalidation;
- two-pass F1-B revalidation with fingerprint drift rejection;
- exact rowcount fail-closed behavior;
- Task-last deletion;
- transcript hard-retain imports;
- R6/R12/CAS ownership boundaries;
- transaction rollback structure.

Integration tests prove:

1. a terminal candidate Task graph is collected atomically while transcript
   representation/payload/chunks remain;
2. a stale external dry-run fingerprint cannot authorize deletion;
3. an injected mid-delete failure rolls back to zero partial collection;
4. repeat collection after successful commit is idempotent.

## 8. Canonical integration boundary

The historical stacked prerequisites are satisfied: PR #83 and the explicit
upstream release dependency PR #86 are merged. The current candidate must now
be proven only against the exact accepted canonical baseline above.

Required integration sequence:

```text
PR #86 MERGED
-> post-#86 canonical main Architecture #1298 GREEN/GREEN
-> re-anchor PR #82 from exact canonical main
-> preserve production + integration-test semantics byte-identically
-> fresh exact-head Linux + Windows Architecture
-> independent exact-head integration re-audit
-> merge only under retained conditional user authorization with unchanged main
```

No R11 production grandchild before #82 canonical landing.

## 9. Merge boundary

This stage changes production destructive runtime behavior.

```text
production merge authorization = GRANTED CONDITIONALLY
```

The retained user authorization becomes executable only after the exact-main
refresh has fresh GREEN/GREEN Architecture, independent FINAL GREEN, no new
P0/P1/review/dependency blocker, and no canonical-main drift.
