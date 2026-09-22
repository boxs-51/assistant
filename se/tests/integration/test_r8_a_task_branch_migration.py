from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
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


def _upgrade_to_r7(database: Path, monkeypatch) -> Config:
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)
    command.upgrade(config, "13b_r7_pending_snapshot")
    return config


def _task(connection, task_id: str, *, status: str = "RUNNING"):
    connection.execute(
        """
        INSERT INTO agent_tasks (
            id,
            session_id,
            created_by,
            assigned_agent_id,
            revision,
            status,
            wait_reasons,
            input
        )
        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            task_id,
            f"session-{task_id}",
            f"user-{task_id}",
            "agent-r8-a",
            status,
            "[]",
            "{}",
        ),
    )


def _execution(
    connection,
    execution_id: str,
    *,
    task_id: str | None,
    branch_id: str | None,
    parent_execution_id: str | None = None,
    state: str = "RUNNING",
    current_checkpoint_id: str | None = None,
):
    connection.execute(
        """
        INSERT INTO agent_executions (
            id,
            session_id,
            agent_id,
            task_id,
            branch_id,
            parent_execution_id,
            correlation_id,
            state,
            wait_reason,
            revision,
            current_checkpoint_id,
            request
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            execution_id,
            f"session-{task_id or 'none'}",
            "agent-r8-a",
            task_id,
            branch_id,
            parent_execution_id,
            f"corr-{execution_id}",
            state,
            "CONNECTION" if state == "WAITING" else None,
            current_checkpoint_id,
            "{}",
        ),
    )


def _checkpoint(
    connection,
    checkpoint_id: str,
    execution_id: str,
    *,
    task_id: str | None,
    branch_id: str | None,
):
    connection.execute(
        """
        INSERT INTO agent_execution_checkpoints (
            checkpoint_id,
            execution_id,
            execution_revision,
            session_id,
            task_id,
            branch_id,
            iteration,
            wait_reason,
            transcript_snapshot,
            metadata
        )
        VALUES (?, ?, 0, ?, ?, ?, 0, 'CONNECTION', ?, ?)
        """,
        (
            checkpoint_id,
            execution_id,
            f"session-{task_id or 'none'}",
            task_id,
            branch_id,
            "[]",
            "{}",
        ),
    )


def _legacy_id(task_id: str) -> str:
    return "r8_legacy_" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:40]


def test_r8_a_has_one_migration_head(tmp_path: Path):
    config = _config(tmp_path / "unused.sqlite")
    assert ScriptDirectory.from_config(config).get_heads() == ["14a_r8_task_branch"]


def test_r8_a_backfills_cases_a_b_c_and_downgrades_losslessly(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r8_a_backfill.sqlite"
    config = _upgrade_to_r7(database, monkeypatch)

    connection = sqlite3.connect(database)
    try:
        # Case A: no execution history -> no fabricated branch.
        _task(connection, "task-a", status="ASSIGNED")

        # Case B: all historical branch IDs are NULL. Include a delegated child
        # so current_execution_id must point to the only top-level execution.
        _task(connection, "task-b")
        _execution(
            connection,
            "exec-b",
            task_id="task-b",
            branch_id=None,
            state="WAITING",
            current_checkpoint_id="cp-b",
        )
        _execution(
            connection,
            "exec-b-child",
            task_id="task-b",
            branch_id=None,
            parent_execution_id="exec-b",
        )
        _checkpoint(
            connection,
            "cp-b",
            "exec-b",
            task_id="task-b",
            branch_id=None,
        )

        # Case C: preserve the one pre-existing branch ID and normalize NULL
        # sibling execution/checkpoint rows into that same root branch.
        _task(connection, "task-c")
        _execution(
            connection,
            "exec-c",
            task_id="task-c",
            branch_id="branch-existing",
            state="WAITING",
            current_checkpoint_id="cp-c",
        )
        _checkpoint(
            connection,
            "cp-c",
            "exec-c",
            task_id="task-c",
            branch_id="branch-existing",
        )
        _execution(
            connection,
            "exec-c-child",
            task_id="task-c",
            branch_id=None,
            parent_execution_id="exec-c",
            state="WAITING",
            current_checkpoint_id="cp-c-child",
        )
        _checkpoint(
            connection,
            "cp-c-child",
            "exec-c-child",
            task_id="task-c",
            branch_id=None,
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        branch_b = _legacy_id("task-b")
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_task_branches WHERE task_id = 'task-a'"
        ).fetchone()[0] == 0

        b_row = connection.execute(
            """
            SELECT branch_id, current_execution_id, resolution_state, reason
            FROM agent_task_branches
            WHERE task_id = 'task-b'
            """
        ).fetchone()
        assert b_row == (
            branch_b,
            "exec-b",
            "OPEN",
            "R8_MIGRATION_SYNTHESIZED_ROOT",
        )
        assert {
            row[0]
            for row in connection.execute(
                "SELECT branch_id FROM agent_executions WHERE task_id = 'task-b'"
            )
        } == {branch_b}
        assert connection.execute(
            "SELECT branch_id FROM agent_execution_checkpoints WHERE checkpoint_id = 'cp-b'"
        ).fetchone()[0] == branch_b

        c_row = connection.execute(
            """
            SELECT branch_id, current_execution_id, reason
            FROM agent_task_branches
            WHERE task_id = 'task-c'
            """
        ).fetchone()
        assert c_row == (
            "branch-existing",
            "exec-c",
            "R8_MIGRATION_NORMALIZED_EXISTING_BRANCH",
        )
        assert {
            row[0]
            for row in connection.execute(
                "SELECT branch_id FROM agent_executions WHERE task_id = 'task-c'"
            )
        } == {"branch-existing"}
        assert {
            row[0]
            for row in connection.execute(
                """
                SELECT branch_id
                FROM agent_execution_checkpoints
                WHERE task_id = 'task-c'
                """
            )
        } == {"branch-existing"}

        assert connection.execute(
            """
            SELECT overlay_messages
            FROM agent_task_branch_contexts
            WHERE branch_id = ?
            """,
            (branch_b,),
        ).fetchone()[0] == "[]"
    finally:
        connection.close()

    command.downgrade(config, "13b_r7_pending_snapshot")

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT branch_id FROM agent_executions WHERE id = 'exec-b'"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT branch_id FROM agent_execution_checkpoints WHERE checkpoint_id = 'cp-b'"
        ).fetchone()[0] is None

        assert connection.execute(
            "SELECT branch_id FROM agent_executions WHERE id = 'exec-c'"
        ).fetchone()[0] == "branch-existing"
        assert connection.execute(
            "SELECT branch_id FROM agent_execution_checkpoints WHERE checkpoint_id = 'cp-c'"
        ).fetchone()[0] == "branch-existing"

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "agent_task_branches" not in tables
        assert "agent_task_branch_contexts" not in tables
    finally:
        connection.close()


@pytest.mark.parametrize(
    "case",
    [
        "multiple-branches",
        "taskless-branch",
        "cross-task-branch",
        "checkpoint-mismatch",
    ],
)
def test_r8_a_backfill_fails_closed_on_ambiguous_historical_authority(
    tmp_path: Path,
    monkeypatch,
    case: str,
):
    database = tmp_path / f"r8_a_conflict_{case}.sqlite"
    config = _upgrade_to_r7(database, monkeypatch)

    connection = sqlite3.connect(database)
    try:
        if case == "multiple-branches":
            _task(connection, "task-d")
            _execution(
                connection,
                "exec-d1",
                task_id="task-d",
                branch_id="branch-d1",
            )
            _execution(
                connection,
                "exec-d2",
                task_id="task-d",
                branch_id="branch-d2",
            )
        elif case == "taskless-branch":
            _execution(
                connection,
                "exec-taskless",
                task_id=None,
                branch_id="branch-invalid",
            )
        elif case == "cross-task-branch":
            _task(connection, "task-x1")
            _task(connection, "task-x2")
            _execution(
                connection,
                "exec-x1",
                task_id="task-x1",
                branch_id="branch-shared",
            )
            _execution(
                connection,
                "exec-x2",
                task_id="task-x2",
                branch_id="branch-shared",
            )
        elif case == "checkpoint-mismatch":
            _task(connection, "task-m")
            _execution(
                connection,
                "exec-m",
                task_id="task-m",
                branch_id="branch-m",
                state="WAITING",
                current_checkpoint_id="cp-m",
            )
            _checkpoint(
                connection,
                "cp-m",
                "exec-m",
                task_id="task-m",
                branch_id=None,
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="R8_BRANCH_BACKFILL_CONFLICT"):
        command.upgrade(config, "head")
