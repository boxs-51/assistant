from __future__ import annotations

import ast
from pathlib import Path


PROMOTION = Path("se/src/context/memory_promotion.py")
SERVICE = Path(
    "se/src/infrastructure/storage/services/memory_promotion_admission.py"
)
CONTRACT = Path(
    "docs/context_future/CTX_F5_3H_B2_SQLITE_ATOMIC_PROMOTION_ADMISSION.md"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(_source(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"class {class_name} not found")


def _top_level_bound_names(path: Path) -> set[str]:
    tree = ast.parse(_source(path))
    names: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            names.add(node.name.id)

    return names


def test_ctx_f5_3h_b2_freezes_exact_error_family_and_service_surface() -> None:
    promotion = _source(PROMOTION)
    service = _source(SERVICE)

    for name in (
        "MemoryPromotionAdmissionError",
        "PromotionAdmissionReservationNotIssuedError",
        "PromotionAdmissionReservationRevokedError",
        "PromotionAdmissionIntentConflictError",
        "PromotionAdmissionMemoryReplayConflictError",
        "PromotionAdmissionConsumedMemoryMissingError",
        "PromotionAdmissionConsumedMemoryMismatchError",
        "PromotionAdmissionTransactionUnavailableError",
        "PromotionAdmissionPersistenceFailureError",
    ):
        assert f"class {name}(" in promotion

    methods = _class_methods(SERVICE, "DurableMemoryPromotionAdmission")
    assert methods == {"__init__", "admit"}

    bound_names = _top_level_bound_names(SERVICE)
    assert "MemoryAdmissionSessionContextFactory" in bound_names
    assert "SessionContextFactory" not in bound_names


def test_ctx_f5_3h_b2_detaches_payload_before_session_or_transaction() -> None:
    service = _source(SERVICE)

    ordered = (
        "validate_promotion_reservation_integrity(reservation)",
        "canonical_payload_bytes = canonical_memory_bytes(content)",
        'content_snapshot = json.loads(canonical_payload_bytes.decode("utf-8"))',
        "payload_digest = memory_content_digest(content_snapshot)",
        "del content",
        "async with self._session_factory() as session:",
        "async with sqlite_memory_admission_transaction(",
    )
    positions = [service.index(fragment) for fragment in ordered]
    assert positions == sorted(positions)

    assert "create_memory_record(" in service
    assert "content=content_snapshot" in service
    assert "durable.intent.model_dump_json()" in service
    assert 'metadata_snapshot = durable_intent_json["metadata"]' in service
    assert "metadata=metadata_snapshot" in service
    assert "metadata=durable.intent.metadata" not in service
    assert "payload_digest != durable.intent.content_digest" in service
    assert "memory_content_digest(content)" not in service


def test_ctx_f5_3h_b2_uses_exact_durable_authority_and_public_equality_helpers() -> None:
    service = _source(SERVICE)

    assert "reservation_repository.get(" in service
    assert "reservation.promotion_authority_id" in service
    assert "validate_reservation_matches_intent(" in service
    assert "memory_records_replay_equivalent(" in service
    assert "_canonical_intent_bytes" not in service
    assert ".get_by_intent(" not in service
    assert ".get_by_proof_authority(" not in service


def test_ctx_f5_3h_b2_keeps_one_transaction_owner_and_phase_sensitive_mapping() -> None:
    service = _source(SERVICE)

    assert "transaction_entered = False" in service
    assert "transaction_entered = True" in service
    assert "except MemoryAdmissionTransactionError as exc:" in service
    assert "except SQLAlchemyError as exc:" in service
    assert "if not transaction_entered:" in service
    assert "PromotionAdmissionTransactionUnavailableError" in service
    assert "PromotionAdmissionPersistenceFailureError" in service

    assert "session.commit(" not in service
    assert "session.rollback(" not in service
    assert "session.begin(" not in service
    assert "except BaseException" not in service


def test_ctx_f5_3h_b2_freezes_issued_consumed_and_revoked_state_machine() -> None:
    service = _source(SERVICE)

    for phrase in (
        "DurablePromotionReservationState.REVOKED",
        "DurablePromotionReservationState.ISSUED",
        "DurablePromotionReservationState.CONSUMED",
        "await memory_repository.get_by_promotion_authority(",
        "await memory_repository.put(expected)",
        "await reservation_repository.mark_consumed(",
        "PromotionAdmissionMemoryReplayConflictError",
        "PromotionAdmissionConsumedMemoryMissingError",
        "PromotionAdmissionConsumedMemoryMismatchError",
        "PromotionAdmissionReservationRevokedError",
    ):
        assert phrase in service


def test_ctx_f5_3h_b2_contract_carries_all_30_exit_proofs_and_closed_authority() -> None:
    contract = " ".join(_source(CONTRACT).split())

    for number in range(1, 31):
        assert f"{number}." in contract

    for phrase in (
        "released production files = exactly 2",
        "schema/migration authority = CLOSED",
        "runtime/API/source/non-SQLite authority = CLOSED",
        "H-B3 = mandatory exit/evidence gate",
        "No repository, SQL model, Alembic, UnitOfWork, DatabaseDriver, runtime, API",
        "exact-head Linux + Windows Architecture",
        "independent FINAL GREEN",
    ):
        assert phrase in contract
