from __future__ import annotations

from sqlalchemy import select, update
from fastapi.encoders import jsonable_encoder

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
)
from ..models.sql.capability import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
)


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
            uow.session.add(CapabilityInvocationRecord(**self._values(invocation)))
            await uow.commit()

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
