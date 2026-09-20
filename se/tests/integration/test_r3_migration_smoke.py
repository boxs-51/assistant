from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


ROOT = Path(__file__).resolve().parents[3]


def test_r3_alembic_upgrade_head_contains_lineage_columns_and_indexes(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r3_migration.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
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
    finally:
        connection.close()

    assert {
        "branch_id",
        "retry_of_execution_id",
        "base_execution_id",
        "base_checkpoint_id",
    } <= columns
    assert {
        "ix_agent_executions_branch_id",
        "ix_agent_executions_retry_of_execution_id",
        "ix_agent_executions_base_execution_id",
    } <= indexes
