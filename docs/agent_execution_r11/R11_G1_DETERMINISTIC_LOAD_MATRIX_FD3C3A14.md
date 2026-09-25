# AE-R11-G1 — Deterministic Contention / Load Matrix

**Repository:** `boxs-51/assistant`  
**Issue authority:** #31  
**Claim baseline:** `main@fd3c3a146f0775ce357b846341f99475af55c665`  
**Entry health:** Architecture #1337 GREEN/GREEN  
**Stage:** R11-G1  
**Merge class at claim:** test/evidence-only; production delta forbidden

## Objective

Execute the five workload classes frozen by R11-G0/G0.1 against real production
persistence/planning surfaces. Correctness is the gate. Wall-clock measurements are
evidence only and must not become machine-specific pass/fail thresholds.

## Deterministic workload sizes

1. **TaskBudget contention**
   - 24 synchronized two-caller races;
   - 48 successful service calls;
   - at least one stale/locked retry signal per race;
   - no unexpected error class;
   - exact final reservation/accounting cardinality;
   - `p50 <= p95 <= p99` only.

2. **Long transcript / many checkpoints**
   - 48 immutable checkpoint representations;
   - checkpoint rows are REF_BACKED (`transcript_snapshot IS NULL`);
   - production transcript writer/materializer is used;
   - DELTA depth never exceeds 9;
   - periodic FULL re-anchor is required;
   - final transcript must reconstruct exactly;
   - unique stored chunk bytes must remain bounded relative to final logical transcript
     bytes, preventing return to full-prefix O(N²) copying.

3. **Multi-branch**
   - one durable source plus eight real forks;
   - canonical path is
     `build_fork_plan() -> consume_fork_plan() -> list_task_branches()`;
   - repeated branch listing must be stable;
   - every fork belongs to the same TaskBudget lineage;
   - final active branch/execution counters must match durable rows.

4. **Large pending invocation batch**
   - 128 pending invocations through production
     `DurableAgentStore.commit_waiting_checkpoint()`;
   - exact ordinal/invocation/tool-call ordering is preserved;
   - checkpoint authority is REF_BACKED;
   - exactly one additional batched INSERT execution versus N=0;
   - no per-row ORM flush growth;
   - one transaction / zero rollback.

5. **Concurrent RESUME / RETRY / FORK read pressure**
   - four legal durable sources for each planner;
   - 12 concurrent plan builds per wave, two waves;
   - all plan fingerprints must repeat deterministically;
   - planning must leave durable execution revision/state/checkpoint pointers unchanged;
   - no planner is allowed to borrow another track's lifecycle authority.

## Late P2 guard correction

Merged PR #95 used the broad source substring `save_task_branch` when freezing the
atomic FORK consume path. That can be satisfied by
`save_task_branch_context(...)`. G1 tightens the guard to the exact call token
`save_task_branch(` (and exact call tokens for sibling persistence operations).
This is test/evidence debt only; it does not reopen the canonically closed G0.1 P1 or
change production behavior.

## Authority boundaries

G1 does not change:
- R6 capability-invocation lifecycle ownership;
- R12 lease/recovery ownership;
- CAS physical GC/provider hydration ownership;
- CTX/Memory promotion ownership;
- R11-F retention/GC semantics.

If this matrix exposes a production defect or a runtime optimization is required, the
finding must be classified separately and production changes must move to a separately
scoped R11-G substage. Production merge authority is not implied by this test/evidence
stage.

## Drift rule

CAS PR #94 and any sibling merge are drift watches. Before integration, exact current
main, post-merge Architecture health, merge-base, exact-head CI, and independent audit
must be re-established under Issue #31's stricter current-main rule.
