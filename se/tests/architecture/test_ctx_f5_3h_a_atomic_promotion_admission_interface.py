from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3H_A_ATOMIC_PROMOTION_ADMISSION_INTERFACE.md"
)
PROMOTION = Path("se/src/context/memory_promotion.py")
MEMORY = Path("se/src/context/memory.py")
MEMORY_REPOSITORY = Path("se/src/infrastructure/storage/repositories/memory.py")
RESERVATION_REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/promotion_reservation.py"
)
VERIFIER = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_verifier.py"
)


def _normalized_contract() -> str:
    return " ".join(CONTRACT.read_text(encoding="utf-8").split()).replace(chr(96), "")


def _class_fields(source: str, class_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
            }
    raise AssertionError(f"class {class_name} not found")


def test_ctx_f5_3h_a_freezes_payload_binding_without_duplicate_intent():
    contract = _normalized_contract()
    promotion = PROMOTION.read_text(encoding="utf-8")
    memory = MEMORY.read_text(encoding="utf-8")

    intent_fields = _class_fields(promotion, "MemoryPromotionIntent")
    reservation_fields = _class_fields(promotion, "PromotionReservation")

    assert "content_digest" in intent_fields
    assert "content" not in intent_fields
    assert reservation_fields == {"promotion_authority_id", "intent"}
    assert "def create_memory_record(" in memory
    assert "content: Any" in memory

    for phrase in (
        "reservation: PromotionReservation, content: Any",
        "There is no second caller-supplied intent argument",
        "computed payload digest to equal the durable intent.content_digest",
        "metadata = durable intent.metadata",
        "source provenance = durable intent.source_ref_snapshot",
        "schema version = durable intent.memory_schema_version",
        "No second metadata, source, owner, schema-version, authority-id, or intent authority",
    ):
        assert phrase in contract


def test_ctx_f5_3h_a_freezes_sqlite_write_intent_and_one_commit_owner():
    contract = _normalized_contract()
    source = MEMORY_REPOSITORY.read_text(encoding="utf-8")

    assert "async def sqlite_memory_admission_transaction(" in source
    assert 'dialect.name != "sqlite"' in source
    assert "if session.in_transaction():" in source
    assert "await _begin_sqlite_write_intent(session)" in source
    assert "yield repository" in source
    assert "await session.commit()" in source
    assert "await session.rollback()" in source

    for phrase in (
        "BEFORE any reservation authority read",
        "all reservation and Memory reads/writes on that same session",
        "context owns the one commit / rollback",
        "call session.commit() inside that scope",
        "Non-SQLite atomic admission remains CLOSED",
    ):
        assert phrase in contract


def test_ctx_f5_3h_a_freezes_exact_authority_and_no_verifier_replay_gate():
    contract = _normalized_contract()
    repository = RESERVATION_REPOSITORY.read_text(encoding="utf-8")
    verifier = VERIFIER.read_text(encoding="utf-8")

    assert "async def get(" in repository
    assert "PromotionReservationRow.promotion_authority_id == authority_id" in repository
    assert "async def mark_consumed(" in repository
    assert "DurablePromotionReservationState.ISSUED" in verifier
    assert "DurablePromotionReservationState.CONSUMED" in verifier
    assert "PromotionReservationAlreadyConsumedError" in verifier

    for phrase in (
        "load by the exact reservation.promotion_authority_id",
        "do not search by intent, digest, proof tuple, or alternate authority",
        "Standalone G-A verifier is not the authoritative atomic replay gate",
        "atomic admission must also handle a legitimate CONSUMED replay",
    ):
        assert phrase in contract


def test_ctx_f5_3h_a_freezes_issued_split_brain_and_consumed_read_only_replay():
    contract = _normalized_contract()
    memory_repository = MEMORY_REPOSITORY.read_text(encoding="utf-8")

    assert "async def get_by_promotion_authority(" in memory_repository
    assert "async def put(" in memory_repository

    for phrase in (
        "ISSUED + existing durable Memory => ADMISSION_MEMORY_REPLAY_CONFLICT",
        'no "healing"',
        "This path is strictly read-only",
        "It must NOT call MemoryRecordRepository.put(...)",
        "ADMISSION_CONSUMED_MEMORY_MISSING",
        "ADMISSION_CONSUMED_MEMORY_MISMATCH",
        "exact canonical replay-equivalent: return the existing durable Memory",
        "must not call mark_consumed(...) again",
    ):
        assert phrase in contract


def test_ctx_f5_3h_a_requires_non_mutating_canonical_replay_helper():
    contract = _normalized_contract()
    memory = MEMORY.read_text(encoding="utf-8")

    assert "def _immutable_record_canonical_bytes(" in memory
    assert 'exclude={"created_at"}' in memory
    assert "def _same_immutable_record(" in memory
    assert "canonical_memory_bytes(material)" in memory

    for phrase in (
        "one canonical reusable non-mutating replay-equivalence helper",
        "performs no write",
        "ignores only created_at",
        "cannot create a missing record",
        "H-A does not implement or expose this helper",
        "Before a production atomic orchestrator may be CLAIMED",
    ):
        assert phrase in contract


def test_ctx_f5_3h_a_freezes_errors_cancellation_and_closed_authority():
    contract = _normalized_contract()

    for phrase in (
        "ADMISSION_RESERVATION_NOT_ISSUED",
        "ADMISSION_RESERVATION_REVOKED",
        "ADMISSION_INTENT_CONFLICT",
        "ADMISSION_MEMORY_REPLAY_CONFLICT",
        "ADMISSION_CONSUMED_MEMORY_MISSING",
        "ADMISSION_CONSUMED_MEMORY_MISMATCH",
        "ADMISSION_TRANSACTION_UNAVAILABLE",
        "ADMISSION_PERSISTENCE_FAILURE",
        "asyncio.CancelledError propagates unchanged",
        "atomic admission orchestrator/service implementation",
        "Memory repository production modification",
        "promotion-reservation repository production modification",
        "Non-SQLite atomic admission remains CLOSED",
        "No se/src/** file changes",
        "Atomic admission production remains CLOSED",
    ):
        assert phrase in contract
