"""RED-first: 31t permission provision must not leave asyncpg parameter $2 ambiguous.

This test does not change grants or execute any database write. Physical PG17
integration tests remain the authoritative behavior/security oracle.
"""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import asyncpg

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "se/src/infrastructure/storage/migrations/sql/versions/31t_direct_terminal_permission.py"


def test_s02_31t_upgrade_types_reused_permission_name_for_asyncpg(monkeypatch):
    spec = importlib.util.spec_from_file_location("s02_terminal_31t_bind", SOURCE)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    recorded = []

    class BindRecorder:
        def execute(self, statement, params):
            recorded.append((statement, params))

    monkeypatch.setattr(migration.op, "get_bind", lambda: BindRecorder())
    migration.upgrade()
    assert len(recorded) == 1, "31t upgrade must execute precisely one permission insert"
    stmt, params = recorded[0]
    compiled = str(stmt.compile(dialect=asyncpg.dialect()))

    # Before this fix PostgreSQL gave the reused $2 TEXT vs VARCHAR types.
    # Explicit CAST or typed bind must yield the same VARCHAR type in both uses.
    assert "SELECT $1, $2::VARCHAR, $3" in compiled, compiled
    assert "WHERE name = $2::VARCHAR" in compiled, compiled
    assert params == {
        "permission_id": "direct-terminal-execute-v1",
        "permission_name": "terminal:execute",
        "description": "Explicit authorization to run or launch host terminal commands",
    }
    assert "INSERT INTO permissions" in compiled
    assert "NOT EXISTS" in compiled
    assert "role_permissions" not in compiled, "No implicit role grant is permitted"
