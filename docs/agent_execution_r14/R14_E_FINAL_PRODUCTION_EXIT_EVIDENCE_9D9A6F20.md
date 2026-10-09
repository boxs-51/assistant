# AE-R14-E-G0 — GAP LEDGER / FINAL EXIT HOLD / NOT CERTIFIED

> **NOT A PRODUCTION-EXIT CERTIFICATE.** Exactly two new documentation/architecture-evidence files. The historical filename suffix `9D9A6F20` is a proposal label, **not** the reviewed main SHA or a successful R14-E production gate.

## Machine-readable negative gates

```ini
EVIDENCE_CLASS=GAP_LEDGER_ONLY
LEDGER_STATUS=GAP_LEDGER_FINAL_EXIT_HOLD_NOT_CERTIFIED
REVIEWED_MAIN=604a1abae68ea17a68b78fcad59be9865d0fe2c4
CANONICAL_POLICY=85_v2.5.2
OWNER_CLAIM_REF=359_6075119391
INDEPENDENT_PRECLAIM_REF=359_6075047756
MAIN_ARCH_RUN=37855824005
MAIN_ARCH_ATTEMPT=2
MAIN_ARCH_LINUX_JOB=113583172394
MAIN_ARCH_WINDOWS_CLIENT_JOB=113583171064
MAIN_ARCH_WINDOWS_SE_TOOLS_JOB=113583173135
WINDOWS_R6_D_ATTEMPT_1=RED_UNWAIVED
R9_H_409=BLOCKED_P1_HOLD
PR411=DRAFT_INTENTIONALLY_RED_NEVER_MERGE_ALONE
LEGACY_R7_L0_SQLITE_PLANNER=NOT_RUN
POSTGRESQL_TASK_CLAIM_PARITY=NOT_RUN
FULL_SERVER_SQL_RESTART_PLUS_AGENT_HITL_K1_K2=GAP_NOT_RUN_AS_COMPOSED
R14_E_FINAL_EXIT=HOLD
NO_P0_EXIT_CERTIFICATE=True
PRODUCTION_AUTHORITY=NONE
MERGE_AUTHORITY=NONE
R13_MIGRATE_FIRST=PRESERVE
R13_KEEP_CROSS_TRACK=PRESERVE
R13_KEEP_STABLE_ERROR=PRESERVE
R13_KEEP_HISTORICAL_MIGRATION=PRESERVE
R13_SUPERSEDED_ALREADY=PRESERVE
```

**Evidence semantics:** `INHERITED` means an existing scoped executable selector, not independently observed execution or one newly composed P0 flow. The reported Linux Architecture job is an aggregate run; none of the individual selectors below is claimed to have a separately inspected job outcome. `BLOCKED` = known live defect, `GAP` = absent composed proof, `NOT_RUN` = no matching exercise, and no row is promoted to runtime `PASS`.

## 17 suite classes

| suite | owner | source_path | test_selector | evidence_type | source_main_sha | matching_job_or_NOT_RUN | disposition | limits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| contract | AE-R14-D | se/tests/architecture/test_r14_d_composed_critical_p0_fault_matrix.py | test_r14_d_all_suite_classes_preserve_canonical_and_negative_gates | ARCHITECTURE | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| state-machine | AE-R7 | se/tests/integration/test_r7_f_resume_claim_atomicity.py | test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| identity-lineage | AE-R7 | se/tests/integration/test_r7_f_resume_claim_atomicity.py | test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| persistence | AE-R7 | se/tests/architecture/test_r7_b_atomic_waiting.py | test_r7_b_execution_update_failure_rolls_back_checkpoint_and_budget_release | TRANSACTIONAL | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| agent-runtime | AE-R14-C | se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py | test_r14_c_hitl_approve_real_websocket_agent_path | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| capability-runtime | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e2_disconnect_while_non_idempotent_tool_running_blocks_fallback | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| continuation | AE-R7 | se/tests/integration/test_r7_f_resume_claim_atomicity.py | test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| reconciliation | AE-R14-D | se/tests/e2e/test_r14_d_composed_critical_p0_fault_matrix.py | test_r14_d_k1_result_loss_k2_wire_reconcile_late_k1_cannot_replay_or_rewrite | COMPOSED_REAL_TCP | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| branching | AE-R8 | se/tests/integration/test_r8_d_atomic_fork_consume.py | test_r8_d_failure_at_each_write_boundary_rolls_back_everything | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| TaskBudget | AE-R8 | se/tests/integration/test_r8_d_atomic_fork_consume.py | test_r8_d_failure_at_each_write_boundary_rolls_back_everything | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| provider-retry | AE-R10 | se/tests/providers/test_r10_c_deadline_retry_policy.py | test_r10_c_cancellation_during_backoff_consumes_no_retry_token | COMPONENT_INTEGRATED | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| real-WebSocket | AE-R14-C | se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py | test_r14_c_hitl_deny_real_websocket_agent_path_fails_closed | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| multi-worker-race | R9-H #409 | se/tests/integration/test_r9_h_race_matrix.py | test_r9_h_resume_claim_create_vs_adopt_never_leaves_created_claim | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | BLOCKED | P1 #409 unresolved; negative exit gate, not runtime PASS |
| server-restart | AE-R7-J | NOT_RUN | NOT_RUN | SERVER_SQL_RESTART_COMPOSED | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | NOT_RUN | GAP | Inherited R7-J restart separate; composed server-owned SQL + Agent/HITL + K1/K2 not run |
| client-restart | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e4_client_process_generation_restart_recovers_terminal_from_sqlite | CLIENT_SQLITE_REAL_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| HITL | AE-R14-C | se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py | test_r14_c_hitl_approve_real_websocket_agent_path | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Inherits scoped selector only; does not prove single composed R14-E P0 E2E |
| fault-injection | AE-R14-D | se/tests/architecture/test_r14_d_composed_critical_p0_fault_matrix.py | test_r14_d_thirteen_fault_boundaries_bind_actual_executable_tests | ARCHITECTURE | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | BLOCKED | P1 #409 unresolved; negative exit gate, not runtime PASS |

## 13 fault boundaries

| boundary | owner | source_path | test_selector | evidence_type | source_main_sha | matching_job_or_NOT_RUN | disposition | limits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| before remote dispatch | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e1_disconnect_before_dispatch_preserves_not_dispatched | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| after dispatch | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e2_disconnect_while_non_idempotent_tool_running_blocks_fallback | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| before side effect | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r14_b_real_tcp_failure_after_running_before_target_entry_is_terminal_no_replay | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| after side effect | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e7_deduplicated_replay_preserves_key_and_external_effect_once | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| before result send | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e3_same_process_reconnect_recovers_lost_terminal_once | REAL_TCP_E2E | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| after result send | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e10_late_k1_terminal_cannot_mutate_k2_committed_reconciliation | COMPOSED_REAL_TCP | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| before SE commit | AE-R6 | se/tests/e2e/test_r6_e_remote_reconciliation_faults.py | test_r6_e4_client_process_generation_restart_recovers_terminal_from_sqlite | COMPOSED_REAL_TCP | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| after SE commit | AE-R14-D | se/tests/e2e/test_r14_d_composed_critical_p0_fault_matrix.py | test_r14_d_k1_result_loss_k2_wire_reconcile_late_k1_cannot_replay_or_rewrite | COMPOSED_REAL_TCP | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| during resume CAS | AE-R7 | se/tests/integration/test_r7_f_resume_claim_atomicity.py | test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| during ADOPT CAS | R9-H #409 | se/tests/integration/test_r9_h_race_matrix.py | test_r9_h_resume_claim_create_vs_adopt_never_leaves_created_claim | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | BLOCKED | Real SQLite orphan CREATED ResumeClaim race P1 #409; draft PR #411 intentionally RED |
| during provider retry | AE-R10 | se/tests/providers/test_r10_c_deadline_retry_policy.py | test_r10_c_cancellation_during_backoff_consumes_no_retry_token | COMPONENT_INTEGRATED | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| during checkpoint persistence | AE-R7 | se/tests/architecture/test_r7_b_atomic_waiting.py | test_r7_b_execution_update_failure_rolls_back_checkpoint_and_budget_release | TRANSACTIONAL | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |
| during TaskBudget reservation | AE-R8 | se/tests/integration/test_r8_d_atomic_fork_consume.py | test_r8_d_failure_at_each_write_boundary_rolls_back_everything | SQL_INTEGRATION | 604a1abae68ea17a68b78fcad59be9865d0fe2c4 | 37855824005/113583172394 (Linux aggregate; node-level proof not asserted) | INHERITED | Source/test locator is inherited; distinct from combined server SQL restart plus Agent HITL |

## Exit conditions that are NOT satisfied

- **#409 is BLOCKED:** durable Task=COMPLETED plus orphan `ResumeClaim=CREATED` was reproduced using real SQLite. Diagnostic PR #411 is DRAFT/INTENTIONALLY RED/NEVER MERGE ALONE, not a production repair. Legacy true R7 L0 no-TaskBudget SQLite full planner remains NOT_RUN. PostgreSQL Task/ResumeClaim concurrency parity remains NOT_RUN.
- **Missing single composed real server SQL restart proof:** R7-J inherited server-restart behavior, R14-C real HITL and R14-D K1/K2 real TCP client-SQLite ledger are distinct. They are NOT one server-owned durable SQL process-crash/restart + Agent/HITL + K1/K2 recovery/reconciliation witness, so `FULL_SERVER_SQL_RESTART_PLUS_AGENT_HITL_K1_K2=GAP_NOT_RUN_AS_COMPOSED`.
- **Same-SHA Windows R6-D reliability concern:** baseline Architecture run #37855824005 attempt2 was 3/3 GREEN (Linux 113583172394, Windows-client 113583171064, Windows-server/Tools 113583173135), but same SHA had a Windows-client R6-D 2-second result-wait RED in attempt1. This does not resolve root cause or authorize production exit.
- **R13 compatibility dispositions preserved:** `MIGRATE_FIRST`, `KEEP_CROSS_TRACK`, `KEEP_STABLE_ERROR`, `KEEP_HISTORICAL_MIGRATION`, `SUPERSEDED_ALREADY`; no new claims over historical R13 migration behavior.
- **Authority fence:** `PRODUCTION_AUTHORITY=NONE`, `MERGE_AUTHORITY=NONE`; no shared runtime, TaskBudget, ResumeClaim, SQL, DDL, E2E, workflow or fixture permission.

## Release interpretation

`R14_E_FINAL_EXIT=HOLD` and `NO_P0_EXIT_CERTIFICATE=True` remain binding after this gap-only artifact. An independent implementation FINAL, actual #409 repair and new composed proof, and a separately user-authorized Integration Wave are separate requirements.