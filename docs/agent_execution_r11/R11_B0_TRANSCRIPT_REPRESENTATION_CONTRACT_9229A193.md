# AE-R11-B0 Transcript Representation Contract Freeze

**Repository:** `boxs-51/assistant`  
**Issue authority:** #31  
**Canonical base:** `main@9229a19308fd121fde8aa2b4e39ecb597e70ea45`  
**Branch:** `work/ae-r11-b-9229a193`  
**Stage:** AE-R11-B / B0  
**Status:** CONTRACT CANDIDATE / PRODUCTION IMPLEMENTATION BLOCKED PENDING INDEPENDENT AUDIT

---

## 1. Entry authority

AE-R11-A is CLOSED / MERGED / POST-MERGE GREEN.

Final R11-A authority:

- PR #40 merged to `main@9229a19308fd121fde8aa2b4e39ecb597e70ea45`;
- final independent audit: GREEN;
- P1-R11-0-DEP-1: CLOSED;
- P1-R11-A-MEAS-1: CLOSED;
- P1-R11-0-GC-1: CLOSED AT CONTRACT LEVEL;
- P2-R11-0-METRICS-1: CLOSED AT CONTRACT LEVEL;
- P2-R11-A-DOCS-1: FIXED before merge;
- post-merge Architecture #970:
  - Linux: 1443 passed, 1 skipped, 51 warnings, 159 subtests passed;
  - Windows: 101 passed, 2 warnings;
  - SUCCESS / SUCCESS.

R11-B owns only the physical transcript representation contract and its
representation-layer implementation.

This B0 document changes no production code, schema, model, repository, runtime,
reader, writer, migration or index.

---

## 2. B0 blocking preflight findings

Independent preflight opened three B-level P1s.

### P1-R11-B-PREFLIGHT-1 — cadence/depth circularity

R11-B must define a finite deterministic reconstruction safety envelope before
R11-C implements the canonical reader. R11-B cannot defer the correctness bound
to future R11-G performance measurements.

### P1-R11-B-PREFLIGHT-2 — ref/version legal states under-specified

The current checkpoint schema can represent ambiguous or incomplete combinations
of `transcript_snapshot`, `transcript_ref` and `transcript_version`.

R11-B must freeze the legal state matrix before any ref-backed writer exists.

### P1-R11-B-PREFLIGHT-3 — checkpoint lineage must not become transcript ancestry

`AgentExecutionCheckpoint.parent_checkpoint_id` is continuation/checkpoint
lineage authority. It is not physical transcript-segment ancestry.

R11-B must define a separate immutable transcript parent/base identity.

---

## 3. Stage ownership and hard non-scope

### R11-B owns

- Agent-local transcript representation identity;
- immutable FULL / DELTA representation semantics;
- representation-local ancestry;
- representation version semantics;
- finite DELTA reconstruction safety bound;
- legal checkpoint representation state matrix;
- representation corruption / missing / ambiguity failure semantics;
- physical structural-sharing requirement needed by the R11 no-O(N²) gate;
- dormant representation-layer schema/model/repository implementation only
  after B0 receives independent GREEN.

### R11-B does not own

- canonical continuation reader convergence — R11-C;
- changing legal continuation callsites to consume ref-only checkpoints — R11-C;
- checkpoint writer cutover, DUAL backfill or REF-BACKED emission — R11-D;
- batching / query / index hardening — R11-E;
- retention deletion / garbage collection — R11-F;
- load tuning beyond the frozen B safety envelope — R11-G;
- final performance/compatibility exit — R11-H;
- lease, owner instance, stale RUNNING detection or crash recovery — AE-R12;
- Central Asset / Context / Memory identity;
- Tools V1 live-harness behavior;
- provider retry/fallback policy.

No R11-B commit may make the current WAITING writer emit a ref-only checkpoint.

---

## 4. Logical authority preserved

A checkpoint still identifies one exact canonical logical transcript.

For checkpoint C:

```text
materialize(C)
==
the same ordered canonical message sequence
that the pre-R11 inline transcript_snapshot represented
```

Logical equivalence includes all inference-significant message fields:

- order;
- role;
- content;
- tool-call identity/payload;
- tool result content;
- metadata already participating in inference semantics.

Physical segmentation never changes logical transcript meaning.

The existing R8/R9 continuation fingerprints remain semantic evidence. R11
must not redefine those fingerprints merely to fit a new storage shape.

---

## 5. Agent-local representation identity

### 5.1 Checkpoint-facing identity

A ref-backed checkpoint identifies an exact immutable representation head by:

```text
(transcript_ref, transcript_version)
```

Both members are required together.

`transcript_ref` is an opaque Agent-persistence identity.

It is explicitly not:

```text
Context locator
Memory identity
Central Asset identity
File/Tool payload identity
checkpoint_id
execution_id
branch_id
```

`transcript_version` is an immutable representation-local sequence value. It
is not AgentExecution.revision and is not globally unique by itself.

The pair is the authority.

### 5.2 Representation-local version

For one physical representation ancestry:

```text
FULL root:
    version = 0

DELTA child:
    version = parent.version + 1
```

Different immutable refs may legally have the same numeric version.

A checkpoint that observes no logical transcript change may reuse the exact same
`(transcript_ref, transcript_version)`. It must not create an empty DELTA only
to manufacture a new version.

---

## 6. FULL and DELTA semantics

### 6.1 FULL

A FULL representation is a logical reconstruction anchor.

Properties:

```text
kind = FULL
parent_transcript_ref = NULL
parent_transcript_version = NULL
delta_depth = 0
version = 0
```

A FULL representation must reconstruct the entire canonical logical transcript
for its checkpoint without consulting checkpoint lineage.

"FULL" is a logical property. It is not permission to rewrite every unchanged
historical message payload into a new JSON blob.

R11's final exit gate is:

> No O(N²) checkpoint growth.

Therefore the B physical representation must permit immutable structural sharing
of unchanged payload. Re-anchoring to FULL must not require duplicating the
unchanged canonical prefix merely because a new checkpoint was created.

The exact storage primitive used to provide that structural sharing is a B
implementation choice and must receive its own tests before writer cutover.

### 6.2 DELTA

A DELTA contains only an exact canonical append suffix relative to one exact
parent representation pair.

```text
kind = DELTA
parent_transcript_ref != NULL
parent_transcript_version != NULL
version = parent.version + 1
delta_depth = parent.delta_depth + 1
```

A DELTA may:

- append one or more canonical messages.

A DELTA may not:

- rewrite a historical message;
- delete a historical message;
- reorder a historical message;
- reinterpret a tool call/result;
- point to a checkpoint ID as its physical parent;
- be empty.

If the new logical transcript is not an exact strict append of one unambiguous
parent transcript, the representation must use a FULL logical anchor rather
than inventing a DELTA.

This rule applies in particular to any future aggregate/reconstruction shape
where no single exact strict-prefix parent can be proven.

---

## 7. Finite reconstruction safety envelope

R11-C requires a finite deterministic boundary before its reader can be
implemented.

The canonical roadmap example is:

```text
C10 FULL
C11 DELTA
C12 DELTA
...
C19 DELTA
C20 FULL
```

That example permits at most nine DELTA hops between FULL anchors.

B0 therefore freezes:

```text
HARD_MAX_DELTA_DEPTH = 9
```

Rules:

1. FULL has `delta_depth = 0`.
2. DELTA has `delta_depth = parent.delta_depth + 1`.
3. A DELTA with depth > 9 is invalid.
4. If the next append would require depth 10, a new FULL logical anchor is
   mandatory.
5. A writer may choose FULL earlier than depth 9.
6. R11-G may tune the normal FULL trigger only inside the compatibility envelope
   `1..9`.
7. R11-G may not increase the hard maximum above 9 without reopening the
   representation contract and rerunning R11-C reader safety evidence.
8. The materializer must fail closed if persisted depth exceeds the hard bound.

The value 9 is a correctness/safety ceiling derived from the roadmap's own
periodic FULL example. It is not a latency target.

Timing remains evidence-only.

### 7.1 No quadratic-growth loophole

The finite depth bound and the no-O(N²) write-growth gate are both mandatory.

An implementation is invalid if it satisfies depth <= 9 only by repeatedly
copying the entire unchanged transcript prefix into new physical payload.

B implementation and R11-H evidence must account separately for:

- newly appended canonical payload bytes;
- representation metadata bytes;
- bytes rewritten solely to create a new FULL anchor.

The third category must not recreate the historical full-prefix-per-checkpoint
quadratic shape.

---

## 8. Separate transcript ancestry

Checkpoint lineage:

```text
AgentExecutionCheckpoint.parent_checkpoint_id
```

remains continuation lineage only.

Transcript physical ancestry uses a distinct identity:

```text
parent_transcript_ref
parent_transcript_version
```

Rules:

- FULL has no transcript parent;
- DELTA has exactly one transcript parent pair;
- the parent pair must resolve to an immutable committed representation;
- parent.version + 1 must equal child.version;
- parent.depth + 1 must equal child.depth;
- a representation cannot parent itself;
- cycles are invalid;
- missing parent is invalid;
- branches may share one immutable parent representation and diverge into
  different child refs;
- retry/fork sharing of immutable prefix data is allowed;
- checkpoint parentage neither proves nor substitutes for transcript parentage.

Example:

```text
checkpoint lineage:
    C1 -> C2 -> C3

physical transcript representation:
    T0/FULL
       |\
       | T1a/DELTA   (branch A)
       |
       T1b/DELTA     (branch B)
```

A forked branch may share `T0` even when its checkpoint lineage differs.

---

## 9. Legal checkpoint representation state matrix

B0 freezes exactly three legal checkpoint representation states.

### 9.1 LEGACY_INLINE

```text
transcript_snapshot != NULL
transcript_ref       == NULL
transcript_version   == NULL
```

This is the current pre-cutover representation.

### 9.2 REF_BACKED

```text
transcript_snapshot == NULL
transcript_ref       != NULL
transcript_version   != NULL
```

This state is semantically defined in B, but current writers are not authorized
to emit it until R11-C reader convergence is GREEN and R11-D opens writer
cutover.

### 9.3 DUAL

```text
transcript_snapshot != NULL
transcript_ref       != NULL
transcript_version   != NULL
```

DUAL is valid only when both representations materialize to exactly the same
canonical logical transcript.

DUAL is migration/equivalence evidence. It is not a precedence rule.

### 9.4 Invalid states

All other shapes fail closed, including:

```text
snapshot NULL, ref NULL
ref != NULL, version NULL
ref NULL, version != NULL
snapshot != NULL, ref NULL, version != NULL
DUAL snapshot/ref logical mismatch
ref points to missing representation
ref/version pair does not match immutable representation
```

The current database may physically permit some of these combinations. B0
freezes their semantic invalidity before later application/schema enforcement.

---

## 10. DUAL mismatch semantics

R11-C will own the canonical materializer and equivalence check.

For DUAL:

```text
inline = canonicalize(transcript_snapshot)
ref    = reconstruct(transcript_ref, transcript_version)

inline == ref
    -> valid

inline != ref
    -> fail closed
```

Forbidden behavior:

```text
prefer snapshot silently
prefer ref silently
choose whichever is newer
fall back to Session history
fall back to another checkpoint
ask Context/Memory to reconstruct
model-generate missing content
```

No representation source outranks corruption evidence.

---

## 11. Representation integrity

Every committed physical representation must be independently verifiable.

Minimum semantic metadata:

```text
transcript_ref
transcript_version
kind                  FULL | DELTA
parent_transcript_ref
parent_transcript_version
delta_depth
logical_message_count
logical_transcript_fingerprint
immutable payload / immutable payload-root identity
created_at
```

The exact SQL/table shape is intentionally not frozen by B0. B implementation
may refine physical columns only if all semantic invariants above remain true.

Integrity requirements:

- representation rows/objects are immutable after commit;
- logical fingerprint is computed from the canonical reconstructed transcript;
- DELTA parent pair is part of the immutable representation identity proof;
- message count must agree with materialized output;
- duplicate replay of the same representation creation intent must not create
  conflicting logical content under one ref;
- missing/corrupt/ambiguous representation fails closed.

---

## 12. Failure categories

The following failure classes are contractually distinct even if R11-C maps
them to one internal exception type initially:

```text
INVALID_CHECKPOINT_REPRESENTATION_STATE
MISSING_TRANSCRIPT_REPRESENTATION
TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH
TRANSCRIPT_REPRESENTATION_ANCESTRY_INVALID
TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED
TRANSCRIPT_REPRESENTATION_CORRUPT
DUAL_TRANSCRIPT_MISMATCH
```

None authorizes fallback to mutable history.

These are internal Agent persistence categories. B0 does not add or change a
public wire protocol.

---

## 13. Reader/writer stage ordering

The stage boundary remains:

```text
R11-B
    define + implement dormant physical representation
    current checkpoint writer remains inline-authoritative

        |
        v

R11-C
    canonical materializer
    all RESUME / RETRY / FORK / AGGREGATE / runtime-seed legal readers
    become dual-capable
    DUAL equality/corruption tests GREEN

        |
        v

R11-D
    writer/backfill cutover
    DUAL emission/backfill as needed
    REF_BACKED emission only after C gate
```

B must not bypass C by wiring a ref-backed writer early.

---

## 14. R11-C required consumer convergence

Current inline assumptions exist in at least:

- resume planning/materialization;
- retry planning/replay;
- fork planning/fork-safe reconstruction;
- runtime persistence validation;
- R8/R9 runtime seed / control paths;
- aggregate continuation inputs where checkpoint transcript is consumed.

R11-B does not edit those callsites.

R11-C must replace direct `checkpoint.transcript_snapshot is not None`
authority with one canonical representation materializer and preserve all
existing lineage/revision/commitment checks.

---

## 15. Fork / retry / aggregate representation rules

### Fork

A fork may share the exact immutable source transcript representation.

If its transcript later appends branch-local canonical messages, a DELTA may use
the shared source representation as parent if and only if the result is a strict
append.

### Retry

Retry may share an immutable checkpoint representation when the logical source
transcript is unchanged.

A new physical representation is not required merely because the execution ID
changed.

### Aggregate

AGGREGATE must not pick an arbitrary source branch as DELTA parent.

If the aggregate transcript is not a strict append of one exact canonical parent
representation, it must create a FULL logical anchor.

This keeps physical ancestry honest and prevents cross-branch message rewriting
from masquerading as append-only DELTA.

---

## 16. Retention handoff

B defines immutable representation edges; R11-F owns deletion.

Future GC must treat a ref-backed checkpoint as a live root for its exact
representation and every immutable representation dependency required to
materialize it.

B0 does not delete anything.

GC must not infer liveness solely from checkpoint
`parent_checkpoint_id`, because checkpoint lineage is not transcript ancestry.

ClientInvocationLedger replay-safety rules frozen in R11-A remain unchanged.

---

## 17. Cross-track boundaries

### TV1-T10

Issue #38 is live Tools authority. Current T10 work remains under
`tools/v1/**` and does not own Agent transcript persistence.

No B0 identity is a Tools artifact identity.

### CAS / CTX / Memory

Agent `transcript_ref` remains Agent-local persistence identity.

B0 does not route transcript payload into Central Asset Storage, Context,
Personalization or Memory.

Those tracks require their own future bridge/retention contracts.

### AE-R12

No lease/recovery semantics are introduced here.

---

## 18. B implementation gate after B0

Only after independent audit closes all three B preflight P1s may R11-B
production implementation start.

Expected first implementation scope remains representation-layer only:

- immutable transcript representation model/storage;
- representation identity factory;
- integrity constraints;
- repository create/read operations;
- representation-specific tests;
- migration from current Alembic head only if required by the approved physical
  design.

Still forbidden at that point:

- changing WAITING writer output to REF_BACKED;
- removing inline snapshot support;
- changing resume/retry/fork/aggregate readers;
- GC deletion.

Any migration must preserve all existing LEGACY_INLINE checkpoints.

---

## 19. B0 acceptance matrix

### P1-R11-B-PREFLIGHT-1

Resolved by contract when auditor accepts:

```text
hard maximum DELTA depth = 9
FULL forced before depth 10
R11-G may tune earlier only inside 1..9
depth violation fails closed
FULL cannot be implemented as an O(N²) unchanged-prefix rewrite loophole
```

### P1-R11-B-PREFLIGHT-2

Resolved by contract when auditor accepts:

```text
LEGACY_INLINE
REF_BACKED
DUAL
exact invalid-state matrix
DUAL mismatch fail-closed
no silent representation precedence
```

### P1-R11-B-PREFLIGHT-3

Resolved by contract when auditor accepts:

```text
checkpoint parent = continuation lineage only
DELTA parent = separate transcript representation pair
branch sharing/divergence is representation-DAG semantics
no checkpoint ID reused as transcript parent authority
```

---

## 20. Stop conditions

Stop R11-B and return to contract review if implementation requires any of:

1. ref-only checkpoint emission before R11-C;
2. using `parent_checkpoint_id` as DELTA parent;
3. accepting ref without version or version without ref;
4. silently preferring one side of a mismatching DUAL checkpoint;
5. DELTA rewriting/deleting/reordering historical messages;
6. unbounded DELTA depth;
7. increasing depth > 9 based only on performance tuning;
8. copying unchanged full transcript payload repeatedly to satisfy depth while
   recreating quadratic write growth;
9. using CAS/CTX/Memory identity as `transcript_ref`;
10. fallback to mutable Session history after representation corruption;
11. changing R12 recovery semantics.

---

## 21. B0 decision

At this contract candidate:

```text
R11-A: CLOSED / MERGED / POST-MERGE GREEN
R11-B: CLAIMED
B0 docs: candidate
production B implementation: BLOCKED
R11-C+: CLOSED

P1-B-PREFLIGHT-1: proposed resolution frozen
P1-B-PREFLIGHT-2: proposed resolution frozen
P1-B-PREFLIGHT-3: proposed resolution frozen
```

Next legal action is independent B0 contract audit.

No production representation code is authorized until that audit is GREEN.
