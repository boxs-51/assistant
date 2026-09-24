# AE-R11-A Completion Candidate — Measurement + Storage Contract Freeze

**Repository:** `boxs-51/assistant`  
**Issue authority:** #31  
**Pull request:** #40  
**Canonical base:** `main@78479a64a97353094817b92a090449191782d366`  
**R11-A code/test candidate:** `688d5f5243db26575bdf453a3aa269d983062886`  
**Status:** COMPLETION CANDIDATE / INDEPENDENT AUDIT PENDING

> This document records R11-A evidence. It does not authorize R11-B by itself.

---

## 1. Stage objective

R11-A establishes reproducible persistence/performance evidence and freezes the
logical storage contract before any physical representation optimization.

R11-A intentionally changes no production persistence behavior.

---

## 2. Exact scope

R11-A candidate delta is limited to:

```text
docs/ROADMAP_NAMESPACE_REGISTRY.md
docs/agent_execution_r11/
se/tests/architecture/test_r11_a_persistence_baseline.py
se/tests/architecture/test_r11_a_branch_budget_memory_baseline.py
```

No production source, schema, migration, index, repository, runtime or client
persistence behavior is modified.

---

## 3. Upstream boundary

R11-A is re-anchored after:

```text
AE-R10   CLOSED / FINAL GREEN
PTC-1-3  CLOSED / FINAL GREEN
TV1-T9   CLOSED / MERGED / FROZEN / POST-MERGE GREEN
Issue #16 CLOSED
CTX #15  FUTURE / PARKED
TV1-T10 Issue #38 OPEN / T10-A CLAIMED / CONTRACT-DOC STAGE / NO RUNTIME DELTA
```

Canonical main remained:

```text
78479a64a97353094817b92a090449191782d366
```

through the R11-A candidate.

---

## 4. Planning findings resolved at contract level

### P1-R11-0-DEP-1

CLOSED by re-anchoring from post-TV1-T9 main and updating the roadmap registry
to current Agent / Tools / PTC / CTX status.

### P1-R11-0-GC-1

CLOSED at contract level.

R11-F must explicitly preserve the R6 ClientInvocationLedger state-aware
retention rule:

```text
expired TERMINAL -> collectible under terminal retention policy
RUNNING crash evidence -> never generic-TTL collected
```

R11 does not reinterpret RUNNING evidence as recovery permission.

### P2-R11-0-METRICS-1

CLOSED at contract level.

Every R11-A baseline dimension must receive an R11-H final disposition,
including memory per active execution.

---

## 5. Immutable RED baseline evidence

### Architecture #941 — persistence baseline probe

HEAD:

```text
68df37c5ebe4af31999669c8a99ca9367b477aac
```

Result:

```text
Windows  SUCCESS
Linux    exactly 1 intentional measurement sentinel failed
         1434 passed
         1 skipped
         51 warnings
         159 subtests passed
```

No non-sentinel failure occurred.

### Full-snapshot growth

```text
messages   final transcript   cumulative full-prefix copies   amplification
10         2,261 B            12,440 B                         5.502x
100        22,601 B           1,141,400 B                      50.502x
1000       226,001 B          113,114,000 B                    500.502x
```

This proves the current full-prefix checkpoint representation has the roadmap's
O(N^2) copied-byte growth shape.

### WAITING checkpoint pending-row write shape

```text
pending  SQL  SELECT  INSERT  UPDATE  flush
0        4    2       1       1       1
1        6    3       2       1       2
8        20   10      9       1       9
32       68   34      33      1       33
```

Current scaling is exactly:

```text
+1 pending invocation
 -> +1 CapabilityInvocation SELECT
 -> +1 pending-row INSERT
 -> +1 ORM flush
```

The entire checkpoint transition still uses one surrounding transaction and one
commit.

### Fork-safe reconstruction

```text
messages   SELECT/SQL   logical bytes   observed Linux elapsed
10         3            2,261           ~3.82 ms
100        3            22,601          ~5.52 ms
1000       3            226,001         ~7.10 ms
```

Query count is constant for this no-tool reconstruction shape.

Elapsed time is measurement evidence only, not a shared-runner correctness
threshold.

---

## 6. Extended RED baseline

### Architecture #944

HEAD:

```text
707ed6574b3b2b013feb796190ad0291e984987a
```

Result:

```text
Windows  SUCCESS
Linux    exactly 2 intentional measurement sentinels failed
         1437 passed
         1 skipped
         51 warnings
         159 subtests passed
```

No extra harness or production failure occurred.

### Durable FORK creation

```text
SQL       20
SELECT    12
INSERT    6
UPDATE    2
DELETE    0
flushes   6
BEGIN     1
COMMIT    1
ROLLBACK  0

branch rows after consume  2
new execution             present
new BranchContext         present
observed elapsed          ~19.66 ms
```

### TaskBudget contention

Two independent FORK requests race at final branch capacity:

```text
attempts                2
winner                  1
conflict                1
final active_branches   2
final budget revision   3

SQL       35
SELECT    26
INSERT    6
UPDATE    3
DELETE    0
flushes   6
```

Observed elapsed on #944 was ~45.43 ms.

Correctness authority is one-winner/one-conflict plus the final durable budget
state; timing is not the correctness gate.

### Memory per active execution

Fixed-shape Linux tracemalloc baseline:

```text
contexts                       128
retained allocation delta      787,812 B
approx bytes/context           6,154 B
```

Exact allocator bytes are informational and are not a hard cross-platform
threshold.

---

## 7. Reproducibility evidence

Architecture #945 ran after the first sentinel was removed.

```text
Windows  SUCCESS
Linux    exactly 1 remaining intentional sentinel failed
         1438 passed
         1 skipped
         51 warnings
         159 subtests passed
```

Structural branch/contention/memory values repeated:

```text
branch SQL/flush     20 / 6
contention outcome   1 winner / 1 conflict
contention SQL       35
memory               787,812 B / ~6,154 B per context
```

Elapsed timings varied, confirming the decision not to encode them as fixed CI
thresholds.

---

## 8. Exact all-sentinel-removed GREEN

Architecture #946:

HEAD:

```text
688d5f5243db26575bdf453a3aa269d983062886
```

Result:

```text
Linux:
1439 passed
1 skipped
51 warnings
159 subtests passed

Windows:
101 passed
2 warnings

Conclusion:
SUCCESS / SUCCESS
```

---

## 9. Transcript storage contract frozen by R11-A

R11-A freezes:

```text
AgentExecutionCheckpoint
    = continuation safe-point authority

transcript_ref
    = opaque Agent-persistence reference to one exact immutable transcript
      representation

transcript_version
    = exact non-negative representation version/sequence
```

Explicit separations:

```text
transcript_ref != Context locator
transcript_ref != Memory identity
transcript_ref != Central Asset identity
transcript_ref != ToolResponsePayload identity

transcript_version != AgentExecution.revision
transcript_version != FileAsset.revision
```

A storage representation may be FULL + immutable DELTA segments, but physical
representation must reconstruct the exact canonical message sequence.

Missing/corrupt/ambiguous physical representation fails closed.

No fallback to mutable Session history, a newer checkpoint, Context search or
model reconstruction is permitted.

---

## 10. Reader convergence gate

Production still contains direct inline-snapshot dependencies in:

```text
FORK planning/consume
RETRY planning/consume
fork-safe/committed transcript loaders
RESUME planning/materialization
legacy resume compatibility path
```

Resume authority additionally fingerprints the materialized canonical messages
inside ResumePlan.

Therefore:

```text
R11-B
  may introduce immutable physical transcript representation

R11-C
  must converge every legal reader onto one canonical materializer
  and prove inline/ref semantic equivalence

R11-D
  only then may cut the checkpoint writer over to ref-only storage
```

Resume/fork/retry fingerprints remain based on canonical logical content, not
physical reference strings.

---

## 11. Retention and GC boundary

Minimum future R11-F roots include:

- AgentExecution.current_checkpoint_id;
- TaskBranch.base_checkpoint_id;
- ForkAdmission source checkpoint;
- RetryAdmission source checkpoint;
- active/usable ResumeClaim checkpoint;
- runtime seed / receipt validation evidence;
- every transcript parent/base segment needed by a live root.

ClientInvocationLedger remains a separate R6 authority:

```text
expired TERMINAL -> collectible
RUNNING           -> preserved as crash/replay-safety evidence
```

Central Asset GC, ToolResponsePayload GC and CTX/Memory retention are not R11
ownership.

---

## 12. Query/index rule

R11-A freezes the hot-query inventory.

R11-E may alter indexes only with:

1. a measured hot query;
2. query-plan evidence;
3. lock/correctness audit;
4. before/after evidence.

R11-A adds no speculative index.

---

## 13. R11-H evidence obligation

The final phase must report a disposition for every R11-A dimension:

- checkpoint bytes written;
- resume latency p50/p95/p99;
- reconstruction latency p50/p95/p99;
- branch create latency;
- DB writes per Agent iteration;
- rows per Task;
- memory per active execution;
- TaskBudget contention;
- reconstruction depth;
- SQL statement count;
- flush count.

Allowed disposition:

```text
IMPROVED
UNCHANGED / NON-REGRESSED
INTENTIONALLY TRADED OFF with rationale
NOT STABLY THRESHOLDABLE with retained measurement evidence
```

No baseline metric may disappear from R11-H.

---

## 14. R11-A closure gate

At this completion-candidate commit:

```text
measurement harness:               GREEN
immutable RED evidence:            PRESENT
storage contract:                  FROZEN
reader convergence map:            FROZEN
retention boundary:                FROZEN
query/index policy:                FROZEN
production behavior change:        NONE
exact code/test HEAD CI:           GREEN (#946)
independent audit:                 PENDING
```

R11-A is **not final-closed** until the independent audit records no blocking
A-level P0/P1.

R11-B remains CLOSED until that decision.
