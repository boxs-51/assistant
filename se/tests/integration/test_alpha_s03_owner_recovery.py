"""Physical S03 SQLite crash/rollback and retained owner on disposable copies."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

ROOT = Path(__file__).resolve().parents[3]
S02 = "32a_alpha_31b_31t_merge"
S03 = "32b_alpha_s03_owner_integrity"
WORKER = """
import os, sqlite3, sys
c=sqlite3.connect(sys.argv[1])
c.execute("PRAGMA foreign_keys=OFF")
c.execute("PRAGMA recursive_triggers=OFF")
c.execute("BEGIN")
c.execute("INSERT INTO users(id,email,password_hash,status) VALUES ('crash-user','crash@invalid','x','active')")
c.execute("INSERT INTO file_blobs(id,storage_backend,object_key,state) VALUES ('crash-blob','local','crash-key','STAGING')")
c.execute("INSERT INTO files(id,owner_user_id,blob_id,filename,mime_type,origin_type,state) VALUES ('crash-file','crash-user','crash-blob','x.txt','text/plain','USER_UPLOAD','STAGING')")
assert c.execute("SELECT COUNT(*) FROM s03_owner_witness WHERE owner_user_id='crash-user'").fetchone()[0] == 1
os._exit(73)
"""


def _cfg(monkeypatch, db):
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", "sqlite+aiosqlite:///" + db.as_posix())
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "se/src/infrastructure/storage/migrations/sql"))
    return cfg


@pytest.mark.parametrize("foreign_keys", (False, True))
@pytest.mark.parametrize("recursive", (False, True))
def test_s03_disposable_owner_crash_replay_and_negative_sql(tmp_path, monkeypatch, foreign_keys, recursive):
    source = tmp_path / "s02-source.sqlite"
    command.upgrade(_cfg(monkeypatch, source), S02)
    with sqlite3.connect(source) as c:
        c.execute("INSERT INTO users(id,email,password_hash,status) VALUES ('original','original@invalid','x','active')")
        c.execute("INSERT INTO file_blobs(id,storage_backend,object_key,state) VALUES ('original-blob','local','original-key','STAGING')")
        c.execute("INSERT INTO files(id,owner_user_id,blob_id,filename,mime_type,origin_type,state) VALUES ('original-file','original','original-blob','x.txt','text/plain','USER_UPLOAD','STAGING')")
    copy = tmp_path / "s03-copy.sqlite"
    with sqlite3.connect(source) as a, sqlite3.connect(copy) as b:
        a.backup(b)
    cfg = _cfg(monkeypatch, copy)
    command.upgrade(cfg, S03)
    command.upgrade(cfg, S03)
    child = subprocess.run([sys.executable, "-c", WORKER, str(copy)],
                           cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=45)
    assert child.returncode == 73, (child.returncode, child.stderr[-1500:])
    with sqlite3.connect(copy) as c:
        c.execute("PRAGMA foreign_keys=" + ("ON" if foreign_keys else "OFF"))
        c.execute("PRAGMA recursive_triggers=" + ("ON" if recursive else "OFF"))
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == S03
        for table, field, value in (
            ("users", "id", "crash-user"),
            ("files", "id", "crash-file"),
            ("s03_owner_witness", "owner_user_id", "crash-user"),
        ):
            assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE {field}=?", (value,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM s03_owner_witness WHERE owner_user_id='original'").fetchone()[0] == 1
        for sql in (
            "DELETE FROM users WHERE id='original'",
            "UPDATE users SET id='intruder' WHERE id='original'",
            "INSERT OR REPLACE INTO users(id,email,password_hash,status) VALUES ('original','original@invalid','poison','active')",
            "INSERT OR REPLACE INTO users(id,email,password_hash,status) VALUES ('intruder','original@invalid','poison','active')",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                c.execute(sql)
            c.rollback()
        c.execute("INSERT INTO users(id,email,password_hash,status) VALUES ('original','original@invalid','x','active') ON CONFLICT(id) DO NOTHING")
        assert c.execute("SELECT password_hash FROM users WHERE id='original'").fetchone()[0] == "x"
        assert c.execute("SELECT COUNT(*) FROM files WHERE id='original-file' AND owner_user_id='original'").fetchone()[0] == 1
    with sqlite3.connect(source) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == S02
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='s03_owner_witness'").fetchone()[0] == 0
