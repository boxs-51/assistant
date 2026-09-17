import structlog
import hashlib

from .....infrastructure.event_bus.bus import EventBus
from .....domain.schemas.event import BaseEvent
from .....domain.schemas.auth import OAuthUserInfoSchema, TokenSchema
from .token_service import TokenService
from .....transport.gateway.authentication import password as PwdHelper
from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from typing import Callable
from .....domain.schemas.identity import Identity
from .guest_session_service import GuestSessionService

logger = structlog.get_logger(__name__)

class OAuthService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        token_service: TokenService,
        event_bus: EventBus,
        guest_session_service: GuestSessionService,
    ):
        self.uow_factory = uow_factory
        self.token_service = token_service
        self.event_bus = event_bus
        self.guest_session_service = guest_session_service

    async def handle_oauth_callback(
        self,
        provider: str,
        oauth_user_info: OAuthUserInfoSchema,
        guest_identity: Identity | None = None,
    ) -> TokenSchema:
        async with self.uow_factory() as uow:
            oauth_account = await uow.oauth_accounts.get_by_provider_user_id(
                provider=provider,
                provider_user_id=oauth_user_info.provider_user_id
            )

            if oauth_account:
                logger.info("OAuth account found, logging in user", user_id=oauth_account.user_id, provider=provider)
                user = await uow.users.get_by_id(oauth_account.user_id)
                user_id = user.id
                user_email = user.email
                organization = await uow.users.get_organization_for_user(user_id)
                organization_id = organization.id if organization else None
                existing_oauth_account = True
            else:
                existing_oauth_account = False

            existing_user = (
                None
                if existing_oauth_account
                else await uow.users.get_by_email(oauth_user_info.email)
            )

            if not existing_oauth_account and existing_user:
                # Nếu user đã tồn tại, cập nhật thông tin (nếu cần) và tạo liên kết
                user = existing_user
                if not user.name and oauth_user_info.name:
                    user.name = oauth_user_info.name
                if not user.picture and oauth_user_info.profile_url:
                    user.picture = oauth_user_info.profile_url
            elif not existing_oauth_account:
                # Nếu user chưa tồn tại, tạo mới hoàn toàn
                logger.info("No existing user found. Creating new user and link.", email=oauth_user_info.email, provider=provider)
                random_password = PwdHelper.get_password_hash(hashlib.sha256(oauth_user_info.email.encode()).hexdigest())
                user = await uow.users.create(email=oauth_user_info.email, hashed_password=random_password, name=oauth_user_info.name, picture=oauth_user_info.picture)
                org_name = f"{user.name or oauth_user_info.email}'s Organization"
                new_org = await uow.organizations.create(name=org_name, owner_id=user.id)
                await uow.members.create(organization_id=new_org.id, user_id=user.id, role="admin")

                # Phát sự kiện user.created sau khi tạo user mới qua OAuth
                # user_created_event = BaseEvent(
                #     event_name="user.created",
                #     payload={
                #         "user_id": user.id,
                #         "email": user.email,
                #         "organization_id": new_org.id,
                #     }
                # )
                # await self.event_bus.publish(user_created_event)

            if not existing_oauth_account:
                logger.info("User found/created, creating new OAuth link.", user_id=user.id, provider=provider)
                await uow.oauth_accounts.create(user.id, provider, oauth_user_info.provider_user_id)
                user_id = user.id
                user_email = user.email
                organization = await uow.users.get_organization_for_user(user_id)
                organization_id = organization.id if organization else None
                await uow.commit()

        claimed_count = await self.guest_session_service.claim_sessions(
            guest_identity,
            target_user_id=user_id,
            target_organization_id=organization_id,
        )
        return await self.token_service.create_user_tokens(
            user_id,
            user_email,
            claimed_guest_sessions=(claimed_count if guest_identity else None),
        )
