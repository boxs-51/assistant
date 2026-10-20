"""S02 crash/restart and reversible merge proof on SQLite backup copies.

A child *migrator* process is terminated at a real SQL after-execute boundary.
Only temporary pytest SQLite databases are created; no user database is opened.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[3]
AIC = "31b_aic0_pg_guard_forward"
TERMINAL = "31t_direct_terminal_permission"
MERGE = "32a_alpha_31b_31t_merge"

_CRASH_WORKER = r"""
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "after_cursor_execute")
def interrupt(conn, cursor, statement, parameters, context, executemany):
    if "INSERT INTO permissions" in statement and "terminal:execute" in str(parameters):
        os._exit(77)  # Actual abrupt client process death, not an exception/rollback.

cfg = Config(str(Path(os.environ["S02_REPO_ROOT"]) / "alembic.ini"))
cfg.set_main_option("script_location", str(Path(os.environ["S02_REPO_ROOT"]) /
    "se/src/infrastructure/storage/migrations/sql"))
command.upgrade(cfg, "head")
raise SystemExit(99)  # Injection was not reached: FAIL CLOSED.
"""


def _config(monkeypatch, path: Path) -> Config:
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL",
                       "sqlite+aiosqlite:///" + path.as_posix())
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    return cfg


def _copy_db(source: Path, target: Path) -> None:
    assert source != target and source.is_file() and not target.exists()
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _seed(tmp_path, monkeypatch, tip: str) -> tuple[Path, int]:
    db = tmp_path / ("seed-" + tip + ".sqlite")
    command.upgrade(_config(monkeypatch, db), tip)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO users (id,email,password_hash,status) "
            "VALUES ('s02-copy-owner','s02-copy-owner@example.invalid','x','active')"
        )
        grants = conn.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0]
    return db, grants


def _check_data(db: Path, grants: int, permission: int) -> None:
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE id='s02-copy-owner' AND status='active'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM role_permissions"
        ).fetchone()[0] == grants, "Migration may not create role grants"
        assert conn.execute(
            "SELECT COUNT(*) FROM permissions WHERE name='terminal:execute'"
        ).fetchone()[0] == permission
        assert conn.execute(
            "SELECT COUNT(*) FROM role_permissions rp JOIN permissions p "
            "ON rp.permission_id=p.id WHERE p.name='terminal:execute'"
        ).fetchone()[0] == 0


def _versions(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def _assert_owner_denied(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA recursive_triggers=OFF")
        conn.execute(
            "INSERT INTO aic_instance_reservations(agent_instance_id,state,generation) "
            "VALUES ('s02-copy-live','ACTIVE',1)"
        )
        conn.execute(
            "INSERT INTO aic_instance_live("
            "agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) VALUES "
            "('s02-copy-live','s02-copy-owner','s02-def','v1','ACTIVE',1)"
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM users WHERE id='s02-copy-owner'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE users SET id='s02-other' WHERE id='s02-copy-owner'"
            )
        assert conn.execute(
            "SELECT owner_user_id FROM aic_instance_live "
            "WHERE agent_instance_id='s02-copy-live'"
        ).fetchone()[0] == "s02-copy-owner"


def test_s02_sqlite_crash_then_restart_from_disposable_backup(tmp_path, monkeypatch):
    seed, grants = _seed(tmp_path, monkeypatch, AIC)
    clone = tmp_path / "crash-copy.sqlite"
    _copy_db(seed, clone)
    assert _versions(seed) == {AIC} and _versions(clone) == {AIC}
    _config(monkeypatch, clone)
    env = os.environ.copy()
    env["S02_REPO_ROOT"] = str(ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", _CRASH_WORKER],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=100,
    )
    assert proc.returncode == 77, (
        f"Real after-SQL crash hook was not reached: rc={proc.returncode}; "
        f"stderr={proc.stderr[-1800:]}"
    )
    assert _versions(clone) == {AIC}, "Crash may not partially stamp 32a"
    _check_data(clone, grants, permission=0)
    assert _versions(seed) == {AIC}, "Backup source must remain untouched"

    cfg = _config(monkeypatch, clone)
    command.upgrade(cfg, "head")  # Fresh migrator process / new DB connection.
    command.upgrade(cfg, "head")  # Idempotent restart.
    assert _versions(clone) == {MERGE}
    _check_data(clone, grants, permission=1)
    _assert_owner_denied(clone)


@pytest.mark.parametrize("tip", (AIC, TERMINAL))
def test_s02_sqlite_rollback_merge_only_and_reupgrade_on_copy(
    tmp_path, monkeypatch, tip,
):
    seed, grants = _seed(tmp_path, monkeypatch, tip)
    clone = tmp_path / "rollback-copy.sqlite"
    _copy_db(seed, clone)
    cfg = _config(monkeypatch, clone)
    command.upgrade(cfg, "head")
    assert _versions(clone) == {MERGE}
    _check_data(clone, grants, permission=1)

    # A relative -1 from merge 32a is ambiguous across 31b/31t.
    # An explicit 31b ancestor drops ONLY 32a, restoring both parent heads.
    # It must never downgrade 31b's security guard or the 31t permission.
    command.downgrade(cfg, AIC)  # Undo ONLY merge marker, keep both parents.
    assert _versions(clone) == {AIC, TERMINAL}
    _check_data(clone, grants, permission=1)
    command.upgrade(cfg, "head")
    assert _versions(clone) == {MERGE}
    _check_data(clone, grants, permission=1)
    _assert_owner_denied(clone)
    assert _versions(seed) == {tip}, "Source snapshot must not be modified"
