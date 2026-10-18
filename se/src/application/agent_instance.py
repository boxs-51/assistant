"""AIC P1A nonwired authenticated facade; no API or execution activation."""

from __future__ import annotations

from se.src.domain.schemas.agent_instance import AgentInstanceLifecycle, AgentInstanceView
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.repositories.agent_instance import AgentInstanceDenied


class AgentInstanceService:
    def __init__(self, uow):
        self.uow = uow

    @staticmethod
    def _owner(identity: Identity) -> str:
        # Mapping API keys/admin principals to trusted users needs a separate gate.
        if (
            not isinstance(identity, Identity)
            or identity.auth_type != "jwt"
            or not identity.user_id
            or not identity.user_id.strip()
        ):
            raise AgentInstanceDenied("AIC requires an authenticated unambiguous user")
        return identity.user_id

    @staticmethod
    def _view(row) -> AgentInstanceView:
        return AgentInstanceView(
            owner_user_id=row.owner_user_id,
            agent_instance_id=row.agent_instance_id,
            definition_id=row.definition_id,
            definition_version=row.definition_version,
            lifecycle_state=AgentInstanceLifecycle(row.lifecycle_state),
            generation=row.generation,
        )

    async def create(self, identity: Identity, *, definition_id: str, definition_version: str):
        return self._view(await self.uow.agent_instances.create(
            self._owner(identity), definition_id, definition_version
        ))

    async def resolve(self, identity: Identity, instance_id: str):
        return self._view(await self.uow.agent_instances.resolve(
            self._owner(identity), instance_id
        ))

    async def suspend(self, identity: Identity, instance_id: str, *, expected_generation: int):
        return await self.uow.agent_instances.suspend(
            self._owner(identity), instance_id, expected_generation
        )

    async def retire(self, identity: Identity, instance_id: str, *, expected_generation: int):
        return await self.uow.agent_instances.retire(
            self._owner(identity), instance_id, expected_generation
        )


__all__ = ["AgentInstanceService"]
