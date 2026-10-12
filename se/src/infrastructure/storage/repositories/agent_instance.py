"""Caller-transaction-scoped AgentInstance persistence; no implicit commits."""
from __future__ import annotations
from uuid import uuid4
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from ..models.sql.agent.agent_instance import AgentInstanceIdReservationRecord as Reservation
from ..models.sql.agent.agent_instance import AgentInstanceLiveRecord as Live
from ..models.sql.user_data.user import User
from ....domain.schemas.agent_instance import AgentInstanceDenied, AgentInstanceConflict


class AgentInstanceRepository:
    def __init__(self, session):
        self.session = session

    async def _active_owner(self, owner_id: str) -> None:
        if not owner_id:
            raise AgentInstanceDenied("trusted owner required")
        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            # No SELECT/autobegin before this lock acquisition: an earlier read can
            # establish a stale snapshot. The write is a no-op but takes the real
            # SQLite writer lock on this SAME physical UoW connection.
            if self.session.in_transaction() or self.session.dirty or self.session.new:
                raise AgentInstanceDenied("unmarked prior session transaction")
            try:
                await self.session.execute(
                    update(User).where(User.id == owner_id).values(status=User.status)
                )
            except (DBAPIError, OperationalError) as exc:
                raise AgentInstanceDenied("owner write-fence failed") from exc
        elif dialect == "postgresql":
            if self.session.in_transaction():
                raise AgentInstanceDenied("preexisting transaction before owner lock")
        else:
            raise AgentInstanceDenied("unsupported owner-lock dialect")
        statement = select(User).where(User.id == owner_id)
        if dialect == "postgresql":
            # FOR SHARE conflicts with non-key UPDATE of users.status.
            statement = statement.with_for_update(read=True)
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None or row.status != "active":
            raise AgentInstanceDenied("owner is not active")

    async def create(self, owner_id: str, definition_id: str, definition_version: str, instance_id: str | None = None):
        if not definition_id or not definition_version:
            raise AgentInstanceDenied("versioned definition required")
        # Caller-provided instance IDs are forbidden for new registration.
        if instance_id is not None:
            raise AgentInstanceDenied("server-issued instance ID only")
        await self._active_owner(owner_id)
        instance_id = str(uuid4())
        self.session.add(Reservation(agent_instance_id=instance_id, status="LIVE"))
        self.session.add(Live(agent_instance_id=instance_id, owner_user_id=owner_id,
                              definition_id=definition_id, definition_version=definition_version,
                              state="ACTIVE", generation=1))
        await self.session.flush()
        return instance_id

    async def resolve(self, owner_id: str, instance_id: str):
        # This entry is intentionally read-only; no cached authorization.
        # Caller must supply a verified owner identity (application layer checks).
        if not owner_id or not instance_id:
            raise AgentInstanceDenied("identity required")
        dialect = self.session.get_bind().dialect.name
        if dialect not in ("sqlite", "postgresql"):
            raise AgentInstanceDenied("unsupported database")
        result = await self.session.execute(
            select(Live, User.status).join(User, Live.owner_user_id == User.id)
            .join(Reservation, Reservation.agent_instance_id == Live.agent_instance_id)
            .where(Live.agent_instance_id == instance_id, Live.owner_user_id == owner_id,
                   Reservation.status == "LIVE", User.status == "active",
                   Live.state == "ACTIVE")
        )
        row = result.one_or_none()
        if row is None:
            raise AgentInstanceDenied("not an active owner-bound instance")
        return row[0]

    async def transition(self, owner_id: str, instance_id: str, generation: int, *, retire: bool = False):
        await self._active_owner(owner_id)
        record = (await self.session.execute(
            select(Live).where(Live.agent_instance_id == instance_id,
                               Live.owner_user_id == owner_id).with_for_update()
        )).scalar_one_or_none()
        if record is None or record.generation != generation:
            raise AgentInstanceConflict("stale/foreign generation")
        if retire:
            # Retire reservation first, then remove owner-bound LIVE row.
            # Both writes share the caller's transaction; DB triggers reject
            # unpaired raw LIVE deletion or retired identity resurrection.
            result = await self.session.execute(
                update(Reservation).where(Reservation.agent_instance_id == instance_id,
                    Reservation.status == "LIVE").values(status="RETIRED")
            )
            if result.rowcount != 1:
                raise AgentInstanceConflict("reservation transition lost")
            await self.session.delete(record)
            await self.session.flush()
        else:
            result = await self.session.execute(
                update(Live).where(Live.agent_instance_id == instance_id,
                    Live.owner_user_id == owner_id, Live.generation == generation)
                .values(state="SUSPENDED", generation=generation + 1)
            )
        if result.rowcount != 1:
            raise AgentInstanceConflict("concurrent lifecycle mutation")
        await self.session.flush()
