"""AIC vE-1 portable SQLite tests; real PG opt-in via AIC_TEST_POSTGRES_URL.

Note: SQLite tests use the *actual Alembic AIC migration*, not a synthetic
security trigger implementation. PostgreSQL tests are opt-in, never mock-green.
"""
import asyncio
import importlib
import os
import sqlite3
import threading
import time
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

MIGRATION = importlib.import_module(
    "se.src.infrastructure.storage.migrations.sql.versions.31a_aic0_agent_instance_identity"
)


def _bootstrap(connection):
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS users (id VARCHAR(255) PRIMARY KEY, status VARCHAR(50) NOT NULL)"
    )
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    ctx = MigrationContext.configure(connection)
    operations = Operations(ctx)
    # Alembic module uses its module-global op proxy: install only inside
    # this call so migration upgrade creates the exact production DDL.
    original = MIGRATION.op
    MIGRATION.op = operations
    try:
        MIGRATION.upgrade()
    finally:
        MIGRATION.op = original


@pytest.fixture
def file_database(tmp_path):
    target = tmp_path / "aic_identity.db"
    engine = create_engine("sqlite:///" + str(target))
    with engine.begin() as cx:
        _bootstrap(cx)
        cx.execute(text("INSERT INTO users(id,status) VALUES ('alice','active'),('bob','active')"))
    engine.dispose()
    return target


def _connect(target):
    cx = sqlite3.connect(str(target), timeout=0.15)
    cx.execute("PRAGMA foreign_keys=OFF")
    assert cx.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    return cx


def _register(cx, instance="a1", user="alice"):
    cx.execute("INSERT INTO aic_instance_id_reservations VALUES (?, 'LIVE')", (instance,))
    cx.execute("""INSERT INTO aic_instance_live
        (agent_instance_id, owner_user_id, definition_id, definition_version, state, generation)
        VALUES (?,?, 'definition','v1','ACTIVE',1)""", (instance, user))


def test_sqlite_fk_off_orphan_guard_active_and_suspended(file_database):
    with _connect(file_database) as cx:
        _register(cx)
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_USER_DELETE_DENIED"):
            cx.execute("DELETE FROM users WHERE id='alice'")
        cx.execute("UPDATE aic_instance_live SET state='SUSPENDED', generation=2 WHERE agent_instance_id='a1'")
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_USER_DELETE_DENIED"):
            cx.execute("DELETE FROM users WHERE id='alice'")
        assert cx.execute("SELECT COUNT(*) FROM users WHERE id='alice'").fetchone()[0] == 1


def test_retired_id_is_ownerless_never_reused(file_database):
    with _connect(file_database) as cx:
        _register(cx)
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_UNAUTHORIZED_DELETE"):
            cx.execute("DELETE FROM aic_instance_live WHERE agent_instance_id='a1'")
        cx.execute("UPDATE aic_instance_id_reservations SET status='RETIRED' WHERE agent_instance_id='a1'")
        cx.execute("DELETE FROM aic_instance_live WHERE agent_instance_id='a1'")
        cx.execute("DELETE FROM users WHERE id='alice'")
        assert cx.execute("SELECT status FROM aic_instance_id_reservations WHERE agent_instance_id='a1'").fetchone() == ("RETIRED",)
        with pytest.raises(sqlite3.IntegrityError):
            _register(cx, instance="a1", user="bob")
        with pytest.raises(sqlite3.IntegrityError, match="AIC_ID_NONREUSE"):
            cx.execute("DELETE FROM aic_instance_id_reservations WHERE agent_instance_id='a1'")
        with pytest.raises(sqlite3.IntegrityError, match="AIC_RESERVATION_IMMUTABLE"):
            cx.execute("UPDATE aic_instance_id_reservations SET status='LIVE' WHERE agent_instance_id='a1'")


def test_inactive_status_prevents_new_binding_with_fks_off(file_database):
    with _connect(file_database) as cx:
        cx.execute("UPDATE users SET status='inactive' WHERE id='alice'")
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_INVALID"):
            _register(cx)
        assert cx.execute("SELECT COUNT(*) FROM aic_instance_live").fetchone()[0] == 0


def test_sqlite_two_physical_connections_serialize_owner_status(file_database):
    a, b = _connect(file_database), _connect(file_database)
    try:
        a.execute("BEGIN IMMEDIATE")
        assert a.execute("SELECT status FROM users WHERE id='alice'").fetchone()[0] == "active"
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            b.execute("UPDATE users SET status='inactive' WHERE id='alice'")
        _register(a)
        a.commit()
        b.execute("UPDATE users SET status='inactive' WHERE id='alice'")
        b.commit()
        with _connect(file_database) as verifier:
            assert verifier.execute("SELECT status FROM users WHERE id='alice'").fetchone()[0] == "inactive"
            assert verifier.execute("SELECT COUNT(*) FROM aic_instance_live").fetchone()[0] == 1
    finally:
        a.close()
        b.close()


def test_downgrade_refuses_nonempty_identity_history(file_database):
    engine = create_engine("sqlite:///" + str(file_database))
    with engine.begin() as cx:
        cx.execute(text("INSERT INTO aic_instance_id_reservations VALUES ('never-reuse','RETIRED')"))
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        original = MIGRATION.op
        MIGRATION.op = Operations(MigrationContext.configure(cx))
        try:
            with pytest.raises(RuntimeError, match="AIC_DOWNGRADE_DENIED"):
                MIGRATION.downgrade()
        finally:
            MIGRATION.op = original
    engine.dispose()


@pytest.mark.skipif(not os.getenv("AIC_TEST_POSTGRES_URL"), reason="Real PostgreSQL URL not configured")
def test_postgresql_real_nonkey_owner_status_conflict():
    from uuid import uuid4
    # Real PostgreSQL two independently checked-out physical connections.
    # A matched, committed users row is MANDATORY; SELECT FOR SHARE on a
    # missing row would acquire no row-lock and generate a false positive.
    from sqlalchemy import create_engine
    url = os.environ["AIC_TEST_POSTGRES_URL"]
    owner_id = "aic-lock-" + str(uuid4())
    engine = create_engine(url)
    try:
        with engine.begin() as seed:
            seed.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES (:id,:email,'disabled','active')"
            ), {"id": owner_id, "email": owner_id + "@example.invalid"})
        with engine.connect() as a, engine.connect() as b:
            tx_a = a.begin()
            tx_b = b.begin()
            try:
                locked = a.execute(text(
                    "SELECT id FROM users WHERE id=:id FOR SHARE"
                ), {"id": owner_id}).scalar_one()
                assert locked == owner_id
                b.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError):
                    b.execute(text(
                        "UPDATE users SET status='inactive' WHERE id=:id"
                    ), {"id": owner_id})
            finally:
                tx_b.rollback()
                tx_a.rollback()
        with engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM users WHERE id=:id"), {"id": owner_id})
    finally:
        engine.dispose()
