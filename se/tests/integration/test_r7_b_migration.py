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


def test_r7_b_migration_completes_pending_invocation_semantic_watermark(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r7_b_snapshot.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)
    command.upgrade(config, "13a_r7_durable_resume")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(agent_checkpoint_pending_invocations)"
            )
        }
        assert {
            "capability_version", "request_fingerprint", "idempotency",
            "observed_remote_outcome_state", "origin_client_id",
            "origin_connection_id",
        }.issubset(columns)
        assert columns["idempotency"][3] == 1
        assert str(columns["idempotency"][4]).strip("'\"") == "UNKNOWN"
    finally:
        connection.close()
