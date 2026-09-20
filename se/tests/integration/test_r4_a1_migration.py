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


def _schema(database: Path):
    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(agent_executions)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_executions)"
            )
        }
        return columns, indexes
    finally:
        connection.close()


def test_r4_a1_alembic_upgrade_adds_nullable_budget_wait_fields_and_index(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r4_a1_migration.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)

    config = _config(database)
    command.upgrade(config, "head")

    columns, indexes = _schema(database)

    assert "remaining_active_budget_seconds" in columns
    assert "wait_expires_at" in columns
    assert columns["remaining_active_budget_seconds"][3] == 0
    assert columns["wait_expires_at"][3] == 0
    assert "ix_agent_executions_wait_expires_at" in indexes
