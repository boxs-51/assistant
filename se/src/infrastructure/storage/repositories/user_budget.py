from __future__ import annotations

import asyncio
import sqlite3
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from se.src.domain.schemas.user_budget import (
    USER_BUDGET_INT64_MAX,
    UserBudgetPolicy,
    UserBudgetReservationIntent,
    UserBudgetReservationState,
    normalize_authoritative_utc,
)

from ..models.sql.user_budget import (
    UserBudgetAccountRecord,
    UserBudgetPolicyRecord,
    UserBudgetReservationRecord,
    UserBudgetWindowRecord,
    UserToolBudgetUsageRecord,
)


_SQLITE_BUSY_RETRIES = 6
_SQLITE_BUSY_BASE_DELAY_SECONDS = 0.01
_SQLITE_TX_KEY = "ubq1_sqlite_write_intent_transaction"
_SQLITE_IN_MEMORY_OWNERS: weakref.WeakKeyDictionary[Any, weakref.ReferenceType[AsyncSession]] = (
    weakref.WeakKeyDictionary()
)


class UserBudgetConflictError(RuntimeError):
    pass


class UserBudgetIntegrityError(RuntimeError):
    pass


class UserBudgetTransactionError(RuntimeError):
    pass


class UserBudgetSerializationError(UserBudgetTransactionError):
    pass


def _require_atomic_counter(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("user budget counters must be exact integers")
    if value < 0 or value > USER_BUDGET_INT64_MAX:
        raise ValueError("user budget counter exceeds signed BIGINT domain")
    return value


def _is_sqlite_busy_error(exc: OperationalError) -> bool:
    original = exc.orig
    code = getattr(original, "sqlite_errorcode", None)
    busy_codes = {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        getattr(sqlite3, "SQLITE_BUSY_SNAPSHOT", 517),
    }
    if code in busy_codes:
        return True
    message = str(original).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database schema is locked" in message
    )


def _is_in_memory_sqlite(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "sqlite" and bind.url.database in (None, "", ":memory:")


def _in_memory_pool_has_physical_transaction(session: AsyncSession) -> bool:
    bind = session.get_bind()
    pool = getattr(bind, "pool", None)
    if pool is None:
        return False
    connection_record = getattr(pool, "__dict__", {}).get("connection")
    if connection_record is None:
        return False
    dbapi_connection = getattr(connection_record, "dbapi_connection", None)
    driver_connection = getattr(dbapi_connection, "driver_connection", None)
    return bool(getattr(driver_connection, "in_transaction", False))


def _clear_stale_in_memory_owner(session: AsyncSession) -> None:
    if not _is_in_memory_sqlite(session):
        return
    bind = session.get_bind()
    owner_ref = _SQLITE_IN_MEMORY_OWNERS.get(bind)
    owner = owner_ref() if owner_ref is not None else None
    if owner is None or owner.get_transaction() is None:
        _SQLITE_IN_MEMORY_OWNERS.pop(bind, None)


def _claim_in_memory_owner(session: AsyncSession) -> None:
    if not _is_in_memory_sqlite(session):
        return
    _clear_stale_in_memory_owner(session)
    bind = session.get_bind()
    owner_ref = _SQLITE_IN_MEMORY_OWNERS.get(bind)
    owner = owner_ref() if owner_ref is not None else None
    if owner is not None and owner is not session:
        raise UserBudgetTransactionError(
            "SQLite in-memory UBQ transaction is owned by another logical session"
        )
    if owner is None and _in_memory_pool_has_physical_transaction(session):
        raise UserBudgetTransactionError(
            "SQLite in-memory UBQ transaction found a pre-existing physical transaction"
        )
    _SQLITE_IN_MEMORY_OWNERS[bind] = weakref.ref(session)


def _release_in_memory_owner_if_idle(session: AsyncSession) -> None:
    if not _is_in_memory_sqlite(session):
        return
    if session.get_transaction() is not None:
        return
    bind = session.get_bind()
    owner_ref = _SQLITE_IN_MEMORY_OWNERS.get(bind)
    owner = owner_ref() if owner_ref is not None else None
    if owner is session or owner is None:
        _SQLITE_IN_MEMORY_OWNERS.pop(bind, None)


async def _begin_sqlite_write_intent(session: AsyncSession) -> None:
    if session.get_bind().dialect.name != "sqlite":
        raise UserBudgetTransactionError(
            "SQLite write intent requested for a non-SQLite session"
        )

    current = session.get_transaction()
    marked = session.info.get(_SQLITE_TX_KEY)
    if current is not None:
        if marked is current:
            return
        raise UserBudgetTransactionError(
            "SQLite UBQ rollover requires BEGIN IMMEDIATE before any authority read"
        )

    _claim_in_memory_owner(session)
    try:
        for attempt in range(_SQLITE_BUSY_RETRIES + 1):
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                current = session.get_transaction()
                if current is None:
                    raise UserBudgetTransactionError(
                        "SQLite UBQ rollover failed to establish write transaction"
                    )
                session.info[_SQLITE_TX_KEY] = current
                return
            except OperationalError as exc:
                if _is_in_memory_sqlite(session):
                    raise UserBudgetSerializationError(
                        "SQLite in-memory UBQ rollover could not acquire write intent"
                    ) from exc
                if session.in_transaction():
                    await session.rollback()
                if (
                    not _is_sqlite_busy_error(exc)
                    or attempt >= _SQLITE_BUSY_RETRIES
                ):
                    raise UserBudgetSerializationError(
                        "SQLite UBQ rollover could not acquire write intent"
                    ) from exc
                await asyncio.sleep(
                    _SQLITE_BUSY_BASE_DELAY_SECONDS * (attempt + 1)
                )
    except BaseException:
        _release_in_memory_owner_if_idle(session)
        raise


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UserBudgetRepository:
    """Caller-UoW-scoped UBQ-1 durable persistence primitives."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_policy(
        self,
        owner_user_id: str,
        policy_id: str,
    ) -> Optional[UserBudgetPolicyRecord]:
        result = await self.session.execute(
            select(UserBudgetPolicyRecord)
            .where(
                UserBudgetPolicyRecord.owner_user_id == owner_user_id,
                UserBudgetPolicyRecord.policy_id == policy_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_policy_by_version(
        self,
        owner_user_id: str,
        policy_version: str,
    ) -> Optional[UserBudgetPolicyRecord]:
        result = await self.session.execute(
            select(UserBudgetPolicyRecord)
            .where(
                UserBudgetPolicyRecord.owner_user_id == owner_user_id,
                UserBudgetPolicyRecord.policy_version == policy_version,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_or_get_immutable_policy(
        self,
        policy: UserBudgetPolicy,
    ) -> UserBudgetPolicyRecord:
        values = policy.durable_values()
        existing = await self.get_policy(policy.owner_user_id, policy.policy_id)
        if existing is None:
            existing = await self._get_policy_by_version(
                policy.owner_user_id,
                policy.policy_version,
            )
        if existing is not None:
            if (
                existing.policy_id != policy.policy_id
                or existing.policy_version != policy.policy_version
                or existing.policy_fingerprint != policy.policy_fingerprint
            ):
                raise UserBudgetConflictError(
                    "user budget policy owner/version identity conflicts with immutable policy"
                )
            return existing

        row = UserBudgetPolicyRecord(**values)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            winner = await self.get_policy(policy.owner_user_id, policy.policy_id)
            if winner is None:
                winner = await self._get_policy_by_version(
                    policy.owner_user_id,
                    policy.policy_version,
                )
            if winner is None:
                raise
            if (
                winner.policy_id != policy.policy_id
                or winner.policy_version != policy.policy_version
                or winner.policy_fingerprint != policy.policy_fingerprint
            ):
                raise UserBudgetConflictError(
                    "concurrent user budget policy creation conflicts with immutable identity"
                )
            return winner
        return row

    async def get_account(
        self,
        owner_user_id: str,
        *,
        for_update: bool = False,
    ) -> Optional[UserBudgetAccountRecord]:
        statement = select(UserBudgetAccountRecord).where(
            UserBudgetAccountRecord.owner_user_id == owner_user_id
        )
        if for_update and self.session.get_bind().dialect.name != "sqlite":
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_or_get_account(
        self,
        owner_user_id: str,
    ) -> UserBudgetAccountRecord:
        existing = await self.get_account(owner_user_id)
        if existing is not None:
            return existing
        row = UserBudgetAccountRecord(
            owner_user_id=owner_user_id,
            revision=0,
            next_policy_id=None,
            active_window_epoch=None,
            next_window_epoch=1,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            winner = await self.get_account(owner_user_id)
            if winner is None:
                raise
            return winner
        return row

    async def select_next_policy(
        self,
        owner_user_id: str,
        *,
        expected_revision: int,
        next_policy_id: str,
    ) -> UserBudgetAccountRecord:
        policy = await self.get_policy(owner_user_id, next_policy_id)
        if policy is None:
            raise UserBudgetIntegrityError(
                "next policy must exist and belong to the same budget owner"
            )
        result = await self.session.execute(
            update(UserBudgetAccountRecord)
            .where(
                UserBudgetAccountRecord.owner_user_id == owner_user_id,
                UserBudgetAccountRecord.revision == expected_revision,
            )
            .values(
                next_policy_id=next_policy_id,
                revision=expected_revision + 1,
                updated_at=func.now(),
            )
            .returning(UserBudgetAccountRecord)
        )
        winner = result.scalar_one_or_none()
        await self.session.flush()
        if winner is None:
            raise UserBudgetConflictError("user budget account policy CAS lost")
        return winner

    async def _active_windows(
        self,
        owner_user_id: str,
    ) -> list[UserBudgetWindowRecord]:
        result = await self.session.execute(
            select(UserBudgetWindowRecord)
            .where(
                UserBudgetWindowRecord.owner_user_id == owner_user_id,
                UserBudgetWindowRecord.state == "ACTIVE",
            )
            .order_by(UserBudgetWindowRecord.epoch.asc())
        )
        return list(result.scalars().all())

    async def get_active_window(
        self,
        owner_user_id: str,
    ) -> Optional[UserBudgetWindowRecord]:
        account = await self.get_account(owner_user_id)
        windows = await self._active_windows(owner_user_id)
        if len(windows) > 1:
            raise UserBudgetIntegrityError(
                "multiple ACTIVE user budget windows exist for one owner"
            )
        active = windows[0] if windows else None
        if account is None:
            if active is not None:
                raise UserBudgetIntegrityError(
                    "ACTIVE user budget window exists without owner account"
                )
            return None
        if account.active_window_epoch is None:
            if active is not None:
                raise UserBudgetIntegrityError(
                    "account pointer is NULL while an ACTIVE window exists"
                )
            return None
        if active is None or active.epoch != account.active_window_epoch:
            raise UserBudgetIntegrityError(
                "account active_window_epoch does not match the ACTIVE window"
            )
        if account.next_window_epoch <= active.epoch:
            raise UserBudgetIntegrityError(
                "account next_window_epoch is not strictly monotonic"
            )
        return active

    async def rollover_window(
        self,
        owner_user_id: str,
        *,
        expected_account_revision: int,
        authoritative_server_now: datetime,
    ) -> UserBudgetWindowRecord:
        now = normalize_authoritative_utc(authoritative_server_now)
        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            await _begin_sqlite_write_intent(self.session)

        account = await self.get_account(
            owner_user_id,
            for_update=dialect != "sqlite",
        )
        if account is None:
            raise UserBudgetIntegrityError(
                "user budget account must exist before rollover"
            )
        if account.revision != expected_account_revision:
            raise UserBudgetConflictError("user budget account rollover CAS is stale")
        if account.next_policy_id is None:
            raise UserBudgetIntegrityError(
                "user budget account has no selected policy for the next window"
            )

        active = await self.get_active_window(owner_user_id)
        if active is not None and now < _db_utc(active.expires_at):
            return active

        policy = await self.get_policy(owner_user_id, account.next_policy_id)
        if policy is None:
            raise UserBudgetIntegrityError(
                "selected user budget policy is missing or belongs to another owner"
            )

        new_epoch = account.next_window_epoch
        if new_epoch <= 0 or new_epoch > USER_BUDGET_INT64_MAX:
            raise UserBudgetIntegrityError("next user budget window epoch is invalid")
        if active is not None and new_epoch <= active.epoch:
            raise UserBudgetIntegrityError("user budget window epoch would be reused")

        if active is not None:
            closed = await self.session.execute(
                update(UserBudgetWindowRecord)
                .where(
                    UserBudgetWindowRecord.owner_user_id == owner_user_id,
                    UserBudgetWindowRecord.epoch == active.epoch,
                    UserBudgetWindowRecord.revision == active.revision,
                    UserBudgetWindowRecord.state == "ACTIVE",
                )
                .values(
                    state="CLOSED",
                    closed_at=now,
                    revision=active.revision + 1,
                    updated_at=func.now(),
                )
            )
            if closed.rowcount != 1:
                raise UserBudgetConflictError("user budget window close CAS lost")

        new_window = UserBudgetWindowRecord(
            owner_user_id=owner_user_id,
            epoch=new_epoch,
            state="ACTIVE",
            governing_policy_id=policy.policy_id,
            governing_policy_version=policy.policy_version,
            governing_policy_fingerprint=policy.policy_fingerprint,
            started_at=now,
            expires_at=now + timedelta(seconds=policy.window_duration_seconds),
            revision=0,
        )
        self.session.add(new_window)
        await self.session.flush()

        if new_epoch >= USER_BUDGET_INT64_MAX:
            raise UserBudgetIntegrityError(
                "user budget next window epoch would overflow signed BIGINT"
            )

        account_result = await self.session.execute(
            update(UserBudgetAccountRecord)
            .where(
                UserBudgetAccountRecord.owner_user_id == owner_user_id,
                UserBudgetAccountRecord.revision == expected_account_revision,
                UserBudgetAccountRecord.next_window_epoch == new_epoch,
            )
            .values(
                active_window_epoch=new_epoch,
                next_window_epoch=new_epoch + 1,
                revision=expected_account_revision + 1,
                updated_at=func.now(),
            )
        )
        if account_result.rowcount != 1:
            raise UserBudgetConflictError("user budget rollover account CAS lost")
        await self.session.flush()

        refreshed = await self.get_active_window(owner_user_id)
        if refreshed is None or refreshed.epoch != new_epoch:
            raise UserBudgetIntegrityError(
                "user budget rollover did not establish the canonical active pointer"
            )
        return refreshed

    async def mutate_window_usage(
        self,
        owner_user_id: str,
        window_epoch: int,
        *,
        expected_revision: int,
        compute_used_atomic: int | None = None,
        compute_reserved_atomic: int | None = None,
        inference_used: int | None = None,
        inference_reserved: int | None = None,
        input_tokens_used: int | None = None,
        output_tokens_used: int | None = None,
        total_tokens_used: int | None = None,
        tokens_reserved: int | None = None,
        tool_calls_used: int | None = None,
        tool_calls_reserved: int | None = None,
        cost_used_atomic: int | None = None,
        cost_reserved_atomic: int | None = None,
    ) -> UserBudgetWindowRecord:
        supplied = {
            "compute_used_atomic": compute_used_atomic,
            "compute_reserved_atomic": compute_reserved_atomic,
            "inference_used": inference_used,
            "inference_reserved": inference_reserved,
            "input_tokens_used": input_tokens_used,
            "output_tokens_used": output_tokens_used,
            "total_tokens_used": total_tokens_used,
            "tokens_reserved": tokens_reserved,
            "tool_calls_used": tool_calls_used,
            "tool_calls_reserved": tool_calls_reserved,
            "cost_used_atomic": cost_used_atomic,
            "cost_reserved_atomic": cost_reserved_atomic,
        }
        values = {
            key: _require_atomic_counter(value)
            for key, value in supplied.items()
            if value is not None
        }
        if not values:
            raise ValueError("window usage mutation requires at least one target value")
        values.update(
            revision=expected_revision + 1,
            updated_at=func.now(),
        )
        result = await self.session.execute(
            update(UserBudgetWindowRecord)
            .where(
                UserBudgetWindowRecord.owner_user_id == owner_user_id,
                UserBudgetWindowRecord.epoch == window_epoch,
                UserBudgetWindowRecord.revision == expected_revision,
                UserBudgetWindowRecord.state == "ACTIVE",
            )
            .values(**values)
            .returning(UserBudgetWindowRecord)
        )
        row = result.scalar_one_or_none()
        await self.session.flush()
        if row is None:
            raise UserBudgetConflictError("user budget window usage CAS lost")
        return row

    async def get_tool_usage(
        self,
        owner_user_id: str,
        window_epoch: int,
        capability_id: str,
    ) -> Optional[UserToolBudgetUsageRecord]:
        return await self.session.get(
            UserToolBudgetUsageRecord,
            (owner_user_id, window_epoch, capability_id),
        )

    async def create_or_get_tool_usage(
        self,
        owner_user_id: str,
        window_epoch: int,
        capability_id: str,
    ) -> UserToolBudgetUsageRecord:
        if not capability_id:
            raise ValueError("canonical capability_id is required")
        existing = await self.get_tool_usage(
            owner_user_id,
            window_epoch,
            capability_id,
        )
        if existing is not None:
            return existing
        row = UserToolBudgetUsageRecord(
            owner_user_id=owner_user_id,
            window_epoch=window_epoch,
            capability_id=capability_id,
            used_calls=0,
            reserved_calls=0,
            revision=0,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            winner = await self.get_tool_usage(
                owner_user_id,
                window_epoch,
                capability_id,
            )
            if winner is None:
                raise
            return winner
        return row

    async def mutate_tool_usage(
        self,
        owner_user_id: str,
        window_epoch: int,
        capability_id: str,
        *,
        expected_revision: int,
        used_calls: int,
        reserved_calls: int,
    ) -> UserToolBudgetUsageRecord:
        result = await self.session.execute(
            update(UserToolBudgetUsageRecord)
            .where(
                UserToolBudgetUsageRecord.owner_user_id == owner_user_id,
                UserToolBudgetUsageRecord.window_epoch == window_epoch,
                UserToolBudgetUsageRecord.capability_id == capability_id,
                UserToolBudgetUsageRecord.revision == expected_revision,
            )
            .values(
                used_calls=_require_atomic_counter(used_calls),
                reserved_calls=_require_atomic_counter(reserved_calls),
                revision=expected_revision + 1,
                updated_at=func.now(),
            )
            .returning(UserToolBudgetUsageRecord)
        )
        row = result.scalar_one_or_none()
        await self.session.flush()
        if row is None:
            raise UserBudgetConflictError("per-capability user budget CAS lost")
        return row

    async def get_reservation(
        self,
        owner_user_id: str,
        idempotency_key: str,
    ) -> Optional[UserBudgetReservationRecord]:
        result = await self.session.execute(
            select(UserBudgetReservationRecord)
            .where(
                UserBudgetReservationRecord.owner_user_id == owner_user_id,
                UserBudgetReservationRecord.idempotency_key == idempotency_key,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_or_get_reservation(
        self,
        intent: UserBudgetReservationIntent,
    ) -> UserBudgetReservationRecord:
        existing = await self.get_reservation(
            intent.owner_user_id,
            intent.idempotency_key,
        )
        if existing is not None:
            if existing.payload_fingerprint != intent.payload_fingerprint:
                raise UserBudgetConflictError(
                    "user budget idempotency key conflicts with canonical payload"
                )
            return existing

        row = UserBudgetReservationRecord(
            reservation_id=intent.reservation_id,
            owner_user_id=intent.owner_user_id,
            window_epoch=intent.window_epoch,
            idempotency_key=intent.idempotency_key,
            resource_kind=intent.resource_kind.value,
            capability_id=intent.capability_id,
            reserved_amount_atomic=intent.reserved_amount_atomic,
            settled_amount_atomic=None,
            state=UserBudgetReservationState.RESERVED.value,
            attribution_json=dict(intent.attribution),
            payload_fingerprint=intent.payload_fingerprint,
            revision=0,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            winner = await self.get_reservation(
                intent.owner_user_id,
                intent.idempotency_key,
            )
            if winner is None:
                raise
            if winner.payload_fingerprint != intent.payload_fingerprint:
                raise UserBudgetConflictError(
                    "concurrent user budget idempotency key conflicts with payload"
                )
            return winner
        return row

    async def transition_reservation(
        self,
        owner_user_id: str,
        idempotency_key: str,
        *,
        expected_revision: int,
        target_state: UserBudgetReservationState | str,
        settled_amount_atomic: int | None = None,
        settled_at: datetime | None = None,
    ) -> UserBudgetReservationRecord:
        target = UserBudgetReservationState(target_state)
        if target is UserBudgetReservationState.RESERVED:
            raise ValueError("reservation transition cannot reopen RESERVED state")

        current = await self.get_reservation(owner_user_id, idempotency_key)
        if current is None:
            raise KeyError("unknown user budget reservation")
        if current.state != UserBudgetReservationState.RESERVED.value:
            if current.state == target.value:
                expected_amount = (
                    None
                    if settled_amount_atomic is None
                    else _require_atomic_counter(settled_amount_atomic)
                )
                if current.settled_amount_atomic == expected_amount:
                    return current
            raise UserBudgetConflictError(
                "terminal or OUTCOME_UNKNOWN reservation state cannot be reopened"
            )
        if current.revision != expected_revision:
            raise UserBudgetConflictError("user budget reservation CAS is stale")

        if target is UserBudgetReservationState.SETTLED:
            if settled_amount_atomic is None or settled_at is None:
                raise ValueError("SETTLED reservation requires amount and timestamp")
            amount = _require_atomic_counter(settled_amount_atomic)
            when = normalize_authoritative_utc(settled_at)
        else:
            if settled_amount_atomic is not None:
                raise ValueError(
                    "non-SETTLED reservation transition cannot set settled amount"
                )
            amount = None
            when = (
                None
                if settled_at is None
                else normalize_authoritative_utc(settled_at)
            )

        result = await self.session.execute(
            update(UserBudgetReservationRecord)
            .where(
                UserBudgetReservationRecord.owner_user_id == owner_user_id,
                UserBudgetReservationRecord.idempotency_key == idempotency_key,
                UserBudgetReservationRecord.revision == expected_revision,
                UserBudgetReservationRecord.state
                == UserBudgetReservationState.RESERVED.value,
            )
            .values(
                state=target.value,
                settled_amount_atomic=amount,
                settled_at=when,
                revision=expected_revision + 1,
                updated_at=func.now(),
            )
            .returning(UserBudgetReservationRecord)
        )
        row = result.scalar_one_or_none()
        await self.session.flush()
        if row is None:
            raise UserBudgetConflictError("user budget reservation CAS lost")
        return row
