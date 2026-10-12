"""AIC vE-1 portable SQLite tests; real PG opt-in via AIC_TEST_POSTGRES_URL.

Note: SQLite tests use the *actual Alembic AIC migration*, not a synthetic
security trigger implementation. PostgreSQL tests are opt-in, never mock-green.
"""
import asyncio
import importlib
import os
import sqlite3
import subprocess
import sys
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



def _install_exact_ubq_user_delete_guards(cx, monkeypatch):
    """Execute the exact raw trigger statements from immutable 25a/26a.

    This is scoped DDL coexistence, not a full Alembic upgrade witness.
    """
    ubq25 = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions.25a_ubq1_user_budget_foundation"
    )
    ubq26 = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions.26a_ubq2_dual_accounting_bridge"
    )
    for name in ("user_budget_policies", "user_budget_accounts",
                 "user_budget_windows", "user_tool_budget_usage",
                 "user_budget_reservations", "user_budget_task_bindings"):
        cx.execute(f"CREATE TABLE {name} (owner_user_id TEXT NOT NULL)")
    def install_25a(sql):
        if "CREATE TRIGGER trg_ubq_user_history_delete" in sql:
            cx.execute(sql)
    def install_26a(sql):
        if "CREATE TRIGGER trg_ubq2_user_binding_delete_restrict" in sql:
            cx.execute(sql)
    with monkeypatch.context() as mp:
        mp.setattr(ubq25, "_execute_sqlite_trigger", install_25a)
        mp.setattr(ubq26, "_sqlite", install_26a)
        ubq25._create_sqlite_triggers()
        ubq26._create_sqlite_triggers()
    found = {row[0] for row in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    )}
    assert {"trg_ubq_user_history_delete",
            "trg_ubq2_user_binding_delete_restrict",
            "trg_aic_users_live_delete"} <= found


@pytest.mark.parametrize("history", ["none", "ubq25", "ubq26"])
def test_real_ubq_25a_26a_user_delete_trigger_coexistence(
    file_database, monkeypatch, history
):
    cx = _connect(file_database)
    try:
        _install_exact_ubq_user_delete_guards(cx, monkeypatch)
        _register(cx)
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_USER_DELETE_DENIED"):
            cx.execute("DELETE FROM users WHERE id='alice'")
        # The ownerless marker is never deleted; the exact AIC LIVE
        # predicate stops applying after authorized atomic retirement.
        cx.execute(
            "UPDATE aic_instance_id_reservations SET status='RETIRED' "
            "WHERE agent_instance_id='a1'"
        )
        cx.execute("DELETE FROM aic_instance_live WHERE agent_instance_id='a1'")
        if history == "ubq25":
            cx.execute("INSERT INTO user_budget_policies(owner_user_id) VALUES ('alice')")
        elif history == "ubq26":
            cx.execute("INSERT INTO user_budget_task_bindings(owner_user_id) VALUES ('alice')")
        if history == "none":
            assert cx.execute("DELETE FROM users WHERE id='alice'").rowcount == 1
        else:
            with pytest.raises(sqlite3.IntegrityError, match="UBQ"):
                cx.execute("DELETE FROM users WHERE id='alice'")
        assert cx.execute(
            "SELECT status FROM aic_instance_id_reservations WHERE agent_instance_id='a1'"
        ).fetchone() == ("RETIRED",)
    finally:
        cx.close()


def test_crash_restart_preserves_committed_marker_and_rolls_back_inflight(file_database):
    with _connect(file_database) as cx:
        _register(cx)
        cx.execute(
            "UPDATE aic_instance_id_reservations SET status='RETIRED' "
            "WHERE agent_instance_id='a1'"
        )
        cx.execute("DELETE FROM aic_instance_live WHERE agent_instance_id='a1'")
    crash = """
import sqlite3, os, sys
cx=sqlite3.connect(sys.argv[1])
cx.execute('PRAGMA foreign_keys=OFF')
cx.execute("BEGIN IMMEDIATE")
cx.execute("INSERT INTO aic_instance_id_reservations VALUES ('uncommitted','RETIRED')")
os._exit(93)
"""
    proc = subprocess.run([sys.executable, "-c", crash, str(file_database)],
                          check=False, capture_output=True, timeout=12)
    assert proc.returncode == 93, proc.stderr.decode(errors="replace")
    with _connect(file_database) as cx:
        assert cx.execute(
            "SELECT status FROM aic_instance_id_reservations WHERE agent_instance_id='a1'"
        ).fetchone() == ("RETIRED",)
        assert cx.execute(
            "SELECT COUNT(*) FROM aic_instance_id_reservations "
            "WHERE agent_instance_id='uncommitted'"
        ).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO aic_instance_id_reservations VALUES ('a1','LIVE')")


def test_status_before_create_and_delete_before_create_fk_off(file_database):
    with _connect(file_database) as cx:
        cx.execute("UPDATE users SET status='inactive' WHERE id='alice'")
    with _connect(file_database) as cx:
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_INVALID"):
            _register(cx)
        cx.rollback()
    # Direct raw DELETE before CREATE -> no owner can be rebound.
    with _connect(file_database) as cx:
        cx.execute("DELETE FROM users WHERE id='alice'")
    with _connect(file_database) as cx:
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_INVALID"):
            _register(cx)


def test_live_direct_owner_reassignment_and_invalid_generation_fail(file_database):
    with _connect(file_database) as cx:
        _register(cx)
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_MUTATION_DENIED"):
            cx.execute("UPDATE aic_instance_live SET owner_user_id='bob' WHERE agent_instance_id='a1'")
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_MUTATION_DENIED"):
            cx.execute("UPDATE aic_instance_live SET generation=99 WHERE agent_instance_id='a1'")
        with pytest.raises(sqlite3.IntegrityError, match="AIC_RESERVATION_IMMUTABLE"):
            cx.execute("UPDATE aic_instance_id_reservations SET status='BROKEN' WHERE agent_instance_id='a1'")


def test_db_genesis_rejects_direct_suspended_or_high_generation_fk_off(file_database):
    with _connect(file_database) as cx:
        cx.execute(
            "INSERT INTO aic_instance_id_reservations VALUES ('forged-genesis','LIVE')"
        )
        for state, generation in (("SUSPENDED", 1), ("ACTIVE", 42)):
            with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_INVALID"):
                cx.execute(
                    "INSERT INTO aic_instance_live VALUES "
                    "(?, 'alice', 'definition', 'v1', ?, ?)",
                    ("forged-genesis", state, generation),
                )
        assert cx.execute(
            "SELECT COUNT(*) FROM aic_instance_live WHERE agent_instance_id='forged-genesis'"
        ).fetchone()[0] == 0


def test_real_identity_contract_does_not_use_nonexistent_auth_flag():
    from se.src.domain.schemas.identity import Identity
    from se.src.application.agent_instance import AgentInstanceService
    from se.src.domain.schemas.agent_instance import AgentInstanceDenied
    valid = Identity(auth_type="jwt", user_id="user-123")
    assert AgentInstanceService.trusted_user_id(valid) == "user-123"
    for kind in ("api_key", "admin_key", "guest"):
        with pytest.raises(AgentInstanceDenied):
            AgentInstanceService.trusted_user_id(Identity(auth_type=kind, user_id="user-123"))
    with pytest.raises(AgentInstanceDenied):
        AgentInstanceService.trusted_user_id(Identity(auth_type="jwt"))


@pytest.mark.asyncio
async def test_postgresql17_real_native_aic_guards_and_nonkey_status_lock():
    """Real PostgreSQL 17 native engine, migrations and two connection locks.

    Creates a uniquely named throwaway schema in the supplied PostgreSQL
    database. Does not mutate the public application schema.
    """
    dsn = os.getenv("AIC_TEST_POSTGRES_URL", "")
    if not dsn:
        pytest.skip("real PostgreSQL 17 not configured: AIC_TEST_POSTGRES_URL")
    assert dsn.startswith("postgresql+asyncpg://"), "native asyncpg URL required"
    from uuid import uuid4
    from sqlalchemy.ext.asyncio import create_async_engine
    schema = "aic_evidence_" + uuid4().hex
    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        async with engine.begin() as setup:
            version = (await setup.execute(text("SHOW server_version_num"))).scalar_one()
            assert 170000 <= int(version) < 180000, (
                f"must execute native PostgreSQL 17, observed {version}"
            )
            await setup.execute(text(f"CREATE SCHEMA {schema}"))
            await setup.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            await setup.execute(text("CREATE TABLE users (id VARCHAR(255) PRIMARY KEY, status VARCHAR(50) NOT NULL)"))
            await setup.run_sync(_bootstrap)  # actual production vE-1 PG migration DDL
            await setup.execute(text("INSERT INTO users(id,status) VALUES ('alice','active'), ('bob','active')"))
        # Use two *distinct physical PG connections* and a registered user.
        async with engine.connect() as a, engine.connect() as b:
            await a.execute(text(f"SET search_path TO {schema}, public"))
            await b.execute(text(f"SET search_path TO {schema}, public"))
            locked = (await a.execute(text(
                "SELECT id FROM users WHERE id='alice' FOR SHARE"
            ))).scalar_one()
            assert locked == "alice"
            await b.execute(text("SET LOCAL lock_timeout='200ms'"))
            with pytest.raises(DBAPIError):
                await b.execute(text(
                    "UPDATE users SET status='inactive' WHERE id='alice'"
                ))
            await b.rollback()
            await a.rollback()
        # Execute raw PG trigger DDL from the AIC migration, not a fake model.
        async with engine.begin() as check:
            await check.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            await check.execute(text(
                "INSERT INTO aic_instance_id_reservations VALUES ('pg-forged','LIVE')"
            ))
            with pytest.raises(DBAPIError):
                async with check.begin_nested():
                    await check.execute(text(
                        "INSERT INTO aic_instance_live VALUES "
                        "('pg-forged','alice','definition','v1','SUSPENDED',42)"
                    ))
            await check.execute(text(
                "INSERT INTO aic_instance_id_reservations VALUES ('pg-i1','LIVE')"
            ))
            await check.execute(text(
                "INSERT INTO aic_instance_live VALUES "
                "('pg-i1','alice','definition','v1','ACTIVE',1)"
            ))
            with pytest.raises(DBAPIError):
                async with check.begin_nested():
                    await check.execute(text("DELETE FROM users WHERE id='alice'"))
            await check.execute(text(
                "UPDATE aic_instance_live SET state='SUSPENDED', generation=2 "
                "WHERE agent_instance_id='pg-i1'"
            ))
            with pytest.raises(DBAPIError):
                async with check.begin_nested():
                    await check.execute(text("DELETE FROM users WHERE id='alice'"))
            with pytest.raises(DBAPIError):
                async with check.begin_nested():
                    await check.execute(text(
                        "UPDATE aic_instance_live SET owner_user_id='bob' "
                        "WHERE agent_instance_id='pg-i1'"
                    ))
            await check.execute(text(
                "UPDATE aic_instance_id_reservations SET status='RETIRED' "
                "WHERE agent_instance_id='pg-i1'"
            ))
            await check.execute(text(
                "DELETE FROM aic_instance_live WHERE agent_instance_id='pg-i1'"
            ))
            assert (await check.execute(text(
                "SELECT status FROM aic_instance_id_reservations "
                "WHERE agent_instance_id='pg-i1'"
            ))).scalar_one() == "RETIRED"
            with pytest.raises(DBAPIError):
                async with check.begin_nested():
                    await check.execute(text(
                        "DELETE FROM aic_instance_id_reservations "
                        "WHERE agent_instance_id='pg-i1'"
                    ))
            with pytest.raises(DBAPIError):
                async with check.begin_nested():
                    await check.execute(text(
                        "INSERT INTO aic_instance_live VALUES "
                        "('pg-i1','alice','definition','v1','ACTIVE',1)"
                    ))
            assert (await check.execute(text(
                "DELETE FROM users WHERE id='alice'"
            ))).rowcount == 1
    finally:
        try:
            async with engine.begin() as cleanup:
                await cleanup.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            await engine.dispose()
