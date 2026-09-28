from __future__ import annotations

import ast
import inspect
from pathlib import Path

from se.src.context.memory_promotion import PromotionReservationIssuer
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)


SERVICE = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_issuer.py"
)
CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3G_B_TRUSTED_PROMOTION_RESERVATION_ISSUER.md"
)


def _normalized_contract() -> str:
    text = " ".join(CONTRACT.read_text(encoding="utf-8").split())
    return text.replace("`", "").replace("*", "")


def test_ctx_f5_3g_b_issuer_is_protocol_compatible_and_keyword_only():
    assert PromotionReservationIssuer in DurablePromotionReservationIssuer.__mro__

    signature = inspect.signature(DurablePromotionReservationIssuer.reserve)
    assert tuple(signature.parameters) == ("self", "intent")
    assert signature.parameters["intent"].kind is inspect.Parameter.KEYWORD_ONLY


def test_ctx_f5_3g_b_owns_one_session_one_repository_and_one_commit_path():
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "validate_memory_promotion_intent_integrity(intent)" in source
    assert "async with self._session_factory() as session:" in source
    assert source.count("DurablePromotionReservationRepository(session)") == 1
    assert source.count("insert_or_converge_issued_candidate(") == 1
    assert source.count("await session.commit()") == 1

    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "commit" in calls
    assert "rollback" not in calls
    assert "mark_consumed" not in calls
    assert "mark_revoked" not in calls


def test_ctx_f5_3g_b_terminal_winner_fence_and_canonical_return_identity():
    source = SERVICE.read_text(encoding="utf-8")

    assert "winner.state is DurablePromotionReservationState.CONSUMED" in source
    assert "PromotionReservationAlreadyConsumedError" in source
    assert "winner.state is DurablePromotionReservationState.REVOKED" in source
    assert "PromotionReservationRevokedError" in source
    assert "winner.state is not DurablePromotionReservationState.ISSUED" in source

    assert "promotion_authority_id=winner.promotion_authority_id" in source
    assert "intent=winner.intent" in source
    assert "promotion_authority_id=candidate_authority_id" in source
    assert "promotion_authority_id=candidate_authority_id,\n                intent=intent" in source


def test_ctx_f5_3g_b_has_no_hidden_uow_memory_runtime_or_retry_authority():
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    forbidden_imports = {
        "async_sessionmaker",
        "create_async_engine",
        "SqlAlchemyUnitOfWork",
        "DatabaseDriver",
        "DurableMemoryRecordRepository",
        "DurablePromotionReservationVerifier",
        "SourcePromotionAuthorityPort",
    }
    assert imported.isdisjoint(forbidden_imports)

    forbidden_source = (
        ".rollback(",
        "mark_consumed",
        "mark_revoked",
        "DurableMemoryRecordRepository",
        "SqlAlchemyUnitOfWork",
        "DatabaseDriver",
        "create_async_engine",
        "async_sessionmaker",
        "SourcePromotionAuthorityPort",
        "asyncio.sleep",
        "while True",
    )
    for fragment in forbidden_source:
        assert fragment not in source


def test_ctx_f5_3g_b_contract_freezes_zero_migration_and_closed_boundaries():
    text = _normalized_contract()

    required = (
        "Production scope is exactly",
        "Schema delta: ZERO",
        "Migration delta: ZERO",
        "Concrete supported production source kinds remain NONE",
        "ISSUED is the only successful issuance state",
        "PromotionReservationAlreadyConsumedError",
        "PromotionReservationRevokedError",
        "commit exactly once",
        "winner.promotion_authority_id",
        "No second idempotency key, cache, distributed lock, or bespoke persistence retry loop",
        "asyncio.CancelledError propagates",
        "Atomic reservation-to-Memory admission remains CLOSED",
    )
    for phrase in required:
        assert phrase in text
