from __future__ import annotations

from hashlib import sha256

from sqlalchemy import false, func, select, update
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError

from ....runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from ....runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
    TERMINAL_INVOCATION_STATES,
)
from ..models.sql.agent.tool_call import AgentToolCallRecord
from ..models.sql.capability import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
)


class InvocationSerializationConflictError(RuntimeError):
    """Invocation id ownership could not be serialized safely."""


def _invocation_advisory_lock_key(invocation_id: str) -> int:
    digest = sha256(
        ("capability-invocation:" + str(invocation_id)).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class CapabilityInvocationRepository:
    """Transaction-scoped R6 invocation access for shared SQL UoWs."""

    def __init__(self, session) -> None:
        self.session = session

    async def get_record(
        self,
        invocation_id: str,
    ) -> CapabilityInvocationRecord | None:
        return await self.session.get(
            CapabilityInvocationRecord,
            invocation_id,
        )


    async def lock_invocation_id_serialization_key(
        self,
        invocation_id: str,
    ) -> None:
        """Lock one invocation id even when no CapabilityInvocation row exists.

        PostgreSQL uses a transaction-scoped advisory lock derived from a
        stable signed 64-bit SHA-256 key. SQLite has no per-key advisory lock,
        so a semantic no-op UPDATE with an always-false predicate deliberately
        acquires SQLite's database writer lock without mutating any row.
        Unsupported SQL dialects fail closed instead of silently providing an
        absent-key race.
        """

        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            key = _invocation_advisory_lock_key(invocation_id)
            await self.session.execute(
                select(func.pg_advisory_xact_lock(key))
            )
            return
        if dialect == "sqlite":
            await self.session.execute(
                update(CapabilityInvocationRecord)
                .where(false())
                .values(
                    revision=CapabilityInvocationRecord.revision,
                    updated_at=CapabilityInvocationRecord.updated_at,
                )
            )
            return
        raise RuntimeError(
            "Invocation-id serialization is unsupported for SQL dialect "
            f"{dialect!r}."
        )

    async def lock_invocation_gc_serialization_fence(
        self,
        invocation_id: str,
    ) -> CapabilityInvocationRecord | None:
        """Serialize invocation-id writers with R11 invocation GC.

        The shared key authority is acquired first and works even when the R6
        row is absent. Existing-row locking is retained as an additional fence;
        this operation remains serialization-only and does not take R6
        lifecycle ownership.
        """

        await self.lock_invocation_id_serialization_key(invocation_id)

        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            result = await self.session.execute(
                update(CapabilityInvocationRecord)
                .where(
                    CapabilityInvocationRecord.invocation_id == invocation_id
                )
                .values(
                    revision=CapabilityInvocationRecord.revision,
                    updated_at=CapabilityInvocationRecord.updated_at,
                )
            )
            if result.rowcount != 1:
                return None
            return await self.get_record(invocation_id)

        result = await self.session.execute(
            select(CapabilityInvocationRecord)
            .where(
                CapabilityInvocationRecord.invocation_id == invocation_id
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_agent_tool_call_bindings(
        self,
        invocation_id: str,
    ) -> list[AgentToolCallRecord]:
        result = await self.session.execute(
            select(AgentToolCallRecord)
            .where(AgentToolCallRecord.invocation_id == invocation_id)
            .order_by(AgentToolCallRecord.id.asc())
        )
        return list(result.scalars().all())

    async def list_records_for_execution(
        self,
        execution_id: str,
    ) -> list[CapabilityInvocationRecord]:
        """Read durable invocation authority for one AgentExecution.

        R8-C uses this exact query for side-effect safety planning.  The
        deterministic ordering is semantic-hash input only; it is not tool
        execution order.
        """
        result = await self.session.execute(
            select(CapabilityInvocationRecord)
            .where(
                CapabilityInvocationRecord.execution_id == execution_id
            )
            .order_by(CapabilityInvocationRecord.invocation_id.asc())
        )
        return list(result.scalars().all())


class SqlCapabilityInvocationStore:
    """Durable invocation authority with revision-based CAS transitions."""

    def __init__(self, uow_factory) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    def _values(invocation: CapabilityInvocation) -> dict:
        values = invocation.model_dump(mode="python")
        values["kind"] = invocation.kind.value
        values["execution_mode"] = invocation.execution_mode.value
        values["idempotency"] = invocation.idempotency.value
        values["state"] = invocation.state.value
        values["wait_reason"] = (
            invocation.wait_reason.value if invocation.wait_reason else None
        )
        values["remote_outcome_state"] = (
            invocation.remote_outcome_state.value
            if invocation.remote_outcome_state is not None
            else None
        )
        for field in ("arguments", "output", "error"):
            values[field] = jsonable_encoder(values[field])
        return values

    @staticmethod
    def _from_record(
        record: CapabilityInvocationRecord,
    ) -> CapabilityInvocation:
        return CapabilityInvocation(
            invocation_id=record.invocation_id,
            capability_id=record.capability_id,
            capability_version=record.capability_version,
            kind=CapabilityKind(record.kind),
            execution_mode=CapabilityExecutionMode(record.execution_mode),
            idempotency=CapabilityIdempotency(record.idempotency),
            request_fingerprint=record.request_fingerprint,
            owner_user_id=record.owner_user_id,
            origin_client_id=record.origin_client_id,
            remote_outcome_state=(
                RemoteOutcomeState(record.remote_outcome_state)
                if record.remote_outcome_state is not None
                else None
            ),
            implementation_id=record.implementation_id,
            driver_kind=record.driver_kind,
            state=CapabilityInvocationState(record.state),
            wait_reason=(
                CapabilityWaitReason(record.wait_reason)
                if record.wait_reason is not None
                else None
            ),
            session_id=record.session_id,
            turn_id=record.turn_id,
            execution_id=record.execution_id,
            workflow_id=record.workflow_id,
            tool_call_id=record.tool_call_id,
            connection_id=record.connection_id,
            attempt=record.attempt,
            max_attempts=record.max_attempts,
            arguments=dict(record.arguments or {}),
            output=record.output,
            error=record.error,
            created_at=record.created_at,
            started_at=record.started_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
            deadline_at=record.deadline_at,
            correlation_id=record.correlation_id,
            trace_id=record.trace_id,
            revision=record.revision,
        )

    @staticmethod
    def _attempt_from_record(
        record: CapabilityInvocationAttemptRecord,
    ) -> CapabilityInvocationAttempt:
        return CapabilityInvocationAttempt(
            attempt_id=record.attempt_id,
            invocation_id=record.invocation_id,
            attempt_number=record.attempt_number,
            implementation_id=record.implementation_id,
            driver_kind=record.driver_kind,
            connection_id=record.connection_id,
            state=CapabilityInvocationState(record.state),
            started_at=record.started_at,
            completed_at=record.completed_at,
            error=record.error,
            metadata=dict(record.metadata_json or {}),
        )

    async def create(self, invocation: CapabilityInvocation) -> None:
        async with self._uow_factory() as uow:
            repository = getattr(uow, "capability_invocations", None)
            if repository is None:
                repository = CapabilityInvocationRepository(uow.session)

            invocation_id = str(invocation.invocation_id)
            existing = await repository.lock_invocation_gc_serialization_fence(
                invocation_id
            )
            if existing is not None:
                raise InvocationSerializationConflictError(
                    "CapabilityInvocation id is already durably owned."
                )

            bindings = await repository.list_agent_tool_call_bindings(
                invocation_id
            )
            if len(bindings) > 1:
                raise InvocationSerializationConflictError(
                    "CapabilityInvocation id has ambiguous AgentToolCall ownership."
                )
            if bindings:
                binding = bindings[0]
                expected = {
                    "execution_id": binding.execution_id,
                    "tool_call_id": binding.tool_call_id,
                    "capability_id": binding.capability_id,
                }
                supplied = {
                    "execution_id": invocation.execution_id,
                    "tool_call_id": invocation.tool_call_id,
                    "capability_id": invocation.capability_id,
                }
                if supplied != expected:
                    raise InvocationSerializationConflictError(
                        "CapabilityInvocation id conflicts with durable "
                        "AgentToolCall ownership."
                    )

            uow.session.add(
                CapabilityInvocationRecord(**self._values(invocation))
            )
            try:
                await uow.commit()
            except IntegrityError:
                await uow.rollback()
                raise

    async def get(
        self, invocation_id: str
    ) -> CapabilityInvocation | None:
        async with self._uow_factory() as uow:
            record = await uow.session.get(
                CapabilityInvocationRecord,
                invocation_id,
            )
            return (
                self._from_record(record)
                if record is not None
                else None
            )

    async def compare_and_set(
        self, invocation: CapabilityInvocation, expected_revision: int
    ) -> bool:
        async with self._uow_factory() as uow:
            result = await uow.session.execute(
                update(CapabilityInvocationRecord)
                .where(
                    CapabilityInvocationRecord.invocation_id == invocation.invocation_id,
                    CapabilityInvocationRecord.revision == expected_revision,
                )
                .values(**self._values(invocation))
            )
            if result.rowcount != 1:
                await uow.rollback()
                return False
            await uow.commit()
            return True

    async def begin_continuation_attempt(
        self,
        invocation: CapabilityInvocation,
        expected_revision: int,
        attempt: CapabilityInvocationAttempt,
    ) -> bool:
        """Atomically CAS WAITING invocation state and insert attempt N+1."""
        expected_attempt = attempt.attempt_number - 1
        if (
            expected_attempt < 0
            or invocation.attempt != attempt.attempt_number
            or invocation.state is not CapabilityInvocationState.DISPATCHING
        ):
            return False

        async with self._uow_factory() as uow:
            try:
                result = await uow.session.execute(
                    update(CapabilityInvocationRecord)
                    .where(
                        CapabilityInvocationRecord.invocation_id
                        == invocation.invocation_id,
                        CapabilityInvocationRecord.revision
                        == expected_revision,
                        CapabilityInvocationRecord.state
                        == CapabilityInvocationState.WAITING.value,
                        CapabilityInvocationRecord.attempt
                        == expected_attempt,
                    )
                    .values(**self._values(invocation))
                )
                if result.rowcount != 1:
                    await uow.rollback()
                    return False

                prior_attempts = (
                    await uow.session.execute(
                        select(CapabilityInvocationAttemptRecord)
                        .where(
                            CapabilityInvocationAttemptRecord.invocation_id
                            == invocation.invocation_id
                        )
                        .order_by(
                            CapabilityInvocationAttemptRecord.attempt_number
                        )
                    )
                ).scalars().all()
                prior_numbers = [
                    int(item.attempt_number) for item in prior_attempts
                ]
                if prior_numbers != list(range(1, expected_attempt + 1)):
                    await uow.rollback()
                    return False
                terminal_values = {
                    item.value for item in TERMINAL_INVOCATION_STATES
                }
                if any(
                    item.state not in terminal_values
                    for item in prior_attempts
                ):
                    await uow.rollback()
                    return False

                values = attempt.model_dump(mode="python")
                values["state"] = attempt.state.value
                values["metadata_json"] = jsonable_encoder(
                    values.pop("metadata")
                )
                values["error"] = jsonable_encoder(values["error"])
                uow.session.add(
                    CapabilityInvocationAttemptRecord(**values)
                )
                await uow.commit()
                return True
            except IntegrityError:
                await uow.rollback()
                return False

    async def start_continuation_attempt(
        self,
        invocation: CapabilityInvocation,
        expected_revision: int,
        attempt: CapabilityInvocationAttempt,
    ) -> bool:
        """Atomically move continuation invocation+attempt to RUNNING."""
        if (
            invocation.state is not CapabilityInvocationState.RUNNING
            or attempt.state is not CapabilityInvocationState.RUNNING
            or attempt.invocation_id != invocation.invocation_id
            or attempt.attempt_number != invocation.attempt
        ):
            return False

        async with self._uow_factory() as uow:
            invocation_result = await uow.session.execute(
                update(CapabilityInvocationRecord)
                .where(
                    CapabilityInvocationRecord.invocation_id
                    == invocation.invocation_id,
                    CapabilityInvocationRecord.revision
                    == expected_revision,
                    CapabilityInvocationRecord.state
                    == CapabilityInvocationState.DISPATCHING.value,
                    CapabilityInvocationRecord.attempt
                    == invocation.attempt,
                )
                .values(**self._values(invocation))
            )
            if invocation_result.rowcount != 1:
                await uow.rollback()
                return False

            attempt_result = await uow.session.execute(
                update(CapabilityInvocationAttemptRecord)
                .where(
                    CapabilityInvocationAttemptRecord.attempt_id
                    == attempt.attempt_id,
                    CapabilityInvocationAttemptRecord.invocation_id
                    == invocation.invocation_id,
                    CapabilityInvocationAttemptRecord.attempt_number
                    == invocation.attempt,
                    CapabilityInvocationAttemptRecord.state
                    == CapabilityInvocationState.DISPATCHING.value,
                )
                .values(state=CapabilityInvocationState.RUNNING.value)
            )
            if attempt_result.rowcount != 1:
                await uow.rollback()
                return False
            await uow.commit()
            return True

    async def list_attempts(
        self, invocation_id: str
    ) -> list[CapabilityInvocationAttempt]:
        async with self._uow_factory() as uow:
            records = (
                await uow.session.execute(
                    select(CapabilityInvocationAttemptRecord)
                    .where(
                        CapabilityInvocationAttemptRecord.invocation_id
                        == invocation_id
                    )
                    .order_by(
                        CapabilityInvocationAttemptRecord.attempt_number
                    )
                )
            ).scalars().all()
            return [
                self._attempt_from_record(record)
                for record in records
            ]

    async def save_attempt(self, attempt: CapabilityInvocationAttempt) -> None:
        values = attempt.model_dump(mode="python")
        values["state"] = attempt.state.value
        values["metadata_json"] = jsonable_encoder(values.pop("metadata"))
        values["error"] = jsonable_encoder(values["error"])
        async with self._uow_factory() as uow:
            uow.session.add(CapabilityInvocationAttemptRecord(**values))
            await uow.commit()

    async def update_attempt(self, attempt: CapabilityInvocationAttempt) -> None:
        values = attempt.model_dump(mode="python")
        values["state"] = attempt.state.value
        values["metadata_json"] = jsonable_encoder(values.pop("metadata"))
        values["error"] = jsonable_encoder(values["error"])
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(CapabilityInvocationAttemptRecord)
                .where(CapabilityInvocationAttemptRecord.attempt_id == attempt.attempt_id)
                .values(**values)
            )
            await uow.commit()
