import ast
from pathlib import Path


MODULE = Path("se/src/context/memory_promotion.py")
CONTRACT = Path(
    "docs/context_future/CTX_F5_3C_PROMOTION_PRIMITIVES_9E746D9A.md"
)


def _source() -> str:
    return MODULE.read_text(encoding="utf-8")


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _normalized_contract() -> str:
    return " ".join(_contract().split())


def _top_level_names() -> set[str]:
    tree = ast.parse(_source())
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _class_methods(name: str) -> set[str]:
    tree = ast.parse(_source())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"class {name} not found")


def test_ctx_f5_3c_releases_exact_dormant_primitive_surface():
    names = _top_level_names()

    required = {
        "MemoryPromotionProofScope",
        "SourcePromotionProof",
        "MemoryPromotionIntent",
        "PromotionReservation",
        "SourcePromotionAuthorityPort",
        "PromotionReservationIssuer",
        "PromotionReservationVerifier",
        "PromotionPrimitiveIntegrityError",
        "SourcePromotionProofIntegrityError",
        "MemoryPromotionIntentIntegrityError",
        "PromotionReservationIntegrityError",
        "PromotionReservationIntentMismatchError",
        "validate_source_promotion_proof_integrity",
        "validate_memory_promotion_intent_integrity",
        "validate_promotion_reservation_integrity",
        "validate_reservation_matches_intent",
    }
    assert required <= names

    assert _class_methods("SourcePromotionAuthorityPort") == {
        "reprove_for_memory_promotion"
    }
    assert _class_methods("PromotionReservationIssuer") == {"reserve"}
    assert _class_methods("PromotionReservationVerifier") == {"verify"}


def test_ctx_f5_3c_keeps_structural_integrity_separate_from_authorization():
    source = _source()
    text = _normalized_contract()

    required = (
        "structural/self-consistency integrity != trusted server authorization",
        "untrusted data envelope until a trusted verifier succeeds",
        "structural validation does not authorize promotion",
        "trusted server state confirms issuance + exact intent match",
        "issuer/verifier implementation = CLOSED",
        "promotion orchestrator/service = CLOSED",
    )
    for phrase in required:
        assert phrase in text

    assert "def authorize" not in source
    assert "def is_authorized" not in source
    assert "authorization = True" not in source


def test_ctx_f5_3c_reuses_existing_memory_and_source_canonicality():
    source = _source()

    assert (
        "from se.src.context.memory import canonical_memory_bytes"
        in source
    )
    assert (
        "validate_context_source_ref_integrity"
        in source
    )
    assert "json.dumps(" not in source
    assert "hashlib." not in source


def test_ctx_f5_3c_has_no_runtime_persistence_or_cross_track_wiring():
    source = _source()
    tree = ast.parse(source)

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden_import_fragments = (
        "infrastructure.storage",
        "provider",
        "application.assets",
        "agent_execution",
        "object_storage",
    )
    assert not any(
        fragment in module
        for module in imported_modules
        for fragment in forbidden_import_fragments
    )

    forbidden_runtime_tokens = (
        "create_memory_record",
        "MemoryRecordRepository",
        "DurableMemoryRecordRepository",
        "FileProviderBinding",
        "ObjectStorage",
        "ContextBuilder",
    )
    for token in forbidden_runtime_tokens:
        assert token not in source


def test_ctx_f5_3c_contract_keeps_all_concrete_sources_and_visibility_closed():
    text = _normalized_contract()

    required = (
        "concrete supported source kinds = NONE",
        "ASSET = UNSUPPORTED",
        "AGENT_TRANSCRIPT / checkpoint = UNSUPPORTED",
        "SESSION / TASK / BRANCH / TOOL_RESPONSE_PAYLOAD = UNSUPPORTED",
        "source-specific adapters = CLOSED",
        "HTTP/public API = CLOSED",
        "retrieval/ContextBuilder/model visibility = CLOSED",
        "CAS/R11 authority transfer = NONE",
        "create_memory_record()/MemoryRecordRepository.put() handoff = CLOSED",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3c_contract_records_production_merge_gate_and_supersedable_dormancy():
    text = _normalized_contract()

    required = (
        "production-code slice",
        "independent FINAL GREEN",
        "explicit production merge authorization",
        "future Integration Wave",
        "The dormant boundary is stage-local and may be superseded by a later independently released stage",
    )
    for phrase in required:
        assert phrase in text
