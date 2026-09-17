from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Callable

from .....domain.schemas.auth import GuestTokenSchema
from .....domain.schemas.identity import Identity
from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ..exceptions import InvalidCredentialsError
from .token_service import TokenService


@dataclass(frozen=True, slots=True)
class IssuedGuest:
    identity: Identity
    token: GuestTokenSchema


class GuestSessionService:
    """Issues isolated guest principals and atomically claims their sessions."""

    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        token_service: TokenService,
    ) -> None:
        self.uow_factory = uow_factory
        self.token_service = token_service

    async def create_guest(self) -> IssuedGuest:
        guest_id = str(uuid.uuid4())
        async with self.uow_factory() as uow:
            user = await uow.users.create(
                email=f"guest-{guest_id}@guest.invalid",
                hashed_password=f"guest_disabled_{secrets.token_hex(32)}",
                name="Guest",
            )
            user.status = "guest"
            persisted_guest_id = user.id
            await uow.commit()

        token = self.token_service.create_guest_token(persisted_guest_id)
        identity = self.token_service.verify_access_token(token.access_token)
        return IssuedGuest(identity=identity, token=token)

    async def claim_sessions(
        self,
        guest_identity: Identity | None,
        *,
        target_user_id: str,
        target_organization_id: str | None,
    ) -> int:
        if guest_identity is None:
            return 0
        if guest_identity.auth_type != "guest" or not guest_identity.user_id:
            raise InvalidCredentialsError("Only a guest principal can claim guest sessions.")
        if guest_identity.user_id == target_user_id:
            return 0

        async with self.uow_factory() as uow:
            guest = await uow.users.get_by_id_for_update(guest_identity.user_id)
            if guest is None or guest.status != "guest":
                raise InvalidCredentialsError("Guest principal is no longer active.")
            claimed_count = await uow.sessions.claim_by_user_id(
                guest.id,
                target_user_id,
                target_organization_id,
            )
            guest.status = "claimed"
            await uow.commit()
            return claimed_count


__all__ = ["GuestSessionService", "IssuedGuest"]
