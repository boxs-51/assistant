from __future__ import annotations

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


def test_r11_b1_is_single_linear_migration_head(tmp_path: Path):
    config = _config(tmp_path / "unused.sqlite")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["16a_r11_transcript_representation"]
    assert (
        script.get_revision("16a_r11_transcript_representation").down_revision
        == "15b_r9_aggregate_admission"
    )


def test_r11_b1_migration_is_reversible_and_enforces_shape(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r11_b1_migration.sqlite"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)

    command.upgrade(config, "15b_r9_aggregate_admission")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "agent_transcript_chunks",
            "agent_transcript_payload_nodes",
            "agent_transcript_representations",
        }.issubset(tables)

        representation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(agent_transcript_representations)"
            )
        }
        assert representation_columns == {
            "transcript_ref",
            "transcript_version",
            "kind",
            "parent_transcript_ref",
            "parent_transcript_version",
            "delta_depth",
            "logical_message_count",
            "logical_transcript_fingerprint",
            "payload_root_ref",
            "created_at",
        }

        explicit_transcript_indexes = {
            row[1]
            for table in (
                "agent_transcript_chunks",
                "agent_transcript_payload_nodes",
                "agent_transcript_representations",
            )
            for row in connection.execute(f"PRAGMA index_list({table})")
            if row[1].startswith("ix_agent_transcript_")
        }
        assert explicit_transcript_indexes == set()

        foreign_targets = {
            (row[3], row[2])
            for row in connection.execute(
                "PRAGMA foreign_key_list(agent_transcript_representations)"
            )
        }
        assert ("payload_root_ref", "agent_transcript_payload_nodes") in foreign_targets
        assert (
            "parent_transcript_ref",
            "agent_transcript_representations",
        ) in foreign_targets
        assert (
            "parent_transcript_version",
            "agent_transcript_representations",
        ) in foreign_targets

        connection.execute(
            """
            INSERT INTO agent_transcript_chunks
                (chunk_id, payload, message_count, canonical_bytes)
            VALUES (?, ?, ?, ?)
            """,
            ("a" * 64, "[]", 0, 2),
        )
        connection.execute(
            """
            INSERT INTO agent_transcript_payload_nodes
                (payload_root_ref, parent_payload_root_ref, chunk_id,
                 logical_message_count)
            VALUES (?, NULL, ?, ?)
            """,
            ("b" * 64, "a" * 64, 0),
        )
        connection.execute(
            """
            INSERT INTO agent_transcript_representations
                (transcript_ref, transcript_version, kind,
                 parent_transcript_ref, parent_transcript_version,
                 delta_depth, logical_message_count,
                 logical_transcript_fingerprint, payload_root_ref)
            VALUES (?, 0, 'FULL', NULL, NULL, 0, 0, ?, ?)
            """,
            ("c" * 64, "d" * 64, "b" * 64),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_transcript_representations
                    (transcript_ref, transcript_version, kind,
                     parent_transcript_ref, parent_transcript_version,
                     delta_depth, logical_message_count,
                     logical_transcript_fingerprint, payload_root_ref)
                VALUES (?, 10, 'DELTA', ?, 0, 10, 1, ?, ?)
                """,
                ("e" * 64, "c" * 64, "f" * 64, "b" * 64),
            )
    finally:
        connection.close()

    command.downgrade(config, "15b_r9_aggregate_admission")

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "agent_transcript_representations" not in tables
        assert "agent_transcript_payload_nodes" not in tables
        assert "agent_transcript_chunks" not in tables
    finally:
        connection.close()
