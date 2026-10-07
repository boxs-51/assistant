from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[3]
TBO1_REVISION = "28a_tbo1_task_policy_representation"
PREVIOUS_REVISION = "27a_cas_f7_t_tool_media_projection"


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


def _legacy_task(connection: sqlite3.Connection):
    connection.execute(
        """
        INSERT INTO agent_tasks (
            id,
            session_id,
            created_by,
            assigned_agent_id,
            revision,
            status,
            wait_reasons,
            input
        )
        VALUES (?, ?, ?, ?, 0, 'ASSIGNED', ?, ?)
        """,
        (
            "task-tbo1-legacy",
            "session-tbo1",
            "user-tbo1",
            "agent-tbo1",
            "[]",
            "{}",
        ),
    )
    connection.commit()


def test_tbo1_is_the_single_linear_migration_head(tmp_path: Path):
    config = _config(tmp_path / "unused.sqlite")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["29a_crt1_capability_invocation_target"]
    assert script.get_revision(TBO1_REVISION).down_revision == PREVIOUS_REVISION


def test_tbo1_migration_preserves_legacy_tasks_and_is_reversible(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "tbo1.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, PREVIOUS_REVISION)

    connection = sqlite3.connect(database)
    try:
        _legacy_task(connection)
    finally:
        connection.close()

    command.upgrade(config, TBO1_REVISION)

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(agent_tasks)")
        }
        assert {
            "task_mode",
            "task_horizon_at",
            "review_horizon_at",
        }.issubset(columns)

        row = connection.execute(
            """
            SELECT task_mode, task_horizon_at, review_horizon_at
            FROM agent_tasks
            WHERE id = 'task-tbo1-legacy'
            """
        ).fetchone()
        assert row == ("FINITE", None, None)

        connection.execute(
            """
            UPDATE agent_tasks
            SET task_mode = 'RECURRING',
                task_horizon_at = 500.0,
                review_horizon_at = 250.0
            WHERE id = 'task-tbo1-legacy'
            """
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_tasks
                SET task_mode = 'UNKNOWN'
                WHERE id = 'task-tbo1-legacy'
                """
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_tasks
                SET task_horizon_at = -1
                WHERE id = 'task-tbo1-legacy'
                """
            )
        connection.rollback()
    finally:
        connection.close()

    command.downgrade(config, PREVIOUS_REVISION)

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(agent_tasks)")
        }
        assert "task_mode" not in columns
        assert "task_horizon_at" not in columns
        assert "review_horizon_at" not in columns
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM agent_tasks
            WHERE id = 'task-tbo1-legacy'
            """
        ).fetchone()[0] == 1
    finally:
        connection.close()
