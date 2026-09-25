from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from se.src.context.memory import (
    MEMORY_IDENTITY_DOMAIN,
    MEMORY_SCHEMA_VERSION,
    InMemoryMemoryRecordRepository,
    MemoryRecord,
    MemoryRecordConflictError,
    canonical_memory_bytes,
    create_memory_record,
    memory_content_digest,
    memory_id,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)


NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def _source(*, authority_id: str = "session-1", owner: str = "user-1"):
    return create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=authority_id,
        owner_user_id=owner,
        session_id=authority_id,
        source_created_at=NOW,
        source_state="active",
        metadata={"label": "canonical"},
    )


def _record(
    *,
    promotion_authority_id: str = "promotion-1",
    source=None,
    content=None,
    metadata=None,
):
    return create_memory_record(
        source_ref=source or _source(),
        promotion_authority_id=promotion_authority_id,
        content={"fact": "alpha"} if content is None else content,
        metadata={"kind": "test"} if metadata is None else metadata,
    )


def test_ctx_f5_1_identity_domain_and_schema_are_frozen():
    assert MEMORY_IDENTITY_DOMAIN == "ctx-memory-v1"
    assert MEMORY_SCHEMA_VERSION == 1


def test_ctx_f5_1_canonical_json_is_deterministic_and_rejects_non_json_containers():
    left = canonical_memory_bytes({"b": [2, 1], "a": "é"})
    right = canonical_memory_bytes({"a": "é", "b": [2, 1]})

    assert left == right == b'{"a":"\xc3\xa9","b":[2,1]}'
    assert memory_content_digest({"a": "é", "b": [2, 1]}) == memory_content_digest(
        {"b": [2, 1], "a": "é"}
    )

    for invalid in (
        {"bad": float("nan")},
        {"bad": float("inf")},
        {"bad": (1, 2)},
        {"bad": {1, 2}},
        {1: "non-string-key"},
    ):
        with pytest.raises(ValueError):
            canonical_memory_bytes(invalid)


def test_ctx_f5_1_memory_id_replay_and_promotion_separation():
    source = _source()
    digest = memory_content_digest({"fact": "alpha"})

    first = memory_id(
        source_ref=source,
        promotion_authority_id="promotion-1",
        content_digest=digest,
    )
    replay = memory_id(
        source_ref=source,
        promotion_authority_id="promotion-1",
        content_digest=digest,
    )
    independent = memory_id(
        source_ref=source,
        promotion_authority_id="promotion-2",
        content_digest=digest,
    )

    assert first == replay
    assert independent != first
    assert first != source.context_source_id


def test_ctx_f5_1_owner_is_derived_from_source_and_content_is_deeply_frozen():
    source = _source(owner="owner-a")
    input_content = {"facts": [{"name": "alpha"}]}
    input_metadata = {"tags": ["one"]}

    record = create_memory_record(
        source_ref=source,
        promotion_authority_id="promotion-1",
        content=input_content,
        metadata=input_metadata,
    )

    input_content["facts"][0]["name"] = "mutated"
    input_metadata["tags"].append("two")

    assert record.owner_user_id == "owner-a"
    assert isinstance(record.content, MappingProxyType)
    assert isinstance(record.content["facts"], tuple)
    assert record.content["facts"][0]["name"] == "alpha"
    assert isinstance(record.metadata, MappingProxyType)
    assert record.metadata["tags"] == ("one",)


def test_ctx_f5_1_direct_owner_override_fails_closed():
    record = _record()

    with pytest.raises(ValueError, match="owner"):
        MemoryRecord(
            **{
                **record.model_dump(),
                "owner_user_id": "different-owner",
                "source_ref_snapshot": record.source_ref_snapshot,
                "content": {"fact": "alpha"},
                "metadata": {"kind": "test"},
            }
        )


@pytest.mark.asyncio
async def test_ctx_f5_1_repository_replay_returns_existing_record():
    repository = InMemoryMemoryRecordRepository()
    first = _record()
    replay = _record()

    stored = await repository.put(first)
    returned = await repository.put(replay)

    assert returned is stored
    assert await repository.get(first.memory_id) is stored
    assert (
        await repository.get_by_promotion_authority("promotion-1")
        is stored
    )


@pytest.mark.asyncio
async def test_ctx_f5_1_same_promotion_with_changed_content_fails_closed():
    repository = InMemoryMemoryRecordRepository()
    await repository.put(_record(content={"fact": "alpha"}))

    with pytest.raises(MemoryRecordConflictError, match="promotion_authority_id"):
        await repository.put(_record(content={"fact": "beta"}))


@pytest.mark.asyncio
async def test_ctx_f5_1_different_promotions_can_store_identical_content_distinctly():
    repository = InMemoryMemoryRecordRepository()
    first = _record(promotion_authority_id="promotion-1")
    second = _record(promotion_authority_id="promotion-2")

    assert first.memory_id != second.memory_id
    assert await repository.put(first) is first
    assert await repository.put(second) is second


@pytest.mark.asyncio
async def test_ctx_f5_1_same_identity_with_conflicting_metadata_fails_closed():
    repository = InMemoryMemoryRecordRepository()
    original = _record(metadata={"kind": "one"})
    await repository.put(original)

    conflicting = MemoryRecord(
        memory_id=original.memory_id,
        promotion_authority_id=original.promotion_authority_id,
        memory_schema_version=original.memory_schema_version,
        source_ref_snapshot=original.source_ref_snapshot,
        owner_user_id=original.owner_user_id,
        content_digest=original.content_digest,
        canonical_bytes=original.canonical_bytes,
        content={"fact": "alpha"},
        metadata={"kind": "two"},
    )

    with pytest.raises(MemoryRecordConflictError, match="memory_id"):
        await repository.put(conflicting)


@pytest.mark.asyncio
async def test_ctx_f5_1_repository_revalidates_unvalidated_record_copy():
    repository = InMemoryMemoryRecordRepository()
    record = _record()

    forged = record.model_copy(
        update={
            "memory_id": "0" * 64,
            "content": {"mutable": "bypass"},
        }
    )

    with pytest.raises(ValueError):
        await repository.put(forged)


@pytest.mark.asyncio
async def test_ctx_f5_1_repository_revalidates_forged_source_snapshot():
    repository = InMemoryMemoryRecordRepository()
    record = _record()
    forged_source = record.source_ref_snapshot.model_copy(
        update={"context_source_id": "f" * 64}
    )
    forged = record.model_copy(update={"source_ref_snapshot": forged_source})

    with pytest.raises(ValueError, match="context_source_id"):
        await repository.put(forged)
