import structlog
import hashlib

from ...schemas.auth import OAuthUserInfoSchema, TokenSchema
from .token_service import TokenService
from .. import password as PwdHelper
from ...storage.core.unit_of_work import SqlAlchemyUnitOfWork
from typing import Callable

logger = structlog.get_logger(__name__)

class OAuthService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        token_service: TokenService
    ):
        self.uow_factory = uow_factory
        self.token_service = token_service

    async def handle_oauth_callback(self, provider: str, oauth_user_info: OAuthUserInfoSchema) -> TokenSchema:
        async with self.uow_factory() as uow:
            oauth_account = await uow.oauth_accounts.get_by_provider_user_id(
                provider=provider,
                provider_user_id=oauth_user_info.provider_user_id
            )

            if oauth_account:
                logger.info("OAuth account found, logging in user", user_id=oauth_account.user_id, provider=provider)
                user = await uow.users.get_by_id(oauth_account.user_id)
                return await self.token_service.create_user_tokens(user.id, user.email)

            existing_user = await uow.users.get_by_email(oauth_user_info.email)

            if existing_user:
                # Nếu user đã tồn tại, cập nhật thông tin (nếu cần) và tạo liên kết
                user = existing_user
                if not user.name and oauth_user_info.name:
                    user.name = oauth_user_info.name
                if not user.picture and oauth_user_info.profile_url:
                    user.picture = oauth_user_info.profile_url
            else:
                # Nếu user chưa tồn tại, tạo mới hoàn toàn
                logger.info("No existing user found. Creating new user and link.", email=oauth_user_info.email, provider=provider)
                random_password = PwdHelper.get_password_hash(hashlib.sha256(oauth_user_info.email.encode()).hexdigest())
                user = await uow.users.create(email=oauth_user_info.email, hashed_password=random_password, name=oauth_user_info.name, picture=oauth_user_info.picture)
                org_name = f"{user.name or oauth_user_info.email}'s Organization"
                new_org = await uow.organizations.create(name=org_name, owner_id=user.id)
                await uow.members.create(organization_id=new_org.id, user_id=user.id, role="admin")

            logger.info("User found/created, creating new OAuth link.", user_id=user.id, provider=provider)
            await uow.oauth_accounts.create(user.id, provider, oauth_user_info.provider_user_id)
            
            await uow.commit()
            return await self.token_service.create_user_tokens(user.id, user.email)