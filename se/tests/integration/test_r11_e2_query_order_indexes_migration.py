from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[3]

ITERATION_ORDER_INDEX = "ix_agent_iterations_execution_iteration"
BRANCH_ORDER_INDEX = "ix_agent_task_branches_task_created_branch"


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


def _index_columns(connection, table: str, index_name: str) -> tuple[str, ...]:
    indexes = {
        row[1]
        for row in connection.execute(f"PRAGMA index_list({table})")
    }
    assert index_name in indexes
    return tuple(
        row[2]
        for row in connection.execute(f"PRAGMA index_info({index_name})")
    )


def _query_plan(connection, sql: str) -> list[str]:
    return [
        str(row[3])
        for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}")
    ]


def _uses_temp_order(plan: list[str]) -> bool:
    return any("USE TEMP B-TREE FOR ORDER BY" in detail.upper() for detail in plan)


def test_r11_e2_is_linear_child_of_cas_head(tmp_path: Path):
    script = ScriptDirectory.from_config(_config(tmp_path / "unused.sqlite"))
    assert script.get_heads() == ["19a_r11_query_order_indexes"]
    assert (
        script.get_revision("19a_r11_query_order_indexes").down_revision
        == "18a_cas_r0_assets"
    )
    assert (
        script.get_revision("18a_cas_r0_assets").down_revision
        == "17a_r11_checkpoint_cutover"
    )


def test_r11_e2_migration_adds_exact_indexes_and_removes_runtime_sorts(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r11_e2_indexes.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)

    command.upgrade(config, "18a_cas_r0_assets")
    command.upgrade(config, "19a_r11_query_order_indexes")

    connection = sqlite3.connect(database)
    try:
        assert _index_columns(
            connection,
            "agent_iterations",
            ITERATION_ORDER_INDEX,
        ) == ("execution_id", "iteration")
        assert _index_columns(
            connection,
            "agent_task_branches",
            BRANCH_ORDER_INDEX,
        ) == ("task_id", "created_at", "branch_id")

        iteration_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_iterations)"
            )
        }
        branch_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_task_branches)"
            )
        }
        assert "ix_agent_iterations_execution_id" in iteration_indexes
        assert "ix_agent_task_branches_task_resolution" in branch_indexes

        iteration_plan = _query_plan(
            connection,
            "SELECT * FROM agent_iterations "
            "WHERE execution_id = 'exec-hot' "
            "ORDER BY iteration ASC",
        )
        branch_plan = _query_plan(
            connection,
            "SELECT * FROM agent_task_branches "
            "WHERE task_id = 'task-hot' "
            "ORDER BY created_at ASC, branch_id ASC",
        )

        assert any(
            ITERATION_ORDER_INDEX in detail for detail in iteration_plan
        ), iteration_plan
        assert any(
            BRANCH_ORDER_INDEX in detail for detail in branch_plan
        ), branch_plan
        assert not _uses_temp_order(iteration_plan), iteration_plan
        assert not _uses_temp_order(branch_plan), branch_plan
    finally:
        connection.close()

    command.downgrade(config, "18a_cas_r0_assets")

    connection = sqlite3.connect(database)
    try:
        iteration_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_iterations)"
            )
        }
        branch_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_task_branches)"
            )
        }
        assert ITERATION_ORDER_INDEX not in iteration_indexes
        assert BRANCH_ORDER_INDEX not in branch_indexes
        assert "ix_agent_iterations_execution_id" in iteration_indexes
        assert "ix_agent_task_branches_task_resolution" in branch_indexes
    finally:
        connection.close()
