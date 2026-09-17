from __future__ import annotations

from sqlalchemy import update
from fastapi.encoders import jsonable_encoder

from ....runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
)
from ..models.sql.capability import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
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
        values["state"] = invocation.state.value
        values["wait_reason"] = (
            invocation.wait_reason.value if invocation.wait_reason else None
        )
        for field in ("arguments", "output", "error"):
            values[field] = jsonable_encoder(values[field])
        return values

    async def create(self, invocation: CapabilityInvocation) -> None:
        async with self._uow_factory() as uow:
            uow.session.add(CapabilityInvocationRecord(**self._values(invocation)))
            await uow.commit()

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
