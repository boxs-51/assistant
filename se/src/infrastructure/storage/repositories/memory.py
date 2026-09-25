from __future__ import annotations

import asyncio
import sqlite3
import weakref
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import event, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from se.src.context.memory import (
    MemoryRecord,
    MemoryRecordConflictError,
    _same_immutable_record,
    canonical_memory_bytes,
    validate_memory_record_integrity,
)
from se.src.context.source_identity import (
    ContextSourceRef,
    validate_context_source_ref_integrity,
)

from ..models.sql.memory import MemoryRecordRow


_SQLITE_BUSY_RETRIES = 6
_SQLITE_BUSY_BASE_DELAY_SECONDS = 0.01
_SQLITE_ADMISSION_TX_KEY = "ctx_f5_2_memory_admission_transaction"
_SQLITE_IN_MEMORY_OWNERS: dict[
    int,
    tuple[weakref.ReferenceType[AsyncSession], Any | None],
] = {}
_SQLITE_IN_MEMORY_PHYSICAL_STATE: weakref.WeakKeyDictionary[
    Any,
    dict[str, Any],
] = weakref.WeakKeyDictionary()


class MemoryAdmissionTransactionError(RuntimeError):
    """Raised when SQLite Memory admission starts after a read snapshot exists."""


def _parse_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("durable Memory created_at must be a non-empty ISO datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_exact_source_payload(
    stored_payload: dict[str, Any],
    source_ref: ContextSourceRef,
) -> None:
    reconstructed = source_ref.model_dump(mode="json")
    if canonical_memory_bytes(stored_payload) != canonical_memory_bytes(
        reconstructed
    ):
        raise ValueError(
            "durable Memory source_ref_json was type-coerced or is non-canonical"
        )


def _row_to_domain(row: MemoryRecordRow) -> MemoryRecord:
    if not isinstance(row.source_ref_json, dict):
        raise ValueError("durable Memory source_ref_json must be an object")
    if not isinstance(row.metadata_json, dict):
        raise ValueError("durable Memory metadata_json must be an object")

    stored_source_payload = dict(row.source_ref_json)
    source_ref = ContextSourceRef.model_validate(stored_source_payload)
    validate_context_source_ref_integrity(source_ref)
    _validate_exact_source_payload(stored_source_payload, source_ref)

    if row.owner_user_id != source_ref.owner_user_id:
        raise ValueError("durable Memory owner column conflicts with provenance")
    if row.source_context_source_id != source_ref.context_source_id:
        raise ValueError(
            "durable Memory source_context_source_id conflicts with provenance"
        )

    record = MemoryRecord(
        memory_id=row.memory_id,
        promotion_authority_id=row.promotion_authority_id,
        memory_schema_version=row.memory_schema_version,
        source_ref_snapshot=source_ref,
        owner_user_id=row.owner_user_id,
        content_digest=row.content_digest,
        canonical_bytes=row.canonical_bytes,
        content=row.content_json,
        metadata=dict(row.metadata_json),
        created_at=_parse_datetime(row.created_at),
    )
    return validate_memory_record_integrity(record)


def _row_values(record: MemoryRecord) -> dict[str, Any]:
    validate_memory_record_integrity(record)
    payload = record.model_dump(mode="json")
    source_payload = payload["source_ref_snapshot"]
    metadata_payload = payload["metadata"]

    if not isinstance(source_payload, dict):
        raise ValueError("Memory source snapshot must serialize as an object")
    if not isinstance(metadata_payload, dict):
        raise ValueError("Memory metadata must serialize as an object")

    return {
        "memory_id": record.memory_id,
        "promotion_authority_id": record.promotion_authority_id,
        "memory_schema_version": record.memory_schema_version,
        "owner_user_id": record.owner_user_id,
        "source_context_source_id": record.source_ref_snapshot.context_source_id,
        "source_ref_json": source_payload,
        "content_digest": record.content_digest,
        "canonical_bytes": record.canonical_bytes,
        "content_json": payload["content"],
        "metadata_json": metadata_payload,
        "created_at": payload["created_at"],
    }


def _require_replay_equivalence(
    winner: MemoryRecord,
    incoming: MemoryRecord,
) -> MemoryRecord:
    if not _same_immutable_record(winner, incoming):
        raise MemoryRecordConflictError(
            "durable Memory identity already exists with conflicting immutable record"
        )
    return winner


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
    return "database is locked" in message or "database table is locked" in message


def _is_in_memory_sqlite(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "sqlite" and bind.url.database in (None, "", ":memory:")


def _in_memory_owner_key(session: AsyncSession) -> int:
    return id(session.get_bind())


def _ensure_in_memory_physical_monitor(
    session: AsyncSession,
) -> dict[str, Any] | None:
    if not _is_in_memory_sqlite(session):
        return None

    bind = session.get_bind()
    state = _SQLITE_IN_MEMORY_PHYSICAL_STATE.get(bind)
    if state is not None:
        return state

    state = {
        "active": False,
        "owner_connection": None,
        "logical_connections": set(),
        "quarantined_dbapi_connections": weakref.WeakSet(),
    }
    _SQLITE_IN_MEMORY_PHYSICAL_STATE[bind] = state

    original_do_rollback = bind.dialect.do_rollback
    original_do_commit = bind.dialect.do_commit

    def _dbapi_in_transaction(dbapi_connection) -> bool:
        driver_connection = getattr(
            dbapi_connection,
            "driver_connection",
            None,
        )
        return bool(getattr(driver_connection, "in_transaction", False))

    def _driver_in_transaction(connection) -> bool:
        connection_proxy = getattr(connection, "connection", None)
        return (
            connection_proxy is not None
            and _dbapi_in_transaction(connection_proxy)
        )

    def _quarantine_connection(connection) -> None:
        connection_proxy = getattr(connection, "connection", None)
        if connection_proxy is not None:
            state["quarantined_dbapi_connections"].add(connection_proxy)

    def _guarded_do_rollback(dbapi_connection) -> None:
        quarantined = state["quarantined_dbapi_connections"]
        if (
            dbapi_connection in quarantined
            and _dbapi_in_transaction(dbapi_connection)
        ):
            return
        quarantined.discard(dbapi_connection)
        original_do_rollback(dbapi_connection)

    def _guarded_do_commit(dbapi_connection) -> None:
        quarantined = state["quarantined_dbapi_connections"]
        if (
            dbapi_connection in quarantined
            and _dbapi_in_transaction(dbapi_connection)
        ):
            return
        quarantined.discard(dbapi_connection)
        original_do_commit(dbapi_connection)

    def _shared_dbapi_in_transaction() -> bool:
        pool = getattr(bind, "pool", None)
        if pool is None:
            return False
        connection_record = getattr(pool, "__dict__", {}).get("connection")
        if connection_record is None:
            return False
        dbapi_connection = getattr(connection_record, "dbapi_connection", None)
        if dbapi_connection is None:
            return False
        return _dbapi_in_transaction(dbapi_connection)

    def _refresh_physical_owner() -> bool:
        owner_connection = state["owner_connection"]
        if state["active"] and owner_connection is not None:
            try:
                still_active = _driver_in_transaction(owner_connection)
            except BaseException:
                still_active = False
            if not still_active:
                state["active"] = False
                state["owner_connection"] = None
            return still_active

        # The physical SQLite transaction may have started before this monitor
        # existed, so the in-memory state bit cannot be the sole authority.
        # When the shared StaticPool driver is already transactional and no
        # monitored owner is known, fail closed as active/unknown-owner. The
        # cursor guard will quarantine any logical Connection that tries to
        # attach to it until the raw transaction ends.
        try:
            raw_active = _shared_dbapi_in_transaction()
        except BaseException:
            raw_active = False
        if raw_active:
            state["active"] = True
            state["owner_connection"] = None
            return True

        state["active"] = False
        state["owner_connection"] = None
        return False

    def _guard_connection_begin(connection) -> None:
        physical_active = _refresh_physical_owner()
        owner_connection = state["owner_connection"]
        if physical_active and owner_connection is not connection:
            _quarantine_connection(connection)
            if owner_connection is None:
                raise MemoryAdmissionTransactionError(
                    "SQLite in-memory shared physical connection has a "
                    "pre-existing transaction"
                )
            raise MemoryAdmissionTransactionError(
                "SQLite in-memory shared physical connection is already owned "
                "by another logical connection"
            )

        # The physical transaction may predate monitor installation. SQLAlchemy
        # has already created B's logical transaction by this event, so mark
        # the rejected Connection as quarantined before raising. Its later
        # rollback/close is blocked until the raw shared transaction ends.
        if not physical_active and _driver_in_transaction(connection):
            _quarantine_connection(connection)
            raise MemoryAdmissionTransactionError(
                "SQLite in-memory shared physical connection has a "
                "pre-existing transaction"
            )

        state["logical_connections"].add(connection)

    def _before_cursor_execute(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        physical_active = _refresh_physical_owner()
        owner_connection = state["owner_connection"]
        if physical_active and owner_connection is not connection:
            _quarantine_connection(connection)
            if owner_connection is None:
                raise MemoryAdmissionTransactionError(
                    "SQLite in-memory shared physical connection has a "
                    "pre-existing transaction"
                )
            raise MemoryAdmissionTransactionError(
                "SQLite in-memory shared physical connection is already owned "
                "by another logical connection"
            )

        normalized = statement.lstrip().upper()
        if normalized.startswith(
            (
                "BEGIN",
                "SAVEPOINT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "REPLACE",
                "CREATE",
                "DROP",
                "ALTER",
            )
        ):
            state["active"] = True
            state["owner_connection"] = connection

    def _guard_transaction_end(connection) -> None:
        state["logical_connections"].discard(connection)

    # Connection event guards can reject SQL, but Connection.close() may later
    # invoke pool rollback-on-return without another Connection rollback event.
    # Guard the dialect DBAPI mutation itself for the specific rejected
    # ConnectionFairy so explicit rollback/commit and pool reset are both
    # harmless while another logical connection owns the raw transaction.
    bind.dialect.do_rollback = _guarded_do_rollback
    bind.dialect.do_commit = _guarded_do_commit

    event.listen(bind, "begin", _guard_connection_begin)
    event.listen(bind, "before_cursor_execute", _before_cursor_execute)
    event.listen(bind, "commit", _guard_transaction_end)
    event.listen(bind, "rollback", _guard_transaction_end)
    return state


def _in_memory_pool_has_physical_transaction(session: AsyncSession) -> bool:
    bind = session.get_bind()
    pool = getattr(bind, "pool", None)
    if pool is None:
        return False

    # Do not access StaticPool.connection unless it already exists: the
    # memoized property may create/connect synchronously. We only need to
    # inspect the existing single connection record used by :memory: SQLite.
    connection_record = getattr(pool, "__dict__", {}).get("connection")
    if connection_record is None:
        return False
    dbapi_connection = getattr(connection_record, "dbapi_connection", None)
    if dbapi_connection is None:
        return False
    driver_connection = getattr(
        dbapi_connection,
        "driver_connection",
        None,
    )
    return bool(getattr(driver_connection, "in_transaction", False))


def _assert_in_memory_physical_idle(session: AsyncSession) -> None:
    state = _ensure_in_memory_physical_monitor(session)
    if state is None:
        return

    # This detects a transaction that started before the Memory repository
    # installed its engine monitor. It runs before Session.execute(), so a
    # rejected Memory admission never attaches this Session to the shared
    # DBAPI connection.
    if not state["active"] and _in_memory_pool_has_physical_transaction(session):
        raise MemoryAdmissionTransactionError(
            "SQLite in-memory shared physical connection has a pre-existing "
            "transaction"
        )
    if state["active"]:
        owner_connection = state["owner_connection"]
        if owner_connection is not None:
            try:
                connection_proxy = getattr(owner_connection, "connection", None)
                driver_connection = getattr(
                    connection_proxy,
                    "driver_connection",
                    None,
                )
                if not bool(
                    getattr(driver_connection, "in_transaction", False)
                ):
                    state["active"] = False
                    state["owner_connection"] = None
            except BaseException:
                state["active"] = False
                state["owner_connection"] = None
    if state["active"]:
        raise MemoryAdmissionTransactionError(
            "SQLite in-memory shared physical connection is already inside "
            "another physical transaction"
        )


def _clear_stale_in_memory_owner(session: AsyncSession) -> None:
    if not _is_in_memory_sqlite(session):
        return
    key = _in_memory_owner_key(session)
    owner_state = _SQLITE_IN_MEMORY_OWNERS.get(key)
    if owner_state is None:
        return

    owner_ref, admission_transaction = owner_state
    owner = owner_ref()
    if owner is None:
        _SQLITE_IN_MEMORY_OWNERS.pop(key, None)
        return

    # A None transaction is the short provisional interval between claiming
    # ownership and BEGIN IMMEDIATE establishing the exact admission tx.
    if admission_transaction is None:
        return

    if owner.get_transaction() is not admission_transaction:
        _SQLITE_IN_MEMORY_OWNERS.pop(key, None)


def _claim_in_memory_owner(session: AsyncSession) -> None:
    if not _is_in_memory_sqlite(session):
        return
    _clear_stale_in_memory_owner(session)
    key = _in_memory_owner_key(session)
    owner_state = _SQLITE_IN_MEMORY_OWNERS.get(key)
    owner = owner_state[0]() if owner_state is not None else None
    if owner is not None and owner is not session:
        raise MemoryAdmissionTransactionError(
            "concurrent SQLite in-memory Memory admission is already owned by "
            "another session on the shared connection"
        )
    _SQLITE_IN_MEMORY_OWNERS[key] = (weakref.ref(session), None)


def _mark_in_memory_owner_transaction(
    session: AsyncSession,
    transaction: Any,
) -> None:
    if not _is_in_memory_sqlite(session):
        return
    key = _in_memory_owner_key(session)
    owner_state = _SQLITE_IN_MEMORY_OWNERS.get(key)
    owner = owner_state[0]() if owner_state is not None else None
    if owner is not session:
        raise MemoryAdmissionTransactionError(
            "SQLite in-memory Memory admission lost logical transaction ownership"
        )
    _SQLITE_IN_MEMORY_OWNERS[key] = (weakref.ref(session), transaction)


def _release_in_memory_owner(session: AsyncSession) -> None:
    if not _is_in_memory_sqlite(session):
        return
    key = _in_memory_owner_key(session)
    owner_state = _SQLITE_IN_MEMORY_OWNERS.get(key)
    owner = owner_state[0]() if owner_state is not None else None
    if owner is session:
        _SQLITE_IN_MEMORY_OWNERS.pop(key, None)


async def _begin_sqlite_write_intent(session: AsyncSession) -> None:
    current = session.get_transaction()
    marked = session.info.get(_SQLITE_ADMISSION_TX_KEY)
    if current is not None:
        if marked is current:
            return
        raise MemoryAdmissionTransactionError(
            "SQLite durable Memory admission requires write intent before any "
            "outer transaction read; enter sqlite_memory_admission_transaction() "
            "before reading or call put() on a clean session."
        )

    in_memory = _is_in_memory_sqlite(session)
    _claim_in_memory_owner(session)
    try:
        if in_memory:
            _assert_in_memory_physical_idle(session)
        for attempt in range(_SQLITE_BUSY_RETRIES + 1):
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                current = session.get_transaction()
                if current is None:
                    raise RuntimeError(
                        "SQLite Memory admission failed to establish outer transaction"
                    )
                session.info[_SQLITE_ADMISSION_TX_KEY] = current
                _mark_in_memory_owner_transaction(session, current)
                return
            except OperationalError as exc:
                if in_memory:
                    # Never roll back or clear shared physical-busy state after
                    # a failed BEGIN. The underlying transaction may belong to
                    # another logical Session (including one that began before
                    # this monitor was installed). Fail closed until an actual
                    # connection commit/rollback event proves it ended.
                    raise MemoryAdmissionTransactionError(
                        "SQLite in-memory Memory admission could not acquire "
                        "its shared-connection write transaction"
                    ) from exc

                # File-backed SQLite gives this logical Session its own DBAPI
                # transaction. The clean-session BEGIN attempt is ours to clear
                # before retry/final propagation.
                await session.rollback()
                if (
                    not _is_sqlite_busy_error(exc)
                    or attempt >= _SQLITE_BUSY_RETRIES
                ):
                    raise
                await asyncio.sleep(
                    _SQLITE_BUSY_BASE_DELAY_SECONDS * (attempt + 1)
                )
    except BaseException:
        if session.get_transaction() is None:
            _release_in_memory_owner(session)
        raise


@asynccontextmanager
async def sqlite_memory_admission_transaction(
    session: AsyncSession,
) -> AsyncIterator["DurableMemoryRecordRepository"]:
    """Own one SQLite admission transaction with write intent before any read."""
    if session.get_bind().dialect.name != "sqlite":
        raise MemoryAdmissionTransactionError(
            "sqlite_memory_admission_transaction requires a SQLite session"
        )
    if session.in_transaction():
        raise MemoryAdmissionTransactionError(
            "SQLite Memory admission scope must start before any outer "
            "transaction/read snapshot."
        )

    await _begin_sqlite_write_intent(session)
    repository = DurableMemoryRecordRepository(session)
    try:
        yield repository
        await session.commit()
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
    finally:
        session.info.pop(_SQLITE_ADMISSION_TX_KEY, None)
        _release_in_memory_owner(session)


class DurableMemoryRecordRepository:
    """Transaction-scoped exact admission/replay persistence for CTX-F5-2."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        _ensure_in_memory_physical_monitor(session)

    async def get(self, memory_id: str) -> MemoryRecord | None:
        row = await self.session.get(MemoryRecordRow, memory_id)
        if row is None:
            return None
        return _row_to_domain(row)

    async def get_by_promotion_authority(
        self,
        promotion_authority_id: str,
    ) -> MemoryRecord | None:
        result = await self.session.execute(
            select(MemoryRecordRow)
            .where(
                MemoryRecordRow.promotion_authority_id
                == promotion_authority_id
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return _row_to_domain(row)

    async def _resolve_winner(self, record: MemoryRecord) -> MemoryRecord | None:
        promotion_winner = await self.get_by_promotion_authority(
            record.promotion_authority_id
        )
        if (
            promotion_winner is not None
            and promotion_winner.memory_id != record.memory_id
        ):
            raise MemoryRecordConflictError(
                "promotion_authority_id already maps to a different Memory identity"
            )

        winner = await self.get(record.memory_id)
        if winner is None:
            winner = promotion_winner
        if winner is None:
            return None
        return _require_replay_equivalence(winner, record)

    async def _put_sqlite(
        self,
        record: MemoryRecord,
        values: dict[str, Any],
    ) -> MemoryRecord:
        statement = (
            sqlite_insert(MemoryRecordRow)
            .values(**values)
            .on_conflict_do_nothing()
        )

        for attempt in range(_SQLITE_BUSY_RETRIES + 1):
            try:
                async with self.session.begin_nested():
                    result = await self.session.execute(statement)
            except OperationalError as exc:
                if (
                    not _is_sqlite_busy_error(exc)
                    or attempt >= _SQLITE_BUSY_RETRIES
                ):
                    raise
                await asyncio.sleep(
                    _SQLITE_BUSY_BASE_DELAY_SECONDS * (attempt + 1)
                )
                continue

            winner = await self._resolve_winner(record)
            if winner is None:
                if result.rowcount:
                    raise RuntimeError(
                        "durable Memory insert succeeded but winner was not readable"
                    )
                raise MemoryRecordConflictError(
                    "durable Memory uniqueness conflict has no readable winner"
                )
            return winner

        raise RuntimeError("unreachable SQLite Memory admission retry state")

    async def _put_generic(
        self,
        record: MemoryRecord,
        values: dict[str, Any],
    ) -> MemoryRecord:
        row = MemoryRecordRow(**values)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            winner = await self._resolve_winner(record)
            if winner is None:
                raise
            return winner
        return _row_to_domain(row)

    async def put(self, record: MemoryRecord) -> MemoryRecord:
        validate_memory_record_integrity(record)
        values = _row_values(record)
        dialect_name = self.session.get_bind().dialect.name

        if dialect_name == "sqlite":
            await _begin_sqlite_write_intent(self.session)
            return await self._put_sqlite(record, values)
        return await self._put_generic(record, values)
