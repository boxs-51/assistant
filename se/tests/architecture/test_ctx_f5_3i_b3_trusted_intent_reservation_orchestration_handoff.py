from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B3_TRUSTED_INTENT_RESERVATION_ORCHESTRATION_HANDOFF.md"
)
B2_SERVICE = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_source_authority.py"
)
PROMOTION = Path("se/src/context/memory_promotion.py")
MEMORY = Path("se/src/context/memory.py")
ISSUER = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_issuer.py"
)
RESERVATION_REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/promotion_reservation.py"
)
ADMISSION = Path(
    "se/src/infrastructure/storage/services/memory_promotion_admission.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic_contract() -> str:
    return " ".join(_read(CONTRACT).replace(chr(96), "").split())


def _class_node(source: str, name: str) -> ast.ClassDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function_node(
    class_node: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_b3_is_exact_two_file_zero_production_contract_slice() -> None:
    contract = _semantic_contract()

    for phrase in (
        "stage = CTX-F5-3I-B3",
        "class = CONTRACT / ARCHITECTURE EVIDENCE ONLY",
        "exact development baseline = "
        "e138db0db9dc7aa165f693af2c9cb2f8a70ef7ec",
        "baseline Architecture #1879 / 37015631037 = GREEN/GREEN",
        "parent CTX-F5-3I-B2 = LANDED / CANONICAL / HEALTHY",
        "production PRE-CLAIM = HOLD pending independent B3 audit",
        "production CLAIM = NONE",
        "runtime/container/API wiring = CLOSED",
        "Memory admission invocation = CLOSED",
        "production/runtime delta = ZERO",
        "CTX-F5-3I-B3 contract candidate is exactly two files",
        "No se/src/** production file changes",
    ):
        assert phrase in contract


def test_landed_b2_exposes_trusted_material_proof_snapshot_and_digest() -> None:
    source = _read(B2_SERVICE)

    for phrase in (
        "class TrustedToolResponsePromotionMaterial:",
        "source_proof: SourcePromotionProof",
        "def content_snapshot(self) -> Any:",
        'return self._payload.model_dump(mode="json")["content"]',
        "def content_digest(self) -> str:",
        "return self._payload.content_digest",
        "async def read_trusted_promotion_material(",
    ):
        assert phrase in source


def test_first_intent_policy_uses_existing_canonical_primitives() -> None:
    promotion = _read(PROMOTION)
    memory = _read(MEMORY)
    contract = _semantic_contract()

    for phrase in (
        "class MemoryPromotionIntent(BaseModel):",
        "owner_user_id: str",
        "source_ref_snapshot: ContextSourceRef",
        "source_proof: SourcePromotionProof",
        "content_digest: str",
        "metadata: dict[str, Any]",
        "memory_schema_version: int",
    ):
        assert phrase in promotion

    assert "MEMORY_SCHEMA_VERSION = 1" in memory

    for phrase in (
        "metadata = {}",
        "memory_schema_version = canonical MEMORY_SCHEMA_VERSION",
        "Caller-supplied owner, source snapshot, proof, digest, metadata, or schema version",
    ):
        assert phrase in contract


def test_reservation_issuer_remains_exact_intent_only_authority() -> None:
    source = _read(ISSUER)
    cls = _class_node(source, "DurablePromotionReservationIssuer")
    reserve = _function_node(cls, "reserve")

    assert isinstance(reserve, ast.AsyncFunctionDef)
    assert [arg.arg for arg in reserve.args.kwonlyargs] == ["intent"]

    call_names = []
    for node in ast.walk(reserve):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            call_names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            call_names.append(node.func.attr)

    assert "validate_memory_promotion_intent_integrity" in call_names
    assert "insert_or_converge_issued_candidate" in call_names
    assert "commit" in call_names
    assert "PromotionReservation" in call_names


def test_durable_reservation_persists_intent_authority_but_not_source_content() -> None:
    source = _read(RESERVATION_REPOSITORY)
    contract = _semantic_contract()

    for phrase in (
        "class DurablePromotionReservationRecord:",
        "promotion_authority_id: str",
        "intent: MemoryPromotionIntent",
        "intent_digest: str",
        "source_context_source_id: str",
        "proof_receipt_id: str",
        "authority_state_token: str",
        "proof_scope: MemoryPromotionProofScope",
        "state: DurablePromotionReservationState",
        '"intent_json": payload',
        '"intent_canonical_bytes": canonical_intent',
    ):
        assert phrase in source

    record = _class_node(source, "DurablePromotionReservationRecord")
    field_names = {
        node.target.id
        for node in record.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert "content" not in field_names
    assert "content_snapshot" not in field_names

    for phrase in (
        "It does not durably store the B2 content_snapshot",
        "a durable reservation alone is insufficient to reconstruct exact Memory content after process loss",
    ):
        assert phrase in contract


def test_admission_requires_explicit_content_and_checks_durable_intent_digest() -> None:
    source = _read(ADMISSION)
    cls = _class_node(source, "DurableMemoryPromotionAdmission")
    admit = _function_node(cls, "admit")

    assert isinstance(admit, ast.AsyncFunctionDef)
    assert [arg.arg for arg in admit.args.kwonlyargs] == [
        "reservation",
        "content",
    ]

    for phrase in (
        "canonical_payload_bytes = canonical_memory_bytes(content)",
        "payload_digest = memory_content_digest(content_snapshot)",
        "if payload_digest != durable.intent.content_digest:",
        "promotion content digest does not match durable intent",
    ):
        assert phrase in source


def test_reservation_to_admission_recovery_fence_stays_open() -> None:
    contract = _semantic_contract()

    for phrase in (
        "FUTURE-FENCE-CTX-B3-RESERVATION-TO-ADMISSION-CONTENT-RECOVERY-1",
        "fence status = OPEN / MUST BE DECIDED BEFORE RUNTIME OR ADMISSION ORCHESTRATION",
        "B3 production PRE-CLAIM = HOLD pending independent decision",
        "Memory admission invocation = CLOSED",
        "public/runtime orchestration = CLOSED",
        "No contract wording may silently treat the reservation as if it stores content",
    ):
        assert phrase in contract


def test_b3_keeps_external_authorities_closed() -> None:
    contract = _semantic_contract()

    for phrase in (
        "change B2 source authority",
        "change MemoryPromotionIntent primitive semantics",
        "change DurablePromotionReservationIssuer",
        "invoke DurableMemoryPromotionAdmission.admit(...)",
        "persist content in reservation rows",
        "expose HTTP/public/model-callable promotion",
        "add runtime/container wiring",
        "inject Memory into ContextBuilder/model Working Set",
        "acquire Agent/R11/R12 retention/GC authority",
        "acquire CAS lifecycle/dereference authority",
        "acquire UBQ quota authority",
    ):
        assert phrase in contract
