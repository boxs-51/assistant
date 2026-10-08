"""R14-D architecture binder: explicit evidence levels and fail-closed exit HOLD.

The corresponding E2E file *executes* a new composed real TCP path.
These architecture assertions alone do not establish correctness of a P0 flow.
Existing R6/R7/R8/R9/R10/R12 evidence is reused, not silently upgraded to
real restart E2E, and R14-C real HITL tests have landed under independently CLOSED Wave #406. Their separate HITL cases
do NOT by themselves establish K1/K2 reconciliation composed with Agent HITL.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
D_E2E = "se/tests/e2e/test_r14_d_composed_critical_p0_fault_matrix.py"
R6_E2E = "se/tests/e2e/test_r6_e_remote_reconciliation_faults.py"
R14_C_E2E = "se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py"
R7_F = "se/tests/integration/test_r7_f_resume_claim_atomicity.py"
R8_D = "se/tests/integration/test_r8_d_atomic_fork_consume.py"
R9_H = "se/tests/integration/test_r9_h_race_matrix.py"
R10_C = "se/tests/providers/test_r10_c_deadline_retry_policy.py"
R7_B = "se/tests/architecture/test_r7_b_atomic_waiting.py"

PRE_CLAIM = "6066465360"
C_PR = "373"
C_WAVE = "IW-2026-10-09-03"
P1_INCIDENT = "409"
PRODUCTION_AUTHORITY = "NONE"

# "INHERITED" means this phase reuses an executable owner; it does not claim
# that the predecessor's component/SQL/real TCP evidence has changed class.
# "CANDIDATE" denotes executable D test not yet independently FINAL-audited.
# "BLOCKED" is a real known correctness issue, not a missing test name.
EVIDENCE_LEVELS = frozenset(
    (
        "REAL_TCP_E2E",
        "SQL_INTEGRATION",
        "TRANSACTIONAL",
        "COMPONENT_INTEGRATED",
        "COMPOSED_REAL_TCP",
    )
)


@dataclass(frozen=True)
class FaultBoundary:
    name: str
    evidence_level: str
    file_path: str
    test_name: str
    disposition: str


FAULT_BOUNDARIES = (
    FaultBoundary(
        "before remote dispatch", "REAL_TCP_E2E", R6_E2E,
        "test_r6_e1_disconnect_before_dispatch_preserves_not_dispatched",
        "INHERITED",
    ),
    FaultBoundary(
        "after dispatch", "REAL_TCP_E2E", R6_E2E,
        "test_r6_e2_disconnect_while_non_idempotent_tool_running_blocks_fallback",
        "INHERITED",
    ),
    FaultBoundary(
        "before side effect", "REAL_TCP_E2E", R6_E2E,
        "test_r14_b_real_tcp_failure_after_running_before_target_entry_is_terminal_no_replay",
        "INHERITED",
    ),
    FaultBoundary(
        "after side effect", "REAL_TCP_E2E", R6_E2E,
        "test_r6_e7_deduplicated_replay_preserves_key_and_external_effect_once",
        "INHERITED",
    ),
    FaultBoundary(
        "before result send", "REAL_TCP_E2E", R6_E2E,
        "test_r6_e3_same_process_reconnect_recovers_lost_terminal_once",
        "INHERITED",
    ),
    FaultBoundary(
        "after result send", "COMPOSED_REAL_TCP", R6_E2E,
        "test_r6_e10_late_k1_terminal_cannot_mutate_k2_committed_reconciliation",
        "INHERITED",
    ),
    FaultBoundary(
        "before SE commit", "COMPOSED_REAL_TCP", R6_E2E,
        "test_r6_e4_client_process_generation_restart_recovers_terminal_from_sqlite",
        "INHERITED",
    ),
    FaultBoundary(
        "after SE commit", "COMPOSED_REAL_TCP", D_E2E,
        "test_r14_d_k1_result_loss_k2_wire_reconcile_late_k1_cannot_replay_or_rewrite",
        "CANDIDATE",
    ),
    FaultBoundary(
        "during resume CAS", "SQL_INTEGRATION", R7_F,
        "test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation",
        "INHERITED",
    ),
    FaultBoundary(
        "during ADOPT CAS", "SQL_INTEGRATION", R9_H,
        "test_r9_h_resume_claim_create_vs_adopt_never_leaves_created_claim",
        "BLOCKED_P1_409",
    ),
    FaultBoundary(
        "during provider retry", "COMPONENT_INTEGRATED", R10_C,
        "test_r10_c_cancellation_during_backoff_consumes_no_retry_token",
        "INHERITED",
    ),
    FaultBoundary(
        "during checkpoint persistence", "TRANSACTIONAL", R7_B,
        "test_r7_b_execution_update_failure_rolls_back_checkpoint_and_budget_release",
        "INHERITED",
    ),
    FaultBoundary(
        "during TaskBudget reservation", "SQL_INTEGRATION", R8_D,
        "test_r8_d_failure_at_each_write_boundary_rolls_back_everything",
        "INHERITED",
    ),
)

# There are 17 required R14 suite classes.  Reusable suite owners from the
# frozen R14-A contract do NOT constitute a new real-E2E proof for each suite.
SUITE_DISPOSITION = {
    "contract": "INHERITED_ARCHITECTURE",
    "state-machine": "INHERITED_EXECUTABLE",
    "identity-lineage": "INHERITED_SQL_INTEGRATION",
    "persistence": "INHERITED_SQL_INTEGRATION",
    "agent-runtime": "INHERITED_REAL_E2E",
    "capability-runtime": "INHERITED_REAL_TCP_E2E",
    "continuation": "INHERITED_REAL_E2E",
    "reconciliation": "COMPOSED_REAL_TCP_CANDIDATE",
    "branching": "INHERITED_SQL_INTEGRATION",
    "TaskBudget": "INHERITED_SQL_INTEGRATION",
    "provider-retry": "INHERITED_COMPONENT_INTEGRATED",
    "real-WebSocket": "COMPOSED_REAL_TCP_CANDIDATE",
    "multi-worker-race": "INHERITED_SQL_INTEGRATION",
    "server-restart": "INHERITED_SQL_BACKED_REAL_E2E_R7_J",
    "client-restart": "INHERITED_REAL_E2E_R6_E4",
    "HITL": "R14_C_CANONICAL_REAL_TCP_HITL_E2E_SCOPED_HEALTHY",
    "fault-injection": "PARTIAL_WITH_R9_H_P1_409",
}

# No review, CI, or code in R14-D can clear this factual defect by implication.
FINAL_EXIT_DISPOSITION = "HOLD_R9_H_P1_409_AND_COMPOSED_AGENT_HITL_RECONCILIATION_GAP"
NO_P0_EXIT_CERTIFICATE = True


def _test_names(path: str) -> set[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    module = ast.parse(text, filename=path)
    return {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def test_r14_d_thirteen_fault_boundaries_bind_actual_executable_tests():
    expected = (
        "before remote dispatch",
        "after dispatch",
        "before side effect",
        "after side effect",
        "before result send",
        "after result send",
        "before SE commit",
        "after SE commit",
        "during resume CAS",
        "during ADOPT CAS",
        "during provider retry",
        "during checkpoint persistence",
        "during TaskBudget reservation",
    )
    assert tuple(row.name for row in FAULT_BOUNDARIES) == expected
    for row in FAULT_BOUNDARIES:
        assert row.evidence_level in EVIDENCE_LEVELS
        assert row.test_name in _test_names(row.file_path), row
    assert len(FAULT_BOUNDARIES) == 13


def test_r14_d_real_transport_is_executable_not_just_documented():
    # This source binder is complementary to the real socket test's execution
    # under pytest and Architecture CI. Do not infer a successful socket E2E
    # run from this static AST check by itself.
    text = (ROOT / D_E2E).read_text(encoding="utf-8")
    assert "r6._start_uvicorn(app)" in text
    assert "r6._connect_client(" in text
    assert 'envelope.get("type") == "capability.reconcile"' in text
    assert "runtime.reconcile_remote_invocation(" in text
    assert "await store.list_attempts(" in text
    assert "ClientInvocationLedger(ledger_path)" in text
    assert "revision = committed.revision" in text
    assert "assert after_late.revision == revision" in text
    assert "assert external_effects == [invocation_id]" in text
    assert "assert server_fallback_calls == []" in text
    assert PRODUCTION_AUTHORITY == "NONE"


def test_r14_d_all_suite_classes_preserve_canonical_and_negative_gates():
    expected_suites = {
        "contract", "state-machine", "identity-lineage", "persistence",
        "agent-runtime", "capability-runtime", "continuation",
        "reconciliation", "branching", "TaskBudget", "provider-retry",
        "real-WebSocket", "multi-worker-race", "server-restart",
        "client-restart", "HITL", "fault-injection",
    }
    assert set(SUITE_DISPOSITION) == expected_suites
    assert len(SUITE_DISPOSITION) == 17
    # R14-C was user-authorized, merged and independently scoped-healthy
    # under closed Wave #406. This does not itself compose HITL with K1/K2.
    assert SUITE_DISPOSITION["HITL"] == (
        "R14_C_CANONICAL_REAL_TCP_HITL_E2E_SCOPED_HEALTHY"
    )
    c_tests = _test_names(R14_C_E2E)
    assert "test_r14_c_hitl_approve_real_websocket_agent_path" in c_tests
    assert "test_r14_c_hitl_deny_real_websocket_agent_path_fails_closed" in c_tests
    # Two independently executable predecessor HITL outcomes do not imply
    # real Agent-to-K1/K2 resume/reconcile composition in THIS D candidate.
    assert "COMPOSED_AGENT_HITL_RECONCILIATION_GAP" in FINAL_EXIT_DISPOSITION
    assert "409" in SUITE_DISPOSITION["fault-injection"]
    assert "P1_409" in next(
        row.disposition
        for row in FAULT_BOUNDARIES
        if row.name == "during ADOPT CAS"
    )
    assert NO_P0_EXIT_CERTIFICATE is True
    assert FINAL_EXIT_DISPOSITION.startswith("HOLD_R9_H")
    assert C_WAVE == "IW-2026-10-09-03"
    assert PRE_CLAIM == "6066465360"
    assert C_PR == "373"
    assert P1_INCIDENT == "409"
