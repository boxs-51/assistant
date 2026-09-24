from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.checkpoint_backfill import (
    CheckpointBackfillError,
    backfill_legacy_inline_checkpoints_in_uow,
)
from se.src.runtimes.agent.persistence import DurableAgentStore


class _Uow:
    def __init__(self, sessions, repository_cls=AgentRepository):
        self._sessions = sessions
        self._repository_cls = repository_cls
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = self._repository_cls(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


async def _db(tmp_path, name):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_execution(session, execution_id: str):
    session.add(
        AgentExecutionRecord(
            id=execution_id,
            session_id=f"session-{execution_id}",
            agent_id="agent-r11-d",
            correlation_id=f"corr-{execution_id}",
            state="WAITING",
            revision=2,
            request={},
        )
    )
    await session.flush()


async def _seed_legacy_checkpoint(
    session,
    *,
    checkpoint_id: str,
    execution_id: str,
    messages,
    parent_checkpoint_id: str | None = None,
    created_at: datetime | None = None,
):
    session.add(
        AgentExecutionCheckpointRecord(
            checkpoint_id=checkpoint_id,
            execution_id=execution_id,
            execution_revision=2,
            session_id=f"session-{execution_id}",
            task_id=None,
            branch_id=None,
            parent_checkpoint_id=parent_checkpoint_id,
            iteration=0,
            wait_reason="RESOURCE",
            transcript_snapshot=list(messages),
            transcript_ref=None,
            transcript_version=None,
            metadata_json={},
            created_at=created_at,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_r11_d_backfill_limit_one_eventually_converts_parent_and_child(tmp_path):
    engine, sessions = await _db(tmp_path, "r11-d-backfill-order.sqlite")
    try:
        async with sessions() as session:
            await _seed_execution(session, "exec-order")
            now = datetime.now(timezone.utc)
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-parent",
                execution_id="exec-order",
                messages=[{"role": "user", "content": "a"}],
                created_at=now,
            )
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-child",
                execution_id="exec-order",
                parent_checkpoint_id="cp-parent",
                messages=[
                    {"role": "user", "content": "a"},
                    {"role": "assistant", "content": "b"},
                ],
                created_at=now - timedelta(seconds=1),
            )
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))

        first = await store.backfill_legacy_inline_checkpoints(limit=1)
        assert first.converted == 1
        assert first.deferred == 1

        second = await store.backfill_legacy_inline_checkpoints(limit=1)
        assert second.converted == 1
        assert second.deferred == 0

        async with sessions() as session:
            repo = AgentRepository(session)
            parent = await repo.get_execution_checkpoint("cp-parent")
            child = await repo.get_execution_checkpoint("cp-child")
            assert parent.transcript_snapshot is not None
            assert parent.transcript_ref is not None
            assert child.transcript_snapshot is not None
            assert child.transcript_ref is not None

        # Later bounded runs perform validation only and create no conversion.
        cursor = None
        seen = 0
        for _ in range(4):
            result = await store.backfill_legacy_inline_checkpoints(
                limit=1,
                validation_after=cursor,
            )
            assert result.converted == 0
            seen += result.converged
            cursor = result.next_validation_after
            if cursor is None:
                break
        assert seen >= 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_backfill_ref_prefix_does_not_starve_later_legacy(tmp_path):
    engine, sessions = await _db(tmp_path, "r11-d-backfill-prefix.sqlite")
    try:
        async with sessions() as session:
            await _seed_execution(session, "exec-prefix")
            now = datetime.now(timezone.utc)
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-early",
                execution_id="exec-prefix",
                messages=[{"role": "user", "content": "early"}],
                created_at=now,
            )
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-late",
                execution_id="exec-prefix",
                messages=[{"role": "user", "content": "late"}],
                created_at=now + timedelta(seconds=1),
            )
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        early = await store.backfill_legacy_inline_checkpoints(limit=1)
        assert early.converted == 1

        # cp-early is now DUAL and remains earlier in global ordering, but the
        # next bounded conversion must still reach cp-late.
        late = await store.backfill_legacy_inline_checkpoints(limit=1)
        assert late.converted == 1

        async with sessions() as session:
            cp_late = await AgentRepository(session).get_execution_checkpoint(
                "cp-late"
            )
            assert cp_late.transcript_ref is not None
            assert cp_late.transcript_version is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_ref_validation_cursor_eventually_visits_all_rows(tmp_path):
    engine, sessions = await _db(tmp_path, "r11-d-backfill-validation.sqlite")
    try:
        async with sessions() as session:
            await _seed_execution(session, "exec-validation")
            now = datetime.now(timezone.utc)
            for index in range(3):
                await _seed_legacy_checkpoint(
                    session,
                    checkpoint_id=f"cp-v-{index}",
                    execution_id="exec-validation",
                    messages=[{"role": "user", "content": f"m{index}"}],
                    created_at=now + timedelta(seconds=index),
                )
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        # Convert all rows first.
        converted = await store.backfill_legacy_inline_checkpoints()
        assert converted.converted == 3

        cursor = None
        visited = 0
        for _ in range(5):
            page = await store.backfill_legacy_inline_checkpoints(
                limit=1,
                validation_after=cursor,
            )
            assert page.converted == 0
            visited += page.converged
            cursor = page.next_validation_after
            if cursor is None:
                break
        assert visited == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_backfill_dual_corruption_fails_closed(tmp_path):
    engine, sessions = await _db(tmp_path, "r11-d-backfill-corrupt.sqlite")
    try:
        async with sessions() as session:
            await _seed_execution(session, "exec-corrupt")
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-corrupt",
                execution_id="exec-corrupt",
                messages=[{"role": "user", "content": "A"}],
            )
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        result = await store.backfill_legacy_inline_checkpoints()
        assert result.converted == 1

        async with sessions() as session:
            await session.execute(
                update(AgentExecutionCheckpointRecord)
                .where(
                    AgentExecutionCheckpointRecord.checkpoint_id == "cp-corrupt"
                )
                .values(
                    transcript_snapshot=[{"role": "user", "content": "FORGED"}]
                )
            )
            await session.commit()

        with pytest.raises(
            CheckpointBackfillError,
            match="DUAL_TRANSCRIPT_MISMATCH",
        ):
            await store.backfill_legacy_inline_checkpoints()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_d_backfill_ref_backed_state_is_verified_and_skipped(tmp_path):
    engine, sessions = await _db(tmp_path, "r11-d-backfill-ref.sqlite")
    try:
        async with sessions() as session:
            await _seed_execution(session, "exec-ref")
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-ref",
                execution_id="exec-ref",
                messages=[{"role": "user", "content": "A"}],
            )
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        converted = await store.backfill_legacy_inline_checkpoints()
        assert converted.converted == 1

        async with sessions() as session:
            await session.execute(
                update(AgentExecutionCheckpointRecord)
                .where(AgentExecutionCheckpointRecord.checkpoint_id == "cp-ref")
                .values(transcript_snapshot=None)
            )
            await session.commit()

        verified = await store.backfill_legacy_inline_checkpoints()
        assert verified.converted == 0
        assert verified.converged == 1
        assert verified.deferred == 0
    finally:
        await engine.dispose()


class _CasLoserRepository(AgentRepository):
    async def bind_checkpoint_transcript_representation_if_legacy(
        self,
        checkpoint_id: str,
        *,
        transcript_ref: str,
        transcript_version: int,
    ):
        winner = await super().bind_checkpoint_transcript_representation_if_legacy(
            checkpoint_id,
            transcript_ref=transcript_ref,
            transcript_version=transcript_version,
        )
        assert winner is not None
        return None


@pytest.mark.asyncio
async def test_r11_d_backfill_cas_loser_reloads_and_verifies_winner(tmp_path):
    engine, sessions = await _db(tmp_path, "r11-d-backfill-cas.sqlite")
    try:
        async with sessions() as session:
            await _seed_execution(session, "exec-cas")
            await _seed_legacy_checkpoint(
                session,
                checkpoint_id="cp-cas",
                execution_id="exec-cas",
                messages=[{"role": "user", "content": "A"}],
            )
            await session.commit()

        async with _Uow(sessions, _CasLoserRepository) as uow:
            result = await backfill_legacy_inline_checkpoints_in_uow(uow)
            assert result.scanned == 1
            assert result.converted == 0
            assert result.converged == 1
            assert result.deferred == 0
            await uow.commit()

        async with sessions() as session:
            checkpoint = await AgentRepository(session).get_execution_checkpoint(
                "cp-cas"
            )
            assert checkpoint.transcript_snapshot is not None
            assert checkpoint.transcript_ref is not None
            assert checkpoint.transcript_version is not None
    finally:
        await engine.dispose()
