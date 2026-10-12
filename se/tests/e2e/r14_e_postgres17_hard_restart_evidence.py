"""Explicit-only PostgreSQL 17 hard-restart witness for AE-R14-E P1A.

The filename intentionally has no test_ prefix: normal full-suite/portable
Architecture jobs remain database-independent. Dedicated Linux and Windows
PostgreSQL CI MUST invoke this filename explicitly and cannot skip this test.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from se.tests.e2e.test_r14_e_sql_restart_agent_hitl_k1_k2 import (
    run_physical_hard_restart_witness,
)


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "approve_terminal",
    "deny_terminal",
    "effect_before_terminal",
])
async def test_postgres17_real_s1_kill_and_k2_rediscovery(tmp_path, scenario):
    url = os.environ.get("R14_E_P1A_POSTGRES_URL", "")
    assert url.startswith("postgresql+asyncpg://"), (
        "Real PostgreSQL 17 DSN is mandatory; no SQLite fallback/skip."
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            version = int((await conn.exec_driver_sql(
                "SHOW server_version_num"
            )).scalar_one())
            database = (await conn.exec_driver_sql(
                "SELECT current_database()"
            )).scalar_one()
            assert 170000 <= version < 180000, (
                f"Expected real PostgreSQL 17, got version_num={version}"
            )
            assert database == "r14e_p1a", (
                f"Refuse shared/unexpected test database {database!r}"
            )
    finally:
        await engine.dispose()

    # Each parameter has a distinct fresh database. Never let pre-existing
    # execution/claim rows from the previous hard-restart scenario pass a gate.
    database_names = {
        "approve_terminal": "r14e_p1a_approve",
        "deny_terminal": "r14e_p1a_deny",
        "effect_before_terminal": "r14e_p1a_unknown",
    }
    database_name = database_names[scenario]
    admin = await asyncpg.connect(
        url.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        await admin.execute(f"CREATE DATABASE {database_name}")
    finally:
        await admin.close()

    scoped_url = url.rsplit("/", 1)[0] + "/" + database_name
    await run_physical_hard_restart_witness(
        tmp_path, postgres_url=scoped_url, scenario=scenario,
    )
