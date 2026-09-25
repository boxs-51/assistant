# AE-R11-G — Contention + Load Performance Contract

**Repository:** `boxs-51/assistant`  
**Issue authority:** #31  
**Canonical claim baseline:** `main@e68fd96e701d90c4707a067487fe374234b11749`  
**Current integration baseline:** `main@bf3e15800905a105eb3d14a7578f2daee308bcc8`  
**Claim-entry health:** post-merge Architecture #1315 GREEN/GREEN  
**Current canonical health:** post-CTX-F5-3A Architecture #1327 GREEN/GREEN  
**Integration refresh reason:** G0 landed after CTX PR #89; G0.1 closes post-merge branch-creation-surface P1  
**Branch:** `work/ae-r11-g-e68fd96e`  
**Status:** G0.1 CONTRACT REPAIR / MEASUREMENT MATRIX FREEZE

---

## 1. Stage objective

R11-G proves that the persistence/continuation design remains correct and bounded under
contention and load after R11-B→R11-F representation, query and GC hardening.

This stage is measurement-first. It does not change lock ordering, lifecycle authority,
schema, migrations, retention authority, continuation semantics, or provider behavior
merely to improve a benchmark.

Correctness failures are blockers even when measured latency improves.

---

## 2. Required workload matrix

Every R11-G completion candidate must publish exact-head evidence for all five surfaces:

1. **TaskBudget contention**
   - synchronized concurrent CAS attempts against one durable TaskBudget;
   - record attempts, successful CAS, stale/conflict/retry signals, final revision/counters;
   - latency percentiles are evidence, not a shared-runner correctness threshold.

2. **Long transcript / many-checkpoint workload**
   - use the production REF_BACKED checkpoint writer/materializer;
   - checkpoint rows must remain reconstructable and immutable;
   - durable checkpoint authority must remain ref-backed;
   - storage growth/reconstruction depth must stay bounded and must not regress toward
     the historical full-prefix O(N²) copied-byte shape.

3. **Multi-branch workload**
   - plan through `AgentForkPlanningService.build_fork_plan()`;
   - atomically create the durable execution + branch + branch context through `TaskBudgetService.consume_fork_plan()`;
   - verify/read the resulting branch set through `DurableAgentStore.list_task_branches()`;
   - preserve deterministic branch ordering and shared TaskBudget accounting;
   - `reserve_branch_slot()` is a contention/accounting primitive, not the canonical branch-creation authority;
   - no branch may obtain a fresh/reset TaskBudget.

4. **Large pending invocation batch**
   - exercise production `commit_waiting_checkpoint()` with a materially larger pending
     invocation set than the R11-A baseline;
   - pending rows must use the R11-E batched persistence authority;
   - identity/order/FK/transaction atomicity remains unchanged.

5. **Concurrent RESUME / RETRY / FORK read pressure**
   - exercise the canonical planning/read surfaces concurrently on durable state;
   - plans must remain deterministic and fail closed on stale/conflicting authority;
   - the workload must not widen R6 capability lifecycle, R12 recovery, CAS lifecycle,
     or CTX/Memory authority.

---

## 3. Frozen production measurement surfaces

R11-G measures existing production authority rather than synthetic substitutes:

- `TaskBudgetService.reserve_branch_slot()`
- `TaskBudgetService.consume_fork_plan()`
- `DurableAgentStore.commit_waiting_checkpoint()`
- `DurableAgentStore.list_task_branches()`
- `AgentResumePlanningService.build_resume_plan()`
- `AgentRetryPlanningService.build_retry_plan()`
- `AgentForkPlanningService.build_fork_plan()`

The exact APIs above may be wrapped by deterministic test fixtures, but the measured
operations themselves must remain the production methods.

For branch creation, the frozen canonical sequence is:

`build_fork_plan() -> consume_fork_plan() -> list_task_branches()`

`reserve_branch_slot()` may still be used for direct TaskBudget contention measurement,
but it must not stand in for durable branch creation.

---

## 4. Correctness invariants

R11-G must preserve:

- TaskBudget CAS/retry/accounting semantics;
- one TaskBudget shared by fork/retry/child execution lineage;
- checkpoint immutability and REF_BACKED reconstruction;
- deterministic pending invocation order;
- transaction atomicity for WAITING checkpoint + pending rows + execution CAS;
- retry = same Task + same Branch + new execution;
- fork = same Task + new Branch + new execution;
- resume = same Task + same Branch + same execution;
- hard connection affinity and R6 ownership boundaries;
- R11-F retention/GC closure;
- no R12 lease/recovery authority;
- no CAS physical-GC/provider-hydration ownership;
- no CTX/Memory lifecycle ownership.

Any semantic/correctness failure is P0/P1 material regardless of timing.

---

## 5. Evidence model

R11-G separates deterministic CI assertions from host timing evidence.

CI MUST assert deterministic properties such as:

- final row/counter cardinality;
- winner/conflict/retry authority;
- no missing/duplicate pending identities;
- reconstructability and logical transcript equality;
- branch ordering and lineage;
- zero unexpected exceptions/error classes;
- percentile ordering `p50 <= p95 <= p99`.

Wall-clock values are retained as exact-run evidence but are not fixed cross-platform
thresholds unless repeated evidence proves a stable threshold is appropriate.

---

## 6. Initial G0 owned scope

This first slice owns only:

```text
docs/agent_execution_r11/R11_G_CONTENTION_LOAD_PERFORMANCE_E68FD96E.md
se/tests/architecture/test_r11_g_contention_load_performance.py
```

No production source, schema, migration, index, runtime or repository behavior is
changed by G0.

Later G1+ benchmark implementation may expand test/evidence scope. Any production
optimization requires measured justification, explicit scope expansion, fresh exact-head
CI and independent audit.

---

## 7. Canonical-main drift rule

The CLAIM remains historically valid from:

`e68fd96e701d90c4707a067487fe374234b11749`.

The landed G0 integration baseline was:

`912cf1ac0386f765a5d6324a337b84b5ae928715`.

G0.1 is refreshed from current accepted canonical main:

`bf3e15800905a105eb3d14a7578f2daee308bcc8`.

Post-merge Architecture #1327 is GREEN/GREEN on that exact main.

The prior exact-head Architecture #1317 RED was a deterministic Markdown-whitespace assertion mismatch only and was superseded before G0 landed.

After G0 merged, review thread `PRRT_kwDOTCp7S86mD65h` identified a valid contract gap: the frozen production surface omitted `TaskBudgetService.consume_fork_plan()`, even though the required multi-branch workload must create real durable branches. G0.1 closes only that contract/test gap; it changes no production behavior.

If canonical main advances before a production integration gate:

1. re-read Issue #31 and material cross-issue/auditor updates;
2. resolve exact new main and accepted post-merge Architecture health;
3. refresh/re-anchor as required;
4. rerun exact-head evidence;
5. re-audit before production progression.

A sibling candidate becoming GREEN does not itself create main drift.

---

## 8. Merge boundary

G0 is contract/evidence/test-only and contains no production behavior change.
Repository contract-freeze auto-merge policy may apply only after its exact contract-only
gate is independently satisfied.

Any later mixed or production-affecting R11-G PR is treated as production and requires a
separate explicit user merge confirmation.

---

## 9. G0 exit gate

G0 closes only when:

- exact claimed baseline is frozen;
- all five required workload surfaces are named;
- production measurement APIs are frozen, including the atomic `consume_fork_plan()` branch-creation authority;
- deterministic-vs-timing evidence rules are frozen;
- cross-track authority boundaries are frozen;
- exact-head Linux + Windows Architecture is GREEN;
- independent audit reports no blocking R11-G0 P0/P1.

G1 then implements/runs the full deterministic load matrix against these frozen surfaces. G1 remains closed until the G0.1 repair is canonically landed and independently accepted.
