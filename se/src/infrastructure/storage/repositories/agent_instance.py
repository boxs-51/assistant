"""Transactional nonwired AIC identity repository; never commits or opens another Session."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.sql.agent.agent_instance import (
    AgentInstanceLiveRecord,
    AgentInstanceReservationRecord,
)
from ..models.sql.user_data.user import User


class AgentInstanceDenied(ValueError):
    """An identity, lifecycle, ownership or transaction boundary failed closed."""


class AgentInstanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _owner_lock(self, owner_user_id: str) -> None:
        """Obtain SQLite write intent/PG non-key-update-conflicting lock BEFORE reading."""
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise AgentInstanceDenied("AIC owner invalid")
        if self.session.in_transaction():
            raise AgentInstanceDenied("AIC requires a fresh caller UoW session")
        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            # SQLite :memory: / shared-cache URI databases can reuse one physical
            # connection between nominally distinct AsyncSessions. AIC's
            # cross-connection owner-status ordering is not established there.
            # Refuse these backends BEFORE the first SQL statement.
            url = self.session.get_bind().url
            database = url.database
            if (
                not isinstance(database, str)
                or not database
                or database == ":memory:"
                or (
                    database.startswith("file:")
                    and (
                        ":memory:" in database
                        or "mode=memory" in database
                        or url.query.get("mode") == "memory"
                    )
                )
            ):
                raise AgentInstanceDenied("AIC SQLite in-memory backend unsupported")
            # SQLAlchemy Session autobegin is virtual until the first statement;
            # explicit BEGIN IMMEDIATE is first physical statement on its connection.
            try:
                await self.session.execute(text("BEGIN IMMEDIATE"))
            except Exception as exc:
                raise AgentInstanceDenied("AIC SQLite write-intent unavailable") from exc
            stmt = select(User.status).where(User.id == owner_user_id)
        elif dialect == "postgresql":
            stmt = select(User.status).where(User.id == owner_user_id).with_for_update(read=True)
        else:
            raise AgentInstanceDenied("Unsupported AIC database dialect")
        status = (await self.session.execute(stmt)).scalar_one_or_none()
        if status != "active":
            raise AgentInstanceDenied("AIC owner is not active")

    async def create(self, owner_user_id: str, definition_id: str, definition_version: str):
        if not all(
            isinstance(v, str) and v.strip() and len(v) <= 255
            for v in (owner_user_id, definition_id, definition_version)
        ):
            raise AgentInstanceDenied("AIC identity or definition invalid")
        await self._owner_lock(owner_user_id)
        instance_id = uuid4().hex
        self.session.add(AgentInstanceReservationRecord(
            agent_instance_id=instance_id, state="ACTIVE", generation=1
        ))
        await self.session.flush()
        live = AgentInstanceLiveRecord(
            agent_instance_id=instance_id,
            owner_user_id=owner_user_id,
            definition_id=definition_id,
            definition_version=definition_version,
            lifecycle_state="ACTIVE",
            generation=1,
        )
        self.session.add(live)
        await self.session.flush()
        return live

    async def _owned(self, owner_user_id: str, instance_id: str, *, active_only: bool):
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise AgentInstanceDenied("AIC instance invalid")
        await self._owner_lock(owner_user_id)
        query = (
            select(AgentInstanceLiveRecord)
            .join(
                AgentInstanceReservationRecord,
                AgentInstanceLiveRecord.agent_instance_id
                == AgentInstanceReservationRecord.agent_instance_id,
            )
            .where(
                AgentInstanceLiveRecord.agent_instance_id == instance_id,
                AgentInstanceLiveRecord.owner_user_id == owner_user_id,
                AgentInstanceReservationRecord.state == "ACTIVE",
            )
        )
        row = (await self.session.execute(query)).scalar_one_or_none()
        if row is None or (active_only and row.lifecycle_state != "ACTIVE"):
            raise AgentInstanceDenied("AIC instance unavailable")
        return row

    async def resolve(self, owner_user_id: str, instance_id: str):
        return await self._owned(owner_user_id, instance_id, active_only=True)

    async def suspend(self, owner_user_id: str, instance_id: str, expected_generation: int):
        row = await self._owned(owner_user_id, instance_id, active_only=True)
        if isinstance(expected_generation, bool) or expected_generation != row.generation:
            raise AgentInstanceDenied("AIC stale generation")
        if expected_generation >= 9223372036854775807:
            raise AgentInstanceDenied("AIC generation exhausted")
        result = await self.session.execute(
            update(AgentInstanceLiveRecord).where(
                AgentInstanceLiveRecord.agent_instance_id == instance_id,
                AgentInstanceLiveRecord.owner_user_id == owner_user_id,
                AgentInstanceLiveRecord.lifecycle_state == "ACTIVE",
                AgentInstanceLiveRecord.generation == expected_generation,
            ).values(lifecycle_state="SUSPENDED", generation=expected_generation + 1)
        )
        if result.rowcount != 1:
            raise AgentInstanceDenied("AIC concurrent suspension")
        await self.session.flush()
        return expected_generation + 1

    async def retire(self, owner_user_id: str, instance_id: str, expected_generation: int):
        row = await self._owned(owner_user_id, instance_id, active_only=False)
        if isinstance(expected_generation, bool) or expected_generation != row.generation:
            raise AgentInstanceDenied("AIC stale generation")
        if expected_generation >= 9223372036854775807:
            raise AgentInstanceDenied("AIC generation exhausted")
        # RETIRING exists only inside the caller's transaction: must never publish/commit it.
        changed = await self.session.execute(
            update(AgentInstanceReservationRecord).where(
                AgentInstanceReservationRecord.agent_instance_id == instance_id,
                AgentInstanceReservationRecord.state == "ACTIVE",
            ).values(state="RETIRING")
        )
        if changed.rowcount != 1:
            raise AgentInstanceDenied("AIC concurrent retirement")
        removed = await self.session.execute(
            delete(AgentInstanceLiveRecord).where(
                AgentInstanceLiveRecord.agent_instance_id == instance_id,
                AgentInstanceLiveRecord.owner_user_id == owner_user_id,
                AgentInstanceLiveRecord.generation == expected_generation,
            )
        )
        if removed.rowcount != 1:
            raise AgentInstanceDenied("AIC concurrent retirement")
        finished = await self.session.execute(
            update(AgentInstanceReservationRecord).where(
                AgentInstanceReservationRecord.agent_instance_id == instance_id,
                AgentInstanceReservationRecord.state == "RETIRING",
            ).values(state="RETIRED", generation=expected_generation + 1)
        )
        if finished.rowcount != 1:
            raise AgentInstanceDenied("AIC retirement did not complete")
        await self.session.flush()
        return expected_generation + 1


__all__ = ["AgentInstanceRepository", "AgentInstanceDenied"]
