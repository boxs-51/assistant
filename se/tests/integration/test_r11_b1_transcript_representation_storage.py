from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.transcript_representation import (
    canonical_json_bytes,
    canonical_transcript_messages,
    logical_transcript_fingerprint,
    transcript_chunk_id,
    transcript_payload_root_ref,
    transcript_representation_ref,
)
from se.src.runtimes.agent.contracts.fork import fork_transcript_fingerprint
from se.src.runtimes.agent.contracts.inference import InferenceMessage


class _AsyncBarrier:
    def __init__(self, parties: int):
        self._parties = parties
        self._arrivals = 0
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            self._arrivals += 1
            if self._arrivals >= self._parties:
                self._event.set()
        await self._event.wait()


class _InsertBarrierRepository(AgentRepository):
    def __init__(self, session, barrier: _AsyncBarrier):
        super().__init__(session)
        self._insert_barrier = barrier

    async def _insert_immutable_do_nothing(
        self,
        model,
        values,
        *,
        conflict_columns,
    ):
        await self._insert_barrier.wait()
        return await super()._insert_immutable_do_nothing(
            model,
            values,
            conflict_columns=conflict_columns,
        )


def _message(label: str) -> dict[str, object]:
    return {
        "role": "user",
        "content": label,
        "metadata": {},
        "tool_calls": [],
        "name": None,
        "tool_call_id": None,
    }


async def _database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r11-b1.sqlite').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _save_chunk(repo: AgentRepository, messages):
    payload = list(messages)
    chunk_id = transcript_chunk_id(payload)
    return await repo.save_transcript_chunk(
        {
            "chunk_id": chunk_id,
            "payload": payload,
            "message_count": len(payload),
            "canonical_bytes": len(canonical_json_bytes(payload)),
        }
    )


async def _save_root(
    repo: AgentRepository,
    *,
    chunk_id: str,
    logical_message_count: int,
    parent_payload_root_ref: str | None = None,
):
    payload_root_ref = transcript_payload_root_ref(
        parent_payload_root_ref=parent_payload_root_ref,
        chunk_id=chunk_id,
        logical_message_count=logical_message_count,
    )
    return await repo.save_transcript_payload_node(
        {
            "payload_root_ref": payload_root_ref,
            "parent_payload_root_ref": parent_payload_root_ref,
            "chunk_id": chunk_id,
            "logical_message_count": logical_message_count,
        }
    )


async def _save_full(
    repo: AgentRepository,
    *,
    payload_root_ref: str,
    messages,
):
    transcript = list(messages)
    fingerprint = logical_transcript_fingerprint(transcript)
    transcript_ref = transcript_representation_ref(
        transcript_version=0,
        kind="FULL",
        parent_transcript_ref=None,
        parent_transcript_version=None,
        delta_depth=0,
        logical_message_count=len(transcript),
        logical_transcript_fingerprint=fingerprint,
        payload_root_ref=payload_root_ref,
    )
    return await repo.save_transcript_representation(
        {
            "transcript_ref": transcript_ref,
            "transcript_version": 0,
            "kind": "FULL",
            "parent_transcript_ref": None,
            "parent_transcript_version": None,
            "delta_depth": 0,
            "logical_message_count": len(transcript),
            "logical_transcript_fingerprint": fingerprint,
            "payload_root_ref": payload_root_ref,
        }
    )


async def _save_delta(
    repo: AgentRepository,
    *,
    parent,
    payload_root_ref: str,
    transcript,
):
    messages = list(transcript)
    version = int(parent.transcript_version) + 1
    depth = int(parent.delta_depth) + 1
    fingerprint = logical_transcript_fingerprint(messages)
    transcript_ref = transcript_representation_ref(
        transcript_version=version,
        kind="DELTA",
        parent_transcript_ref=parent.transcript_ref,
        parent_transcript_version=parent.transcript_version,
        delta_depth=depth,
        logical_message_count=len(messages),
        logical_transcript_fingerprint=fingerprint,
        payload_root_ref=payload_root_ref,
    )
    return await repo.save_transcript_representation(
        {
            "transcript_ref": transcript_ref,
            "transcript_version": version,
            "kind": "DELTA",
            "parent_transcript_ref": parent.transcript_ref,
            "parent_transcript_version": parent.transcript_version,
            "delta_depth": depth,
            "logical_message_count": len(messages),
            "logical_transcript_fingerprint": fingerprint,
            "payload_root_ref": payload_root_ref,
        }
    )


def test_r11_b1_canonical_identity_matches_r8_fork_semantics():
    raw = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "lookup",
                    "arguments": {"z": 2, "a": 1},
                }
            ],
            "metadata": {"trace": {"b": 2, "a": 1}},
        },
    ]
    canonical = canonical_transcript_messages(raw)
    models = tuple(InferenceMessage.model_validate(item) for item in raw)

    assert canonical == [item.model_dump(mode="json") for item in models]
    assert canonical_transcript_messages(canonical) == canonical
    assert logical_transcript_fingerprint(raw) == logical_transcript_fingerprint(
        canonical
    )
    assert logical_transcript_fingerprint(raw) == fork_transcript_fingerprint(
        list(models)
    )
    assert transcript_chunk_id(raw) == transcript_chunk_id(canonical)


@pytest.mark.asyncio
async def test_r11_b1_raw_and_defaulted_messages_reuse_one_chunk(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            repo = AgentRepository(session)
            raw = [{"role": "user", "content": "same"}]
            canonical = canonical_transcript_messages(raw)
            chunk_id = transcript_chunk_id(raw)
            canonical_bytes = len(canonical_json_bytes(canonical))

            first = await repo.save_transcript_chunk(
                {
                    "chunk_id": chunk_id,
                    "payload": raw,
                    "message_count": 1,
                    "canonical_bytes": canonical_bytes,
                }
            )
            second = await repo.save_transcript_chunk(
                {
                    "chunk_id": transcript_chunk_id(canonical),
                    "payload": canonical,
                    "message_count": 1,
                    "canonical_bytes": canonical_bytes,
                }
            )

            assert first.chunk_id == second.chunk_id == chunk_id
            assert list(first.payload) == canonical
            chunk_count = await session.scalar(
                select(func.count()).select_from(AgentTranscriptChunkRecord)
            )
            assert int(chunk_count or 0) == 1
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_b1_concurrent_duplicate_chunk_create_converges(tmp_path):
    engine, sessions = await _database(tmp_path)
    barrier = _AsyncBarrier(2)
    raw = [{"role": "user", "content": "race"}]
    canonical = canonical_transcript_messages(raw)
    values = {
        "chunk_id": transcript_chunk_id(raw),
        "payload": raw,
        "message_count": 1,
        "canonical_bytes": len(canonical_json_bytes(canonical)),
    }

    async def create_once():
        async with sessions() as session:
            repo = _InsertBarrierRepository(session, barrier)
            chunk = await repo.save_transcript_chunk(values)

            # The losing duplicate-create caller must keep a usable UoW.
            count = await session.scalar(
                select(func.count()).select_from(AgentTranscriptChunkRecord)
            )
            assert int(count or 0) == 1

            await session.commit()
            return chunk.chunk_id, list(chunk.payload)

    try:
        first, second = await asyncio.gather(
            create_once(),
            create_once(),
        )
        assert first == second
        assert first == (values["chunk_id"], canonical)

        async with sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(AgentTranscriptChunkRecord)
            )
            assert int(count or 0) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_b1_full_delta_and_full_reanchor_share_payload(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            repo = AgentRepository(session)

            a = [_message("a")]
            chunk_a = await _save_chunk(repo, a)
            root_a = await _save_root(
                repo,
                chunk_id=chunk_a.chunk_id,
                logical_message_count=1,
            )
            full_a = await _save_full(
                repo,
                payload_root_ref=root_a.payload_root_ref,
                messages=a,
            )

            b = [_message("b")]
            chunk_b = await _save_chunk(repo, b)

            # DELTA owns only the append suffix.
            suffix_b = await _save_root(
                repo,
                chunk_id=chunk_b.chunk_id,
                logical_message_count=1,
            )
            delta_ab = await _save_delta(
                repo,
                parent=full_a,
                payload_root_ref=suffix_b.payload_root_ref,
                transcript=a + b,
            )

            assert delta_ab.kind == "DELTA"
            assert delta_ab.delta_depth == 1
            assert delta_ab.parent_transcript_ref == full_a.transcript_ref

            # A later FULL logical anchor structurally reuses the old payload
            # root and the existing B chunk. It does not write A or B again.
            root_ab = await _save_root(
                repo,
                parent_payload_root_ref=root_a.payload_root_ref,
                chunk_id=chunk_b.chunk_id,
                logical_message_count=2,
            )
            full_ab = await _save_full(
                repo,
                payload_root_ref=root_ab.payload_root_ref,
                messages=a + b,
            )

            assert full_ab.kind == "FULL"
            assert full_ab.parent_transcript_ref is None
            assert root_ab.parent_payload_root_ref == root_a.payload_root_ref
            assert root_ab.chunk_id == suffix_b.chunk_id

            chunk_count = await session.scalar(
                select(func.count()).select_from(AgentTranscriptChunkRecord)
            )
            root_count = await session.scalar(
                select(func.count()).select_from(
                    AgentTranscriptPayloadNodeRecord
                )
            )
            representation_count = await session.scalar(
                select(func.count()).select_from(
                    AgentTranscriptRepresentationRecord
                )
            )

            assert int(chunk_count or 0) == 2
            assert int(root_count or 0) == 3
            assert int(representation_count or 0) == 3

            # Deterministic duplicate creation intent is idempotent.
            replay = await _save_full(
                repo,
                payload_root_ref=root_ab.payload_root_ref,
                messages=a + b,
            )
            assert replay.transcript_ref == full_ab.transcript_ref

            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_b1_rejects_identity_missing_parent_and_empty_delta(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            repo = AgentRepository(session)
            one = [_message("one")]
            chunk = await _save_chunk(repo, one)
            root = await _save_root(
                repo,
                chunk_id=chunk.chunk_id,
                logical_message_count=1,
            )
            full = await _save_full(
                repo,
                payload_root_ref=root.payload_root_ref,
                messages=one,
            )

            with pytest.raises(ValueError, match="chunk identity"):
                await repo.save_transcript_chunk(
                    {
                        "chunk_id": chunk.chunk_id,
                        "payload": [_message("different")],
                        "message_count": 1,
                        "canonical_bytes": len(
                            canonical_json_bytes([_message("different")])
                        ),
                    }
                )

            missing_parent_values = {
                "transcript_ref": "0" * 64,
                "transcript_version": 1,
                "kind": "DELTA",
                "parent_transcript_ref": "1" * 64,
                "parent_transcript_version": 0,
                "delta_depth": 1,
                "logical_message_count": 2,
                "logical_transcript_fingerprint": "2" * 64,
                "payload_root_ref": root.payload_root_ref,
            }
            with pytest.raises(ValueError, match="missing parent"):
                await repo.save_transcript_representation(
                    missing_parent_values
                )

            empty_chunk = await _save_chunk(repo, [])
            empty_root = await _save_root(
                repo,
                chunk_id=empty_chunk.chunk_id,
                logical_message_count=0,
            )
            empty_delta_values = {
                "transcript_ref": "3" * 64,
                "transcript_version": 1,
                "kind": "DELTA",
                "parent_transcript_ref": full.transcript_ref,
                "parent_transcript_version": 0,
                "delta_depth": 1,
                "logical_message_count": 1,
                "logical_transcript_fingerprint":
                    logical_transcript_fingerprint(one),
                "payload_root_ref": empty_root.payload_root_ref,
            }
            with pytest.raises(ValueError, match="append at least one"):
                await repo.save_transcript_representation(
                    empty_delta_values
                )

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_b1_representation_integrity_rejects_fingerprint_and_corruption(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            repo = AgentRepository(session)
            messages = [_message("one")]
            chunk = await _save_chunk(repo, messages)
            root = await _save_root(
                repo,
                chunk_id=chunk.chunk_id,
                logical_message_count=1,
            )

            wrong_fingerprint = "f" * 64
            wrong_ref = transcript_representation_ref(
                transcript_version=0,
                kind="FULL",
                parent_transcript_ref=None,
                parent_transcript_version=None,
                delta_depth=0,
                logical_message_count=1,
                logical_transcript_fingerprint=wrong_fingerprint,
                payload_root_ref=root.payload_root_ref,
            )
            with pytest.raises(ValueError, match="logical fingerprint mismatch"):
                await repo.save_transcript_representation(
                    {
                        "transcript_ref": wrong_ref,
                        "transcript_version": 0,
                        "kind": "FULL",
                        "parent_transcript_ref": None,
                        "parent_transcript_version": None,
                        "delta_depth": 0,
                        "logical_message_count": 1,
                        "logical_transcript_fingerprint": wrong_fingerprint,
                        "payload_root_ref": root.payload_root_ref,
                    }
                )

            full = await _save_full(
                repo,
                payload_root_ref=root.payload_root_ref,
                messages=messages,
            )

            # Simulate pre-existing physical corruption outside the append-only
            # repository API. Replaying the same representation intent must
            # fail closed instead of silently returning the damaged row.
            await session.execute(
                update(AgentTranscriptChunkRecord)
                .where(AgentTranscriptChunkRecord.chunk_id == chunk.chunk_id)
                .values(payload=[_message("bad")])
            )
            await session.flush()

            with pytest.raises(ValueError, match="chunk content is corrupt"):
                await _save_full(
                    repo,
                    payload_root_ref=root.payload_root_ref,
                    messages=messages,
                )

            assert full.transcript_ref is not None
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_b1_hard_delta_depth_is_nine(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            repo = AgentRepository(session)

            transcript = [_message("root")]
            root_chunk = await _save_chunk(repo, transcript)
            root_payload = await _save_root(
                repo,
                chunk_id=root_chunk.chunk_id,
                logical_message_count=1,
            )
            current = await _save_full(
                repo,
                payload_root_ref=root_payload.payload_root_ref,
                messages=transcript,
            )

            for depth in range(1, 10):
                suffix = [_message(f"delta-{depth}")]
                chunk = await _save_chunk(repo, suffix)
                suffix_root = await _save_root(
                    repo,
                    chunk_id=chunk.chunk_id,
                    logical_message_count=1,
                )
                transcript = transcript + suffix
                current = await _save_delta(
                    repo,
                    parent=current,
                    payload_root_ref=suffix_root.payload_root_ref,
                    transcript=transcript,
                )
                assert current.delta_depth == depth

            tenth_chunk = await _save_chunk(repo, [_message("delta-10")])
            tenth_root = await _save_root(
                repo,
                chunk_id=tenth_chunk.chunk_id,
                logical_message_count=1,
            )
            with pytest.raises(ValueError, match="safety envelope"):
                await repo.save_transcript_representation(
                    {
                        "transcript_ref": "f" * 64,
                        "transcript_version": 10,
                        "kind": "DELTA",
                        "parent_transcript_ref": current.transcript_ref,
                        "parent_transcript_version":
                            current.transcript_version,
                        "delta_depth": 10,
                        "logical_message_count": len(transcript) + 1,
                        "logical_transcript_fingerprint": "e" * 64,
                        "payload_root_ref": tenth_root.payload_root_ref,
                    }
                )

            await session.rollback()
    finally:
        await engine.dispose()
