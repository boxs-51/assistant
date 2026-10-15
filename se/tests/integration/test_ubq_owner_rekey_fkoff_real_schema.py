"""UBQ #141 / Human Owner T1-only: CAS-free owner REKEY physical RED-first.

Run the *actual* Alembic 30a -> AIC 31a -> 31b composed schema in disposable
SQLite files. A retained UBQ owner must not be deleted/rekeyed by raw SQL,
even when foreign_keys=OFF and recursive_triggers=OFF. All destructive
attempts are rolled back. Successful forbidden DML is a genuine test FAIL;
no mock schema, skip, xfail, SQL guard, migration, or production change.

This test does not certify #426 shared users REPLACE/W-K, CTX private memory,
R1/R2 erasure, PG17 two-OS parity, or production admission.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_HEAD = "31b_aic0_pg_guard_forward"
MODES = (
    pytest.param(0, 0, id="FK-OFF-recursive-OFF"),
    pytest.param(0, 1, id="FK-OFF-recursive-ON"),
    pytest.param(1, 0, id="FK-ON-recursive-OFF"),
    pytest.param(1, 1, id="FK-ON-recursive-ON"),
)


def _upgrade_real_schema(url: str) -> None:
    child = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini",
         "upgrade", ALEMBIC_HEAD],
        cwd=ROOT,
        env={**os.environ, "ASSISTANT_ALEMBIC_DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert child.returncode == 0, (
        "UBQ_T1_REAL_ALEMBIC_FAILED", child.returncode,
        child.stdout[-3000:], child.stderr[-5000:],
    )


async def _pragmas(conn, foreign_keys: int, recursive: int) -> None:
    await conn.exec_driver_sql(f"PRAGMA foreign_keys={foreign_keys}")
    await conn.exec_driver_sql(f"PRAGMA recursive_triggers={recursive}")
    actual_fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()
    actual_recursive = (
        await conn.exec_driver_sql("PRAGMA recursive_triggers")
    ).scalar_one()
    assert (actual_fk, actual_recursive) == (foreign_keys, recursive)


async def _scalar(engine, sql: str, params=None):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar_one()


async def _assert_unchanged(engine, *, full_history: bool) -> None:
    # New connection: read the on-disk database rather than an uncommitted
    # mutation's connection. CAS, CTX, and AIC owner links must be ABSENT.
    async with engine.connect() as conn:
        users = (await conn.execute(text(
            "SELECT id,email,password_hash FROM users ORDER BY id"
        ))).all()
        assert users == [
            ("alice", "alice@ubq.invalid", "original-alice"),
            ("bob", "bob@ubq.invalid", "original-bob"),
        ], users
        assert (await conn.execute(text(
            "SELECT owner_user_id FROM user_budget_policies"
        ))).scalar_one() == "alice"
        assert (await conn.execute(text(
            "SELECT owner_user_id FROM user_budget_accounts"
        ))).scalar_one() == "alice"

        expected = 1 if full_history else 0
        for table in (
            "user_budget_windows",
            "user_tool_budget_usage",
            "user_budget_reservations",
            "user_budget_task_bindings",
            "agent_tasks",
        ):
            count = (await conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            )).scalar_one()
            assert count == expected, (table, count, expected)
        if full_history:
            for table in (
                "user_budget_windows",
                "user_tool_budget_usage",
                "user_budget_reservations",
                "user_budget_task_bindings",
            ):
                assert (await conn.execute(
                    text(f"SELECT owner_user_id FROM {table}")
                )).scalar_one() == "alice", table
            assert (await conn.execute(text(
                "SELECT created_by FROM agent_tasks"
            ))).scalar_one() == "alice"

        for table in ("files", "memory_records", "aic_instance_live"):
            assert (await conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            )).scalar_one() == 0, table


async def _probe(
    engine, *, foreign_keys: int, recursive: int, name: str, sql: str
):
    """Record a real accepted SQL operation; always ROLLBACK its effects."""
    async with engine.connect() as conn:
        await _pragmas(conn, foreign_keys, recursive)
        observation = {"case": name, "denied": False}
        try:
            result = await conn.execute(text(sql))
            observation["affected_rows"] = result.rowcount
            for table in (
                "user_budget_policies",
                "user_budget_accounts",
                "user_budget_windows",
                "user_tool_budget_usage",
                "user_budget_reservations",
                "user_budget_task_bindings",
            ):
                observation[table + "_orphans"] = (
                    await conn.execute(text(
                        f"SELECT COUNT(*) FROM {table} x LEFT JOIN users u "
                        "ON x.owner_user_id=u.id WHERE u.id IS NULL"
                    ))
                ).scalar_one()
            observation["task_created_by_orphans"] = (
                await conn.execute(text(
                    "SELECT COUNT(*) FROM agent_tasks t LEFT JOIN users u "
                    "ON t.created_by=u.id WHERE u.id IS NULL"
                ))
            ).scalar_one()
            observation["alice_exists"] = (
                await conn.execute(text(
                    "SELECT COUNT(*) FROM users WHERE id='alice'"
                ))
            ).scalar_one()
        except DBAPIError as exc:
            observation["denied"] = True
            observation["db_error_type"] = type(exc.orig).__name__
            observation["db_error"] = str(exc.orig)[:200]
        finally:
            await conn.rollback()
    return observation


async def _assert_do_nothing(engine, foreign_keys: int, recursive: int):
    # Genuine winner no-op: never mistake a REPLACE for create-or-get.
    async with engine.connect() as conn:
        await _pragmas(conn, foreign_keys, recursive)
        await conn.execute(text(
            "INSERT INTO users(id,email,password_hash,status) VALUES "
            "('alice','ignored@ubq.invalid','poison','active') "
            "ON CONFLICT DO NOTHING"
        ))
        assert (await conn.execute(text(
            "SELECT email,password_hash FROM users WHERE id='alice'"
        ))).one() == ("alice@ubq.invalid", "original-alice")
        await conn.rollback()


async def _precommit_os_exit(
    engine, db_path: Path, foreign_keys: int, recursive: int,
):
    # Independent native SQLite client in a separate PID. Killing it before
    # COMMIT tests physical rollback/reopen; it does NOT repair a rekey hole.
    worker = r"""
import os, sqlite3, sys
db, fk, recursive = sys.argv[1:]
conn = sqlite3.connect(db, timeout=20)
conn.execute("PRAGMA foreign_keys=" + fk)
conn.execute("PRAGMA recursive_triggers=" + recursive)
conn.execute("BEGIN IMMEDIATE")
try:
    conn.execute("UPDATE users SET id='alice-crash' WHERE id='alice'")
    print("UBQ_T1_PRECOMMIT_REKEY_ACCEPTED", flush=True)
except sqlite3.DatabaseError as exc:
    print("UBQ_T1_PRECOMMIT_REKEY_DENIED:" + type(exc).__name__, flush=True)
os._exit(76)
"""
    child = subprocess.run(
        [sys.executable, "-c", worker, str(db_path),
         str(foreign_keys), str(recursive)],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=50,
    )
    assert child.returncode == 76, (
        "UBQ_T1_CHILD_EXIT", child.returncode, child.stdout, child.stderr
    )
    assert "UBQ_T1_PRECOMMIT_REKEY_" in child.stdout, child.stdout
    assert await _scalar(engine, (
        "SELECT COUNT(*) FROM users WHERE id='alice'"
    )) == 1
    return child.stdout.strip()



async def _seed_ubq_only_real_schema(conn, full_history: bool) -> None:
    """Identical real UBQ fixture for original T1 and additive U4A oracles."""
    await conn.execute(text(
        "INSERT INTO users(id,email,password_hash,status) VALUES "
        "('alice','alice@ubq.invalid','original-alice','active'),"
        "('bob','bob@ubq.invalid','original-bob','active')"
    ))
    await conn.execute(text(
        "INSERT INTO user_budget_policies "
        "(policy_id,owner_user_id,policy_version,policy_fingerprint,"
        "window_duration_seconds,tool_limits_json) "
        "VALUES ('policy1','alice','v1',:fp,3600,'{}')"
    ), {"fp": "a" * 64})
    await conn.execute(text(
        "INSERT INTO user_budget_accounts"
        "(owner_user_id,next_window_epoch) VALUES ('alice',2)"
    ))
    if full_history:
        await conn.execute(text(
            "INSERT INTO user_budget_windows "
            "(owner_user_id,epoch,governing_policy_id,"
            "governing_policy_version,governing_policy_fingerprint,"
            "started_at,expires_at) VALUES "
            "('alice',1,'policy1','v1',:fp,"
            "'2026-01-01 00:00:00','2026-01-01 01:00:00')"
        ), {"fp": "a" * 64})
        await conn.execute(text(
            "INSERT INTO user_tool_budget_usage "
            "(owner_user_id,window_epoch,capability_id) "
            "VALUES ('alice',1,'tool.demo')"
        ))
        await conn.execute(text(
            "INSERT INTO user_budget_reservations "
            "(reservation_id,owner_user_id,window_epoch,"
            "idempotency_key,resource_kind,reserved_amount_atomic,"
            "attribution_json,payload_fingerprint) VALUES "
            "('reservation1','alice',1,'key1','INFERENCE_CALL',"
            "1,'{}',:fp)"
        ), {"fp": "b" * 64})
        await conn.execute(text(
            "INSERT INTO agent_tasks "
            "(id,session_id,created_by,assigned_agent_id,status,"
            "wait_reasons,input) VALUES "
            "('task1','session','alice','agent','CREATED','[]','{}')"
        ))
        await conn.execute(text(
            "INSERT INTO user_budget_task_bindings "
            "(task_id,owner_user_id,enrollment_version,"
            "source_auth_type,resolution_fingerprint) VALUES "
            "('task1','alice','v1','JWT',:fp)"
        ), {"fp": "c" * 64})


async def _u4a_retained_snapshot(engine):
    """Independent, complete source-row readthrough; includes quota/reservation."""
    tables = (
        "users",
        "user_budget_policies",
        "user_budget_accounts",
        "user_budget_windows",
        "user_tool_budget_usage",
        "user_budget_reservations",
        "user_budget_task_bindings",
        "user_budget_dual_accounting_receipts",
        "agent_tasks",
    )
    async with engine.connect() as conn:
        return {
            table: tuple(
                tuple(row) for row in (
                    await conn.exec_driver_sql(
                        f"SELECT * FROM {table} ORDER BY 1"
                    )
                ).all()
            )
            for table in tables
        }


async def _u4a_one_victim_probe(engine, *, foreign_keys, recursive, name, sql):
    """Run one raw user-identity DML and observe before always rolling back."""
    async with engine.connect() as conn:
        await _pragmas(conn, foreign_keys, recursive)
        witness = {"case": name, "denied": False}
        try:
            result = await conn.execute(text(sql))
            witness["affected_rows"] = result.rowcount
            witness["users_in_transaction"] = [
                tuple(row) for row in (
                    await conn.execute(text(
                        "SELECT id,email,password_hash FROM users ORDER BY id"
                    ))
                ).all()
            ]
            for table in (
                "user_budget_policies",
                "user_budget_accounts",
                "user_budget_windows",
                "user_tool_budget_usage",
                "user_budget_reservations",
                "user_budget_task_bindings",
            ):
                witness[table + "_orphans"] = (
                    await conn.execute(text(
                        f"SELECT COUNT(*) FROM {table} x "
                        "LEFT JOIN users u ON x.owner_user_id=u.id "
                        "WHERE u.id IS NULL"
                    ))
                ).scalar_one()
        except IntegrityError as exc:
            # A syntax, setup or connection error must never count as DENY.
            witness["denied"] = True
            witness["db_error_type"] = type(exc.orig).__name__
            witness["db_error"] = str(exc.orig)[:200]
            assert (
                str(exc.orig).startswith(("UBQ_", "UBQ2_"))
                or str(exc.orig) == "FOREIGN KEY constraint failed"
            ), ("UBQ_U4A_UNEXPECTED_DENIAL_SIGNATURE", name, str(exc.orig))
        finally:
            await conn.rollback()
    return witness


@pytest.mark.parametrize(("foreign_keys", "recursive"), MODES)
@pytest.mark.parametrize(
    "full_history", (False, True),
    ids=("UBQ-policy-account-only", "UBQ-window-receipt-task-binding"),
)
@pytest.mark.asyncio
async def test_ubq_only_user_owner_delete_rekey_fkoff_real_schema(
    tmp_path, foreign_keys, recursive, full_history
):
    """Zero CAS/CTX: physical DELETE and users.id REKEY must be DENIED."""
    db_path = tmp_path / ("ubqt1_" + uuid4().hex[:14] + ".db")
    url = "sqlite+aiosqlite:///" + str(db_path)
    _upgrade_real_schema(url)
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        assert await _scalar(
            engine, "SELECT version_num FROM alembic_version"
        ) == ALEMBIC_HEAD
        async with engine.begin() as conn:
            await _pragmas(conn, foreign_keys, recursive)
            sqlite_triggers = {
                row[0] for row in (await conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ))).all()
            }
            assert {
                "trg_ubq_user_history_delete",
                "trg_ubq2_user_binding_delete_restrict",
            }.issubset(sqlite_triggers), sqlite_triggers

            await _seed_ubq_only_real_schema(conn, full_history)

        await _assert_unchanged(engine, full_history=full_history)
        await _assert_do_nothing(engine, foreign_keys, recursive)
        mutations = [
            ("users-delete", "DELETE FROM users WHERE id='alice'"),
            ("users-id-rekey",
             "UPDATE users SET id='alice-moved' WHERE id='alice'"),
            ("ubq-account-owner-transfer",
             "UPDATE user_budget_accounts SET owner_user_id='bob' "
             "WHERE owner_user_id='alice'"),
            ("ubq-policy-owner-transfer",
             "UPDATE user_budget_policies SET owner_user_id='bob' "
             "WHERE policy_id='policy1'"),
        ]
        if full_history:
            mutations += [
                ("ubq-window-owner-transfer",
                 "UPDATE user_budget_windows SET owner_user_id='bob' "
                 "WHERE owner_user_id='alice'"),
                ("ubq-reservation-owner-transfer",
                 "UPDATE user_budget_reservations "
                 "SET owner_user_id='bob' WHERE reservation_id='reservation1'"),
                ("ubq-task-binding-owner-transfer",
                 "UPDATE user_budget_task_bindings SET owner_user_id='bob' "
                 "WHERE task_id='task1'"),
            ]
        witnesses = []
        for name, statement in mutations:
            witnesses.append(await _probe(
                engine, foreign_keys=foreign_keys, recursive=recursive,
                name=name, sql=statement,
            ))
            await _assert_unchanged(engine, full_history=full_history)
        crash = await _precommit_os_exit(
            engine, db_path, foreign_keys, recursive,
        )
        await _assert_unchanged(engine, full_history=full_history)
        print(
            "UBQ_T1_ZERO_CAS_PHYSICAL_WITNESSES",
            {"foreign_keys": foreign_keys, "recursive_triggers": recursive,
             "full_history": full_history, "cases": witnesses,
             "physical_precommit": crash},
        )
        violations = [case for case in witnesses if not case["denied"]]
        assert not violations, (
            "UBQ_T1_P0_FKOFF_RAW_REKEY_OR_OWNER_MUTATION_ACCEPTED; "
            "expected strict physical DENY: "
            + repr((foreign_keys, recursive, full_history, violations))
        )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(("foreign_keys", "recursive"), MODES)
@pytest.mark.parametrize(
    "full_history", (False, True),
    ids=("U4A-policy-account-only", "U4A-reservation-task-binding"),
)
@pytest.mark.asyncio
async def test_ubq_u4a_one_victim_replace_rekey_real_schema(
    tmp_path, foreign_keys, recursive, full_history
):
    """Strict CAS/CTX-free one-protected-user REPLACE/REKEY regression.

    Global K-ID/K-EMAIL two-victim and authenticated W/K registrar remain
    #426 S5/S6-only; this test makes no shared authority claim.
    """
    db_path = tmp_path / ("ubq_u4a_" + uuid4().hex[:14] + ".db")
    url = "sqlite+aiosqlite:///" + str(db_path)
    _upgrade_real_schema(url)
    engine = create_async_engine(url, pool_pre_ping=True)
    observations = []
    try:
        assert await _scalar(
            engine, "SELECT version_num FROM alembic_version"
        ) == ALEMBIC_HEAD
        async with engine.begin() as conn:
            await _pragmas(conn, foreign_keys, recursive)
            existing_triggers = {
                row[0] for row in (await conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ))).all()
            }
            assert {
                "trg_ubq_user_history_delete",
                "trg_ubq2_user_binding_delete_restrict",
            }.issubset(existing_triggers)
            await _seed_ubq_only_real_schema(conn, full_history)

        await _assert_unchanged(engine, full_history=full_history)
        original = await _u4a_retained_snapshot(engine)
        await _assert_do_nothing(engine, foreign_keys, recursive)
        assert await _u4a_retained_snapshot(engine) == original

        # Every statement targets Alice as the ONLY existing protected
        # victim; no ID-A + email-B two-victim collision belongs here.
        one_victim_sql = (
            (
                "same-id-insert-or-replace",
                "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice-replaced@ubq.invalid','poison','active')",
            ),
            (
                "same-id-replace",
                "REPLACE INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@ubq.invalid','poison','active')",
            ),
            (
                "email-only-insert-or-replace",
                "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
                "VALUES ('foreign-id','alice@ubq.invalid','poison','active')",
            ),
            (
                "owner-id-update-or-replace",
                "UPDATE OR REPLACE users SET id='alice-moved' "
                "WHERE id='alice'",
            ),
        )
        for name, statement in one_victim_sql:
            observation = await _u4a_one_victim_probe(
                engine, foreign_keys=foreign_keys, recursive=recursive,
                name=name, sql=statement,
            )
            observations.append(observation)
            # Verify the *entire* durable source/owner snapshot after
            # explicit rollback: policy identity, quota, reservation,
            # task binding, immutable source fingerprint and owner.
            assert await _u4a_retained_snapshot(engine) == original, (
                "UBQ_U4A_DURABLE_SOURCE_OR_OWNER_DRIFT", name, observation
            )
            await _assert_unchanged(engine, full_history=full_history)
    finally:
        await engine.dispose()

    # Independent native sqlite3 reopen, not the SQLAlchemy writer handle.
    with sqlite3.connect(db_path) as reopened:
        assert reopened.execute(
            "SELECT id,email,password_hash FROM users ORDER BY id"
        ).fetchall() == [
            ("alice", "alice@ubq.invalid", "original-alice"),
            ("bob", "bob@ubq.invalid", "original-bob"),
        ], "UBQ_U4A_REOPEN_ORIGINAL_USER_IDENTITY_DRIFT"

    print("UBQ_U4A_ONE_VICTIM_REAL_SCHEMA_WITNESSES", {
        "foreign_keys": foreign_keys,
        "recursive_triggers": recursive,
        "full_history": full_history,
        "cases": observations,
    })
    violations = [case for case in observations if not case["denied"]]
    assert not violations, (
        "UBQ_U4A_P0_ONE_VICTIM_REPLACE_OR_REKEY_ACCEPTED; "
        "all protected identity DML must be physically denied: "
        + repr((foreign_keys, recursive, full_history, violations))
    )
