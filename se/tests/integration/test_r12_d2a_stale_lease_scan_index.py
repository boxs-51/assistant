from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base


ROOT = Path(__file__).resolve().parents[3]
INDEX_NAME = "ix_agent_executions_state_lease_expiry_id"
INDEX_COLUMNS = ("state", "lease_expires_at", "id")


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


def _index_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, ...], bool]:
    index_rows = {
        row[1]: row
        for row in connection.execute("PRAGMA index_list(agent_executions)")
    }
    assert INDEX_NAME in index_rows
    columns = tuple(
        row[2]
        for row in connection.execute(f"PRAGMA index_info({INDEX_NAME})")
    )
    unique = bool(index_rows[INDEX_NAME][2])
    return columns, unique


def _index_absent(connection: sqlite3.Connection) -> bool:
    names = {
        row[1]
        for row in connection.execute("PRAGMA index_list(agent_executions)")
    }
    return INDEX_NAME not in names


def _query_plan(connection: sqlite3.Connection, sql: str) -> list[str]:
    return [
        str(row[3])
        for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}")
    ]


def _assert_scan_plan(plan: list[str]) -> None:
    upper = [detail.upper() for detail in plan]
    assert any(INDEX_NAME.upper() in detail for detail in upper), plan
    assert not any(
        "SCAN AGENT_EXECUTIONS" in detail
        and INDEX_NAME.upper() not in detail
        for detail in upper
    ), plan
    assert not any("USE TEMP B-TREE FOR ORDER BY" in detail for detail in upper), plan


def _first_page_sql() -> str:
    return (
        "SELECT id FROM agent_executions "
        "WHERE state = 'RUNNING' "
        "AND owner_instance_id IS NOT NULL "
        "AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at <= '2026-09-27 12:00:00.000000' "
        "ORDER BY lease_expires_at ASC, id ASC "
        "LIMIT 10"
    )


def _continuation_sql() -> str:
    return (
        "SELECT id FROM agent_executions "
        "WHERE state = 'RUNNING' "
        "AND owner_instance_id IS NOT NULL "
        "AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at <= '2026-09-27 12:00:00.000000' "
        "AND ("
        "lease_expires_at > '2026-09-27 10:00:00.000000' "
        "OR (lease_expires_at = '2026-09-27 10:00:00.000000' "
        "AND id > 'exec-r12-d2a-active'))"
        " ORDER BY lease_expires_at ASC, id ASC "
        "LIMIT 10"
    )


def test_r12_d2a_metadata_schema_exposes_exact_index_and_query_plan(
    tmp_path: Path,
) -> None:
    database = tmp_path / "r12_d2a_metadata.sqlite"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    connection = sqlite3.connect(database)
    try:
        assert _index_signature(connection) == (INDEX_COLUMNS, False)
        _assert_scan_plan(_query_plan(connection, _first_page_sql()))
        _assert_scan_plan(_query_plan(connection, _continuation_sql()))
    finally:
        connection.close()


def test_r12_d2a_migration_is_linear_and_preserves_lease_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "r12_d2a_migration.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["26a_ubq2_dual_accounting_bridge"]
    assert (
        script.get_revision("24a_r12_stale_lease_scan_index").down_revision
        == "23a_ctx_f5_promotion_reservation"
    )

    command.upgrade(config, "23a_ctx_f5_promotion_reservation")

    expected_row = (
        "exec-r12-d2a-active",
        "RUNNING",
        11,
        "worker-r12-d2a",
        "2026-09-27 10:00:00.000000",
        4,
        "checkpoint-r12-d2a",
        19.5,
        "{}",
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO agent_executions (
                id, session_id, agent_id, correlation_id,
                state, revision,
                owner_instance_id, lease_expires_at, lease_generation,
                current_checkpoint_id, remaining_active_budget_seconds,
                request
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expected_row[0],
                "session-r12-d2a",
                "agent-r12-d2a",
                "corr-r12-d2a",
                expected_row[1],
                expected_row[2],
                expected_row[3],
                expected_row[4],
                expected_row[5],
                expected_row[6],
                expected_row[7],
                expected_row[8],
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "24a_r12_stale_lease_scan_index")

    connection = sqlite3.connect(database)
    try:
        assert _index_signature(connection) == (INDEX_COLUMNS, False)
        row = connection.execute(
            """
            SELECT id, state, revision,
                   owner_instance_id, lease_expires_at, lease_generation,
                   current_checkpoint_id, remaining_active_budget_seconds,
                   request
            FROM agent_executions
            WHERE id = 'exec-r12-d2a-active'
            """
        ).fetchone()
        assert row == expected_row
        _assert_scan_plan(_query_plan(connection, _first_page_sql()))
        _assert_scan_plan(_query_plan(connection, _continuation_sql()))
    finally:
        connection.close()

    command.downgrade(config, "23a_ctx_f5_promotion_reservation")

    connection = sqlite3.connect(database)
    try:
        assert _index_absent(connection)
        row = connection.execute(
            """
            SELECT id, state, revision,
                   owner_instance_id, lease_expires_at, lease_generation,
                   current_checkpoint_id, remaining_active_budget_seconds,
                   request
            FROM agent_executions
            WHERE id = 'exec-r12-d2a-active'
            """
        ).fetchone()
        assert row == expected_row
    finally:
        connection.close()
