from __future__ import annotations

import json
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


def test_r7_a_migration_adds_normalized_schema_without_fake_resume_certainty(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r7_a_durable_resume.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "12a_r6_remote_reconciliation")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO agent_executions
                (
                    id,
                    session_id,
                    agent_id,
                    correlation_id,
                    state,
                    wait_reason,
                    revision,
                    request,
                    context_state
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-r7-exec",
                "session-r7",
                "agent-r7",
                "corr-r7",
                "WAITING",
                "CONNECTION",
                4,
                "{}",
                json.dumps(
                    {
                        "continuation": {
                            "checkpoint_id": "legacy-checkpoint",
                            "pending_invocation_id": "legacy-invocation",
                        }
                    }
                ),
            ),
        )

        connection.execute(
            """
            INSERT INTO agent_iterations
                (id, execution_id, iteration, state, tool_call_ids)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "legacy-iteration",
                "legacy-r7-exec",
                1,
                "WAITING_TOOL",
                "[]",
            ),
        )

        rows = [
            (
                "result-success",
                True,
                None,
                {},
            ),
            (
                "result-known-error",
                False,
                "CAPABILITY_EXECUTION_FAILED",
                {},
            ),
            (
                "result-unknown",
                False,
                "REMOTE_OUTCOME_UNKNOWN",
                {},
            ),
            (
                "result-normalized-loss",
                False,
                "CAPABILITY_EXECUTION_FAILED",
                {"original_error_code": "REMOTE_CONNECTION_LOST"},
            ),
        ]
        for result_id, success, error_code, metadata in rows:
            connection.execute(
                """
                INSERT INTO agent_tool_results
                    (
                        id,
                        execution_id,
                        iteration_id,
                        tool_call_id,
                        invocation_id,
                        capability_id,
                        success,
                        error_code,
                        retryable,
                        metadata,
                        attempt
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    "legacy-r7-exec",
                    "legacy-iteration",
                    f"call-{result_id}",
                    f"inv-{result_id}",
                    "tool.remote",
                    int(success),
                    error_code,
                    0,
                    json.dumps(metadata),
                    1,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        execution_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(agent_executions)")
        }
        result_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(agent_tool_results)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        execution_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(agent_executions)")
        }
        result_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(agent_tool_results)")
        }

        assert {
            "current_checkpoint_id",
            "bound_client_id",
            "bound_connection_id",
        }.issubset(execution_columns)
        assert execution_columns["current_checkpoint_id"][3] == 0
        assert execution_columns["bound_client_id"][3] == 0
        assert execution_columns["bound_connection_id"][3] == 0
        assert {
            "ix_agent_executions_current_checkpoint_id",
            "ix_agent_executions_bound_client_id",
            "ix_agent_executions_bound_connection_id",
        }.issubset(execution_indexes)

        assert "commit_state" in result_columns
        assert result_columns["commit_state"][3] == 1
        assert str(result_columns["commit_state"][4]).strip("'\"") == "PROVISIONAL"
        assert "ix_agent_tool_results_commit_state" in result_indexes

        assert {
            "agent_execution_checkpoints",
            "agent_checkpoint_pending_invocations",
            "agent_resume_claims",
        }.issubset(tables)

        legacy_execution = connection.execute(
            """
            SELECT current_checkpoint_id, bound_client_id, bound_connection_id
            FROM agent_executions
            WHERE id = 'legacy-r7-exec'
            """
        ).fetchone()
        assert legacy_execution == (None, None, None)

        # R7-A creates representation only.  Legacy continuation materialization
        # is runtime work in a later phase and migration must not invent it.
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_execution_checkpoints"
        ).fetchone()[0] == 0

        states = dict(
            connection.execute(
                "SELECT id, commit_state FROM agent_tool_results"
            ).fetchall()
        )
        assert states["result-success"] == "COMMITTED"
        assert states["result-known-error"] == "COMMITTED"
        assert states["result-unknown"] == "PROVISIONAL"
        assert states["result-normalized-loss"] == "PROVISIONAL"

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_tool_results
                SET commit_state = 'FAKE_CERTAINTY'
                WHERE id = 'result-unknown'
                """
            )
    finally:
        connection.close()
