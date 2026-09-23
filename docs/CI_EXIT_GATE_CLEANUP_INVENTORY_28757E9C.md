# CI / Exit-Gate Cleanup Inventory

**Issue:** #16  
**Branch:** `work/pre-roadmap-ci-cleanup-28757e9c`  
**Baseline:** `main@28757e9c46355083ed16ee7bfda9c98fd7883a9b`  
**Scope:** test/CI cleanup only; no runtime semantic changes.

## Workflow ownership

| Historical surface | Canonical owner after cleanup | Action |
|---|---|---|
| `architecture-baseline.yml` | Linux full suite + Windows client contracts | KEEP; PR validation + push only on `main` |
| `phase5-exit-gates.yml` | Architecture full suite | RETIRE; all tests were already included |
| `r8-exit-gates.yml` | Architecture full suite | RETIRE; all R8 tests were already included |

The repository ruleset does not make the historical subset workflow names authoritative blocking checks. Behavioral coverage, not workflow-file presence, is the source of truth.

## Phase 5 wrapper mapping

### Phase 5.6

- caller cancellation / shared execution -> `test_phase5_tool_execution_coordinator.py`
- execution cancellation -> `test_phase5_tool_execution_coordinator.py`
- completed invocation reuse / completion-cancel race -> move to canonical coordinator race regression
- bounded completed ledger retention -> move to canonical coordinator race regression
- CI/doc assertions -> REMOVE

### Phase 5.7

- invalid arguments -> `test_phase5_adapters.py`
- invalid capability schema -> `test_phase5_adapters.py`
- visibility/authorization -> `test_phase5_adapters.py`
- canonical error/retryability vocabulary -> move to feature-oriented tool-error contract regression
- CI/doc assertions -> REMOVE

### Phase 5.8

E1-E5 are covered by canonical coordinator behavior:
- bounded dispatch;
- result order;
- duplicate/mismatch rejection;
- retry-budget ceiling;
- waiter cancellation/shared invocation ownership.

Action: RETIRE wrapper after canonical race coverage is retained.

### Phase 5.9

Historical persistence/resume tests are superseded by R7:
- legacy execution-plane row linking -> normalized R7 representation/persistence tests;
- legacy `resume_execution()` rehydration -> R7 checkpoint + ResumePlan reconstruction;
- committed-result dedupe -> `test_r7_c_tool_result_commitment.py`;
- resumed committed result reuse -> R7-D/F resume-plan/action regressions.

Action: RETIRE historical wrapper; do not preserve legacy WAITING_TOOL-era authority.

### Phase 5.10

Lifecycle event/correlation/EventBus adapter behavior remains valuable.

Action: RE-HOME to a canonical feature-oriented event publication test file; remove gate/doc/workflow/source-text assertions.

### Phase 5.11

ContextBuilder snapshot/tool-result/policy/cancellation behavior remains valuable.

Action: RE-HOME to a canonical ContextBuilder integration test file; remove gate/doc/workflow/source-text assertions.

## R4 wrapper mapping

`test_r4_exit_gate.py`:
- deadline hierarchy -> already owned by R4-C1/C2;
- typed capability timing provenance -> already covered by R4 capability/delegation suites;
- unique invariant: process-local monotonic deadlines must not become durable SQL columns.

Action: graft the unique SQL representation invariant into `test_r4_c1_iteration_deadline.py`, then retire the wrapper.

## R7-J real-network E2E

`test_r7_j_real_network_exit_gate.py` is **not redundant**.

Preserve all four real-network behaviors:
- K1 disconnect -> K2 auto-resume same execution;
- server restart while WAITING from SQL-only state;
- lost accepted ACK -> same request replay;
- two concurrent resume requests -> one authority winner.

This suite remains canonical real TCP/restart recovery evidence even if the historical filename is retained temporarily.

## R8-G vertical integration

Preserve G1-G4:
- vertical FORK happy path + replay;
- sibling branch isolation;
- root wrapper compatibility;
- capacity rejection atomicity;
- restart activation exactly once;
- restart-safe dormant fork cancellation.

G5 carried one additional mixed-terminal invariant beyond the existing FAILED+FAILED R8-F case:

- one OPEN branch head is `COMPLETED` with a durable result;
- another OPEN branch head is terminal (for example `FAILED`);
- all OPEN heads are terminal;
- branch result presence alone must not implicitly resolve/adopt the Task.

That invariant is now owned canonically by:
`test_r8_f_branch_activity.py::test_r8_f_completed_open_branch_does_not_implicitly_resolve_task`

Action: keep the historical G5 wrapper removed; retain G1-G4 vertical integration and the mixed-terminal reconcile invariant under the R8-F branch-activity owner.

## Exit criteria

Cleanup is complete only when:
- one same-repo PR SHA gets one canonical PR validation lane;
- post-merge `main` still runs Linux full suite + Windows client contracts;
- historical workflow/doc presence is not asserted as runtime behavior;
- functional invariants above remain owned by canonical feature tests;
- full Linux suite + Windows client contracts are green;
- no production semantic diff is introduced.
