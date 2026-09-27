from __future__ import annotations

import ast
import inspect
from pathlib import Path

from se.src.context.memory_promotion import PromotionReservationVerifier
from se.src.infrastructure.storage.services.promotion_reservation_verifier import (
    DurablePromotionReservationVerifier,
)


SERVICE = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_verifier.py"
)
CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3G_A_TRUSTED_PROMOTION_RESERVATION_VERIFIER.md"
)


def test_ctx_f5_3g_a_verifier_is_protocol_compatible_and_exact_id_only():
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert issubclass(DurablePromotionReservationVerifier, PromotionReservationVerifier)
    signature = inspect.signature(DurablePromotionReservationVerifier.verify)
    assert tuple(signature.parameters) == ("self", "reservation", "intent")
    assert signature.parameters["reservation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["intent"].kind is inspect.Parameter.KEYWORD_ONLY

    assert "validate_reservation_matches_intent(reservation, intent)" in source
    assert "repository.get(" not in source
    assert "self._repository.get(" in source
    assert "get_by_intent" not in source
    assert "get_by_proof_authority" not in source
    assert "_get_by_digest" not in source

    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "get" in calls
    assert "mark_consumed" not in calls
    assert "mark_revoked" not in calls
    assert "insert_or_converge_issued_candidate" not in calls


def test_ctx_f5_3g_a_reuses_canonical_memory_bytes_without_new_serializer():
    source = SERVICE.read_text(encoding="utf-8")

    assert "canonical_memory_bytes" in source
    assert 'intent.model_dump(mode="json")' in source
    assert "json.dumps(" not in source
    assert "hashlib" not in source


def test_ctx_f5_3g_a_owns_no_session_transaction_memory_or_runtime_wiring():
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    forbidden_imports = {
        "AsyncSession",
        "async_sessionmaker",
        "create_async_engine",
        "DurableMemoryRecordRepository",
        "PromotionReservationIssuer",
        "SourcePromotionAuthorityPort",
    }
    assert imported.isdisjoint(forbidden_imports)

    forbidden_source = (
        ".commit(",
        ".rollback(",
        "mark_consumed",
        "mark_revoked",
        "insert_or_converge_issued_candidate",
        "DurableMemoryRecordRepository",
        "SourcePromotionAuthorityPort",
    )
    for fragment in forbidden_source:
        assert fragment not in source


def test_ctx_f5_3g_a_contract_freezes_non_consuming_zero_migration_scope():
    text = " ".join(CONTRACT.read_text(encoding="utf-8").split())

    required = (
        "Production scope is exactly",
        "The caller PromotionReservation remains an untrusted envelope",
        "only durable identity",
        "never commits or rolls back",
        "Schema delta: ZERO",
        "Migration delta: ZERO",
        "Concrete supported production source kinds remain NONE",
        "PromotionReservationIssuer",
        "atomic reservation-to-Memory admission",
    )
    for phrase in required:
        assert phrase in text
