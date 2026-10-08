from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import AgentExecution
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository


ROOT = Path(__file__).resolve().parents[3]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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

    assert (
        (mh0_heads := script.get_heads())
        and len(mh0_heads) == 1
        and script.get_bases() == ["2b8eaa45108e"]
        and (
            mh0_revisions := tuple(
                script.walk_revisions(base="base", head=mh0_heads[0])
            )
        )
        and all(not rev.dependencies for rev in mh0_revisions)
        and (
            mh0_parents := {
                rev.revision: (
                    rev.down_revision
                    if isinstance(rev.down_revision, tuple)
                    else (rev.down_revision,) if rev.down_revision else ()
                )
                for rev in mh0_revisions
            }
        )
        and len(mh0_revisions) == len(mh0_parents)
        and mh0_heads[0] in mh0_parents
        and "30a_ctx_f5_user_wide_memory_scope" in mh0_parents
        and mh0_parents.get("4f_phase4_multi_agent") == ("2b8eaa45108e",)
        and mh0_parents.get("5a_phase5_9_execution_resume")
        == ("4f_phase4_multi_agent",)
        and mh0_parents.get("896c456631dd") == ("4f_phase4_multi_agent",)
        and mh0_parents.get("5b_conversation_temporal_contract")
        == ("5a_phase5_9_execution_resume", "896c456631dd")
        and all(
            len(parents)
            == (
                2 if revision == "5b_conversation_temporal_contract"
                else 0 if revision == "2b8eaa45108e"
                else 1
            )
            and all(parent in mh0_parents for parent in parents)
            for revision, parents in mh0_parents.items()
        )
        and all(
            sum(revision in parents for parents in mh0_parents.values())
            == (
                0 if revision == mh0_heads[0]
                else 2 if revision == "4f_phase4_multi_agent"
                else 1
            )
            for revision in mh0_parents
        )
    )
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
            assert _as_utc(updated.lease_expires_at) == expiry
            assert updated.lease_generation == 1
            await session.commit()

        async with sessions() as session:
            repository = AgentRepository(session)
            loaded = await repository.get_execution("exec-r12-b-roundtrip")
            assert loaded is not None
            assert loaded.owner_instance_id == "worker-r12-b"
            assert loaded.lease_expires_at is not None
            assert _as_utc(loaded.lease_expires_at) == expiry
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


def test_r12_b_domain_normalizes_aware_lease_expiry_to_utc_and_rejects_naive():
    offset_expiry = datetime(
        2026,
        9,
        27,
        8,
        0,
        tzinfo=timezone(timedelta(hours=7)),
    )
    execution = AgentExecution(
        execution_id="exec-r12-b-domain",
        session_id="session-r12-b",
        agent_id="agent-r12-b",
        correlation_id="corr-r12-b",
        state="RUNNING",
        owner_instance_id="worker-r12-b",
        lease_expires_at=offset_expiry,
        lease_generation=1,
        created_at=0.0,
        updated_at=0.0,
    )
    assert execution.lease_expires_at == datetime(
        2026,
        9,
        27,
        1,
        0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        AgentExecution(
            execution_id="exec-r12-b-domain-naive",
            session_id="session-r12-b",
            agent_id="agent-r12-b",
            correlation_id="corr-r12-b-naive",
            state="RUNNING",
            owner_instance_id="worker-r12-b",
            lease_expires_at=datetime(2026, 9, 27, 1, 0),
            lease_generation=1,
            created_at=0.0,
            updated_at=0.0,
        )


@pytest.mark.asyncio
async def test_r12_b_generic_repository_normalizes_non_utc_and_rejects_naive():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AgentExecutionRecord.__table__.create)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    local_expiry = datetime(
        2026,
        9,
        27,
        8,
        0,
        tzinfo=timezone(timedelta(hours=7)),
    )
    expected_utc = datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)
    try:
        async with sessions() as session:
            repository = AgentRepository(session)
            stored = await repository.save_execution(
                {
                    "id": "exec-r12-b-offset",
                    "session_id": "session-r12-b",
                    "agent_id": "agent-r12-b",
                    "correlation_id": "corr-r12-b-offset",
                    "state": "RUNNING",
                    "revision": 0,
                    "owner_instance_id": "worker-r12-b",
                    "lease_expires_at": local_expiry,
                    "lease_generation": 1,
                    "request": {},
                }
            )
            assert stored.lease_expires_at == expected_utc
            await session.commit()

        async with sessions() as session:
            repository = AgentRepository(session)
            loaded = await repository.get_execution("exec-r12-b-offset")
            assert loaded is not None
            assert loaded.lease_expires_at == expected_utc

        async with sessions() as session:
            repository = AgentRepository(session)
            with pytest.raises(StatementError, match="timezone-aware"):
                await repository.save_execution(
                    {
                        "id": "exec-r12-b-naive",
                        "session_id": "session-r12-b",
                        "agent_id": "agent-r12-b",
                        "correlation_id": "corr-r12-b-naive",
                        "state": "RUNNING",
                        "revision": 0,
                        "owner_instance_id": "worker-r12-b",
                        "lease_expires_at": datetime(2026, 9, 27, 1, 0),
                        "lease_generation": 1,
                        "request": {},
                    }
                )
            await session.rollback()
    finally:
        await engine.dispose()
