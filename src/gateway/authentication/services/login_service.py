import structlog

from ...schemas.auth import LoginRequestSchema, TokenSchema
from ..exceptions import InvalidCredentialsError
from .token_service import TokenService
from .. import password as PwdHelper
from ...storage.core.unit_of_work import SqlAlchemyUnitOfWork
from typing import Callable

logger = structlog.get_logger(__name__)

class LoginService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        token_service: TokenService
    ):
        self.uow_factory = uow_factory
        self.token_service = token_service

    async def login(self, login_data: LoginRequestSchema) -> TokenSchema:
        async with self.uow_factory() as uow:
            user = await uow.users.get_by_email(login_data.email)
            if not user or not PwdHelper.verify_password(login_data.password, user.password_hash):
                raise InvalidCredentialsError()

            logger.info("User logged in successfully", user_id=user.id)
            return await self.token_service.create_user_tokens(user.id, user.email)