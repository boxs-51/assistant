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


def test_r8_e_14d_adds_nullable_seed_evidence_and_is_reversible(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r8_e_14d.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    command.upgrade(config, "14c_r8_fork_admission")
    command.upgrade(config, "14d_r8_fork_runtime_seed")

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(agent_task_fork_admissions)"
            )
        }
        assert columns["runtime_seed_json"][3] == 0
        assert columns["runtime_seed_fingerprint"][3] == 0
    finally:
        connection.close()

    command.downgrade(config, "14c_r8_fork_admission")
    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(agent_task_fork_admissions)"
            )
        }
        assert "runtime_seed_json" not in columns
        assert "runtime_seed_fingerprint" not in columns
        assert "plan_fingerprint" in columns
    finally:
        connection.close()
