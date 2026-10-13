"""Strict disposable PG17 Linux/native-Windows Alembic OS-interruption evidence.

Two physical cuts: kill migration while its 31b transaction is open (after
guard replacement but before commit), and kill the parent wrapper only AFTER
an authentic unmodified Alembic CLI has exited successfully. These are NOT
production migration admission, traffic drain, or operational authorization.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from se.tests.integration.test_aic0_31b_composed_alembic import (
    ROOT, SCRIPT_PATH, REV31A, REV31B, _new_database,
    _old31a_script_config, _alembic, _revision, _retire_raw,
    _assert_denied_after_repair,
)
from se.tests.integration.test_aic0_31b_quiescence_rehearsal import (
    _require_ephemeral_github_runner, _install_roles, _drop_roles, _role_url,
)


async def _pg_admin_count(admin, database, term):
    async with admin.connect() as connection:
        return (await connection.execute(text("""
            SELECT max(pid) FROM pg_catalog.pg_stat_activity
             WHERE datname=:db AND state='active'
               AND query LIKE :query
        """), {"db": database, "query": term})).scalar_one()


async def _guard_snapshot(engine):
    async with engine.connect() as connection:
        trigger_oids = (await connection.execute(text("""
            SELECT array_agg(t.oid ORDER BY t.tgname)
              FROM pg_catalog.pg_trigger t
              JOIN pg_catalog.pg_class rel ON rel.oid=t.tgrelid
              JOIN pg_catalog.pg_namespace ns ON ns.oid=rel.relnamespace
             WHERE ns.nspname='public'
               AND t.tgname IN ('trg_aic_id_guard','trg_aic_live_guard',
                                'trg_aic_user_delete','trg_aic_user_rekey')
        """))).scalar_one()
        functions = (await connection.execute(text("""
            SELECT p.proname, p.proconfig FROM pg_catalog.pg_proc p
              JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
             WHERE ns.nspname='public' AND p.pronargs=0 AND
               p.proname IN ('aic_id_guard','aic_live_guard','aic_user_guard')
             ORDER BY p.proname
        """))).all()
    assert trigger_oids and len(trigger_oids) == 4
    assert len(functions) == 3
    return tuple(trigger_oids), {name: config for name, config in functions}


def _fault_cut_config(tmp_path):
    """Test-copy ONLY: inject a sleep *inside* the real 31b upgrade transaction.

    The production migration, branch source and canonical Alembic script are
    unchanged. Validate byte-exact trusted production 31b before instrumenting.
    """
    source_file = ROOT / SCRIPT_PATH / "versions" / (REV31B + ".py")
    # GitHub stores LF bytes; Windows checkout can convert only newline bytes
    # to CRLF. Hash the canonical LF representation, refusing any other edit.
    # The copied test-only revision is also written with LF for deterministic
    # injection of the exact pre-commit fault cut on both native platforms.
    raw = source_file.read_bytes()
    old = raw.replace(b"\r\n", b"\n")
    assert b"\r" not in old, "Unexpected non-CRLF line ending in 31b source"
    blob = hashlib.sha1(b"blob " + str(len(old)).encode() + b"\0" + old).hexdigest()
    assert blob == "71ee2332d360ae7cdb851fc802aa43db384cb46c", (
        "31b production migration changed; fault-cut re-review required"
    )
    marker = b"\n\ndef downgrade() -> None:"
    assert old.count(marker) == 1, "Expected one complete production 31b upgrade"
    injection = (
        b'\n    op.execute(sa.text("SELECT pg_sleep(90) '
        b'/* AIC31B_PRECOMMIT_FAULT_CUT */"))'
    )
    copied = tmp_path / "aic_crash_cut_scripts"
    shutil.copytree(ROOT / SCRIPT_PATH, copied)
    (copied / "versions" / (REV31B + ".py")).write_bytes(
        old.replace(marker, injection + marker, 1)
    )
    ini = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    original = "script_location = se/src/infrastructure/storage/migrations/sql"
    assert ini.count(original) == 1
    conf = tmp_path / "aic_crash_cut.ini"
    conf.write_text(
        ini.replace(original, "script_location = " + copied.as_posix()),
        encoding="utf-8",
    )
    return conf


async def _wait_marker_or_exit(proc, predicate, *, timeout=85):
    loop = asyncio.get_running_loop()
    until = loop.time() + timeout
    while loop.time() < until:
        if await predicate():
            return
        if proc.poll() is not None:
            break
        await asyncio.sleep(0.3)
    stdout, stderr = proc.communicate(timeout=10) if proc.poll() is not None else ("", "")
    raise AssertionError(
        f"Never reached expected 31b fault cut: exit={proc.poll()}, "
        f"stdout={stdout[-1000:]}, stderr={stderr[-2500:]}"
    )


@pytest.mark.parametrize("cut", ["before_commit", "after_commit"])
@pytest.mark.asyncio
async def test_pg17_interrupted_31b_cli_fail_closed(cut, tmp_path):
    _require_ephemeral_github_runner()
    if os.getenv("AIC31B_EPHEMERAL_CRASH") != "1":
        pytest.skip("Explicit hosted PG17 crash rehearsal opt-in required")

    admin, initial_engine, database, admin_url = await _new_database()
    migration_role = app_role = None
    engine = None
    proc = None
    try:
        migration_role, migration_password, app_role, _ = await _install_roles(
            admin, database
        )
        url = _role_url(admin_url, migration_role, migration_password)
        engine = create_async_engine(url, pool_pre_ping=True)
        _alembic(url, REV31A, config=_old31a_script_config(tmp_path))
        assert await _revision(engine) == REV31A
        async with engine.begin() as cx:
            await cx.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@aic-crash.invalid','x','active')"
            ))
        identity = "retired-" + uuid4().hex
        await _retire_raw(engine, identity)
        before_oids, before_functions = await _guard_snapshot(engine)
        assert all(v is None for v in before_functions.values())

        # Held throughout migration AND postchecks; NOLOGIN does not drain
        # existing sessions, so verify no application-role backend survived.
        async with admin.connect() as cx:
            await cx.exec_driver_sql(f"ALTER ROLE {app_role} NOLOGIN")
            active = (await cx.execute(text("""
                SELECT count(*) FROM pg_catalog.pg_stat_activity
                WHERE datname=:db AND usename=:role
            """), {"db": database, "role": app_role})).scalar_one()
            assert active == 0

        env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
        backend_pid = None
        if cut == "before_commit":
            ini = _fault_cut_config(tmp_path)
            proc = subprocess.Popen(
                [sys.executable, "-m", "alembic", "-c", str(ini),
                 "upgrade", REV31B],
                cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            await _wait_marker_or_exit(
                proc,
                lambda: _pg_admin_count(
                    admin, database, "%AIC31B_PRECOMMIT_FAULT_CUT%"
                ),
            )
            backend_pid = await _pg_admin_count(
                admin, database, "%AIC31B_PRECOMMIT_FAULT_CUT%"
            )
            assert backend_pid, "PostgreSQL crash-cut backend was not observed"
        else:
            # The real, UNMODIFIED Alembic CLI completes and commits before
            # the process-wrapper readiness marker is written.
            flag = tmp_path / "committed.flag"
            wrapper = (
                "import os,subprocess,sys,time;from pathlib import Path;"
                "r=subprocess.run([sys.executable,'-m','alembic','-c',"
                "'alembic.ini','upgrade','31b_aic0_pg_guard_forward'],"
                "cwd=sys.argv[1],env=dict(os.environ,"
                "ASSISTANT_ALEMBIC_DATABASE_URL=sys.argv[2]),"
                "capture_output=True,text=True,timeout=180);"
                "assert r.returncode==0,(r.stdout,r.stderr);"
                "Path(sys.argv[3]).write_text('CLI_COMMITTED');time.sleep(90)"
            )
            proc = subprocess.Popen(
                [sys.executable, "-u", "-c", wrapper, str(ROOT), url, str(flag)],
                cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            await _wait_marker_or_exit(proc, lambda: asyncio.sleep(0, result=flag.exists()))

        # A hard OS-process termination, not graceful cancellation.
        proc.kill()
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode != 0, (stdout[-1000:], stderr[-2500:])
        proc = None
        if backend_pid is not None:
            # An OS-killed client can leave pg_sleep executing for its full
            # duration before the server discovers the disconnected socket.
            # Terminate exactly the observed, disposable test backend to
            # model the server-side cancellation cut and avoid stale locks.
            # This requires the already-verified ephemeral hosted PG profile.
            async with admin.connect() as cx:
                await cx.execute(text(
                    "SELECT pg_catalog.pg_terminate_backend(:pid)"
                ), {"pid": backend_pid})

        # New physical connection after OS kill: distinguish rollback from
        # already-committed upgrade, with no ambiguous revision stamp.
        expected = REV31A if cut == "before_commit" else REV31B
        for _ in range(40):
            try:
                assert await _revision(engine) == expected
                break
            except Exception:
                await asyncio.sleep(0.4)
        else:
            pytest.fail("OS cut left missing/ambiguous Alembic revision")

        after_oids, after_functions = await _guard_snapshot(engine)
        assert after_oids == before_oids, "Physical trigger identities changed"
        if cut == "before_commit":
            assert all(v is None for v in after_functions.values()), (
                "31b function changes leaked from aborted migration"
            )
            # No automatic retry: simulate a separately authorized fresh CLI
            # only while isolated. The genuine unmodified 31b must succeed.
            _alembic(url, REV31B)
            assert await _revision(engine) == REV31B
        _, final_functions = await _guard_snapshot(engine)
        assert all(
            config and len(config) == 1
            and config[0].startswith("search_path=pg_catalog,")
            and config[0].endswith(", pg_temp")
            for config in final_functions.values()
        ), "31b physical functions were not pinned after recovery"
        await _assert_denied_after_repair(url, identity)
        assert await _revision(engine) == REV31B
        async with engine.connect() as cx:
            assert (await cx.execute(text("""
                SELECT state FROM public.aic_instance_reservations
                WHERE agent_instance_id=:id
            """), {"id": identity})).scalar_one() == "RETIRED"
            assert (await cx.execute(text("""
                SELECT count(*) FROM public.aic_instance_live
                WHERE agent_instance_id=:id
            """), {"id": identity})).scalar_one() == 0
        # Deliberately NEVER reopen the application role in this evidence test.
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=20)
        if engine is not None:
            await engine.dispose()
        await initial_engine.dispose()
        async with admin.connect() as cx:
            await cx.exec_driver_sql(
                f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"
            )
        if migration_role and app_role:
            await _drop_roles(admin, migration_role, app_role)
        await admin.dispose()
