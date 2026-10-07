# AE-R14-A — HEAD Audit / Fault-Matrix Contract Freeze

**Repository:** `boxs-51/assistant`  
**Canonical workspace:** Issue #359  
**Canonical policy:** Issue #85 v2.5  
**Claim baseline:** `main@10071f4ef3729e5c9e70117f4b7b41d6b6258778`  
**Current-main health at release:** Architecture #2319 / `37643228197` GREEN/GREEN  
**Independent PRE-CLAIM:** Issue #359 comment #6041346377 — PASS / RELEASED  
**Owner CLAIM:** Issue #359 comment #6041532888 — exact 2 NEW files  
**Class:** CONTRACT / ARCHITECTURE EVIDENCE ONLY  
**Production authority:** NONE  
**Fault-hook authority:** NONE  
**Merge authority:** NONE

## 1. Purpose

AE-R14 turns existing tests into architecture evidence. R14-A does not add a new
fault mechanism. It freezes the current evidence topology, names proven gaps,
and prevents later R14 work from confusing historical tests, component tests,
integration tests, real TCP/WebSocket E2E evidence, or cross-track ownership.

The canonical R14 exit rule remains:

> Critical P0 flows have real integration/E2E tests, not only unit tests.

R14-A is evidence classification only. A row classified as a GAP is not
implementation authority.

## 2. Exact claimed scope

R14-A owns exactly two NEW files:

1. `docs/agent_execution_r14/R14_A_HEAD_AUDIT_FAULT_MATRIX_CONTRACT_FREEZE_10071F4E.md`
2. `se/tests/architecture/test_r14_a_head_audit_fault_matrix_contract_freeze.py`

There is no third path.

R14-A does not authorize any edit under existing `se/src/**`, `cl/src/**`,
workflow files, SQL models, migrations, repositories, provider implementations,
TaskBudget/UBQ/TBO code, checkpoint persistence, CTX, CAS, #156, APR, HITL
production code, or post-R14 MCP integration.

## 3. Required R14 suite inventory

| Required suite | R14-A classification | Canonical evidence / disposition |
|---|---|---|
| contract | COVERED_ARCHITECTURE_COMPOSED | Existing phase exit contracts plus this R14-A architecture binder |
| state-machine | COVERED_EXECUTABLE | Existing Agent execution state-machine and transition regressions |
| identity-lineage | COVERED_INTEGRATION | R3/R8/R9/R12 durable lineage and race integration |
| persistence | COVERED_INTEGRATION | R7/R8/R9/R11/R12 SQL-backed integration |
| agent-runtime | COVERED_REAL_E2E | Phase 6.9/6.10 and R7 real-network Agent loops |
| capability-runtime | COVERED_REAL_E2E | R6-E real TCP capability dispatch/reconciliation |
| continuation | COVERED_REAL_E2E | R7-J real network continuation/restart/lost-ACK |
| reconciliation | COVERED_REAL_E2E | R6-E reconciliation fault suite |
| branching | COVERED_INTEGRATION | R8 atomic FORK plus R9 branch-resolution races |
| TaskBudget | COVERED_INTEGRATION | R5-C, R8-D, R9 and R12 reservation/accounting integration |
| provider-retry | COVERED_COMPONENT_INTEGRATED | R10-C/D/H deterministic retry/fallback/deadline matrix; no real external-provider E2E is inferred |
| real-WebSocket | COVERED_REAL_E2E | Phase 6.9/6.10, R6-E, R7-E/J |
| multi-worker-race | COVERED_INTEGRATION | R12-G two-service recovery one-winner matrix |
| server-restart | COVERED_REAL_E2E_AND_INTEGRATION | R7-J server restart plus R12-G durable restart |
| client-restart | COVERED_REAL_E2E | R6-E client process-generation restart |
| HITL | GAP | `R14-GAP-HITL-E2E-1` |
| fault-injection | COMPOSED_WITH_BOUNDED_GAPS | Existing R6/R7/R9/R10/R12 evidence plus the exact-boundary gap below |

No component-level row is promoted to “real E2E” unless the executable evidence
actually crosses the corresponding real transport/process boundary.

## 4. Required fault-boundary matrix

| Canonical R14 fault point | Classification | Evidence / exact disposition |
|---|---|---|
| before remote dispatch | COVERED_REAL_E2E | R6-E1 disconnect before send boundary proves NOT_DISPATCHED and zero client execution |
| after dispatch | COVERED_REAL_E2E | R6-E2 disconnect while non-idempotent tool is running plus R6-E11 timeout-is-not-rollback-proof |
| before side effect | GAP_EXACT_BOUNDARY | `R14-GAP-REMOTE-PRE-SIDE-EFFECT-1`: no executable owner proves the distinct dispatch-accepted / local-side-effect-not-started cut |
| after side effect | COVERED_REAL_E2E | R6-E7 deduplicated replay preserves one external effect; R6-E11 proves a side effect may occur after caller timeout |
| before result send | COVERED_REAL_E2E | R6-E3/E4 persist client terminal truth while the connection closes before result delivery |
| after result send | COMPOSED_REAL_E2E | R6-E10 allows a late old-connection terminal delivery after newer reconciliation authority and proves it cannot rewrite the committed outcome |
| before SE commit | COMPOSED_REAL_E2E | R6-E3/E4 show CL terminal truth while SE remains OUTCOME_UNKNOWN, followed by exact reconciliation commit/reuse |
| after SE commit | COVERED_REAL_E2E | R6-E10 freezes revision/output after K2 reconciliation despite the late K1 terminal |
| during resume CAS | COVERED_INTEGRATION | R7-F ResumeClaim / WAITING-to-RUNNING loss, rollback and one-winner evidence plus R12 recovery arbitration |
| during ADOPT CAS | COVERED_INTEGRATION | R9-H ADOPT / resume / branch-resolution race matrix |
| during provider retry | COVERED_COMPONENT_INTEGRATED | R10-C cancellation/deadline during backoff and shared retry budget; R10-D/H fallback and visible-output terminal fences |
| during checkpoint persistence | COVERED_TRANSACTIONAL | R7-B and R11-D atomic checkpoint/execution/budget rollback/persistence regressions |
| during TaskBudget reservation | COVERED_INTEGRATION | R5-C atomic reservation/slot transitions plus R8-D failure-at-each-write-boundary rollback |

The phrase “before side effect” in this matrix means the exact cut **after remote
dispatch has been accepted but before local tool execution has begun**. R6-E1
is earlier than that cut, and R6-E2 is later than that cut. Therefore neither is
silently relabelled as exact coverage.

## 5. Proven bounded gaps

### R14-GAP-REMOTE-PRE-SIDE-EFFECT-1

The repository has real E2E coverage before dispatch and while a remote
non-idempotent call is running, but R14-A found no executable owner for the
distinct boundary after dispatch acceptance and before local side-effect
execution starts.

Disposition: **GAP / NO REPAIR AUTHORITY**.

Any test-only harness, client dispatcher seam, or production fault hook needed
to close this gap requires a fresh exact PRE-CLAIM. Any shared production path
also requires the applicable bilateral owner disposition.

### R14-GAP-HITL-E2E-1

The repository has architecture-level HITL/wait-reason and approval evidence,
but R14-A found no HITL integration or E2E owner under the current test tree.

Disposition: **GAP / NO REPAIR AUTHORITY**.

A later HITL closure must separately define the real integration boundary,
durable WAITING semantics, approval identity, restart behavior, and ownership.
R14-A grants none of those semantics.

## 6. Reusable canonical executable owners

R14-A intentionally reuses, rather than duplicates, these landed surfaces:

- `se/tests/e2e/test_r6_e_remote_reconciliation_faults.py`
  - `test_r6_e1_disconnect_before_dispatch_preserves_not_dispatched`
  - `test_r6_e2_disconnect_while_non_idempotent_tool_running_blocks_fallback`
  - `test_r6_e3_same_process_reconnect_recovers_lost_terminal_once`
  - `test_r6_e4_client_process_generation_restart_recovers_terminal_from_sqlite`
  - `test_r6_e7_deduplicated_replay_preserves_key_and_external_effect_once`
  - `test_r6_e10_late_k1_terminal_cannot_mutate_k2_committed_reconciliation`
  - `test_r6_e11_timeout_is_not_rollback_proof_and_late_terminal_cannot_resurrect`
- `se/tests/e2e/test_r7_j_real_network_exit_gate.py`
  - real TCP reconnect, server restart, lost accepted ACK, concurrent resume.
- `se/tests/integration/test_r7_f_resume_claim_atomicity.py`
  - claim CAS loss rollback and one consumed winner.
- `se/tests/integration/test_r8_d_atomic_fork_consume.py`
  - atomic fork consume, one winner, failure-at-each-write-boundary rollback.
- `se/tests/integration/test_r9_h_race_matrix.py`
  - retry/ADOPT/DISCARD/AGGREGATE/resume races.
- `se/tests/providers/test_r10_c_deadline_retry_policy.py`
  - cancellation/deadline during retry backoff and shared retry-budget semantics.
- `se/tests/providers/test_r10_h_exit_matrix.py`
  - visible-output terminal no-replay and runtime cleanup.
- `se/tests/architecture/test_r7_b_atomic_waiting.py` and R11 integration
  - atomic checkpoint/execution/budget persistence and rollback.
- `se/tests/integration/test_r12_g_recovery_resume_race_matrix.py`
  - multi-worker one winner, durable restart and recovery-vs-reconnect.
- `se/tests/integration/test_r12_h0_full_fault_exit_matrix.py`
  - composed R6/R7/R9/R10/R11/R12 fault evidence.

## 7. CI topology and Issue #16

Issue #16 is historical pre-roadmap CI / exit-gate cleanup and is CLOSED. It is
not an R14 production or workflow authority source.

Architecture Baseline remains the canonical Linux/Windows architecture health
owner. R14-A MUST NOT recreate retired duplicate Phase 5.6/5.7-style workflows
or add a new workflow merely to duplicate Architecture Baseline.

## 8. R13 handoff remains terminal

R14-A preserves the R13-H compatibility-removal exit matrix exactly:

- `MIGRATE_FIRST` remains deferred;
- timeout dual-read compatibility remains `KEEP_CROSS_TRACK` / Issue #147-owned;
- stable WAITING vocabulary remains `KEEP_STABLE_ERROR`;
- migration history remains `KEEP_HISTORICAL_MIGRATION`;
- production `TextContent` remains `SUPERSEDED_ALREADY`.

No R14 test classification converts a retained R13 item into removal authority.

## 9. Cross-track ownership

Issue #359 ↔ Issue #163 has a bounded bilateral PASS for SBX-2 lifecycle-only
use of `se/src/runtimes/agent/runtime.py`.

That bilateral grants **zero R14 fault-hook transfer**. SBX-2 may not derive
R14 authority from this matrix, and R14 may not derive #156 sandbox authority
from the bilateral.

The same no-transfer rule applies to CTX #15, CAS #74, UBQ #141/#147, TBO #333,
APR #278, CL-UI #242, GAME-AUTO-CLIENT #221, and post-R14 MCP ownership.

## 10. Reserved later-stage dependency graph

The following ordering is descriptive only and grants no implementation claim:

```text
R14-A  HEAD audit + evidence/fault matrix freeze
   |
   +--> R14-B  exact remote pre-side-effect gap closure proposal
   |
   +--> R14-C  HITL real integration/E2E gap closure proposal
   |
   +--> R14-D  composed critical-P0 integration/E2E matrix
   |
   '--> R14-E  final production-exit evidence + handoff
```

Each later stage requires a fresh current-main audit and its own PRE-CLAIM.
R14-B/R14-C may remain evidence/test-only if the gap can be closed without a
production hook. If a production mutation is required, production authority
must be independently released before implementation.

## 11. R14-A exit gate

R14-A can reach FINAL only when:

1. changed paths remain exactly the two NEW claimed files;
2. production/runtime/client/schema/migration/config/workflow delta is ZERO;
3. every required suite and fault boundary is classified;
4. the two proven gaps are preserved as gaps, not papered over by weaker tests;
5. R13 and cross-track ownership fences remain explicit;
6. fresh exact-head Architecture Linux + Windows is GREEN;
7. independent FINAL reports P0/P1/P2 = 0/0/0.

**R14-A does not close either gap. It freezes the evidence needed to close them lawfully.**
