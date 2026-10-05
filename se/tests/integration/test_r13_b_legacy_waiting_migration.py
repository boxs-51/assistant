from __future__ import annotations

import json
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


def test_r13b_8a_normalizes_real_legacy_waiting_rows_without_recreation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "r13b_legacy_waiting.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "7a_connection_affinity")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO agent_tasks (
                id, session_id, created_by, assigned_agent_id,
                status, input, output, error, connection_id, client_id
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
            """,
            (
                "task-r13b-legacy",
                "session-r13b",
                "user-r13b",
                "agent-r13b",
                "WAITING_FOR_CONNECTION",
                json.dumps({"kind": "legacy"}),
            ),
        )
        connection.executemany(
            """
            INSERT INTO agent_executions (
                id, session_id, agent_id, task_id, parent_execution_id,
                correlation_id, state, request, result, error
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL)
            """,
            [
                (
                    "exec-r13b-connection",
                    "session-r13b",
                    "agent-r13b",
                    "task-r13b-legacy",
                    "corr-r13b-connection",
                    "WAITING_FOR_CONNECTION",
                    json.dumps({"kind": "legacy-connection"}),
                ),
                (
                    "exec-r13b-agent",
                    "session-r13b",
                    "agent-r13b",
                    "task-r13b-legacy",
                    "corr-r13b-agent",
                    "WAITING_AGENT",
                    json.dumps({"kind": "legacy-agent"}),
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "8a_agent_execution_waiting_cas")

    connection = sqlite3.connect(database)
    try:
        task = connection.execute(
            """
            SELECT id, status, wait_reasons
            FROM agent_tasks
            WHERE id = 'task-r13b-legacy'
            """
        ).fetchone()
        executions = connection.execute(
            """
            SELECT id, state, wait_reason
            FROM agent_executions
            WHERE id IN ('exec-r13b-connection', 'exec-r13b-agent')
            ORDER BY id
            """
        ).fetchall()
        task_legacy_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM agent_tasks
            WHERE status = 'WAITING_FOR_CONNECTION'
            """
        ).fetchone()[0]
        execution_legacy_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM agent_executions
            WHERE state IN ('WAITING_FOR_CONNECTION', 'WAITING_AGENT')
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert task is not None
    assert task[0] == "task-r13b-legacy"
    assert task[1] == "WAITING"
    assert json.loads(task[2]) == ["CONNECTION"]

    assert executions == [
        ("exec-r13b-agent", "WAITING", "AGENT"),
        ("exec-r13b-connection", "WAITING", "CONNECTION"),
    ]
    assert task_legacy_count == 0
    assert execution_legacy_count == 0
