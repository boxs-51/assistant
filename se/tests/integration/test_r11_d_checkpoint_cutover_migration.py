from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


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


def test_r11_d_is_single_linear_migration_head(tmp_path: Path):
    script = ScriptDirectory.from_config(_config(tmp_path / "unused.sqlite"))
    assert script.get_heads() == ["17a_r11_checkpoint_cutover"]
    assert (
        script.get_revision("17a_r11_checkpoint_cutover").down_revision
        == "16a_r11_transcript_representation"
    )


def test_r11_d_migration_adds_ref_pair_constraint_and_is_reversible(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r11_d.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)

    command.upgrade(config, "16a_r11_transcript_representation")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='agent_execution_checkpoints'"
        ).fetchone()[0]
        assert "ck_agent_execution_checkpoints_transcript_ref_pair" in sql
        assert "transcript_ref IS NULL AND transcript_version IS NULL" in sql
        assert "transcript_ref IS NOT NULL AND transcript_version IS NOT NULL" in sql
    finally:
        connection.close()

    command.downgrade(config, "16a_r11_transcript_representation")

    connection = sqlite3.connect(database)
    try:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='agent_execution_checkpoints'"
        ).fetchone()[0]
        assert "ck_agent_execution_checkpoints_transcript_ref_pair" not in sql
    finally:
        connection.close()
