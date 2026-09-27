import ast
from pathlib import Path


MODEL = Path("se/src/infrastructure/storage/models/sql/promotion_reservation.py")
REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/promotion_reservation.py"
)
MIGRATION = Path(
    "se/src/infrastructure/storage/migrations/sql/versions/"
    "23a_ctx_f5_promotion_reservation.py"
)
CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3F_B_DURABLE_PROMOTION_RESERVATION_PERSISTENCE_8EF71058.md"
)


def test_ctx_f5_3f_b_owns_only_persistence_boundary():
    repository = REPOSITORY.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    contract = CONTRACT.read_text(encoding="utf-8")

    assert "class PromotionReservationRow" in model
    assert "class DurablePromotionReservationRepository" in repository
    assert 'revision: str = "23a_ctx_f5_promotion_reservation"' in migration
    assert (
        'down_revision: Union[str, None] = "22a_r12_execution_lease_fence"'
        in migration
    )
    assert "production merge authority = NONE" in contract
    assert "Trusted PromotionReservationIssuer/PromotionReservationVerifier" in contract
    assert "atomic reservation-to-Memory admission" in contract


def test_ctx_f5_3f_b_repository_keeps_transaction_ownership_with_caller():
    source = REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "async_sessionmaker" not in imported_names
    assert ".commit(" not in source
    assert "PromotionReservationIssuer" not in source
    assert "PromotionReservationVerifier" not in source
    assert "DurableMemoryRecordRepository" not in source


def test_ctx_f5_3f_b_reuses_existing_canonical_memory_boundary():
    source = REPOSITORY.read_text(encoding="utf-8")

    assert "canonical_memory_bytes" in source
    assert 'intent.model_dump(mode="json")' in source
    assert "json.dumps(" not in source
    assert "intent_canonical_bytes" in source
    assert "intent_digest" in source


def test_ctx_f5_3f_b_contract_is_future_stage_compatible():
    text = " ".join(CONTRACT.read_text(encoding="utf-8").split())

    required = (
        "A later independently released stage may add issuer/verifier/orchestrator",
        "This stage tests only the persistence boundary it owns",
        "revocation policy/runtime",
        "ContextBuilder",
    )
    for phrase in required:
        assert phrase in text
