from __future__ import annotations

import ast
from pathlib import Path

RECOVERY = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_recovery.py"
)
ORCHESTRATION = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_promotion_orchestration.py"
)
ADMISSION = Path("se/src/infrastructure/storage/services/memory_promotion_admission.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function(cls: ast.ClassDef, name: str):
    for node in cls.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"function {name} not found")


def test_resolver_is_read_only_non_minting_and_uses_exact_dual_lookup() -> None:
    source = _read(RECOVERY)
    recover = _function(
        _class(source, "DurablePromotionReservationRecovery"), "recover"
    )

    assert isinstance(recover, ast.AsyncFunctionDef)
    calls = {
        node.func.attr
        for node in ast.walk(recover)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "get_by_intent" in calls
    assert "get_by_proof_authority" in calls
    assert "insert_or_converge_issued_candidate" not in calls
    assert "mark_consumed" not in calls
    assert "mark_revoked" not in calls

    for forbidden in (
        "uuid4",
        "authority_id_factory",
        "DurableMemoryPromotionAdmission",
        "memory_promotion_admission",
        "session.commit",
        "session.rollback",
    ):
        assert forbidden not in source


def test_recovery_matrix_distinguishes_no_winner_from_terminal_or_conflict() -> None:
    source = _read(RECOVERY)

    for required in (
        "if exact_winner is None and proof_winner is None:",
        "return None",
        "PromotionReservationProofReuseConflictError",
        "PromotionReservationReconstructionCorruptionError",
        "DurablePromotionReservationState.REVOKED",
        "PromotionReservationRevokedError",
        "DurablePromotionReservationState.ISSUED",
        "DurablePromotionReservationState.CONSUMED",
        "PromotionReservation(",
    ):
        assert required in source


def test_orchestration_reproofs_then_uses_recovery_handoff() -> None:
    source = _read(ORCHESTRATION)
    reserve = _function(
        _class(source, "DurableToolResponsePayloadPromotionOrchestration"),
        "reserve",
    )
    awaited = []
    for node in ast.walk(reserve):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            function = node.value.func
            if isinstance(function, ast.Attribute):
                awaited.append(function.attr)

    assert awaited == ["read_trusted_promotion_material", "reserve"]
    assert "DurablePromotionReservationRecoveryHandoff" in source
    assert "memory_promotion_admission" not in source
    assert "DurableMemoryPromotionAdmission" not in source


def test_slice_does_not_modify_admission_or_open_external_wiring() -> None:
    recovery = _read(RECOVERY)
    orchestration = _read(ORCHESTRATION)
    admission = _read(ADMISSION)

    assert "async def admit(" in admission
    for source in (recovery, orchestration):
        for forbidden in (
            "ContextBuilder",
            "ApplicationContainer",
            "FastAPI",
            "FileAsset",
            "TaskBudget",
            "MemoryRecord",
            "create_memory_record",
        ):
            assert forbidden not in source
