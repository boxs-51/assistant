import structlog

from .....domain.schemas.auth import LoginRequestSchema, TokenSchema
from ..exceptions import InvalidCredentialsError
from .token_service import TokenService
from .....transport.gateway.authentication import password as PwdHelper
from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from typing import Callable
from .....domain.schemas.identity import Identity
from .guest_session_service import GuestSessionService

logger = structlog.get_logger(__name__)

class LoginService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        token_service: TokenService,
        guest_session_service: GuestSessionService,
    ):
        self.uow_factory = uow_factory
        self.token_service = token_service
        self.guest_session_service = guest_session_service

    async def login(
        self,
        login_data: LoginRequestSchema,
        guest_identity: Identity | None = None,
    ) -> TokenSchema:
        async with self.uow_factory() as uow:
            user = await uow.users.get_by_email(login_data.email)
            if not user or not PwdHelper.verify_password(login_data.password, user.password_hash):
                raise InvalidCredentialsError()
            user_id = user.id
            user_email = user.email
            organization = await uow.users.get_organization_for_user(user_id)
            organization_id = organization.id if organization else None

        claimed_count = await self.guest_session_service.claim_sessions(
            guest_identity,
            target_user_id=user_id,
            target_organization_id=organization_id,
        )
        logger.info(
            "User logged in successfully",
            user_id=user_id,
            claimed_guest_sessions=(claimed_count if guest_identity else None),
        )
        return await self.token_service.create_user_tokens(
            user_id,
            user_email,
            claimed_guest_sessions=claimed_count,
        )
