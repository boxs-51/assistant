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


def test_f1_expand_migration_adds_assets_without_dropping_legacy_attachments(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "f1_assets.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "17a_r11_checkpoint_cutover")
    command.upgrade(config, "18a_cas_r0_assets")

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "attachments",
            "file_blobs",
            "files",
            "file_references",
            "file_provider_bindings",
        }.issubset(tables)

        files_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(files)")
        }
        assert {
            "owner_user_id",
            "blob_id",
            "origin_type",
            "state",
            "revision",
        }.issubset(files_columns)

        reference_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(file_references)"
            )
        }
        assert {
            "file_id",
            "reference_type",
            "message_id",
            "session_id",
            "project_id",
            "content_part_index",
        }.issubset(reference_columns)
    finally:
        connection.close()

    command.downgrade(config, "17a_r11_checkpoint_cutover")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "attachments" in tables
        assert "files" not in tables
        assert "file_blobs" not in tables
        assert "file_references" not in tables
        assert "file_provider_bindings" not in tables
    finally:
        connection.close()
