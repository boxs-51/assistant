from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B2_TRUSTED_TOOL_RESPONSE_PROMOTION_MATERIAL.md"
)
B1_SERVICE = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_source_authority.py"
)
MEMORY_PROMOTION = Path("se/src/context/memory_promotion.py")
MEMORY_ADMISSION = Path(
    "se/src/infrastructure/storage/services/memory_promotion_admission.py"
)
TOOL_PAYLOAD = Path("se/src/context/tool_response_payload.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic_contract() -> str:
    return " ".join(_read(CONTRACT).replace(chr(96), "").split())


def test_b2_is_exact_two_file_zero_production_contract_slice() -> None:
    contract = _semantic_contract()

    for phrase in (
        "stage = CTX-F5-3I-B2",
        "class = CONTRACT / ARCHITECTURE EVIDENCE ONLY",
        "exact development baseline = "
        "1ceb34f561d73e26775910cd3468c962c17d61cd",
        "baseline Architecture #1822 / 36994059445 = GREEN/GREEN",
        "parent CTX-F5-3I-B1 = LANDED / CANONICAL / HEALTHY",
        "production PRE-CLAIM = HOLD pending independent B2 audit",
        "production CLAIM = NONE",
        "runtime/container/API wiring = CLOSED",
        "reservation/Memory admission = CLOSED",
        "retrieval/ContextBuilder/model visibility = CLOSED",
        "production/runtime/schema/migration delta = ZERO",
        "CTX-F5-3I-B2 contract candidate is exactly two files",
        "No se/src/** production file changes",
    ):
        assert phrase in contract


def test_current_b1_returns_only_source_proof_while_computing_content_digest() -> None:
    source = _read(B1_SERVICE)

    for phrase in (
        "async def reprove_for_memory_promotion(",
        ") -> SourcePromotionProof:",
        "canonical_content = canonical_payload_bytes(result.output)",
        "content_digest = hashlib.sha256(canonical_content).hexdigest()",
        "content_digest=content_digest",
        '"content_digest": content_digest',
        "return SourcePromotionProof(",
    ):
        assert phrase in source

    assert "TrustedToolResponsePromotionMaterial" not in source
    assert "content_snapshot=" not in source


def test_downstream_contracts_need_digest_and_explicit_content() -> None:
    promotion = _read(MEMORY_PROMOTION)
    admission = _read(MEMORY_ADMISSION)

    assert "class MemoryPromotionIntent(BaseModel):" in promotion
    assert "content_digest: str" in promotion
    assert "async def admit(" in admission
    assert "reservation: PromotionReservation" in admission
    assert "content: Any" in admission
    assert "canonical_payload_bytes = canonical_memory_bytes(content)" in admission


def test_process_local_tool_payload_repository_is_not_durable_authority() -> None:
    payload = _read(TOOL_PAYLOAD)

    assert "class InMemoryToolResponsePayloadRepository:" in payload
    assert "Process-local reference repository; not durable CTX storage authority." in payload
    assert "async def get(self, payload_id: str)" in payload
    assert "async def get_by_source_result(" in payload


def test_same_read_proof_content_digest_binding_is_frozen() -> None:
    contract = _semantic_contract()

    for phrase in (
        "same durable result and the same source-authority read snapshot",
        "Authoritative content is exactly: AgentToolResultRecord.output",
        "detach a canonical JSON snapshot from those canonical bytes",
        "B2.content_digest == sha256(canonical_payload_bytes(B2.content_snapshot))",
        "== digest used to reconstruct B1 payload_id",
        "== content_digest bound into B1 authority_state_token material",
        "caller-supplied content value",
        "caller-supplied digest",
        "later independent re-read is not a substitute",
    ):
        assert phrase in contract


def test_b2_requires_one_authority_algorithm_and_preserves_b1_surface() -> None:
    contract = _semantic_contract()

    for phrase in (
        "B2 must not fork the landed B1 proof algorithm",
        "one internal trusted durable material builder/read path",
        "B1 reprove_for_memory_promotion(...) returns material.source_proof only",
        "B2 trusted-material reader returns the complete material",
        "existing B1 public protocol signature and behavior must remain compatible",
        "no independently drifting duplicate source-authority algorithm",
    ):
        assert phrase in contract


def test_b2_keeps_runtime_reservation_admission_and_retrieval_closed() -> None:
    contract = _semantic_contract()

    for phrase in (
        "B2 does not construct or persist a MemoryPromotionIntent",
        "call PromotionReservationIssuer.reserve(...)",
        "call DurableMemoryPromotionAdmission.admit(...)",
        "expose HTTP/public/model-callable promotion",
        "register runtime/container wiring",
        "enable automatic promotion",
        "create ContextSourceKind.MEMORY",
        "open Memory retrieval/search/ranking/vector/index/chunk/embedding",
        "inject Memory into ContextBuilder or model Working Set",
        "CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE",
    ):
        assert phrase in contract


def test_b2_retention_and_whole_payload_fences_remain_external() -> None:
    contract = _semantic_contract()

    for phrase in (
        "whole canonical JSON value from exact AgentToolResultRecord.output",
        "subset/field selection",
        "summarization or transformation",
        "Embedded asset/provider-looking values remain opaque JSON",
        "Agent/R11/R12 remain source-liveness and retention owners",
        "B2 creates no Agent live root",
        "does not pin source history",
        "B2 itself issues no reservation",
    ):
        assert phrase in contract
