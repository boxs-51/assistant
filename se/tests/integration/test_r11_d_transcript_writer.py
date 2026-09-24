from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentTranscriptRepresentationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.transcript_representation import (
    canonical_json_bytes,
    canonical_transcript_messages,
    logical_transcript_fingerprint,
    transcript_chunk_id,
    transcript_payload_root_ref,
    transcript_representation_ref,
)
from se.src.runtimes.agent.checkpoint_transcript import (
    CheckpointTranscriptMaterializationError,
)
from se.src.runtimes.agent.checkpoint_transcript_writer import (
    write_transcript_representation_in_uow,
)


class _Uow:
    def __init__(self, session):
        self.session = session
        self.agents = AgentRepository(session)


async def _database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r11-d-writer.sqlite').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _message(value: str):
    return {"role": "user", "content": value}


async def _execution_and_checkpoint(
    uow,
    *,
    execution_id: str,
    checkpoint_id: str,
    representation,
    snapshot,
    parent_checkpoint_id: str | None = None,
):
    uow.session.add(
        AgentExecutionRecord(
            id=execution_id,
            session_id=f"session-{execution_id}",
            agent_id="agent-r11-d",
            correlation_id=f"corr-{execution_id}",
            state="WAITING",
            revision=1,
            current_checkpoint_id=checkpoint_id,
            request={},
        )
    )
    await uow.session.flush()
    await uow.agents.save_execution_checkpoint(
        {
            "checkpoint_id": checkpoint_id,
            "execution_id": execution_id,
            "execution_revision": 1,
            "session_id": f"session-{execution_id}",
            "task_id": None,
            "branch_id": None,
            "parent_checkpoint_id": parent_checkpoint_id,
            "iteration": 0,
            "wait_reason": "RESOURCE",
            "transcript_snapshot": snapshot,
            "transcript_ref": representation.transcript_ref,
            "transcript_version": representation.transcript_version,
            "metadata_json": {},
        }
    )


@pytest.mark.asyncio
async def test_r11_d_writer_full_reuse_and_delta(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)

            full = await write_transcript_representation_in_uow(
                uow,
                messages=[_message("a")],
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-a",
                checkpoint_id="cp-a",
                representation=full,
                snapshot=[_message("a")],
            )

            reused = await write_transcript_representation_in_uow(
                uow,
                messages=[_message("a")],
                candidate_parent_checkpoint_id="cp-a",
            )
            assert reused == full

            delta = await write_transcript_representation_in_uow(
                uow,
                messages=[_message("a"), _message("b")],
                candidate_parent_checkpoint_id="cp-a",
            )
            record = await uow.agents.get_transcript_representation(
                delta.transcript_ref,
                delta.transcript_version,
            )
            assert record.kind == "DELTA"
            assert int(record.delta_depth) == 1
            assert record.parent_transcript_ref == full.transcript_ref
            assert await uow.agents.materialize_transcript_representation(
                delta.transcript_ref,
                delta.transcript_version,
            ) == [
                {
                    "role": "user",
                    "content": "a",
                    "tool_calls": [],
                    "name": None,
                    "tool_call_id": None,
                    "metadata": {},
                },
                {
                    "role": "user",
                    "content": "b",
                    "tool_calls": [],
                    "name": None,
                    "tool_call_id": None,
                    "metadata": {},
                },
            ]
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_depth_ten_reanchors_full_with_structural_prefix_share(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            messages = [_message("m0")]
            representation = await write_transcript_representation_in_uow(
                uow,
                messages=messages,
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-0",
                checkpoint_id="cp-0",
                representation=representation,
                snapshot=list(messages),
            )
            parent_checkpoint = "cp-0"

            # Build DELTA depths 1..9.
            for index in range(1, 10):
                messages = messages + [_message(f"m{index}")]
                representation = await write_transcript_representation_in_uow(
                    uow,
                    messages=messages,
                    candidate_parent_checkpoint_id=parent_checkpoint,
                )
                record = await uow.agents.get_transcript_representation(
                    representation.transcript_ref,
                    representation.transcript_version,
                )
                assert record.kind == "DELTA"
                assert int(record.delta_depth) == index
                checkpoint_id = f"cp-{index}"
                await _execution_and_checkpoint(
                    uow,
                    execution_id=f"exec-{index}",
                    checkpoint_id=checkpoint_id,
                    representation=representation,
                    snapshot=list(messages),
                    parent_checkpoint_id=parent_checkpoint,
                )
                parent_checkpoint = checkpoint_id

            depth_nine = await uow.agents.get_transcript_representation(
                representation.transcript_ref,
                representation.transcript_version,
            )
            old_suffix_root = depth_nine.payload_root_ref

            # Next append would be depth 10, so writer must FULL re-anchor.
            messages = messages + [_message("m10")]
            reanchored = await write_transcript_representation_in_uow(
                uow,
                messages=messages,
                candidate_parent_checkpoint_id=parent_checkpoint,
            )
            full = await uow.agents.get_transcript_representation(
                reanchored.transcript_ref,
                reanchored.transcript_version,
            )
            assert full.kind == "FULL"
            assert int(full.transcript_version) == 0
            assert int(full.delta_depth) == 0

            root = await uow.agents.get_transcript_payload_node(full.payload_root_ref)
            assert root.parent_payload_root_ref is not None
            assert root.parent_payload_root_ref != old_suffix_root

            materialized = await uow.agents.materialize_transcript_representation(
                reanchored.transcript_ref,
                reanchored.transcript_version,
            )
            assert [item["content"] for item in materialized] == [
                f"m{i}" for i in range(11)
            ]
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_rewrite_full_shares_maximal_common_prefix(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            original = [_message("a"), _message("b"), _message("c"), _message("d")]
            first = await write_transcript_representation_in_uow(
                uow,
                messages=original,
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-rewrite",
                checkpoint_id="cp-rewrite",
                representation=first,
                snapshot=original,
            )

            first_record = await uow.agents.get_transcript_representation(
                first.transcript_ref,
                first.transcript_version,
            )
            prefix_root = await uow.agents.get_transcript_payload_node(
                first_record.payload_root_ref
            )
            while int(prefix_root.logical_message_count) > 3:
                prefix_root = await uow.agents.get_transcript_payload_node(
                    prefix_root.parent_payload_root_ref
                )

            rewritten_messages = [
                _message("a"),
                _message("b"),
                _message("c"),
                _message("x"),
            ]
            rewritten = await write_transcript_representation_in_uow(
                uow,
                messages=rewritten_messages,
                candidate_parent_checkpoint_id="cp-rewrite",
            )
            record = await uow.agents.get_transcript_representation(
                rewritten.transcript_ref,
                rewritten.transcript_version,
            )
            assert record.kind == "FULL"
            assert record.parent_transcript_ref is None
            assert record.parent_transcript_version is None

            root = await uow.agents.get_transcript_payload_node(record.payload_root_ref)
            assert root.parent_payload_root_ref == prefix_root.payload_root_ref
            assert await uow.agents.get_transcript_chunk(
                transcript_chunk_id(canonical_transcript_messages(rewritten_messages))
            ) is None
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_delete_full_reuses_existing_prefix_root(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            original = [_message("a"), _message("b"), _message("c"), _message("d")]
            first = await write_transcript_representation_in_uow(
                uow,
                messages=original,
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-delete",
                checkpoint_id="cp-delete",
                representation=first,
                snapshot=original,
            )

            first_record = await uow.agents.get_transcript_representation(
                first.transcript_ref,
                first.transcript_version,
            )
            prefix_root = await uow.agents.get_transcript_payload_node(
                first_record.payload_root_ref
            )
            while int(prefix_root.logical_message_count) > 3:
                prefix_root = await uow.agents.get_transcript_payload_node(
                    prefix_root.parent_payload_root_ref
                )

            chunk_count_before = await session.scalar(
                select(func.count()).select_from(
                    type(await uow.agents.get_transcript_chunk(prefix_root.chunk_id))
                )
            )

            deleted = await write_transcript_representation_in_uow(
                uow,
                messages=original[:3],
                candidate_parent_checkpoint_id="cp-delete",
            )
            record = await uow.agents.get_transcript_representation(
                deleted.transcript_ref,
                deleted.transcript_version,
            )
            assert record.kind == "FULL"
            assert record.payload_root_ref == prefix_root.payload_root_ref

            chunk_count_after = await session.scalar(
                select(func.count()).select_from(
                    type(await uow.agents.get_transcript_chunk(prefix_root.chunk_id))
                )
            )
            assert chunk_count_after == chunk_count_before
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_dual_mismatch_candidate_fails_before_child_representation(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            original = [_message("a"), _message("b")]
            first = await write_transcript_representation_in_uow(
                uow,
                messages=original,
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-dual",
                checkpoint_id="cp-dual",
                representation=first,
                snapshot=original,
            )

            await session.execute(
                update(AgentExecutionCheckpointRecord)
                .where(AgentExecutionCheckpointRecord.checkpoint_id == "cp-dual")
                .values(transcript_snapshot=[_message("forged")])
            )
            await session.flush()

            before = await session.scalar(
                select(func.count()).select_from(AgentTranscriptRepresentationRecord)
            )
            with pytest.raises(
                CheckpointTranscriptMaterializationError,
                match="DUAL_TRANSCRIPT_MISMATCH",
            ):
                await write_transcript_representation_in_uow(
                    uow,
                    messages=original + [_message("child")],
                    candidate_parent_checkpoint_id="cp-dual",
                )
            after = await session.scalar(
                select(func.count()).select_from(AgentTranscriptRepresentationRecord)
            )
            assert after == before
            await session.rollback()
    finally:
        await engine.dispose()


async def _seed_coarse_full(uow, messages):
    canonical = canonical_transcript_messages(messages)
    chunk_id = transcript_chunk_id(canonical)
    chunk = await uow.agents.save_transcript_chunk(
        {
            "chunk_id": chunk_id,
            "payload": canonical,
            "message_count": len(canonical),
            "canonical_bytes": len(canonical_json_bytes(canonical)),
        }
    )
    root_ref = transcript_payload_root_ref(
        parent_payload_root_ref=None,
        chunk_id=chunk.chunk_id,
        logical_message_count=len(canonical),
    )
    root = await uow.agents.save_transcript_payload_node(
        {
            "payload_root_ref": root_ref,
            "parent_payload_root_ref": None,
            "chunk_id": chunk.chunk_id,
            "logical_message_count": len(canonical),
        }
    )
    fingerprint = logical_transcript_fingerprint(canonical)
    ref = transcript_representation_ref(
        transcript_version=0,
        kind="FULL",
        parent_transcript_ref=None,
        parent_transcript_version=None,
        delta_depth=0,
        logical_message_count=len(canonical),
        logical_transcript_fingerprint=fingerprint,
        payload_root_ref=root.payload_root_ref,
    )
    record = await uow.agents.save_transcript_representation(
        {
            "transcript_ref": ref,
            "transcript_version": 0,
            "kind": "FULL",
            "parent_transcript_ref": None,
            "parent_transcript_version": None,
            "delta_depth": 0,
            "logical_message_count": len(canonical),
            "logical_transcript_fingerprint": fingerprint,
            "payload_root_ref": root.payload_root_ref,
        }
    )
    return SimpleNamespace(
        transcript_ref=record.transcript_ref,
        transcript_version=record.transcript_version,
    )


@pytest.mark.asyncio
async def test_r11_d_coarse_full_rewrite_normalizes_prefix_boundary_once(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            original = [_message("m0"), _message("m1"), _message("m2"), _message("m3")]
            coarse = await _seed_coarse_full(uow, original)
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-coarse-rewrite",
                checkpoint_id="cp-coarse-rewrite",
                representation=coarse,
                snapshot=original,
            )

            before_chunks = await session.scalar(
                select(func.count()).select_from(
                    type(await uow.agents.get_transcript_chunk(
                        transcript_chunk_id(canonical_transcript_messages(original))
                    ))
                )
            )

            rewritten = original[:3] + [_message("x")]
            first = await write_transcript_representation_in_uow(
                uow,
                messages=rewritten,
                candidate_parent_checkpoint_id="cp-coarse-rewrite",
            )
            first_record = await uow.agents.get_transcript_representation(
                first.transcript_ref,
                first.transcript_version,
            )
            assert first_record.kind == "FULL"
            assert await uow.agents.materialize_transcript_representation(
                first.transcript_ref,
                first.transcript_version,
            ) == canonical_transcript_messages(rewritten)
            assert await uow.agents.get_transcript_chunk(
                transcript_chunk_id(canonical_transcript_messages(rewritten))
            ) is None

            after_first = await session.scalar(
                select(func.count()).select_from(
                    type(await uow.agents.get_transcript_chunk(
                        transcript_chunk_id(canonical_transcript_messages(original))
                    ))
                )
            )

            second = await write_transcript_representation_in_uow(
                uow,
                messages=rewritten,
                candidate_parent_checkpoint_id="cp-coarse-rewrite",
            )
            after_second = await session.scalar(
                select(func.count()).select_from(
                    type(await uow.agents.get_transcript_chunk(
                        transcript_chunk_id(canonical_transcript_messages(original))
                    ))
                )
            )

            assert second == first
            assert after_second == after_first
            assert after_first >= before_chunks
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_coarse_full_delete_normalizes_inside_chunk_prefix(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            original = [_message("m0"), _message("m1"), _message("m2"), _message("m3")]
            coarse = await _seed_coarse_full(uow, original)
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-coarse-delete",
                checkpoint_id="cp-coarse-delete",
                representation=coarse,
                snapshot=original,
            )

            deleted_messages = original[:3]
            deleted = await write_transcript_representation_in_uow(
                uow,
                messages=deleted_messages,
                candidate_parent_checkpoint_id="cp-coarse-delete",
            )
            record = await uow.agents.get_transcript_representation(
                deleted.transcript_ref,
                deleted.transcript_version,
            )
            assert record.kind == "FULL"
            assert await uow.agents.materialize_transcript_representation(
                deleted.transcript_ref,
                deleted.transcript_version,
            ) == canonical_transcript_messages(deleted_messages)
            assert await uow.agents.get_transcript_chunk(
                transcript_chunk_id(canonical_transcript_messages(deleted_messages))
            ) is None
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_delta_anchored_on_coarse_full_normalizes_prefix_boundary(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            base_messages = [_message("m0"), _message("m1"), _message("m2"), _message("m3")]
            coarse = await _seed_coarse_full(uow, base_messages)
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-coarse-base",
                checkpoint_id="cp-coarse-base",
                representation=coarse,
                snapshot=base_messages,
            )

            extended = base_messages + [_message("m4")]
            delta = await write_transcript_representation_in_uow(
                uow,
                messages=extended,
                candidate_parent_checkpoint_id="cp-coarse-base",
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-coarse-delta",
                checkpoint_id="cp-coarse-delta",
                representation=delta,
                snapshot=extended,
                parent_checkpoint_id="cp-coarse-base",
            )

            rewritten = base_messages[:3] + [_message("x"), _message("m4")]
            result = await write_transcript_representation_in_uow(
                uow,
                messages=rewritten,
                candidate_parent_checkpoint_id="cp-coarse-delta",
            )
            record = await uow.agents.get_transcript_representation(
                result.transcript_ref,
                result.transcript_version,
            )
            assert record.kind == "FULL"
            assert await uow.agents.materialize_transcript_representation(
                result.transcript_ref,
                result.transcript_version,
            ) == canonical_transcript_messages(rewritten)
            assert await uow.agents.get_transcript_chunk(
                transcript_chunk_id(canonical_transcript_messages(rewritten))
            ) is None
            await session.rollback()
    finally:
        await engine.dispose()
