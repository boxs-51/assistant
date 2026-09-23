# AE-R11-0 HEAD Audit, Contract Freeze, and Implementation Plan

**Repository:** `boxs-51/assistant`  
**Canonical baseline:** `main@78479a64a97353094817b92a090449191782d366`  
**Boundary:** AE-R10 CLOSED / FINAL GREEN -> AE-R11 Persistence / Performance Hardening  
**Work branch:** `work/ae-r11-a-78479a64`  
**Status:** RE-ANCHORED / R11-A ENTRY CONTRACT / MEASUREMENT-FIRST

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
- PTC-1→PTC-3 / Issue #8: CLOSED / FINAL GREEN / merged;
- TV1-T9 / Issue #12: CLOSED / MERGED / FROZEN / post-merge GREEN;
- post-TV1-T9 Architecture #939 on canonical main ancestry: Linux 1431 passed, 1 skipped, 159 subtests; Windows 101 passed;
- no known blocking upstream R10/PTC/TV1-T9 P0/P1.

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

### P1-R11-4 — durable retention / GC authority is not implemented

The roadmap requires retention for checkpoints, claims, superseded branches,
failed retry executions and related durable history, with the hard rule:

> GC must never delete data required by an active WAITING execution or evidence
> required to prevent unsafe replay.

Current durable checkpoint graph contains RESTRICT/CASCADE relationships but no
R11 retention coordinator or proven live-root computation.

The retention inventory MUST include the separate R6 client-side
`ClientInvocationLedger` authority. Its current replay-safety rule is frozen:

```text
expired TERMINAL rows
    -> eligible for bounded collection according to the existing ledger policy

RUNNING crash evidence
    -> NOT eligible for generic TTL collection
    -> must survive because it can be required to prevent unsafe replay of
       NON_IDEMPOTENT / UNKNOWN remote work
```

R11 owns only preservation and safe deletion policy for this evidence.
Interpretation/recovery of stale execution ownership remains AE-R12.

### P1-R11-5 — no R11 performance baseline or complete exit evidence is frozen

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

Every frozen baseline dimension MUST receive final R11-H evidence. A metric
cannot be listed at R11-A and silently omitted from the exit gate. Where a
stable numeric threshold is inappropriate across CI hosts, R11-H must still
record an explicit comparison and non-regression disposition.

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
14. ClientInvocationLedger RUNNING crash evidence is never generically
    TTL-collected; R11 retention must preserve R6 replay safety.
15. Agent transcript references remain Agent persistence identities; they do not
    silently become Central Asset, Context, Memory or ToolResponsePayload
    identities.
16. R11 must not introduce execution leases or stale-RUNNING recovery; that is
    AE-R12.

---

## 5. Proposed R11 staged plan

### R11-A — measurement + storage contract freeze

- benchmark fixture sizes;
- bytes-written instrumentation;
- SQL statement/flush counters;
- reconstruction latency harness;
- current query-plan inventory;
- memory-per-active-execution measurement;
- TaskBudget contention measurement;
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
- provider-attempt or other auxiliary durable rows where applicable;
- R6 ClientInvocationLedger with state-aware retention.

Active WAITING/retry/fork/recovery authority must never be collected.
ClientInvocationLedger RUNNING crash evidence must never be erased by generic
retention even when its wall-clock age exceeds terminal-row TTL policy.

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
- final evidence for every R11-A baseline dimension:
  - checkpoint bytes written;
  - resume latency p50/p95/p99;
  - checkpoint reconstruction p50/p95/p99;
  - branch create latency;
  - DB writes per Agent iteration;
  - rows per Task;
  - memory per active execution;
  - TaskBudget contention;
- explicit threshold/non-regression disposition for each metric;
- ClientInvocationLedger retention safety regression.

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

PTC-1 through PTC-3 are CLOSED / FINAL GREEN / merged.

R11 inherits those contracts and must not modify provider TOOL_CALLING
eligibility, provider tool lowering, or R10 bounded capability-probe/deadline
authority.

### TV1 / Issue #12

TV1-T9 is CLOSED / MERGED / FROZEN / post-merge GREEN on the canonical main
line. R11 may measure payload/storage effects but must not redefine canonical
logical tool identity, Metadata V2 contracts, or physical ToolResult provenance.

TV1-T10 is only boundary-audited; its live-harness implementation is not part of
R11 and does not authorize cross-track production edits.

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
PTC-1→3: CLOSED / FINAL GREEN
TV1-T9: CLOSED / MERGED / FROZEN / POST-MERGE GREEN
canonical main: 78479a64a97353094817b92a090449191782d366

AE-R11:
RE-ANCHORED AFTER TV1-T9
BOUNDARY AUDITED
INITIAL P1 PERFORMANCE/CORRECTNESS RISKS IDENTIFIED
R11-A MEASUREMENT/CONTRACT WORK AUTHORIZED
NO REPRESENTATION OPTIMIZATION YET
```

The next legal action is R11-A measurement + exact persistence contract freeze.


---

## 10. Post-TV1-T9 re-anchor record

The earlier R11 boundary candidate based on `main@7569e9ea` was intentionally
not merged. It is historical planning evidence only.

After PTC-3B and TV1-T9 completed:

```text
canonical main:
78479a64a97353094817b92a090449191782d366

TV1-T9:
MERGED / FROZEN / POST-MERGE GREEN

PTC:
CLOSED / FINAL GREEN
```

Exact overlap audit for TV1-T9 merge `28ae6119 -> 78479a64` found no changes
under:

```text
se/src/runtimes/agent/**
se/src/infrastructure/storage/**
docs/ROADMAP_NAMESPACE_REGISTRY.md
docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md
```

Therefore R11 persistence semantics did not move during TV1-T9 integration, but
the coordination snapshot required semantic re-freeze.

Planning findings closed by this re-anchor:

```text
P1-R11-0-DEP-1      CLOSED
  registry/current-track status corrected against post-TV1-T9 main

P1-R11-0-GC-1       CLOSED AT CONTRACT LEVEL
  ClientInvocationLedger retention/replay-safety rule is now an explicit
  mandatory R11-F inventory item and regression requirement

P2-R11-0-METRICS-1  CLOSED AT CONTRACT LEVEL
  every R11-A baseline metric now requires R11-H evidence and an explicit
  threshold/non-regression disposition
```

The next implementation step is R11-A only. R11-B through R11-H remain closed
until R11-A publishes reproducible baseline evidence and an exact storage
contract.
