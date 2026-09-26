from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository


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


def test_r12_b_migration_is_linear_and_preserves_legacy_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "r12_b_upgrade.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["22a_r12_execution_lease_fence"]
    assert (
        script.get_revision("22a_r12_execution_lease_fence").down_revision
        == "21a_ctx_f5_memory_foundation"
    )

    command.upgrade(config, "21a_ctx_f5_memory_foundation")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO agent_executions (
                id, session_id, agent_id, correlation_id,
                state, revision, current_checkpoint_id,
                remaining_active_budget_seconds, request
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "exec-r12-b-legacy",
                "session-r12-b",
                "agent-r12-b",
                "corr-r12-b",
                "RUNNING",
                9,
                "checkpoint-r12-b",
                17.5,
                "{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "22a_r12_execution_lease_fence")

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(agent_executions)")
        }
        assert "owner_instance_id" in columns
        assert "lease_expires_at" in columns
        assert "lease_generation" in columns
        assert columns["owner_instance_id"][3] == 0
        assert columns["lease_expires_at"][3] == 0
        assert columns["lease_generation"][3] == 1

        row = connection.execute(
            """
            SELECT state, revision, current_checkpoint_id,
                   remaining_active_budget_seconds,
                   owner_instance_id, lease_expires_at, lease_generation
            FROM agent_executions
            WHERE id = 'exec-r12-b-legacy'
            """
        ).fetchone()
        assert row == (
            "RUNNING",
            9,
            "checkpoint-r12-b",
            17.5,
            None,
            None,
            0,
        )

        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='agent_executions'"
        ).fetchone()[0]
        assert "ck_agent_executions_lease_owner_expiry_pair" in table_sql
        assert "ck_agent_executions_lease_generation_nonnegative" in table_sql
        assert "ck_agent_executions_lease_owner_generation_positive" in table_sql
    finally:
        connection.close()


def test_r12_b_migration_constraints_and_fail_closed_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "r12_b_constraints.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    command.upgrade(config, "22a_r12_execution_lease_fence")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO agent_executions (
                id, session_id, agent_id, correlation_id,
                state, revision, request
            )
            VALUES (?, ?, ?, ?, 'RUNNING', 0, ?)
            """,
            (
                "exec-r12-b-constraints",
                "session-r12-b",
                "agent-r12-b",
                "corr-r12-b",
                "{}",
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_executions
                SET owner_instance_id = 'worker-r12-b',
                    lease_generation = 1
                WHERE id = 'exec-r12-b-constraints'
                """
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_executions
                SET lease_generation = -1
                WHERE id = 'exec-r12-b-constraints'
                """
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_executions
                SET owner_instance_id = 'worker-r12-b',
                    lease_expires_at = '2026-09-27 01:00:00',
                    lease_generation = 0
                WHERE id = 'exec-r12-b-constraints'
                """
            )
        connection.rollback()

        connection.execute(
            """
            UPDATE agent_executions
            SET owner_instance_id = 'worker-r12-b',
                lease_expires_at = '2026-09-27 01:00:00',
                lease_generation = 1
            WHERE id = 'exec-r12-b-constraints'
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RuntimeError,
        match="durable execution lease/fence authority is non-default",
    ):
        command.downgrade(config, "21a_ctx_f5_memory_foundation")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            UPDATE agent_executions
            SET owner_instance_id = NULL,
                lease_expires_at = NULL,
                lease_generation = 0
            WHERE id = 'exec-r12-b-constraints'
            """
        )
        connection.commit()
    finally:
        connection.close()

    command.downgrade(config, "21a_ctx_f5_memory_foundation")

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(agent_executions)")
        }
        assert "owner_instance_id" not in columns
        assert "lease_expires_at" not in columns
        assert "lease_generation" not in columns
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_r12_b_generic_repository_round_trip_and_revision_cas() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AgentExecutionRecord.__table__.create)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    expiry = datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)
    try:
        async with sessions() as session:
            repository = AgentRepository(session)
            stored = await repository.save_execution(
                {
                    "id": "exec-r12-b-roundtrip",
                    "session_id": "session-r12-b",
                    "agent_id": "agent-r12-b",
                    "correlation_id": "corr-r12-b",
                    "state": "RUNNING",
                    "revision": 0,
                    "owner_instance_id": None,
                    "lease_expires_at": None,
                    "lease_generation": 0,
                    "request": {},
                }
            )
            assert stored.owner_instance_id is None
            assert stored.lease_expires_at is None
            assert stored.lease_generation == 0

            updated = await repository.compare_and_set_execution(
                "exec-r12-b-roundtrip",
                0,
                {
                    "owner_instance_id": "worker-r12-b",
                    "lease_expires_at": expiry,
                    "lease_generation": 1,
                },
            )
            assert updated is not None
            assert updated.revision == 1
            assert updated.owner_instance_id == "worker-r12-b"
            assert updated.lease_expires_at is not None
            assert updated.lease_generation == 1
            await session.commit()

        async with sessions() as session:
            repository = AgentRepository(session)
            loaded = await repository.get_execution("exec-r12-b-roundtrip")
            assert loaded is not None
            assert loaded.owner_instance_id == "worker-r12-b"
            assert loaded.lease_expires_at is not None
            assert loaded.lease_generation == 1

            stale = await repository.compare_and_set_execution(
                "exec-r12-b-roundtrip",
                0,
                {
                    "owner_instance_id": None,
                    "lease_expires_at": None,
                    "lease_generation": 1,
                },
            )
            assert stale is None
    finally:
        await engine.dispose()
