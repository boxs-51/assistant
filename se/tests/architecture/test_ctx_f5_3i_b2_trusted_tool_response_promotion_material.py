from __future__ import annotations

import ast
import inspect
from pathlib import Path

from se.src.context.memory_promotion import SourcePromotionAuthorityPort
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
    TrustedToolResponsePromotionMaterial,
)


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B2_TRUSTED_TOOL_RESPONSE_PROMOTION_MATERIAL.md"
)
SERVICE = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_source_authority.py"
)
UNIT = Path(
    "se/tests/unit/"
    "test_ctx_f5_3i_b2_trusted_tool_response_promotion_material.py"
)
INTEGRATION = Path(
    "se/tests/integration/"
    "test_ctx_f5_3i_b2_trusted_tool_response_promotion_material.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic_contract() -> str:
    return " ".join(_read(CONTRACT).replace(chr(96), "").split())


def test_b2_canonical_contract_keeps_downstream_authority_closed() -> None:
    contract = _semantic_contract()

    for phrase in (
        "stage = CTX-F5-3I-B2",
        "Authoritative content is exactly: AgentToolResultRecord.output",
        "same durable result and the same source-authority read snapshot",
        "B2 must not fork the landed B1 proof algorithm",
        "B2 creates no Agent live root",
        "B2 itself issues no reservation",
        "B2 does not construct or persist a MemoryPromotionIntent",
        "runtime/container/API wiring = CLOSED",
        "reservation/Memory admission = CLOSED",
        "retrieval/ContextBuilder/model visibility = CLOSED",
        "CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE",
    ):
        assert phrase in contract


def test_b2_service_surface_preserves_legacy_b1_protocol() -> None:
    source = _read(SERVICE)

    assert SourcePromotionAuthorityPort in DurableToolResponsePayloadSourceAuthority.__mro__
    assert inspect.isclass(TrustedToolResponsePromotionMaterial)

    legacy_signature = inspect.signature(
        DurableToolResponsePayloadSourceAuthority.reprove_for_memory_promotion
    )
    assert tuple(legacy_signature.parameters) == (
        "self",
        "source_ref",
        "owner_user_id",
    )
    assert (
        legacy_signature.parameters["source_ref"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert (
        legacy_signature.parameters["owner_user_id"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )

    for phrase in (
        "class TrustedToolResponsePromotionMaterial:",
        "async def read_trusted_promotion_material(",
        ") -> TrustedToolResponsePromotionMaterial:",
        "async def reprove_for_memory_promotion(",
        ") -> SourcePromotionProof:",
        "material = await self.read_trusted_promotion_material(",
        "return material.source_proof",
    ):
        assert phrase in source


def test_b2_has_one_durable_read_and_one_material_builder() -> None:
    source = _read(SERVICE)

    assert source.count("async with self._session_factory() as session:") == 1
    assert source.count("await session.get(") == 4
    assert source.count("await session.execute(") == 1
    assert source.count("def _build_material(") == 1
    assert "def _build_proof(" not in source

    for forbidden in (
        "await session.commit(",
        "await session.flush(",
        "await session.rollback(",
        "session.add(",
        "session.delete(",
        ".with_for_update(",
        "InMemoryToolResponsePayloadRepository",
    ):
        assert forbidden not in source


def test_b2_reuses_tool_response_payload_canonicalization_and_deep_freeze() -> None:
    source = _read(SERVICE)

    for phrase in (
        "payload = create_tool_response_payload(",
        "content=result.output",
        "source_commit_state=COMMITTED_RESULT_STATE",
        "owner_user_id=session_owner",
        "session_id=execution_session_id",
        "content_digest = payload.content_digest",
        "payload_id = payload.payload_id",
        '"content_digest": content_digest',
        '"payload_id": payload_id',
        "content_snapshot=payload.content",
        "content_digest=payload.content_digest",
    ):
        assert phrase in source

    assert "canonical_content = canonical_payload_bytes(result.output)" not in source
    assert "content_digest = hashlib.sha256(canonical_content).hexdigest()" not in source


def test_b2_exact_eligibility_lineage_and_error_boundary_remain_b1_compatible() -> None:
    source = _read(SERVICE)

    for phrase in (
        "result.commit_state != COMMITTED_RESULT_STATE",
        "result.success is not True",
        "result.error_code is not None",
        "result.error_message is not None",
        "result.retryable is not False",
        "canonical Session/execution/source_ref session mismatch",
        "canonical Session owner mismatch",
        "durable capability invocation lineage mismatch",
        "durable agent tool-call lineage mismatch",
        "except SQLAlchemyError as exc:",
        "ToolResponsePayloadSourceUnavailableError",
    ):
        assert phrase in source

    assert "except asyncio.CancelledError" not in source


def test_b2_claim_evidence_is_dedicated_and_no_forbidden_runtime_imports_exist() -> None:
    assert UNIT.exists()
    assert INTEGRATION.exists()

    source = _read(SERVICE)
    tree = ast.parse(source)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    forbidden_modules = {
        "se.src.infrastructure.storage.services.memory_promotion_admission",
        "se.src.infrastructure.storage.services.promotion_reservation_issuer",
        "se.src.infrastructure.storage.services.promotion_reservation_verifier",
        "se.src.runtimes.agent.runtime",
        "se.src.context.context_builder",
    }
    assert modules.isdisjoint(forbidden_modules)


def test_b2_focused_evidence_covers_detach_gc_and_b1_compatibility() -> None:
    unit = _read(UNIT)
    integration = _read(INTEGRATION)

    for phrase in (
        "material.source_proof == legacy_proof",
        "MappingProxyType",
        "changed-after-read",
        "ToolResponsePayloadSourceUnavailableError",
        "asyncio.CancelledError",
    ):
        assert phrase in unit

    for phrase in (
        "material.source_proof == legacy_proof",
        "assert await _counts(sessions) == before_counts",
        "assert await _snapshot(sessions) == before",
        "await _delete_all_source_rows(sessions)",
        "ToolResponsePayloadSourceRejectedError",
    ):
        assert phrase in integration
