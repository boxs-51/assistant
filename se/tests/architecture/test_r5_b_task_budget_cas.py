from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentTaskRecord,
    TaskBudgetRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _seed(sessions):
    async with sessions() as session:
        session.add(
            AgentTaskRecord(
                id="task-cas",
                session_id="session-cas",
                created_by="user-cas",
                assigned_agent_id="agent-cas",
                status="CREATED",
                input={},
            )
        )
        session.add(
            TaskBudgetRecord(
                task_id="task-cas",
                revision=0,
                state="OPEN",
                max_total_executions=4,
                max_active_executions=2,
                max_active_branches=2,
                max_parallel_agents=1,
                max_total_tool_calls=10,
                max_total_inference_calls=10,
                max_total_tokens=None,
                max_total_cost_usd=None,
                max_delegation_depth=3,
                policy_version="r5-test",
                policy_fingerprint="a" * 64,
                deny_recursive_agent_cycle=True,
                used_executions=0,
                active_executions=0,
                active_branches=0,
                active_parallel_agents=0,
                used_tool_calls=0,
                used_inference_calls=0,
                used_tokens=0,
                used_cost_usd=Decimal("0"),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_r5_b_task_and_budget_cas_reject_stale_revisions():
    engine, sessions = await _database()
    try:
        await _seed(sessions)

        async with sessions() as session:
            repo = AgentRepository(session)
            task = await repo.compare_and_set_task(
                "task-cas",
                0,
                {"status": "ASSIGNED"},
            )
            budget = await repo.compare_and_set_task_budget(
                "task-cas",
                0,
                {"used_tool_calls": 1},
            )
            await session.commit()
            assert task.revision == 1
            assert budget.revision == 1

        async with sessions() as session:
            repo = AgentRepository(session)
            assert (
                await repo.compare_and_set_task(
                    "task-cas",
                    0,
                    {"status": "CANCELLED"},
                )
                is None
            )
            assert (
                await repo.compare_and_set_task_budget(
                    "task-cas",
                    0,
                    {"used_tool_calls": 2},
                )
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_budget_reservation_identity_is_unique():
    engine, sessions = await _database()
    try:
        await _seed(sessions)
        async with sessions() as session:
            repo = AgentRepository(session)
            first = await repo.save_task_budget_reservation(
                {
                    "task_id": "task-cas",
                    "kind": "TOOL_CALL",
                    "reservation_key": "tool-1",
                    "payload_fingerprint": "b" * 64,
                }
            )
            await session.commit()
            assert first.reservation_key == "tool-1"

        async with sessions() as session:
            found = await AgentRepository(
                session
            ).get_task_budget_reservation(
                "task-cas",
                "TOOL_CALL",
                "tool-1",
            )
            assert found is not None
            assert found.payload_fingerprint == "b" * 64
    finally:
        await engine.dispose()
