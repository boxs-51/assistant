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
    assert script.get_heads() == ["21a_ctx_f5_memory_foundation"]
    assert (
        script.get_revision("21a_ctx_f5_memory_foundation").down_revision
        == "20a_cas_f5_binding_foundation"
    )
    assert (
        script.get_revision("20a_cas_f5_binding_foundation").down_revision
        == "19a_r11_query_order_indexes"
    )
    assert (
        script.get_revision("19a_r11_query_order_indexes").down_revision
        == "18a_cas_r0_assets"
    )
    assert (
        script.get_revision("18a_cas_r0_assets").down_revision
        == "17a_r11_checkpoint_cutover"
    )
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


def test_r11_d_populated_upgrade_preserves_checkpoint_and_pending_child(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r11_d_populated.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    command.upgrade(config, "16a_r11_transcript_representation")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO agent_executions (
                id, session_id, agent_id, correlation_id,
                state, revision, request
            )
            VALUES (?, ?, ?, ?, 'WAITING', 1, ?)
            """,
            ("exec-r11-d-mig", "session-r11-d-mig", "agent-r11-d", "corr-r11-d", "{}"),
        )
        connection.execute(
            """
            INSERT INTO agent_execution_checkpoints (
                checkpoint_id,
                execution_id,
                execution_revision,
                session_id,
                iteration,
                wait_reason,
                transcript_snapshot,
                metadata
            )
            VALUES (?, ?, 1, ?, 0, 'CONNECTION', ?, ?)
            """,
            (
                "cp-r11-d-mig",
                "exec-r11-d-mig",
                "session-r11-d-mig",
                '[{"role":"user","content":"hello"}]',
                "{}",
            ),
        )
        connection.execute(
            """
            INSERT INTO agent_checkpoint_pending_invocations (
                checkpoint_id,
                ordinal,
                invocation_id,
                invocation_revision,
                tool_call_id,
                capability_id,
                idempotency
            )
            VALUES (?, 0, ?, 0, ?, ?, 'UNKNOWN')
            """,
            (
                "cp-r11-d-mig",
                "inv-r11-d-mig",
                "call-r11-d-mig",
                "tool.r11-d-mig",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        checkpoint = connection.execute(
            """
            SELECT checkpoint_id, transcript_snapshot, transcript_ref, transcript_version
            FROM agent_execution_checkpoints
            WHERE checkpoint_id = 'cp-r11-d-mig'
            """
        ).fetchone()
        assert checkpoint[0] == "cp-r11-d-mig"
        assert checkpoint[1] == '[{"role":"user","content":"hello"}]'
        assert checkpoint[2:] == (None, None)

        pending = connection.execute(
            """
            SELECT checkpoint_id, invocation_id, tool_call_id, capability_id
            FROM agent_checkpoint_pending_invocations
            WHERE checkpoint_id = 'cp-r11-d-mig'
            """
        ).fetchone()
        assert pending == (
            "cp-r11-d-mig",
            "inv-r11-d-mig",
            "call-r11-d-mig",
            "tool.r11-d-mig",
        )

        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
