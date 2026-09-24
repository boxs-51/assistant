from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
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
async def test_r11_d_unproven_rewrite_forces_independent_full(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            uow = _Uow(session)
            first = await write_transcript_representation_in_uow(
                uow,
                messages=[_message("a"), _message("b")],
            )
            await _execution_and_checkpoint(
                uow,
                execution_id="exec-rewrite",
                checkpoint_id="cp-rewrite",
                representation=first,
                snapshot=[_message("a"), _message("b")],
            )

            rewritten = await write_transcript_representation_in_uow(
                uow,
                messages=[_message("a"), _message("c")],
                candidate_parent_checkpoint_id="cp-rewrite",
            )
            record = await uow.agents.get_transcript_representation(
                rewritten.transcript_ref,
                rewritten.transcript_version,
            )
            assert record.kind == "FULL"
            assert record.parent_transcript_ref is None
            assert record.parent_transcript_version is None
            await session.rollback()
    finally:
        await engine.dispose()
