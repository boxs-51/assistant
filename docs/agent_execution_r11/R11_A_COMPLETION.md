# AE-R11-A Completion Candidate — Measurement + Storage Contract Freeze

**Repository:** `boxs-51/assistant`  
**Issue authority:** #31  
**Pull request:** #40  
**Canonical base:** `main@78479a64a97353094817b92a090449191782d366`  
**R11-A code/test candidate:** `efdc3924d1bec4453772d746a9b4b5e18831281a`  
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
TV1-T10 Issue #38 OPEN / T10-A CLOSED-GREEN / T10-B OPEN / T10-C CLOSED
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


---

## 15. Independent-audit P1 closure candidate — expanded pre-optimization baseline

Independent audit on the earlier completion candidate found that R11-A had not
yet measured every dimension promised before R11-B. The owner preserved all
earlier evidence and added new RED-first measurement coverage.

### Architecture #951 — real writer bytes + reconstruction percentiles

Exact RED candidate:
```text
eb168e5326563c4f4e9825d985d7c05a819cf3dd
```

Result:
```text
Linux   1 intentional sentinel failed
        1439 passed / 1 skipped / 159 subtests
Windows SUCCESS
```

Real DBAPI checkpoint INSERT bound-parameter bytes through the production
`commit_waiting_checkpoint()` path:

```text
10 messages      2,503 B
100 messages    23,926 B
1000 messages  238,129 B
```

Fork-safe reconstruction, 20 samples per shape:

```text
messages   p50        p95         p99
10         ~2.37 ms   ~2.56 ms    ~4.78 ms
100        ~2.94 ms   ~3.02 ms    ~5.93 ms
1000       ~8.54 ms   ~10.49 ms   ~181.47 ms
```

The high p99 outlier is retained as evidence and is why timing remains a
measurement/non-regression input rather than an exact shared-runner CI
threshold.

### Architecture #952 — branch percentiles, rows-per-Task, synchronized CAS

Exact RED candidate:
```text
9428426544466871791466464c74ecc88c5f9154
```

Result:
```text
Linux   2 intentional sentinels failed
        1439 passed / 1 skipped / 159 subtests
Windows SUCCESS
```

Branch-create distribution over ten real durable FORK consumes:

```text
p50 ~16.63 ms
p95 ~35.60 ms
p99 ~35.60 ms
```

Rows-per-Task for the frozen workload:

```text
root WAITING  10 durable rows
after 1 FORK  16 durable rows
```

The synchronized contention harness uses a one-shot barrier at the first
Task CAS boundary so both contenders arrive before either CAS proceeds.

Observed authority:
```text
barrier arrivals      2
winner                1
conflict              1
final active branches 2
final budget revision 3

Task CAS p50          ~1.93 ms
Task CAS p95/p99      ~20.30 ms
TaskBudget CAS        one winner sample ~1.73 ms
```

Only the winning contender reaches TaskBudget CAS in this final-capacity race;
the losing contender fails at the earlier Task activity CAS authority.

### Architecture #953 — real RESUME planning/claim distribution

Exact RED candidate:
```text
3206b061bb3e6d69a4311197fe049b79982206c5
```

Result:
```text
Linux   exactly 3 intentional measurement sentinels failed
        1439 passed / 1 skipped / 159 subtests
Windows SUCCESS
```

Twenty independent normalized CONNECTION-WAITING executions exercised:

```text
AgentResumePlanningService.build_resume_plan()
  -> DurableAgentStore.get_or_create_resume_claim()
  -> DurableAgentStore.consume_resume_claim()
```

using 100-message checkpoint transcripts.

```text
phase           p50        p95        p99
planning        ~9.65 ms   ~10.72 ms  ~13.07 ms
claim+consume   ~11.76 ms  ~13.31 ms  ~17.69 ms
end-to-end      ~21.51 ms  ~24.03 ms  ~30.76 ms
```

### Architecture #957 — all new sentinels removed

Exact test HEAD:
```text
42339745f7939a98e5d417d4a6d2d2f738ea6470
```

Result:
```text
Linux:
1442 passed
1 skipped
51 warnings
159 subtests passed

Windows:
101 passed
2 warnings

Conclusion:
SUCCESS / SUCCESS
```

All new timing measurements remain evidence-only. CI asserts deterministic
storage shape, row counts, materialization validity, percentile ordering, and
race authority rather than exact host timing.

### P1-R11-A-MEAS-1 status

```text
resume latency distribution:              PRESENT
reconstruction p50/p95/p99:              PRESENT
rows-per-Task baseline:                   PRESENT
real checkpoint writer serialized bytes: PRESENT
synchronized Task/TaskBudget CAS race:    PRESENT
branch-create distribution:               PRESENT
production behavior change:               NONE
```

P1-R11-A-MEAS-1 is ready for independent closure review.

### P1-R11-0-DEP-1 current-state sync

At this same checkpoint:

```text
Issue #38   OPEN
T10-A       CLOSED / GREEN
T10-B       OPEN / side-effect-free helper+unit-test scope
candidate   e8a74f26b70fa76c75dd0607ccd131bdeb888826
T10-C       CLOSED
R11 overlap none
```

This replaces the stale earlier T10-A-claimed snapshot. P1-R11-0-DEP-1 is ready
for independent closure review against this current-state record.


---

## 16. Direct TaskBudget CAS contention addendum

A parallel R11-A agent strengthened the contention baseline after the first
expanded-measurement GREEN by instrumenting the TaskBudget CAS directly.

### Architecture #959

Exact RED candidate:
```text
da5a3a6c10defcb80d6bcf905c2c02dc69eb4f0b
```

Result:
```text
Linux   exactly 1 intentional sentinel failed
        1442 passed / 1 skipped / 159 subtests
Windows SUCCESS
```

Ten independent Tasks each run two concurrent `reserve_branch_slot()` calls.
A one-shot barrier is inside
`AgentRepository.compare_and_set_task_budget()`, so both contenders reach the
TaskBudget CAS boundary before proceeding.

Observed:
```text
samples                  10
service calls            20
service successes        20
barrier arrivals         20

TaskBudget CAS attempts  30
CAS successes            20
CAS stale/retry          10
CAS locked                0
other CAS errors          0

final active branches    20 total
reservation rows         20 total

CAS latency:
p50 ~1.57 ms
p95 ~4.49 ms
p99 ~4.62 ms

two-call race latency:
p50 ~11.78 ms
p95 ~16.31 ms
p99 ~16.31 ms
```

The extra ten CAS attempts are the expected stale/retry authority: each race
produces two committed reservations while one contender must retry after losing
the first shared revision.

The sentinel was removed at:
```text
efdc3924d1bec4453772d746a9b4b5e18831281a
```

CI retains deterministic assertions for:
- both service calls succeeding;
- exactly two barrier arrivals per race;
- two committed CAS successes per Task;
- one or more stale/locked retry signals per race;
- zero unexpected CAS error classes;
- final budget counters;
- reservation-row cardinality;
- valid percentile ordering.

Exact timing values remain evidence-only.

This direct CAS distribution supersedes any ambiguity in the earlier
FORK-capacity contention measurement and fully satisfies the frozen
TaskBudget-contention measurement requirement for R11-A.
