from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3H_B0_ATOMIC_ADMISSION_PRODUCTION_ENTRY.md"
)
MEMORY = Path("se/src/context/memory.py")
PROMOTION = Path("se/src/context/memory_promotion.py")
MEMORY_REPOSITORY = Path("se/src/infrastructure/storage/repositories/memory.py")
RESERVATION_REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/promotion_reservation.py"
)
ISSUER = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_issuer.py"
)


def _normalized_contract() -> str:
    return " ".join(CONTRACT.read_text(encoding="utf-8").split()).replace(chr(96), "")


def _function_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_ctx_f5_3h_b0_freezes_public_replay_helper_migration() -> None:
    contract = _normalized_contract()
    memory = MEMORY.read_text(encoding="utf-8")
    memory_repository = MEMORY_REPOSITORY.read_text(encoding="utf-8")

    assert "def _immutable_record_canonical_bytes(" in memory
    assert 'exclude={"created_at"}' in memory
    assert "def _same_immutable_record(" in memory
    assert "_same_immutable_record" in memory_repository

    for phrase in (
        "memory_records_replay_equivalent",
        "single canonical replay-equivalence authority",
        "ignore only top-level created_at",
        "preserve canonical source-ref datetime normalization",
        "private duplicate comparison authority must not remain",
        "both in-memory and durable Memory replay handling migrate to this helper",
        "atomic admission must consume this helper",
    ):
        assert phrase in contract


def test_ctx_f5_3h_b0_freezes_error_family_and_exact_mapping() -> None:
    contract = _normalized_contract()
    promotion = PROMOTION.read_text(encoding="utf-8")

    assert "class PromotionPrimitiveIntegrityError(" in promotion
    assert "class PromotionReservationIntegrityError(" in promotion

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
        assert name in contract

    for phrase in (
        "PromotionPrimitiveIntegrityError family propagates unchanged",
        "supplied content not valid canonical Memory JSON -> PromotionAdmissionIntentConflictError",
        "missing durable reservation authority -> PromotionAdmissionReservationNotIssuedError",
        "durable reservation REVOKED -> PromotionAdmissionReservationRevokedError",
        "MemoryRecordConflictError on first-admission persistence/convergence -> PromotionAdmissionMemoryReplayConflictError",
        "CONSUMED + missing Memory -> PromotionAdmissionConsumedMemoryMissingError",
        "CONSUMED + reconstructed Memory present but not canonical replay-equivalent -> PromotionAdmissionConsumedMemoryMismatchError",
        "MemoryAdmissionTransactionError or failure acquiring the SQLite authoritative BEGIN IMMEDIATE boundary -> PromotionAdmissionTransactionUnavailableError",
        "or commit failure -> PromotionAdmissionPersistenceFailureError",
        "asyncio.CancelledError propagates unchanged",
        "must not use a broad BaseException translation",
    ):
        assert phrase in contract


def test_ctx_f5_3h_b0_freezes_pure_preflight_before_write_intent() -> None:
    contract = _normalized_contract()
    memory = MEMORY.read_text(encoding="utf-8")
    memory_repository = MEMORY_REPOSITORY.read_text(encoding="utf-8")
    promotion = PROMOTION.read_text(encoding="utf-8")

    assert "def memory_content_digest(" in memory
    assert "def validate_promotion_reservation_integrity(" in promotion
    assert "async def sqlite_memory_admission_transaction(" in memory_repository
    assert "await _begin_sqlite_write_intent(session)" in memory_repository
    assert 'dialect.name != "sqlite"' in memory_repository
    assert "if session.in_transaction():" in memory_repository

    for phrase in (
        "validate_promotion_reservation_integrity(reservation)",
        "compute payload digest using existing memory_content_digest(content)",
        "No repository read is allowed in preflight",
        "immediately enter sqlite_memory_admission_transaction(session)",
        "BEFORE any reservation or Memory durable read",
        "No second digest implementation is allowed",
    ):
        assert phrase in contract


def test_ctx_f5_3h_b0_freezes_service_session_and_authority_ownership() -> None:
    contract = _normalized_contract()
    reservation_repository = RESERVATION_REPOSITORY.read_text(encoding="utf-8")
    issuer = ISSUER.read_text(encoding="utf-8")

    assert "async def get(" in reservation_repository
    assert "async def mark_consumed(" in reservation_repository
    assert "SessionContextFactory" in issuer

    for phrase in (
        "se/src/infrastructure/storage/services/memory_promotion_admission.py",
        "class DurableMemoryPromotionAdmission",
        "MemoryAdmissionSessionContextFactory",
        "exactly one fresh session context",
        "transaction context is the sole commit/rollback owner",
        "Do not reuse/import the issuer's SessionContextFactory",
        "no second session or UoW",
        "no SqlAlchemyUnitOfWork or DatabaseDriver modification",
        "non-SQLite admission fails closed",
    ):
        assert phrase in contract


def test_ctx_f5_3h_b0_freezes_expected_memory_and_state_machine() -> None:
    contract = _normalized_contract()
    memory = MEMORY.read_text(encoding="utf-8")
    memory_repository = MEMORY_REPOSITORY.read_text(encoding="utf-8")

    assert "def create_memory_record(" in memory
    assert "async def get_by_promotion_authority(" in memory_repository
    assert "async def put(" in memory_repository

    for phrase in (
        "source_ref = durable intent.source_ref_snapshot",
        "promotion_authority_id = durable reservation authority id",
        "metadata = durable intent.metadata",
        "memory_schema_version = durable intent.memory_schema_version",
        "pre-existing Memory by exact authority -> PromotionAdmissionMemoryReplayConflictError",
        "ZERO put",
        "ZERO consume",
        "ZERO mutation",
        "ISSUED + existing Memory must never be healed",
        "No lookup by intent, digest, proof tuple, or alternate authority",
        "Standalone G-A verifier remains outside this authoritative replay path",
    ):
        assert phrase in contract


def test_ctx_f5_3h_b0_freezes_exit_evidence_and_closed_authority() -> None:
    contract = _normalized_contract()

    for phrase in (
        "H-B2 production cannot merge without H-B3-equivalent exit evidence",
        "22. durable-row corruption cannot be misreported as reservation-not-issued or replay success",
        "H-B1 production CLAIM remains CLOSED",
        "H-B2 production CLAIM remains CLOSED",
        "runtime/API/source/non-SQLite authority = CLOSED",
        "No se/src/** file changes are permitted in H-B0",
        "production/runtime/schema/migration delta = ZERO",
        "baseline = 73714a4405f9ed10f16117b763215467d0a3e607",
        "canonical migration head = 25a_ubq1_user_budget_foundation",
    ):
        assert phrase in contract


def test_ctx_f5_3h_b0_does_not_create_production_surface() -> None:
    contract = _normalized_contract()
    assert "Production/runtime/schema/migration delta = ZERO" in contract
    assert "It grants no production implementation authority" in contract
