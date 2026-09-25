from __future__ import annotations

from pathlib import Path

from se.src.context.source_identity import ContextSourceKind


CONTRACT = Path(
    "docs/context_future/CTX_F5_0_LONG_TERM_MEMORY_AUTHORITY_FREEZE_C50D0670.md"
)


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_ctx_f5_0_identity_authority_is_distinct_and_memory_source_kind_is_unopened():
    text = _contract_text()

    required = (
        "memory_id = opaque durable Memory identity",
        "memory_id != context_source_id",
        "memory_id != transcript_ref",
        "memory_id != asset_id",
        "memory_id != provider id / FileProviderBinding id",
        "memory_id != Session/Task/Branch id",
        "memory_id != tool_response_payload_id",
        "memory_id != chunk/vector/index id",
        "CTX-F5-0 does not add or assume `ContextSourceKind.MEMORY`",
    )
    for phrase in required:
        assert phrase in text

    assert "MEMORY" not in ContextSourceKind.__members__


def test_ctx_f5_0_legacy_memory_vocabulary_is_explicitly_non_authoritative():
    text = _contract_text()

    required = (
        "cl/src/schemas/context.py::MemoryQueryResult",
        "legacy client/schema vocabulary and is not canonical CTX-F5 durable Memory authority",
        "AgentContextSession.retrieved_memories",
        "legacy direct-injection field",
        "se/src/domain/schemas/agent.py::AgentMemoryConfig",
        "Agent conversation-window/summary configuration",
        'SessionRuntime "memory" wording',
        "CanonicalMessageService.persist_message(...)",
        "That wording does not create CTX-F5 authority",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_0_source_lifetime_durable_existence_and_revocation_are_separate():
    text = _contract_text()

    required = (
        "source physical lifetime != Memory retention root",
        "Memory durable existence",
        "!= retrieval authorization",
        "!= model visibility",
        "!= personalization authority",
        "!= source authorization",
        "authoritative source revocation/deletion/erasure handling = UNRELEASED / FAIL-CLOSED",
        "uncertain or unproven authorization => FAIL CLOSED",
        "source revoked/deleted => Memory remains authorized",
        "source physically GC'd => Memory must automatically be physically deleted",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_0_promotion_is_explicit_and_not_implied_by_search_or_model_output():
    text = _contract_text()

    required = (
        "explicit server-authorized promotion operation",
        "Discoverability or searchability is never promotion authority",
        "derive owner scope from trusted server-side authority",
        "re-prove source identity/provenance at commit time",
        "reject cross-owner provenance",
        "freeze a content fingerprint",
        "fail closed on conflicting replay",
        "Automatic Session/Task/Branch -> Memory promotion remains CLOSED",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_0_retrieval_model_visibility_and_later_stages_remain_closed():
    text = _contract_text()

    required = (
        "F4B source search",
        "finite already-supplied Session/Task/Branch evidence",
        "future F5 Memory retrieval",
        "separate committed-Memory loader/repository/index contract",
        "retrieved Memory",
        "!= selected model Working Set",
        "Memory persistence/retrieval (F5)",
        "!= Personalization/profile mutation (F6)",
        "!= Pins/scoring/dedup policy (F7)",
        "!= Working Set / ContextSnapshot selection (F9)",
        "!= CompactContext (F10)",
        "No direct Memory injection into ContextBuilder is released",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_0_does_not_release_schema_repository_or_cross_track_lifecycle_authority():
    text = _contract_text()

    required = (
        "NO Memory table",
        "NO Memory ORM/model",
        "NO repository",
        "NO migration",
        "R11 retains checkpoint/transcript persistence, retention, serialization, and destructive-GC authority",
        "CAS retains:",
        "FileAsset",
        "FileBlob",
        "FileReference",
        "FileProviderBinding",
        "provider hydration/runtime binding",
        "CAS physical GC",
        "Provider ids/bindings never become Memory identity",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_0_contract_freezes_its_stage_without_forbidding_later_released_stages():
    text = _contract_text()

    required = (
        "F5-0 itself creates:",
        "NO Memory table",
        "NO Memory ORM/model",
        "NO repository",
        "NO migration",
        "NO schema mutation",
        "Any F5-1 schema/migration must receive a fresh independent release.",
        "Possible later candidate direction, not released here:",
        "dormant immutable Memory record + exact provenance/promotion contract",
    )
    for phrase in required:
        assert phrase in text
