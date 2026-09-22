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


def test_f2h_migration_adds_r7_reference_and_declared_mime_without_new_head(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "f2h_assets.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "f1a_central_asset_storage")
    command.upgrade(config, "f2h_r7_asset_compat")

    connection = sqlite3.connect(database)
    try:
        blob_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(file_blobs)")
        }
        reference_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(file_references)")
        }
        assert "declared_mime_type" in blob_columns
        assert "agent_tool_result_id" in reference_columns

        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(file_references)")
        }
        assert "ix_file_references_agent_tool_result_id" in indexes

        foreign_keys = list(
            connection.execute("PRAGMA foreign_key_list(file_references)")
        )
        assert any(
            row[2] == "agent_tool_results"
            and row[3] == "agent_tool_result_id"
            and row[4] == "id"
            for row in foreign_keys
        )
    finally:
        connection.close()

    command.downgrade(config, "f1a_central_asset_storage")
    connection = sqlite3.connect(database)
    try:
        blob_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(file_blobs)")
        }
        reference_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(file_references)")
        }
        assert "declared_mime_type" not in blob_columns
        assert "agent_tool_result_id" not in reference_columns
    finally:
        connection.close()
