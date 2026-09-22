from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import AgentTaskRecord
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import BranchConflictError, DurableAgentStore


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
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


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _seed_task(sessions):
    async with sessions() as session:
        session.add(
            AgentTaskRecord(
                id="task-r8-a",
                session_id="session-r8-a",
                created_by="user-r8-a",
                assigned_agent_id="agent-r8-a",
                status="RUNNING",
                input={},
            )
        )
        await session.commit()


def _branch_values(branch_id: str = "branch-r8-a"):
    return {
        "branch_id": branch_id,
        "task_id": "task-r8-a",
        "parent_branch_id": None,
        "base_execution_id": None,
        "base_checkpoint_id": None,
        "current_execution_id": None,
        "resolution_state": "OPEN",
        "revision": 0,
        "created_by": "user-r8-a",
        "reason": "repository-test",
    }


@pytest.mark.asyncio
async def test_r8_a_repository_branch_crud_and_revision_cas():
    engine, sessions = await _database()
    try:
        await _seed_task(sessions)

        async with sessions() as session:
            repo = AgentRepository(session)
            first = await repo.save_task_branch(_branch_values())
            await session.commit()
            assert first.branch_id == "branch-r8-a"

        async with sessions() as session:
            repo = AgentRepository(session)
            loaded = await repo.get_task_branch("branch-r8-a")
            listed = await repo.list_task_branches("task-r8-a")
            assert loaded is not None
            assert [item.branch_id for item in listed] == ["branch-r8-a"]

            updated = await repo.compare_and_set_task_branch(
                "branch-r8-a",
                0,
                {"resolution_state": "CANCELLED"},
            )
            assert updated is not None
            assert updated.revision == 1
            assert updated.resolution_state == "CANCELLED"
            await session.commit()

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.compare_and_set_task_branch(
                "branch-r8-a",
                0,
                {"resolution_state": "OPEN"},
            ) is None

            with pytest.raises(ValueError, match="immutable fields"):
                await repo.compare_and_set_task_branch(
                    "branch-r8-a",
                    1,
                    {"task_id": "other-task"},
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_a_branch_context_round_trip_and_store_conflicts():
    engine, sessions = await _database()
    try:
        await _seed_task(sessions)
        store = DurableAgentStore(lambda: _Uow(sessions))

        await store.save_task_branch(_branch_values("branch-context"))
        context = await store.save_task_branch_context(
            {
                "branch_id": "branch-context",
                "revision": 0,
                "overlay_messages": [
                    {
                        "role": "user",
                        "content": ("branch", "local"),
                    }
                ],
            }
        )
        assert context.overlay_messages[0]["content"] == ["branch", "local"]

        updated = await store.compare_and_set_task_branch_context(
            "branch-context",
            0,
            [{"role": "user", "content": "new overlay"}],
        )
        assert updated.revision == 1
        assert updated.overlay_messages == [
            {"role": "user", "content": "new overlay"}
        ]

        with pytest.raises(BranchConflictError, match="TaskBranchContext"):
            await store.compare_and_set_task_branch_context(
                "branch-context",
                0,
                [{"role": "user", "content": "stale"}],
            )

        branch = await store.compare_and_set_task_branch(
            "branch-context",
            0,
            {"resolution_state": "CANCELLED"},
        )
        assert branch.revision == 1

        with pytest.raises(BranchConflictError, match="TaskBranch revision"):
            await store.compare_and_set_task_branch(
                "branch-context",
                0,
                {"resolution_state": "OPEN"},
            )
    finally:
        await engine.dispose()
