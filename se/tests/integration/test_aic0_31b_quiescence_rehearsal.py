"""Disposable PG17 role-admission + active-reader/writer quiescence rehearsal.

This is a TEST of one bounded PostgreSQL-role admission mechanism, not proof
that a production gateway/worker/deployment system enforces maintenance mode.
Every database/role is randomly named, local to the CI PostgreSQL 17 service,
and removed at teardown. Never use against production.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from se.tests.integration.test_aic0_31b_composed_alembic import (
    ROOT, REV31A, REV31B, _new_database, _old31a_script_config,
    _alembic, _revision, _retire_raw, _assert_denied_after_repair,
)


def _require_ephemeral_github_runner():
    # Fail BEFORE creating any database or role. A localhost DSN alone does not
    # prove ephemerality (e.g. someone might port-forward a production DB).
    if os.getenv("AIC31B_EPHEMERAL_REHEARSAL") != "1":
        pytest.skip("Ephemeral AIC31b PG17 rehearsal requires explicit CI opt-in")
    dsn = os.getenv("AIC_TEST_POSTGRES_URL", "")
    # asyncpg interprets URI query arguments such as ?host=remote AFTER the
    # authority; checking only url.host is therefore insufficient. The CI
    # fixture URL is intentionally exact, with no query/driver overrides.
    expected = "postgresql+asyncpg://postgres:root@127.0.0.1:5432/aic_p1a"
    assert dsn == expected, "Refusing non-fixture PostgreSQL role/database target"
    url = make_url(dsn)
    assert not url.query, "PostgreSQL URI query overrides are forbidden"
    assert (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("RUNNER_ENVIRONMENT") == "github-hosted"
        and url.host == "127.0.0.1"
        and (url.port or 5432) == 5432
        and url.database == "aic_p1a"
        and url.username == "postgres"
        and url.drivername == "postgresql+asyncpg"
    ), "Refusing role/database mutation outside isolated hosted PG17 test profile"


def _role_url(url, name, password):
    return make_url(url).set(username=name, password=password).render_as_string(
        hide_password=False
    )


async def _install_roles(admin, database):
    suffix = uuid4().hex[:12]
    migration_role = "aic_migration_" + suffix
    app_role = "aic_application_" + suffix
    migration_password = uuid4().hex + "A"
    app_password = uuid4().hex + "B"
    try:
        async with admin.connect() as cx:
            await cx.exec_driver_sql(
                f"CREATE ROLE {migration_role} LOGIN PASSWORD '{migration_password}'"
            )
            await cx.exec_driver_sql(
                f"CREATE ROLE {app_role} LOGIN PASSWORD '{app_password}'"
            )
            await cx.exec_driver_sql(
                f"ALTER DATABASE {database} OWNER TO {migration_role}"
            )
            # CREATE DB grants CONNECT/TEMPORARY to PUBLIC by default.
            await cx.exec_driver_sql(
                f"REVOKE CONNECT, TEMPORARY ON DATABASE {database} FROM PUBLIC"
            )
            await cx.exec_driver_sql(
                f"GRANT CONNECT, TEMPORARY ON DATABASE {database} "
                f"TO {migration_role}"
            )
            await cx.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {database} TO {app_role}"
            )
    except Exception:
        # Setup statements are AUTOCOMMIT, not atomic. Never abandon roles
        # when a later GRANT/ALTER fails before returning their names.
        # This function is restricted to a GitHub-hosted disposable PG17 DB.
        async with admin.connect() as cx:
            await cx.exec_driver_sql(
                f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"
            )
            await cx.exec_driver_sql(f"DROP ROLE IF EXISTS {app_role}")
            await cx.exec_driver_sql(f"DROP ROLE IF EXISTS {migration_role}")
        raise
    return migration_role, migration_password, app_role, app_password

async def _drop_roles(admin, migration_role, app_role):
    async with admin.connect() as cx:
        await cx.exec_driver_sql(f"DROP ROLE IF EXISTS {app_role}")
        await cx.exec_driver_sql(f"DROP ROLE IF EXISTS {migration_role}")


# Use Alembic's *actual CommandLine parser/dispatcher* in its own process.
# Only the test child adds a machine-readable SQLSTATE error witness; neither
# env.py nor any production migration is modified to emit synthetic evidence.
_CLI_SQLSTATE_CHILD = r"""
import json
import sys
import traceback
from alembic.config import CommandLine

try:
    CommandLine(prog="alembic").main(argv=sys.argv[1:])
except BaseException as exc:
    seen = set()
    pending = [exc]
    sqlstate = None
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        code = getattr(item, "sqlstate", None) or getattr(item, "pgcode", None)
        if isinstance(code, str) and len(code) == 5:
            sqlstate = code
            break
        for key in ("orig", "__cause__", "__context__"):
            child = getattr(item, key, None)
            if child is not None:
                pending.append(child)
    print("AIC31B_CLI_SQLSTATE=" + json.dumps({"sqlstate": sqlstate}),
          file=sys.stderr, flush=True)
    traceback.print_exc()
    sys.exit(96)
"""


def _run_actual_cli(url):
    env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
    return subprocess.run(
        [sys.executable, "-c", _CLI_SQLSTATE_CHILD, "-c", "alembic.ini",
         "upgrade", REV31B],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
        check=False,
    )


def _require_exact_cli_lock_sqlstate(result):
    # The *failing Alembic CLI subprocess*, not a control probe or a traceback
    # substring, must emit exactly one structured SQLSTATE 55P03 witness.
    import json
    assert result.returncode != 0, "Expected Alembic CLI failure"
    marker = "AIC31B_CLI_SQLSTATE="
    witnesses = [line[len(marker):] for line in result.stderr.splitlines()
                 if line.startswith(marker)]
    assert len(witnesses) == 1, "Missing/ambiguous CLI SQLSTATE witness"
    assert json.loads(witnesses[0]) == {"sqlstate": "55P03"}, (
        "Alembic CLI did not fail with PostgreSQL lock_not_available", witnesses
    )


@pytest.mark.parametrize("held_lock", ["reader", "writer"])
@pytest.mark.asyncio
async def test_pg17_ephemeral_login_barrier_drain_and_old31a_forward(
    held_lock, tmp_path
):
    """Real old CLI + active table lock => 31b abort => LOGIN fence/drain =>
    distinct successful CLI, post-upgrade guard validation, then release.
    """
    _require_ephemeral_github_runner()
    admin, initial_engine, database, admin_url = await _new_database()
    migration_role = app_role = None
    mig_engine = None
    app_conn = None
    try:
        migration_role, migration_password, app_role, app_password = (
            await _install_roles(admin, database)
        )
        mig_url = _role_url(admin_url, migration_role, migration_password)
        app_url = _role_url(admin_url, app_role, app_password)
        mig_engine = create_async_engine(mig_url, pool_pre_ping=True)

        _alembic(mig_url, REV31A, config=_old31a_script_config(tmp_path))
        assert await _revision(mig_engine) == REV31A

        async with mig_engine.begin() as cx:
            await cx.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@ephemeral.invalid','x','active')"
            ))
            await cx.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA public TO {app_role}"
            )
            await cx.exec_driver_sql(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {app_role}"
            )
            await cx.exec_driver_sql(
                f"GRANT UPDATE ON public.users TO {app_role}"
            )
        identity = "retired-" + uuid4().hex
        await _retire_raw(mig_engine, identity)

        # Test profile is deliberately non-privileged; production roles are
        # NOT inventoried/proven by this ephemeral assertion.
        # ACL checks must resolve objects in the disposable *target* DB,
        # not the administrative parent database used to provision roles.
        async with mig_engine.connect() as cx:
            acl = (await cx.execute(text("""
                SELECT has_database_privilege(:r, :d, 'TEMPORARY'),
                       has_schema_privilege(:r, 'public', 'CREATE'),
                       has_table_privilege(:r, 'public.users', 'TRUNCATE'),
                       (SELECT rolcanlogin FROM pg_catalog.pg_roles
                        WHERE rolname=:r)
            """), {"r": app_role, "d": database})).one()
            assert tuple(acl) == (False, False, False, True)

        # Existing sessions persist after NOLOGIN; it is not a session drain.
        app_conn = await asyncpg.connect(
            dsn=app_url.replace("postgresql+asyncpg://", "postgresql://")
        )
        tx = app_conn.transaction()
        await tx.start()
        if held_lock == "reader":
            assert await app_conn.fetchval(
                "SELECT id FROM public.users WHERE id='alice'"
            ) == "alice"
        else:
            await app_conn.execute(
                "UPDATE public.users SET status=status WHERE id='alice'"
            )

        # Prove the precise database failure class under this physical lock.
        # A CLI traceback substring ALONE is not adequate SQLSTATE evidence.
        control = await asyncpg.connect(
            dsn=mig_url.replace("postgresql+asyncpg://", "postgresql://")
        )
        try:
            try:
                async with control.transaction():
                    await control.execute(
                        "LOCK TABLE public.users, public.aic_instance_reservations, "
                        "public.aic_instance_live IN ACCESS EXCLUSIVE MODE NOWAIT"
                    )
            except asyncpg.PostgresError as exc:
                assert exc.sqlstate == "55P03", (
                    "Expected physical PG lock_not_available", exc.sqlstate
                )
            else:
                pytest.fail("Contended physical NOWAIT lock unexpectedly succeeded")
        finally:
            await control.close()

        # Separate out-of-process Alembic CLI must also fail while the SAME
        # physical reader/writer is active; installed 31a remains unchanged.
        failed = _run_actual_cli(mig_url)
        assert failed.returncode != 0, (
            held_lock, failed.stdout[-1200:], failed.stderr[-3000:]
        )
        # A non-lock CLI error must NOT pass merely by mentioning a lock in
        # its traceback. Keep the negative-oracle regression in both schedules.
        bogus = subprocess.CompletedProcess(
            args=["alembic"], returncode=1, stdout="",
            stderr="could not obtain lock; locknotavailable; 55P03; "
                   "AIC31B_CLI_SQLSTATE={\\"sqlstate\\": \\"42P01\\"}",
        )
        with pytest.raises(AssertionError):
            _require_exact_cli_lock_sqlstate(bogus)
        _require_exact_cli_lock_sqlstate(failed)
        assert await _revision(mig_engine) == REV31A

        # PostgreSQL itself rejects NEW app-role logins while existing app
        # sessions remain. Do not resume app admission merely on failed 31b.
        async with admin.connect() as cx:
            await cx.exec_driver_sql(f"ALTER ROLE {app_role} NOLOGIN")
            active = (await cx.execute(text("""
                SELECT COUNT(*) FROM pg_catalog.pg_stat_activity
                WHERE datname=:db AND usename=:app
            """), {"db": database, "app": app_role})).scalar_one()
            assert active >= 1
        blocked_dsn = app_url.replace("postgresql+asyncpg://", "postgresql://")
        try:
            unexpected = await asyncpg.connect(dsn=blocked_dsn)
        except asyncpg.InvalidAuthorizationSpecificationError:
            pass
        else:
            await unexpected.close()
            pytest.fail("Application was admitted while PostgreSQL role NOLOGIN")

        await tx.rollback()
        await app_conn.close()
        app_conn = None
        # Admission closure + explicit session drain are both necessary.
        async with admin.connect() as cx:
            remaining = (await cx.execute(text("""
                SELECT COUNT(*) FROM pg_catalog.pg_stat_activity
                WHERE datname=:db AND usename=:app
            """), {"db": database, "app": app_role})).scalar_one()
            assert remaining == 0, "App session not drained"
        assert await _revision(mig_engine) == REV31A

        # Exactly one new migration CLI attempt after quiescence.
        _alembic(mig_url, REV31B)
        assert await _revision(mig_engine) == REV31B
        await _assert_denied_after_repair(mig_url, identity)

        async with mig_engine.connect() as cx:
            assert (await cx.execute(text("""
                SELECT COUNT(*) FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class rel ON rel.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace ns ON ns.oid=rel.relnamespace
                WHERE ns.nspname='public' AND t.tgname LIKE 'trg_aic_%'
                  AND t.tgenabled::text='O'
            """))).scalar_one() == 4
            assert (await cx.execute(text("""
                SELECT COUNT(*) FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
                WHERE ns.nspname='public' AND p.proname IN
                    ('aic_id_guard','aic_live_guard','aic_user_guard')
                  AND array_length(p.proconfig,1)=1
            """))).scalar_one() == 3

        # Reopen only AFTER durable version/guards and OS-process denial.
        async with admin.connect() as cx:
            await cx.exec_driver_sql(f"ALTER ROLE {app_role} LOGIN")
        fresh = await asyncpg.connect(dsn=blocked_dsn)
        try:
            assert await fresh.fetchval(
                "SELECT COUNT(*) FROM public.users WHERE id='alice'"
            ) == 1
        finally:
            await fresh.close()
    finally:
        if app_conn is not None:
            await app_conn.close()
        if mig_engine is not None:
            await mig_engine.dispose()
        await initial_engine.dispose()
        async with admin.connect() as cx:
            await cx.exec_driver_sql(
                f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"
            )
        if migration_role and app_role:
            await _drop_roles(admin, migration_role, app_role)
        await admin.dispose()
