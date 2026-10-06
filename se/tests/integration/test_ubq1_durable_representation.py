from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.user_budget import (
    USER_BUDGET_INT64_MAX,
    UserBudgetPolicy,
    UserBudgetReservationIntent,
    UserBudgetResourceKind,
)
from se.src.infrastructure.storage.repositories.user_budget import (
    UserBudgetConflictError,
    UserBudgetIntegrityError,
    UserBudgetRepository,
    UserBudgetSerializationError,
)


ROOT = Path(__file__).resolve().parents[3]


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


def _insert_user(connection: sqlite3.Connection, user_id: str) -> None:
    connection.execute(
        "INSERT INTO users (id, email, password_hash, status) VALUES (?, ?, ?, ?)",
        (user_id, f"{user_id}@example.test", "hash", "active"),
    )


def _insert_policy(
    connection: sqlite3.Connection,
    *,
    policy_id: str = "policy-1",
    owner_user_id: str = "user-1",
    fingerprint: str = "a" * 64,
) -> None:
    connection.execute(
        """
        INSERT INTO user_budget_policies (
            policy_id,
            owner_user_id,
            policy_version,
            policy_fingerprint,
            window_duration_seconds,
            tool_limits_json
        ) VALUES (?, ?, 'v1', ?, 3600, '{}')
        """,
        (policy_id, owner_user_id, fingerprint),
    )


def test_ubq1_25a_is_linear_and_sqlite_triggers_hold_with_fk_pragma_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ubq1-migration.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["28a_tbo1_task_policy_representation"]
    assert (
        script.get_revision("25a_ubq1_user_budget_foundation").down_revision
        == "24a_r12_stale_lease_scan_index"
    )

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        _insert_user(connection, "user-1")
        _insert_user(connection, "user-2")

        with pytest.raises(sqlite3.IntegrityError, match="UBQ_INTEGRITY_POLICY_OWNER"):
            _insert_policy(
                connection,
                policy_id="foreign-policy",
                owner_user_id="missing-user",
            )

        _insert_policy(connection)
        connection.execute(
            """
            INSERT INTO user_budget_accounts (
                owner_user_id, revision, next_policy_id,
                active_window_epoch, next_window_epoch
            ) VALUES ('user-1', 0, 'policy-1', NULL, 1)
            """
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_WINDOW_REFERENCE",
        ):
            connection.execute(
                """
                INSERT INTO user_budget_windows (
                    owner_user_id, epoch, state,
                    governing_policy_id, governing_policy_version,
                    governing_policy_fingerprint, started_at, expires_at
                ) VALUES (
                    'user-1', 1, 'ACTIVE',
                    'policy-1', 'v1', ?, ?, ?
                )
                """,
                (
                    "b" * 64,
                    "2026-09-29 00:00:00",
                    "2026-09-29 01:00:00",
                ),
            )

        connection.execute(
            """
            INSERT INTO user_budget_windows (
                owner_user_id, epoch, state,
                governing_policy_id, governing_policy_version,
                governing_policy_fingerprint, started_at, expires_at
            ) VALUES (
                'user-1', 1, 'ACTIVE',
                'policy-1', 'v1', ?, ?, ?
            )
            """,
            (
                "a" * 64,
                "2026-09-29 00:00:00",
                "2026-09-29 01:00:00",
            ),
        )
        connection.execute(
            """
            UPDATE user_budget_accounts
            SET active_window_epoch = 1, next_window_epoch = 2
            WHERE owner_user_id = 'user-1'
            """
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_TOOL_USAGE_WINDOW",
        ):
            connection.execute(
                """
                INSERT INTO user_tool_budget_usage (
                    owner_user_id, window_epoch, capability_id
                ) VALUES ('user-2', 1, 'tool.a')
                """
            )

        connection.execute(
            """
            INSERT INTO user_budget_reservations (
                reservation_id, owner_user_id, window_epoch,
                idempotency_key, resource_kind, capability_id,
                reserved_amount_atomic, state, attribution_json,
                payload_fingerprint
            ) VALUES (
                'reservation-trigger', 'user-1', 1,
                'logical-trigger', 'TOOL_CALL', 'tool.a',
                1, 'RESERVED', '{}', ?
            )
            """,
            ("c" * 64,),
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_RESERVATION_PROVENANCE_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE user_budget_reservations
                SET idempotency_key = 'rewritten'
                WHERE reservation_id = 'reservation-trigger'
                """
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_POLICY_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE user_budget_policies
                SET policy_version = 'v2'
                WHERE policy_id = 'policy-1'
                """
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_WINDOW_HISTORY_RESTRICTED",
        ):
            connection.execute(
                """
                DELETE FROM user_budget_windows
                WHERE owner_user_id = 'user-1' AND epoch = 1
                """
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_USER_HISTORY_RESTRICTED",
        ):
            connection.execute("DELETE FROM users WHERE id = 'user-1'")

        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="durable user-budget history"):
        command.downgrade(config, "24a_r12_stale_lease_scan_index")


def test_ubq1_empty_downgrade_drops_triggers_before_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ubq1-empty-downgrade.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    command.upgrade(config, "head")
    command.downgrade(config, "24a_r12_stale_lease_scan_index")

    connection = sqlite3.connect(database)
    try:
        ubq_tables = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'user_budget%'
            """
        ).fetchall()
        ubq_triggers = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'trigger' AND name LIKE 'trg_ubq_%'
            """
        ).fetchall()
        assert ubq_tables == []
        assert ubq_triggers == []
    finally:
        connection.close()


def _policy() -> UserBudgetPolicy:
    return UserBudgetPolicy(
        policy_id="policy-concurrency",
        owner_user_id="user-concurrency",
        policy_version="v1",
        window_duration_seconds=1,
        max_compute_units=Decimal("10.00000000"),
        max_inference_calls=10,
        max_input_tokens=100,
        max_output_tokens=100,
        max_total_tokens=200,
        max_tool_calls_total=10,
        default_per_tool_limit=5,
        tool_limits={"tool.a": 5},
        max_cost_usd=Decimal("1.00000000"),
    )


@pytest.fixture
def ubq1_concurrency_database(
    tmp_path: Path,
    monkeypatch,
) -> Path:
    database = tmp_path / "ubq1-concurrency.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    command.upgrade(_config(database), "head")

    raw = sqlite3.connect(database)
    try:
        _insert_user(raw, "user-concurrency")
        raw.commit()
    finally:
        raw.close()
    return database


@pytest.mark.asyncio
async def test_ubq1_sqlite_rollover_converges_and_idempotency_crosses_epochs(
    ubq1_concurrency_database: Path,
) -> None:
    database = ubq1_concurrency_database

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 0.01},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as session:
            repository = UserBudgetRepository(session)
            policy = await repository.create_or_get_immutable_policy(_policy())
            assert policy.policy_id == "policy-concurrency"
            account = await repository.create_or_get_account("user-concurrency")
            assert account.revision == 0
            account = await repository.select_next_policy(
                "user-concurrency",
                expected_revision=0,
                next_policy_id="policy-concurrency",
            )
            assert account.revision == 1
            await session.commit()

        started = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)

        async def _roll(expected_revision: int, now: datetime) -> int:
            async with sessions() as session:
                repository = UserBudgetRepository(session)
                window = await repository.rollover_window(
                    "user-concurrency",
                    expected_account_revision=expected_revision,
                    authoritative_server_now=now,
                )
                await session.commit()
                return window.epoch

        first = await asyncio.gather(
            _roll(1, started),
            _roll(1, started),
        )
        assert first == [1, 1]

        second_now = started + timedelta(seconds=2)

        locker = sqlite3.connect(database, timeout=0.01)
        try:
            locker.execute("BEGIN IMMEDIATE")
            with pytest.raises(UserBudgetSerializationError):
                await _roll(2, second_now)
        finally:
            locker.rollback()
            locker.close()

        async with sessions() as session:
            repository = UserBudgetRepository(session)
            after_busy = await repository.get_account("user-concurrency")
            assert after_busy is not None
            assert after_busy.active_window_epoch == 1
            assert after_busy.next_window_epoch == 2
            await session.rollback()

        second = await asyncio.gather(
            _roll(2, second_now),
            _roll(2, second_now),
        )
        assert second == [2, 2]

        async with sessions() as session:
            repository = UserBudgetRepository(session)
            account = await repository.get_account("user-concurrency")
            active = await repository.get_active_window("user-concurrency")
            assert account is not None
            assert active is not None
            assert account.active_window_epoch == 2
            assert account.next_window_epoch == 3
            assert active.epoch == 2

            historical = await repository.mutate_window_usage(
                "user-concurrency",
                1,
                expected_revision=1,
                inference_used=1,
                inference_reserved=0,
            )
            assert historical.state == "CLOSED"
            assert historical.inference_used == 1
            assert historical.revision == 2

            with pytest.raises(
                UserBudgetIntegrityError,
                match="committed user budget usage cannot decrease",
            ):
                await repository.mutate_window_usage(
                    "user-concurrency",
                    1,
                    expected_revision=2,
                    inference_used=0,
                )

            with pytest.raises(ValueError, match="signed BIGINT"):
                await repository.mutate_window_usage(
                    "user-concurrency",
                    1,
                    expected_revision=2,
                    inference_used=USER_BUDGET_INT64_MAX + 1,
                )

            original = UserBudgetReservationIntent(
                reservation_id="reservation-epoch-1",
                owner_user_id="user-concurrency",
                window_epoch=1,
                idempotency_key="logical-operation-1",
                resource_kind=UserBudgetResourceKind.TOOL_CALL,
                capability_id="tool.a",
                reserved_amount_atomic=1,
                attribution={"execution_id": "e1"},
            )
            winner = await repository.create_or_get_reservation(original)
            assert winner.window_epoch == 1

            replay = UserBudgetReservationIntent(
                reservation_id="reservation-epoch-2",
                owner_user_id="user-concurrency",
                window_epoch=2,
                idempotency_key="logical-operation-1",
                resource_kind=UserBudgetResourceKind.TOOL_CALL,
                capability_id="tool.a",
                reserved_amount_atomic=1,
                attribution={"execution_id": "e1"},
            )
            replay_winner = await repository.create_or_get_reservation(replay)
            assert replay_winner.reservation_id == "reservation-epoch-1"
            assert replay_winner.window_epoch == 1

            conflict = UserBudgetReservationIntent(
                reservation_id="reservation-conflict",
                owner_user_id="user-concurrency",
                window_epoch=2,
                idempotency_key="logical-operation-1",
                resource_kind=UserBudgetResourceKind.TOOL_CALL,
                capability_id="tool.a",
                reserved_amount_atomic=2,
                attribution={"execution_id": "e1"},
            )
            with pytest.raises(UserBudgetConflictError):
                await repository.create_or_get_reservation(conflict)

            settled_at = second_now + timedelta(seconds=1)
            settled = await repository.transition_reservation(
                "user-concurrency",
                "logical-operation-1",
                expected_revision=0,
                target_state="SETTLED",
                settled_amount_atomic=1,
                settled_at=settled_at,
            )
            assert settled.state == "SETTLED"
            assert settled.settled_amount_atomic == 1
            assert settled.revision == 1

            replay_settled = await repository.transition_reservation(
                "user-concurrency",
                "logical-operation-1",
                expected_revision=0,
                target_state="SETTLED",
                settled_amount_atomic=1,
                settled_at=settled_at,
            )
            assert replay_settled.reservation_id == settled.reservation_id
            assert replay_settled.revision == 1

            with pytest.raises(
                UserBudgetConflictError,
                match="cannot be reopened",
            ):
                await repository.transition_reservation(
                    "user-concurrency",
                    "logical-operation-1",
                    expected_revision=1,
                    target_state="RELEASED",
                )
            await session.rollback()
    finally:
        await engine.dispose()
