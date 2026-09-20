from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
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


def test_r6_a_migration_adds_reconciliation_fields_without_fake_certainty(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r6_a_reconciliation.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "11a_r5_task_budget")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO capability_invocations
                (
                    invocation_id,
                    capability_id,
                    kind,
                    execution_mode,
                    state,
                    arguments
                )
            VALUES
                (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-invocation",
                "tool.legacy",
                "TOOL",
                "ONE_SHOT",
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
        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(capability_invocations)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(capability_invocations)"
            )
        }
        legacy = connection.execute(
            """
            SELECT
                capability_version,
                idempotency,
                request_fingerprint,
                owner_user_id,
                origin_client_id,
                remote_outcome_state
            FROM capability_invocations
            WHERE invocation_id = 'legacy-invocation'
            """
        ).fetchone()

        assert {
            "capability_version",
            "idempotency",
            "request_fingerprint",
            "owner_user_id",
            "origin_client_id",
            "remote_outcome_state",
        }.issubset(columns)
        assert columns["idempotency"][3] == 1
        assert (
            str(columns["idempotency"][4]).strip("'\"")
            == "UNKNOWN"
        )
        assert {
            "ix_capability_invocations_owner_user_id",
            "ix_capability_invocations_origin_client_id",
            "ix_capability_invocations_remote_outcome_state",
        }.issubset(indexes)

        # Historical rows receive only the fail-safe idempotency default.
        # R6 must not invent remote delivery certainty or client provenance.
        assert legacy == (
            None,
            "UNKNOWN",
            None,
            None,
            None,
            None,
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE capability_invocations
                SET idempotency = 'UNSAFE_MAGIC'
                WHERE invocation_id = 'legacy-invocation'
                """
            )
    finally:
        connection.close()
