# AE-R11-H — Final Exit Matrix / Full-CI Freeze

**Primary authority:** Issue #31  
**Stage:** R11-H  
**Status:** H0 FINAL EXIT FREEZE CANDIDATE / EXACT-HEAD CI GREEN / PRE-FINAL-AUDIT  
**Claim baseline:** `main@ede34b9f495981684edb841d3a8f28aea074ff33`  
**Entry health:** post-wave Architecture #1355 GREEN/GREEN  
**Candidate CI evidence:** Architecture #1356 / run `36231568069` — Linux SUCCESS / Windows SUCCESS  
**Branch:** `work/ae-r11-h-final-exit-ede34b9f`  
**Policy:** Issue #85 v2

## 1. Scope and final gate

R11-H closes the R11 roadmap by binding every required exit condition to landed
A→G evidence. This candidate changes **no production/runtime/schema/migration/
repository behavior**.

Final R11 exit remains fail-closed until all of the following are true:

- linear/bounded checkpoint storage growth;
- bounded reconstruction depth;
- historical inline checkpoint compatibility;
- resume/retry/fork/aggregate semantic equivalence remains covered;
- retention safety remains closed;
- no transaction atomicity regression;
- ClientInvocationLedger terminal-vs-RUNNING retention safety remains frozen;
- exact-head Architecture Linux + Windows GREEN;
- independent exact-head audit finds no blocking R11 P0/P1/P2;
- no MATERIAL cross-track drift invalidates the frozen candidate.

Final roadmap gate:

> No O(N²) checkpoint growth and no persistence optimization may weaken durable continuation correctness.

## 2. Correctness exit matrix

| Required exit evidence | Disposition | Canonical landed evidence |
| --- | --- | --- |
| Linear/bounded checkpoint storage growth | PASS | `test_r11_g1_many_checkpoint_ref_backed_growth_is_bounded`: 48 REF_BACKED checkpoints, no inline snapshot, stored chunk bytes bounded to at most 2x final logical transcript bytes. |
| Bounded reconstruction depth | PASS | Same G1 workload freezes `delta_depth <= 9` and observes `max_delta_depth == 9`. |
| Historical inline checkpoint compatibility | PASS | `test_r11_c_legacy_inline_is_canonicalized`, dual-representation equivalence/fail-closed tests, plus D backfill/cutover integration coverage. |
| RESUME semantic equivalence | PASS | R11-C canonical materialization + G1 concurrent RESUME read pressure with stable plan fingerprints and zero durable mutation. |
| RETRY semantic equivalence | PASS | R11-C canonical reader convergence + G1 concurrent RETRY read pressure; R9 retry integration remains in full Architecture. |
| FORK semantic equivalence | PASS | R11-C fork-safe canonical materialization + G1 eight real durable forks through `build_fork_plan() -> consume_fork_plan() -> list_task_branches()`. |
| AGGREGATE semantic equivalence | PASS / inherited | R11 does not redefine R9 aggregate semantics; R9-F durable aggregate activation/restart tests remain in full Architecture and R11-F0 preserves AggregateAdmission provenance/live-root authority. |
| Retention / GC safety | PASS | R11-F0/F1/F1-B/F1-C contracts and architecture/integration tests preserve live-root closure, deterministic fail-closed dry-run, transactional deletion, lineage fences and ownership boundaries. |
| Transaction atomicity | PASS | G1 128-pending workload proves one successful checkpoint transaction with ordered batched pending rows; `test_r11_d_non_task_waiting_update_failure_rolls_back_dual_graph` proves a failed checkpoint transition rolls back the checkpoint/pending/transcript dual graph; F1-C injected-failure tests separately preserve zero-partial GC. |
| ClientInvocationLedger retention safety | PASS | Executable `cl/tests/test_r11_f0_client_ledger_retention.py` regressions `test_r11_f0_generic_ttl_collects_terminal_but_preserves_running` and `test_r11_f0_running_crash_evidence_survives_repeated_far_future_gc` prove terminal collection while RUNNING crash/replay evidence survives; `test_f1_preserves_r6_client_ledger_terminal_vs_running_fence` keeps that authority R6-owned. |
| Full Architecture Linux + Windows | PASS | Architecture #1356 / run `36231568069` completed GREEN/GREEN on exact pre-repair candidate `ffcddd7e0110a0237ad591618614e2f358aea0f4`: Linux SUCCESS, Windows SUCCESS. Any replacement evidence-only HEAD must also pass fresh Architecture before FINAL GREEN. |

## 3. R11-A metric disposition

R11-A required every baseline dimension to survive into R11-H. Exact wall-clock
or allocator values remain evidence, not portable correctness thresholds unless
a stable threshold was proven.

| R11-A dimension | Final disposition | Rationale / final evidence |
| --- | --- | --- |
| **checkpoint bytes written** | **IMPROVED** | A measured the historical full-prefix O(N²) shape and real writer bytes (2,503 B / 23,926 B / 238,129 B for 10/100/1000 messages). G1 now proves REF_BACKED growth with bounded stored chunk bytes and no inline snapshot on 48 checkpoints. |
| **resume latency p50/p95/p99** | **NOT STABLY THRESHOLDABLE with retained measurement evidence** | A #953 retains real planning/claim/end-to-end percentiles. H keeps percentile ordering and semantic correctness as CI authority; G1 concurrent RESUME pressure detects semantic regressions without a host-specific time threshold. |
| **reconstruction latency p50/p95/p99** | **NOT STABLY THRESHOLDABLE with retained measurement evidence** | A #951 retains 10/100/1000-message reconstruction distributions, including the high p99 outlier. G1 requires exact reconstruction under bounded depth; no fixed shared-runner timing threshold is introduced. |
| **branch create latency** | **NOT STABLY THRESHOLDABLE with retained measurement evidence** | A #952 retains real FORK-consume p50/p95/p99. G1 exercises eight canonical durable forks and checks deterministic lineage/accounting instead of a machine-specific latency ceiling. |
| **DB writes per Agent iteration** | **IMPROVED** | R11-E batched pending persistence is exercised by G1: 128 pending rows add one batched INSERT rather than one pending-row INSERT per item, while the surrounding checkpoint transition remains atomic. |
| **rows per Task** | **UNCHANGED / NON-REGRESSED** | A preserves the durable row-cardinality baseline; G1 verifies one source + eight forks produce exactly nine distinct branch identities with matching TaskBudget branch/execution accounting and no duplicate lineage. |
| **memory per active execution** | **NOT STABLY THRESHOLDABLE with retained measurement evidence** | A retains the fixed-shape tracemalloc method and ~6,154 B/context evidence. The measurement test remains part of full Architecture; allocator bytes are informational rather than a cross-platform correctness threshold. |
| **TaskBudget contention** | **UNCHANGED / NON-REGRESSED** | G1 runs 24 synchronized races / 48 service calls, requires retry signals, no unexpected errors, and exact final branch/reservation cardinality. |
| **reconstruction depth** | **IMPROVED** | Historical inline snapshots had no structural sharing bound. REF_BACKED reconstruction is now deterministically bounded at depth <= 9 and G1 observes the safety boundary under 48 checkpoints. |
| **SQL statement count** | **INTENTIONALLY TRADED OFF with rationale** | Pending-row INSERT amplification is batched, but per-invocation authority SELECT validation remains linear to preserve owner/state/fail-closed checks. G1 freezes the 128-row shape rather than deleting safety validation for a lower SQL count. |
| **flush count** | **IMPROVED** | G1 proves a 128-row pending batch adds no per-row flush amplification: flush count remains the zero-pending checkpoint baseline while ordered pending rows are committed atomically. |

No R11-A metric disappears from this final matrix.

## 4. Evidence inventory frozen by H

The final freeze depends on these landed regression surfaces:

- `se/tests/architecture/test_r11_a_persistence_baseline.py`
- `se/tests/architecture/test_r11_a_branch_budget_memory_baseline.py`
- `se/tests/architecture/test_r11_a_resume_performance_baseline.py`
- `se/tests/architecture/test_r11_c_checkpoint_materialization.py`
- `se/tests/architecture/test_r7_b_atomic_waiting.py`
- `se/tests/integration/test_r11_d_checkpoint_backfill.py`
- `se/tests/integration/test_r11_d_checkpoint_cutover_migration.py`
- `se/tests/architecture/test_r11_e2_query_plan_baseline.py`
- `se/tests/architecture/test_r11_f0_retention_contract.py`
- `cl/tests/test_r11_f0_client_ledger_retention.py`
- `se/tests/architecture/test_r11_f1_gc_root_closure_contract.py`
- `se/tests/architecture/test_r11_f1b_gc_dry_run.py`
- `se/tests/architecture/test_r11_f1c_gc_executor.py`
- `se/tests/integration/test_r11_f1c_gc_executor.py`
- `se/tests/architecture/test_r11_g_contention_load_performance.py`
- `se/tests/architecture/test_r11_g1_deterministic_load_matrix.py`
- `se/tests/integration/test_r9_f_aggregate.py`

R11-H adds no new production authority. Its architecture test prevents future edits
from silently dropping an R11 exit dimension or canonical regression surface.

## 5. Ownership boundaries at R11 exit

R11-H does not authorize or implement:

- R12 owner-instance, lease, stale-RUNNING detection, recovery scanner/coordinator,
  WAITING(RECOVERY), or competing recovery ownership;
- R6 CapabilityInvocation/ClientInvocationLedger lifecycle redefinition;
- CAS FileAsset/FileBlob/FileReference/FileProviderBinding lifecycle, provider
  hydration, ObjectStorage reconciliation, or physical CAS GC;
- CTX/Memory lifecycle or promotion authority;
- provider TOOL_CALLING eligibility or TV1 logical-tool/provenance semantics.

Agent transcript refs remain Agent persistence identities.

## 6. Cross-track drift at CLAIM

At CLAIM, PR #101 is OPEN/DRAFT and changes CTX-only paths, including
`se/src/context/memory_promotion.py`. It does not transfer AGENT_TRANSCRIPT,
checkpoint, R11 retention, or GC authority and is not current-main drift.

If #101 or another sibling lands before the R11-H integration boundary, classify
that movement under Policy #85 v2. NON_MATERIAL drift does not force a re-anchor;
MATERIAL drift invalidates the affected H gate and requires refresh/re-audit.

## 7. H completion gate

R11-H may be declared FINAL GREEN only after:

1. this exact docs/tests-only scope remains intact;
2. Architecture #1356 / run `36231568069` is recorded GREEN/GREEN (Linux SUCCESS / Windows SUCCESS), and any replacement HEAD created by evidence repair also receives fresh exact-head Architecture GREEN;
3. independent audit confirms every matrix row/evidence reference and finds no
   blocking R11 P0/P1/P2;
4. review threads are resolved;
5. integration governance under Policy #85 v2 is satisfied;
6. post-merge canonical main is verified GREEN before Issue #31 is closed.

Until then, R11-H is a freeze candidate, not canonical completion.
