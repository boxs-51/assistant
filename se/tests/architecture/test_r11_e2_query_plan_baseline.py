from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentIterationRecord,
    AgentTaskBranchRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base


ITERATION_ORDER_INDEX = "ix_agent_iterations_execution_iteration"
BRANCH_ORDER_INDEX = "ix_agent_task_branches_task_created_branch"


async def _sqlite_query_plan(tmp_path, name: str, sql: str) -> list[str]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            rows = (
                await connection.execute(text(f"EXPLAIN QUERY PLAN {sql}"))
            ).all()
        return [str(row[3]) for row in rows]
    finally:
        await engine.dispose()


def _uses_temp_order(plan: list[str]) -> bool:
    return any("USE TEMP B-TREE FOR ORDER BY" in detail.upper() for detail in plan)


@pytest.mark.asyncio
async def test_r11_e2_runtime_hot_query_plans_use_ordering_indexes(tmp_path):
    """E2.2 must remove only the two planner-visible ORDER BY sorts from E2.1."""

    iteration_plan = await _sqlite_query_plan(
        tmp_path,
        "r11-e2-iterations.sqlite",
        "SELECT * FROM agent_iterations "
        "WHERE execution_id = 'exec-hot' "
        "ORDER BY iteration ASC",
    )
    branch_plan = await _sqlite_query_plan(
        tmp_path,
        "r11-e2-branches.sqlite",
        "SELECT * FROM agent_task_branches "
        "WHERE task_id = 'task-hot' "
        "ORDER BY created_at ASC, branch_id ASC",
    )

    assert any(ITERATION_ORDER_INDEX in detail for detail in iteration_plan), (
        iteration_plan
    )
    assert any(BRANCH_ORDER_INDEX in detail for detail in branch_plan), branch_plan
    assert not _uses_temp_order(iteration_plan), iteration_plan
    assert not _uses_temp_order(branch_plan), branch_plan


def test_r11_e2_query_order_indexes_are_exact_model_metadata():
    iteration_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in AgentIterationRecord.__table__.indexes
    }
    branch_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in AgentTaskBranchRecord.__table__.indexes
    }

    assert iteration_indexes[ITERATION_ORDER_INDEX] == (
        "execution_id",
        "iteration",
    )
    assert branch_indexes[BRANCH_ORDER_INDEX] == (
        "task_id",
        "created_at",
        "branch_id",
    )

    # Preserve the separately owned filtering/resolution indexes.
    assert "ix_agent_iterations_execution_id" in iteration_indexes
    assert "ix_agent_task_branches_task_resolution" in branch_indexes
