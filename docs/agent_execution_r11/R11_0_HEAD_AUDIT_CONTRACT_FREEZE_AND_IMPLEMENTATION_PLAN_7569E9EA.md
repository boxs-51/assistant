# AE-R11-0 HEAD Audit, Contract Freeze, and Implementation Plan

**Repository:** `boxs-51/assistant`  
**Canonical baseline:** `main@7569e9eadf76eccee492b125c594833a5d4fcbb2`  
**Boundary:** AE-R10 CLOSED / FINAL GREEN -> AE-R11 Persistence / Performance Hardening  
**Work branch:** `work/ae-r11-boundary-7569e9ea`  
**Status:** PRE-IMPLEMENTATION / AUDIT + PLAN ONLY

---

## 1. Entry authority

AE-R10 is closed and merged on canonical main.

Final R10 authority carried into R11:

- merged main: `7569e9eadf76eccee492b125c594833a5d4fcbb2`;
- superseding R10 runtime/test semantic baseline:
  `bd6107014cd486cd8ab43010304d074ff74a1a0c`;
- post-merge Architecture #927:
  - Linux: 1145 passed, 1 skipped;
  - Windows: 68 passed;
- Issue #14: CLOSED / completed;
- no known blocking R10 P0/P1.

R11 must consume R10 semantics unchanged. Persistence/performance work must not
redefine provider retry/fallback, provider deadlines, capability probe
deadlines, stream replay boundaries, or breaker accounting.

---

## 2. Canonical R11 objective

Scale continuation and branching without weakening correctness.

Roadmap-owned R11 scope:

- checkpoint snapshot/delta strategy;
- transcript references;
- persistence/query indexes;
- retention / garbage collection;
- transaction batching;
- query optimization;
- TaskBudget contention monitoring;
- load benchmarks.

Canonical exit gate:

> No O(N²) checkpoint growth.

R11 is allowed to optimize representation and access paths only when durable
resume/retry/fork/aggregate semantics remain equivalent to the R7-R10 frozen
contracts.

---

## 3. Initial HEAD audit findings

### P1-R11-1 — full transcript copy remains the default checkpoint representation

`stage_waiting_checkpoint()` serializes and persists the complete
`transcript_snapshot` for each new checkpoint.

For a monotonically growing transcript, repeated checkpoint writes can approach:

```text
C1 = T1
C2 = T1 + delta2
C3 = T1 + delta2 + delta3
...
```

which creates the roadmap's known O(N²) copied-byte risk.

### P1-R11-2 — transcript_ref / transcript_version representation exists but is not an active write/reconstruction authority

The checkpoint schema and R7 contracts already contain:

```text
transcript_snapshot
transcript_ref
transcript_version
```

but the production waiting-checkpoint writer still materializes full snapshots.

Current R8/R9 reconstruction paths were deliberately frozen around inline
checkpoint transcript snapshots; ref-only reconstruction was deferred to R11.

R11 therefore needs one canonical transcript representation contract that is
usable by:

- RESUME;
- RETRY;
- FORK;
- AGGREGATE;
- future recovery in R12.

It must not create a representation that only one continuation path understands.

### P1-R11-3 — normalized checkpoint pending rows are persisted with repeated flushes

Current checkpoint creation writes the checkpoint and then persists pending
invocation rows one at a time. Repository helpers flush after each inserted row.

This is correct but creates avoidable transaction/flush amplification for
parallel tool batches and should be benchmarked before selecting a batching
implementation.

### P1-R11-4 — durable checkpoint retention / GC authority is not implemented

The roadmap requires retention for checkpoints, claims, superseded branches,
failed retry executions and related durable history, with the hard rule:

> GC must never delete data required by an active WAITING execution.

Current durable checkpoint graph contains RESTRICT/CASCADE relationships but no
R11 retention coordinator or proven live-root computation.

### P1-R11-5 — no R11 performance baseline or regression thresholds are frozen

The roadmap requires measurement of:

```text
checkpoint bytes written
resume latency p50/p95/p99
checkpoint reconstruction p50/p95/p99
branch create latency
DB writes per Agent iteration
rows per Task
memory per active execution
TaskBudget contention
```

R11 must establish deterministic benchmark fixtures and measurement hooks before
large storage rewrites. Optimization without baseline evidence is not an exit
gate.

### Audit item R11-Q — index/query-plan completeness is not yet proven

Existing models already carry several useful single-column indexes. R11 must
audit actual hot queries and query plans before adding indexes. Do not add
speculative indexes solely from column names.

---

## 4. Frozen correctness invariants carried into R11

R11 optimization must preserve:

1. Checkpoints remain immutable durable safe points.
2. A WAITING execution always points to a reconstructable committed checkpoint.
3. Resume checkpoint lineage/revision validation remains fail-closed.
4. Pending capability invocation identity/order remains deterministic.
5. COMMITTED tool-result reconstruction remains commitment-aware.
6. Retry remains same Task + same Branch + new execution.
7. Fork remains same Task + new Branch + new execution.
8. Branch context isolation remains unchanged.
9. Aggregate/retry/fork receipts remain durable replay authority.
10. TaskBudget CAS and accounting semantics remain unchanged.
11. R10 logical provider deadline/retry/fallback semantics remain unchanged.
12. A transcript optimization may change physical storage, never logical
    transcript content/order/fingerprint authority.
13. GC may delete data only after proving it is unreachable from every live
    execution/branch/claim/admission authority.
14. R11 must not introduce execution leases or stale-RUNNING recovery; that is
    AE-R12.

---

## 5. Proposed R11 staged plan

### R11-A — measurement + storage contract freeze

- benchmark fixture sizes;
- bytes-written instrumentation;
- SQL statement/flush counters;
- reconstruction latency harness;
- current query-plan inventory;
- freeze transcript logical identity and snapshot/delta/ref contract.

No production optimization before R11-A measurements are reproducible.

### R11-B — transcript reference / snapshot-delta representation

Define:

- transcript object identity;
- full snapshot cadence;
- delta identity and parent/base reference;
- transcript_version semantics;
- fingerprint rules;
- maximum reconstruction depth;
- corruption/missing-delta failure behavior.

### R11-C — reconstruction convergence

Wire one reader capable of reconstructing equivalent transcript content for:

- resume;
- retry;
- fork;
- aggregate.

Inline historical checkpoints must remain readable during migration.

### R11-D — checkpoint writer cutover + migration

Introduce the new writer after readers are dual-capable.

Migration/backfill must be deterministic and restart-safe.

No destructive removal of historical inline snapshots in the first cutover.

### R11-E — transaction batching + query/index hardening

- batch checkpoint pending-row persistence where proven beneficial;
- reduce unnecessary flush/query round trips;
- add only query-plan-backed indexes;
- preserve transaction atomicity and lock ordering.

### R11-F — retention / GC

Freeze live roots and safe deletion order for:

- checkpoints;
- resume claims;
- terminal executions;
- superseded/discarded branches;
- retry/fork/aggregate admissions;
- provider-attempt or other auxiliary durable rows where applicable.

Active WAITING/retry/fork/recovery authority must never be collected.

### R11-G — contention + load performance

- TaskBudget contention measurements;
- long transcript / many-checkpoint workload;
- multi-branch workload;
- large pending invocation batch;
- concurrent resume/retry/fork read pressure.

Correctness failures remain blockers even if performance improves.

### R11-H — final exit matrix

Required evidence:

- linear/bounded checkpoint storage growth;
- bounded reconstruction depth;
- historical inline checkpoint compatibility;
- resume/retry/fork/aggregate semantic equivalence;
- retention safety;
- no transaction atomicity regression;
- full Architecture Linux + Windows GREEN;
- documented p50/p95/p99 and bytes/write metrics.

Final exit gate:

> No O(N²) checkpoint growth and no persistence optimization may weaken durable continuation correctness.

---

## 6. R11 / R12 boundary

AE-R11 owns:

- physical persistence representation;
- storage/read/write amplification;
- query/index performance;
- retention policy and safe GC of already-terminal/unreachable history;
- benchmark/metrics.

AE-R12 owns:

- owner_instance_id;
- execution lease;
- stale RUNNING detection;
- recovery scanner/coordinator;
- WAITING(RECOVERY);
- competing recovery worker ownership;
- crash-time pending invocation reconciliation.

R11 must not implement a lease or use GC as a substitute for recovery.

---

## 7. Cross-track boundaries

### PTC / Issue #8

PTC may proceed from canonical main after R10 closure.

R11 must not modify provider TOOL_CALLING eligibility or provider tool lowering.
If PTC and R11 touch a common persistence/context file, perform an exact overlap
audit before edits.

### TV1 / Issue #12

TV1 tool export migration is logically separate. R11 may measure payload/storage
effects but must not redefine Tools Metadata V2 contracts.

### CAS / CTX

CAS-F5+ and CTX-F* remain parked behind the later Agent hardening gates unless
explicitly re-frozen.

R11 transcript references are Agent persistence references; they must not
silently become Central Asset or long-term Memory identity.

---

## 8. Implementation discipline

Before each production stage:

1. post `[CLAIM] AE-R11-<stage>` on the R11 issue;
2. list exact files owned;
3. run targeted correctness regression first;
4. record benchmark baseline before optimization;
5. keep semantic changes separate from performance-only changes where possible;
6. treat newly discovered correctness P0/P1 as blocking;
7. publish exact HEAD + metric/test evidence at every handoff.

No AE-R11 production code is authorized by this R11-0 document alone until the
R11 issue has been reviewed and the R11-A measurement/contract scope is claimed.

---

## 9. R11 entry decision

```text
AE-R10: CLOSED / FINAL GREEN
canonical main: 7569e9eadf76eccee492b125c594833a5d4fcbb2

AE-R11:
BOUNDARY AUDITED
INITIAL P1 PERFORMANCE/CORRECTNESS RISKS IDENTIFIED
IMPLEMENTATION PLAN FROZEN
PRODUCTION CODE NOT STARTED
```

The next legal action is R11-A measurement + exact persistence contract freeze.
