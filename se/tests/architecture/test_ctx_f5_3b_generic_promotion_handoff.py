import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3B_GENERIC_PROMOTION_HANDOFF_F7C02B40.md"
)
MEMORY = Path("se/src/context/memory.py")


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _normalized_contract() -> str:
    return " ".join(_contract().split())


def _memory_source() -> str:
    return MEMORY.read_text(encoding="utf-8")


def _top_level_source(name: str) -> str:
    source = _memory_source()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                segment = ast.get_source_segment(source, node)
                assert segment is not None
                return segment
    raise AssertionError(f"{name} not found in se/src/context/memory.py")


def _model_dump_exclude_fields(function_source: str) -> set[str]:
    tree = ast.parse(function_source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_dump"
    ]
    assert len(calls) == 1
    exclude = next(
        (keyword.value for keyword in calls[0].keywords if keyword.arg == "exclude"),
        None,
    )
    assert isinstance(exclude, ast.Set)
    values = {
        element.value
        for element in exclude.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }
    assert len(values) == len(exclude.elts)
    return values


def test_ctx_f5_3b_is_contract_evidence_only_and_keeps_runtime_closed():
    text = _normalized_contract()

    required = (
        "mode = CONTRACT / AUTHORITY FREEZE ONLY",
        "production delta = ZERO",
        "concrete supported source kinds = NONE",
        "production promotion runtime = NOT RELEASED",
        "source-specific adapters = NOT RELEASED",
        "HTTP/public promotion API = CLOSED",
        "automatic promotion = CLOSED",
        "It changes no production service, protocol implementation, endpoint, adapter, repository, schema, migration",
        "Any production implementation requires a fresh explicit release and separate production merge authorization",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3b_freezes_provider_neutral_source_proof_and_freshness():
    text = _normalized_contract()

    required = (
        "source_kind",
        "context_source_id",
        "authority_id",
        "authority_version / source revision where applicable",
        "owner_user_id",
        "current read/liveness authorization scope",
        "proof_receipt_id",
        "source authority revision/state token",
        "ordering evidence sufficient for stale-proof rejection",
        "A timestamp alone is NOT sufficient freshness authority",
        "deterministic stale/foreign-proof rejection",
        "that source kind remains unsupported and promotion fails closed",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3b_requires_exact_intent_promotion_reservation():
    text = _normalized_contract()

    required = (
        "A server-owned promotion_authority_id is necessary but not sufficient authorization",
        "promotion_authority_id",
        "trusted owner_user_id",
        "exact validated ContextSourceRef snapshot or canonical snapshot digest",
        "exact context_source_id",
        "source proof_receipt_id",
        "source authority revision/state token used for the admission decision",
        "content_digest",
        "canonical metadata digest or exact canonical metadata",
        "memory_schema_version",
        "promotion reservation intent mismatch",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3b_separates_source_proof_reservation_and_memory_admission():
    text = _normalized_contract()

    required = (
        "source proof / receipt != promotion reservation / authorization envelope != durable Memory persistence",
        "source authority port requested validated source claim + trusted owner -> current source proof / receipt",
        "promotion authority issuer / reserver exact validated promotion intent -> server-owned promotion reservation",
        "promotion handoff validator / orchestrator verifies source claim + proof + reservation + canonical payload -> create_memory_record(...) -> exactly one existing MemoryRecordRepository.put(...)",
        "create_memory_record() is not promotion authorization",
        "No alternate Memory repository, identity, admission path, replay authority, or idempotency authority is released",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3b_locks_current_f5_2_identity_and_replay_facts():
    memory_id_source = _top_level_source("memory_id")
    replay_source = _top_level_source("_immutable_record_canonical_bytes")

    required_identity_facts = (
        '"owner_user_id": source_ref.owner_user_id',
        '"source_context_source_id": source_ref.context_source_id',
        '"promotion_authority_id": promotion_authority_id',
        '"content_digest": content_digest',
        '"memory_schema_version": memory_schema_version',
    )
    for phrase in required_identity_facts:
        assert phrase in memory_id_source

    # The immutable replay material is the full MemoryRecord model dump with
    # exactly one top-level exclusion: created_at.
    assert _model_dump_exclude_fields(replay_source) == {"created_at"}

    # source_ref_snapshot remains positively handled inside that immutable
    # material so nested source_created_at receives deterministic canonical
    # datetime normalization before replay comparison.
    assert 'source_snapshot = material.get("source_ref_snapshot")' in replay_source
    assert 'source_snapshot.get("source_created_at")' in replay_source


def test_ctx_f5_3b_locks_non_authorizing_constructor_and_single_repository_handoff():
    constructor_source = _top_level_source("create_memory_record")
    repository_source = _top_level_source("MemoryRecordRepository")
    text = _normalized_contract()

    assert "already-authorized provenance input" in constructor_source
    assert "async def put(self, record: MemoryRecord) -> MemoryRecord" in repository_source
    assert "perform exactly one existing MemoryRecordRepository.put(...) admission" in text


def test_ctx_f5_3b_releases_no_concrete_source_kind_and_keeps_cross_track_owners():
    text = _normalized_contract()

    required = (
        "F5-3B claims ZERO concrete source kinds as supported for production promotion",
        "SESSION/TASK/BRANCH/TOOL_RESPONSE_PAYLOAD require separately audited source-specific proof semantics",
        "ASSET production promotion remains blocked until CAS-owned read/liveness proof semantics are separately frozen and released",
        "AGENT_TRANSCRIPT or other R11-owned durable evidence promotion remains blocked until R11-owned read/liveness proof semantics are separately frozen and released",
        "Issue #74 / CAS retains FileAsset, FileBlob, FileReference, ObjectStorage",
        "Issue #31 / R11 retains transcript/checkpoint source authority",
        "Memory provenance must not become a source retention root",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3b_does_not_encode_a_future_global_absence_invariant():
    text = _contract()

    assert (
        "no global historical absence invariant prevents a later separately released"
        in text
    )
    assert "se/src/context/memory_promotion.py must not exist" not in text
    assert "Memory promotion implementation may never exist" not in text
