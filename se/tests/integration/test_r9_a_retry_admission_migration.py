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


def test_r9_a_15a_is_single_head_after_r8(tmp_path: Path):
    config = _config(tmp_path / "unused.sqlite")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["22a_r12_execution_lease_fence"]
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
    assert (
        script.get_revision("16a_r11_transcript_representation").down_revision
        == "15b_r9_aggregate_admission"
    )
    assert (
        script.get_revision("15b_r9_aggregate_admission").down_revision
        == "15a_r9_retry_admission"
    )
    assert (
        script.get_revision("15a_r9_retry_admission").down_revision
        == "14d_r8_fork_runtime_seed"
    )


def test_r9_a_retry_admission_migration_is_empty_and_reversible(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r9_a_retry_admission.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    command.upgrade(config, "14d_r8_fork_runtime_seed")
    command.upgrade(config, "15a_r9_retry_admission")

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(agent_task_retry_admissions)"
            )
        }
        assert set(columns) == {
            "task_id",
            "retry_request_id",
            "plan_fingerprint",
            "branch_id",
            "source_execution_id",
            "source_checkpoint_id",
            "execution_id",
            "created_by",
            "created_at",
        }
        assert columns["task_id"][5] == 1
        assert columns["retry_request_id"][5] == 2
        assert columns["source_checkpoint_id"][3] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_task_retry_admissions"
        ).fetchone()[0] == 0

        unique_indexes = {
            tuple(
                col[2]
                for col in connection.execute(
                    f"PRAGMA index_info({row[1]})"
                )
            )
            for row in connection.execute(
                "PRAGMA index_list(agent_task_retry_admissions)"
            )
            if row[2]
        }
        assert ("execution_id",) in unique_indexes

        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(agent_task_retry_admissions)"
            )
        }
        assert {
            "ix_agent_task_retry_admissions_branch_id",
            "ix_agent_task_retry_admissions_source_execution_id",
        } <= indexes

        foreign_targets = {
            (row[3], row[2])
            for row in connection.execute(
                "PRAGMA foreign_key_list(agent_task_retry_admissions)"
            )
        }
        assert ("task_id", "agent_tasks") in foreign_targets
        assert ("branch_id", "agent_task_branches") in foreign_targets
        assert ("source_execution_id", "agent_executions") in foreign_targets
        assert (
            "source_checkpoint_id",
            "agent_execution_checkpoints",
        ) in foreign_targets
        assert ("execution_id", "agent_executions") in foreign_targets
    finally:
        connection.close()

    command.downgrade(config, "14d_r8_fork_runtime_seed")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "agent_task_retry_admissions" not in tables
    finally:
        connection.close()
