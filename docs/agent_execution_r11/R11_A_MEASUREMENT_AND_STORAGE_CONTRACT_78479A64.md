# AE-R11-A — Measurement and Storage Contract Freeze

**Repository:** `boxs-51/assistant`  
**Issue:** #31  
**Canonical baseline:** `main@78479a64a97353094817b92a090449191782d366`  
**Work branch:** `work/ae-r11-a-78479a64`  
**Status:** R11-A ACTIVE / MEASUREMENT-FIRST / NO REPRESENTATION CUTOVER

---

## 1. Purpose

R11-A establishes reproducible evidence before changing persistence behavior.

It does **not** implement snapshot/delta storage, new indexes, batching, retention
deletion, or migration.

The first optimization stage must begin from measured current behavior rather
than assumptions.

---

## 2. Current production storage shape

WAITING persistence currently follows:

```text
DurableAgentStore.commit_waiting_checkpoint()
  -> load AgentExecution
  -> stage_waiting_checkpoint()
       -> serialize full transcript_snapshot
       -> save AgentExecutionCheckpoint
          -> flush
       -> for each pending invocation
            -> load CapabilityInvocation
            -> save AgentCheckpointPendingInvocation
               -> flush
  -> compare_and_set_execution()
       -> UPDATE execution
       -> flush
       -> reload execution
  -> commit
```

Correctness is already strong because checkpoint, pending invocation snapshots
and execution CAS live inside one UoW.

The R11 problem is physical amplification, not missing atomicity.

---

## 3. Baseline measurement surfaces

The R11-A harness measures the real SQLite production path for:

### 3.1 Growing checkpoint transcript

Message counts:

```text
10
100
1000
```

Record:

- final logical transcript bytes;
- cumulative bytes copied when every checkpoint persists the full prefix;
- copy amplification ratio.

This proves the existing growth model independently of machine speed.

### 3.2 Pending invocation batch

Batch sizes:

```text
0
1
8
32
```

Record:

- SELECT count;
- INSERT count;
- UPDATE count;
- DELETE count;
- total SQL statements;
- ORM flush count;
- transaction begin count;
- commit/rollback count.

The harness uses real `commit_waiting_checkpoint()`, not a synthetic writer.

### 3.3 Checkpoint reconstruction

Transcript sizes:

```text
10
100
1000
```

The initial reader measured is
`DurableAgentStore.load_fork_safe_checkpoint_transcript()`.

Record:

- SELECT count;
- total SQL count;
- logical transcript bytes;
- message count;
- elapsed nanoseconds.

Default CI asserts deterministic query/count properties only. Wall-clock
latency is recorded but is not a fixed CI threshold because shared-runner timing
variance is not a correctness signal.

---

## 4. Remaining R11-A measurement methods

Before R11-A closes, measurement definitions must also exist for:

### Branch creation latency

Measure the full durable FORK admission/consume path on a pre-seeded source
checkpoint, not only raw repository INSERTs.

Required outputs:

- SQL statement count;
- commit count;
- elapsed ns;
- branch/context/execution rows created.

### TaskBudget contention

Use synchronized concurrent CAS attempts against one TaskBudget row.

Required outputs:

- attempts;
- successful CAS count;
- conflict count;
- lock/CAS elapsed distribution;
- final revision and counter correctness.

No production lock semantics may be changed merely to improve a benchmark.

### Memory per active execution

Use a deterministic process-local harness with fixed execution/context payload
sizes.

Record:

- baseline process allocation snapshot;
- N active execution/context objects;
- retained allocation delta after construction;
- approximate bytes per active execution.

This metric is informational/non-regression unless a stable CI threshold can be
demonstrated across Python/OS versions.

---

## 5. Canonical transcript identity contract for R11-B

R11-A freezes logical semantics before physical representation changes.

### 5.1 Authority

```text
AgentExecutionCheckpoint
    = continuation safe-point authority

Agent transcript object/segment
    = immutable physical transcript representation

transcript_ref
    = opaque Agent-persistence reference to the exact immutable transcript
      representation consumed by this checkpoint

transcript_version
    = exact non-negative version/sequence required to reconstruct and validate
      that referenced transcript representation
```

The following are explicitly false:

```text
transcript_ref != Context locator
transcript_ref != Memory identity
transcript_ref != Central Asset identity
transcript_ref != ToolResponsePayload identity
transcript_version != AgentExecution.revision
transcript_version != FileAsset.revision
```

### 5.2 Logical equivalence

For any checkpoint C:

```text
reconstruct(C.transcript_ref, C.transcript_version)
==
canonical_message_sequence(C)
```

where equality includes:

- message ordering;
- role;
- content;
- tool_call_id;
- canonical tool-call payload;
- metadata that participates in inference semantics.

Physical segmentation may change. Logical transcript content may not.

### 5.3 Immutable representation

Transcript objects/segments are append-only/immutable after commit.

A checkpoint never points at a mutable "latest transcript" locator.

Checkpoint reconstruction must not silently observe a newer transcript version
than the one committed with that checkpoint.

### 5.4 Snapshot + delta

R11-B may represent transcript history as:

```text
FULL snapshot
  +
zero or more immutable DELTA segments
```

A delta may only append the exact canonical suffix after its parent/base
transcript. It may not reinterpret, reorder or rewrite historical messages.

Periodic FULL cadence and maximum delta depth are chosen from R11-A/R11-G
measurements; they are not guessed in R11-A.

### 5.5 Reconstruction failure

Missing/corrupt/ambiguous transcript representation fails closed.

It must never fall back to mutable Session history, a newer checkpoint, Context
search, or model-generated reconstruction.

---

## 6. Reader convergence gate before writer cutover

Current production readers still contain inline-snapshot assumptions in:

- resume planning/loading;
- retry planning/replay;
- fork planning and fork-safe reconstruction;
- R8/R9 runtime seed/control paths.

Therefore R11 ordering is frozen:

```text
R11-B representation
  -> R11-C dual-capable readers
  -> prove inline + ref reconstruction equivalence
  -> only then R11-D writer cutover
```

A ref-only checkpoint MUST NOT be emitted while any legal continuation path
still requires `transcript_snapshot is not None`.

---

## 7. Retention / GC live-root contract

R11-F cannot delete first and discover roots later.

Minimum Agent-side live roots:

- `AgentExecution.current_checkpoint_id`;
- `TaskBranch.base_checkpoint_id`;
- ForkAdmission source checkpoint;
- RetryAdmission source checkpoint;
- active/usable ResumeClaim checkpoint;
- immutable runtime seed / receipt evidence that validates against checkpoint
  transcript content;
- transcript parent/base segments needed to reconstruct any live-root
  checkpoint;
- historical rows explicitly retained by policy/audit requirements.

R12 may later add recovery-specific roots. R11 must keep the root model
extensible and must not make recovery impossible.

### Separate R6 client ledger rule

`ClientInvocationLedger` is a separate client SQLite authority.

Frozen retention behavior:

```text
TERMINAL + expired
    -> collectible under current terminal TTL policy

RUNNING
    -> never generic-TTL collected
    -> crash evidence required to avoid unsafe replay
```

R11 may measure/preserve this policy but must not reinterpret RUNNING crash
evidence as recovery permission. That belongs to R12/server reconciliation
contracts.

---

## 8. Query-plan inventory

Current hot query families to measure before index changes:

1. checkpoint by checkpoint_id;
2. checkpoint list by execution_id ordered by created_at;
3. pending invocation rows by checkpoint_id ordered by ordinal;
4. TaskBranch by branch_id;
5. branches by task_id;
6. TaskBudget by task_id;
7. TaskBudget FOR UPDATE / CAS;
8. ForkAdmission lookup by logical request and execution;
9. RetryAdmission lookup by logical request and execution;
10. AggregateAdmission lookup by logical request and execution;
11. current WAITING executions filtered by owner/client/wait state;
12. cancellation/control-plane branch lock scans.

Existing single-column/composite indexes remain untouched in R11-A.

R11-E may add/change an index only with:

- measured hot-query evidence;
- query-plan evidence;
- correctness/lock-order audit;
- before/after measurement.

---

## 9. Exit evidence completeness

Every baseline dimension frozen in R11-A must reappear in R11-H:

```text
checkpoint bytes written
resume latency p50/p95/p99
checkpoint reconstruction p50/p95/p99
branch create latency
DB writes per Agent iteration
rows per Task
memory per active execution
TaskBudget contention
reconstruction depth
SQL statement count
flush count
```

For each metric R11-H records one of:

```text
IMPROVED
UNCHANGED / NON-REGRESSED
INTENTIONALLY TRADED OFF with documented reason
NOT STABLY THRESHOLDABLE, measurement evidence retained
```

No metric may disappear from the final exit report.

---

## 10. Cross-roadmap boundaries

### R10 / PTC / TV1

Inherited and frozen. R11 does not redefine provider lifecycle, TOOL_CALLING
eligibility, canonical logical tool IDs, Metadata V2 or ToolResult physical
provenance.

### CTX / Central Asset

Issue #15 remains parked.

R11 transcript storage remains Agent-owned. R11 does not create Memory,
ContextSnapshot, ToolResponsePayload or Central Asset lifecycle/GC authority.

### R12

R11 may optimize physical persistence and safe retention.

R11 does not implement:

- owner_instance_id;
- execution lease;
- stale RUNNING scanner;
- WAITING(RECOVERY);
- recovery ownership races.

---

## 11. R11-A closure rule

R11-A closes only when:

1. deterministic baseline harness is GREEN;
2. red first-run baseline output is preserved on Issue #31;
3. checkpoint-growth and pending-write amplification are measured;
4. reconstruction query behavior is measured;
5. branch creation / TaskBudget contention measurement methods are implemented;
6. memory-per-active-execution method is implemented;
7. query-plan inventory is frozen;
8. transcript reference/snapshot-delta logical contract is frozen;
9. independent audit finds no open R11-A P0/P1;
10. exact-head Architecture Linux + Windows is GREEN.

R11-B remains closed until all ten conditions hold.
