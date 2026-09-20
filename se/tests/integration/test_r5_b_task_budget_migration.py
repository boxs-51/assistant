from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


ROOT = Path(__file__).resolve().parents[3]


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


def test_r5_b_migration_adds_budget_schema_without_fake_legacy_backfill(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r5_b_budget.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "10a_r4_active_budget_wait_ttl")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO agent_tasks
                (id, session_id, created_by, assigned_agent_id, status, input)
            VALUES
                (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "session-legacy",
                "user-legacy",
                "agent-legacy",
                "RUNNING",
                "{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        task_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(agent_tasks)")
        }
        budget_columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(agent_task_budgets)"
            )
        }
        reservation_columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(agent_task_budget_reservations)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_task_budgets)"
            )
        }
        legacy_budget_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM agent_task_budgets
            WHERE task_id = 'legacy-task'
            """
        ).fetchone()[0]

        assert task_columns["revision"][3] == 1
        assert str(task_columns["revision"][4]).strip("'\"") == "0"
        assert {
            "task_id",
            "revision",
            "state",
            "max_total_executions",
            "max_active_executions",
            "max_active_branches",
            "max_parallel_agents",
            "max_total_tool_calls",
            "max_total_inference_calls",
            "max_total_tokens",
            "max_total_cost_usd",
            "max_delegation_depth",
            "policy_version",
            "policy_fingerprint",
            "deny_recursive_agent_cycle",
            "used_executions",
            "active_executions",
            "active_branches",
            "active_parallel_agents",
            "used_tool_calls",
            "used_inference_calls",
            "used_tokens",
            "used_cost_usd",
            "closed_at",
        }.issubset(budget_columns)
        assert {
            "task_id",
            "kind",
            "reservation_key",
            "payload_fingerprint",
        }.issubset(reservation_columns)
        assert "ix_agent_task_budgets_state" in indexes
        assert legacy_budget_count == 0
    finally:
        connection.close()
