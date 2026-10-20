"""S02 disposable SQLite migration proof for BOTH historical heads.

Only creates temporary databases. Never touches user or production state.
Negative owner/role assertions supplement, never replace, the original tests.
"""
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[3]
BASE = "30a_ctx_f5_user_wide_memory_scope"
AIC_31B = "31b_aic0_pg_guard_forward"
TERMINAL_31T = "31t_direct_terminal_permission"
MERGE = "32a_alpha_31b_31t_merge"


def _config(path: Path, monkeypatch) -> Config:
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        "sqlite+aiosqlite:///" + path.as_posix(),
    )
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    return c


@pytest.mark.parametrize("starting_tip", (BASE, AIC_31B, TERMINAL_31T))
@pytest.mark.parametrize("fk_on,recursive_on", (
    (False, False), (False, True), (True, False), (True, True),
))
def test_s02_sqlite_populated_history_merges_without_grants_or_owner_loss(
    tmp_path, monkeypatch, starting_tip, fk_on, recursive_on,
):
    database = tmp_path / "s02-migration.sqlite"
    cfg = _config(database, monkeypatch)
    command.upgrade(cfg, starting_tip)
    with sqlite3.connect(database) as con:
        con.execute(
            "INSERT INTO users (id, email, password_hash, status) "
            "VALUES ('s02-owner', 's02-owner@example.invalid', 'x', 'active')"
        )
        # A real historical populated user is present before reconciling tips.
        assert con.execute("SELECT COUNT(*) FROM users WHERE id='s02-owner'").fetchone()[0] == 1
        before_grants = con.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0]
    command.upgrade(cfg, MERGE)
    command.upgrade(cfg, MERGE)  # idempotent replay at the merged tip
    with sqlite3.connect(database) as con:
        con.execute(f"PRAGMA foreign_keys={'ON' if fk_on else 'OFF'}")
        con.execute(f"PRAGMA recursive_triggers={'ON' if recursive_on else 'OFF'}")
        versions = con.execute("SELECT version_num FROM alembic_version").fetchall()
        assert versions == [(MERGE,)]
        assert con.execute(
            "SELECT COUNT(*) FROM users WHERE id='s02-owner' AND status='active'"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM permissions WHERE name='terminal:execute'"
        ).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0] == before_grants
        assert con.execute(
            "SELECT COUNT(*) FROM role_permissions rp "
            "JOIN permissions p ON p.id=rp.permission_id WHERE p.name='terminal:execute'"
        ).fetchone()[0] == 0

        # 31a/31b owner guards must survive the no-SQL merge under every mode.
        con.execute(
            "INSERT INTO aic_instance_reservations(agent_instance_id,state,generation) "
            "VALUES ('s02-live', 'ACTIVE', 1)"
        )
        con.execute(
            "INSERT INTO aic_instance_live("
            "agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) "
            "VALUES ('s02-live','s02-owner','s02-def','v1','ACTIVE',1)"
        )
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("DELETE FROM users WHERE id='s02-owner'")
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("UPDATE users SET id='s02-other' WHERE id='s02-owner'")
        assert con.execute("SELECT owner_user_id FROM aic_instance_live").fetchone()[0] == "s02-owner"
        assert con.execute("SELECT COUNT(*) FROM users WHERE id='s02-owner'").fetchone()[0] == 1
