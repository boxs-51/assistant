"""Strict composed PostgreSQL 17 CLI evidence for AIC 31b (evidence-only).

An immutable old PR494 checkout (old PR467 31a + genuine PG bootstrap)
performs a real Alembic upgrade to 31a on a disposable database. The new
PR500 + #494 env branch then runs actual Alembic CLI to 31b. This test never
uses alembic stamp, manually widens the version table, or rewrites 31a.
"""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


OLD_494_SHA = "f7efcd63c479626d43733ad836fc092db84a8506"
REV31A = "31a_aic0_agent_instance_identity"
REV31B = "31b_aic0_pg_guard_forward"


def _alembic(root: Path, url: str, revision: str) -> None:
    call = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini",
         "upgrade", revision],
        cwd=str(root), env=dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url),
        capture_output=True, text=True, timeout=240, check=False,
    )
    assert call.returncode == 0, (
        f"Real Alembic {root} → {revision} failed: {call.returncode}\n"
        f"stdout={call.stdout[-3000:]}\nstderr={call.stderr[-5000:]}"
    )


@pytest.mark.parametrize("history", ["actual_stamped_old31a", "pristine_to_31b"])
@pytest.mark.asyncio
async def test_aic31b_real_pg17_composed_cli_revision_and_retired_guard(
    request, history,
):
    if not os.environ.get("AIC_TEST_POSTGRES_URL") or not os.environ.get(
        "AIC31B_OLD494_WORKTREE"
    ):
        pytest.skip("composed evidence requires native PG17 and pinned old worktree")
    root = Path(__file__).resolve().parents[3]
    old_worktree = Path(os.environ["AIC31B_OLD494_WORKTREE"])
    expected = subprocess.check_output(
        ["git", "-C", str(old_worktree), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert expected == OLD_494_SHA, "Old 31a historical worktree pin mismatch"

    parent = os.environ["AIC_TEST_POSTGRES_URL"]
    admin = create_async_engine(parent, isolation_level="AUTOCOMMIT")
    name = "aic31b_composed_" + uuid4().hex[:14]
    async with admin.connect() as c:
        version = int((await c.execute(text(
            "SHOW server_version_num"
        ))).scalar_one())
        assert 170000 <= version < 180000
        await c.execute(text("CREATE DATABASE " + name))
    url = make_url(parent).set(database=name).render_as_string(
        hide_password=False
    )
    engine = create_async_engine(url)
    retired_id = "retired-31b-" + uuid4().hex[:22]

    async def read_one(sql, params=None):
        await engine.dispose()  # Old/new Alembic CLI alters backend schema.
        async with engine.connect() as cx:
            return (await cx.execute(text(sql), params or {})).scalar_one()

    try:
        if history == "actual_stamped_old31a":
            _alembic(old_worktree, url, "head")
            assert await read_one("SELECT version_num FROM alembic_version") == REV31A
            assert await read_one("""
                SELECT character_maximum_length
                  FROM information_schema.columns
                 WHERE table_schema='public' AND table_name='alembic_version'
                   AND column_name='version_num'
            """) >= 255
            assert await read_one("""
                SELECT p.proconfig IS NULL
                  FROM pg_catalog.pg_proc p
                  JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                 WHERE n.nspname='public' AND p.proname='aic_live_guard'
            """) is True, "old installed 31a must be unpinned"
            async with engine.begin() as c:
                await c.execute(text(
                    "INSERT INTO users(id,email,password_hash,status) "
                    "VALUES ('alice','alice@forward.invalid','x','active')"
                ))
                await c.execute(text(
                    "INSERT INTO aic_instance_reservations "
                    "(agent_instance_id,state,generation) "
                    "VALUES (:id,'ACTIVE',1)"
                ), {"id": retired_id})
                await c.execute(text(
                    "INSERT INTO aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,"
                    "definition_version,lifecycle_state,generation) "
                    "VALUES (:id,'alice','definition','v1','ACTIVE',1)"
                ), {"id": retired_id})
                await c.execute(text(
                    "UPDATE aic_instance_reservations SET state='RETIRING' "
                    "WHERE agent_instance_id=:id"
                ), {"id": retired_id})
                await c.execute(text(
                    "DELETE FROM aic_instance_live WHERE agent_instance_id=:id"
                ), {"id": retired_id})
                await c.execute(text(
                    "UPDATE aic_instance_reservations "
                    "SET state='RETIRED',generation=2 WHERE agent_instance_id=:id"
                ), {"id": retired_id})

            # Reproduce the old installed function's genuine susceptibility.
            # Never COMMIT the forged LIVE record.
            async with engine.connect() as c:
                await c.execute(text(
                    "CREATE TEMP TABLE users(id TEXT, status TEXT)"
                ))
                await c.execute(text(
                    "CREATE TEMP TABLE aic_instance_reservations"
                    "(agent_instance_id TEXT,state TEXT)"
                ))
                await c.execute(text(
                    "INSERT INTO pg_temp.users VALUES('alice','active')"
                ))
                await c.execute(text(
                    "INSERT INTO pg_temp.aic_instance_reservations "
                    "VALUES (:id,'ACTIVE')"
                ), {"id": retired_id})
                await c.execute(text("SET LOCAL search_path TO pg_temp, public"))
                await c.execute(text(
                    "INSERT INTO public.aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,"
                    "definition_version,lifecycle_state,generation)"
                    "VALUES (:id,'alice','forged','v1','ACTIVE',1)"
                ), {"id": retired_id})
                assert (await c.execute(text(
                    "SELECT COUNT(*) FROM public.aic_instance_live "
                    "WHERE agent_instance_id=:id"
                ), {"id": retired_id})).scalar_one() == 1
                await c.rollback()
            assert await read_one(
                "SELECT state FROM public.aic_instance_reservations "
                "WHERE agent_instance_id=:id", {"id": retired_id}
            ) == "RETIRED"

        # Real CLI on the composed tree. Old branch is unchanged and the
        # second process obtains a fresh physical connection.
        # Integrated alpha has multiple Alembic heads; this proof targets AIC31b only.
        _alembic(root, url, REV31B)
        assert await read_one("SELECT version_num FROM alembic_version") == REV31B
        assert await read_one("""
            SELECT character_maximum_length
              FROM information_schema.columns
             WHERE table_schema='public' AND table_name='alembic_version'
               AND column_name='version_num'
        """) >= 255
        assert await read_one("""
            SELECT COUNT(*)
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class rel ON rel.oid=t.tgrelid
            JOIN pg_catalog.pg_namespace rn ON rn.oid=rel.relnamespace
            JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
            WHERE rn.nspname='public' AND pn.nspname='public'
              AND t.tgname IN (
                  'trg_aic_id_guard','trg_aic_live_guard',
                  'trg_aic_user_delete','trg_aic_user_rekey'
              )
        """) == 4
        async with engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT p.proname, p.proconfig
                FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public'
                  AND p.proname IN ('aic_id_guard','aic_live_guard','aic_user_guard')
            """))).all()
            assert len(rows) == 3
            for name_, config in rows:
                assert config and len(config) == 1, (name_, config)
                assert config[0].startswith("search_path=pg_catalog,")
                assert config[0].endswith(", pg_temp")
        if history == "actual_stamped_old31a":
            # Real different OS process; pg_temp must no longer resurrect ID.
            worker = r"""
import asyncio,asyncpg,os,sys
async def main():
    dsn,marker=sys.argv[1:]
    c=await asyncpg.connect(dsn=dsn)
    await c.execute("CREATE TEMP TABLE users(id TEXT,status TEXT)")
    await c.execute("CREATE TEMP TABLE aic_instance_reservations(agent_instance_id TEXT,state TEXT)")
    await c.execute("INSERT INTO pg_temp.users VALUES ('alice','active')")
    await c.execute("INSERT INTO pg_temp.aic_instance_reservations VALUES ($1,'ACTIVE')",marker)
    await c.execute("SET search_path TO pg_temp,public")
    try:
        await c.execute(
            "INSERT INTO public.aic_instance_live "
            "(agent_instance_id,owner_user_id,definition_id,definition_version,lifecycle_state,generation)"
            " VALUES ($1,'alice','forged','v1','ACTIVE',1)",marker)
    except Exception as exc:
        if "AIC_LIVE_OWNER_OR_ID_INVALID" in str(exc):
            print("AIC31B_STAMPED_OLD31A_UPGRADE_DENIED",flush=True)
            os._exit(77)
        print("UNEXPECTED:"+str(exc),flush=True)
        os._exit(17)
    print("UNSAFE_RESURRECTION",flush=True)
    os._exit(19)
asyncio.run(main())
"""
            dsn = url.replace("postgresql+asyncpg://", "postgresql://")
            child = subprocess.run(
                [sys.executable, "-c", worker, dsn, retired_id],
                capture_output=True, text=True, timeout=40,
            )
            assert child.returncode == 77, (
                child.returncode, child.stdout, child.stderr
            )
            assert "AIC31B_STAMPED_OLD31A_UPGRADE_DENIED" in child.stdout
            assert await read_one(
                "SELECT state FROM public.aic_instance_reservations "
                "WHERE agent_instance_id=:id", {"id": retired_id}
            ) == "RETIRED"
            assert await read_one(
                "SELECT COUNT(*) FROM public.aic_instance_live "
                "WHERE agent_instance_id=:id", {"id": retired_id}
            ) == 0
    finally:
        await engine.dispose()
        async with admin.connect() as c:
            await c.execute(text("DROP DATABASE IF EXISTS " + name + " WITH (FORCE)"))
        await admin.dispose()
