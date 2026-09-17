from ....domain.schemas.auth import (
    UserCreateSchema, LoginRequestSchema, TokenSchema,
    OAuthUserInfoSchema, AccessTokenSchema, UserMeSchema
)
from ....domain.schemas.identity import Identity

from .services.registration_service import RegistrationService
from .services.login_service import LoginService
from .services.oauth_service import OAuthService
from .services.token_service import TokenService
from .services.user_service import UserService
from .services.password_reset_service import PasswordResetService

class Authentication:
    def __init__(
        self,
        registration_service: RegistrationService,
        login_service: LoginService,
        oauth_service: OAuthService,
        token_service: TokenService,
        user_service: UserService,
        password_reset_service: PasswordResetService,
    ):
        self.registration_service = registration_service
        self.login_service = login_service
        self.oauth_service = oauth_service
        self.token_service = token_service
        self.user_service = user_service
        self.password_reset_service = password_reset_service

    async def initiate_registration(self, user_data: UserCreateSchema) -> dict:
        return await self.registration_service.initiate_registration(user_data)

    async def confirm_registration(
        self, email: str, otp: str, guest_identity: Identity | None = None
    ) -> TokenSchema:
        return await self.registration_service.confirm_registration(
            email, otp, guest_identity
        )

    async def login(
        self, login_data: LoginRequestSchema, guest_identity: Identity | None = None
    ) -> TokenSchema:
        return await self.login_service.login(login_data, guest_identity)

    async def handle_oauth_callback(
        self,
        provider: str,
        oauth_user_info: OAuthUserInfoSchema,
        guest_identity: Identity | None = None,
    ) -> TokenSchema:
        return await self.oauth_service.handle_oauth_callback(
            provider, oauth_user_info, guest_identity
        )

    async def refresh_access_token(self, refresh_token: str) -> AccessTokenSchema:
        return await self.token_service.refresh_access_token(refresh_token)

    async def logout(self, refresh_token: str):
        await self.token_service.logout(refresh_token)

    async def get_current_user_info(self, identity: Identity) -> UserMeSchema:
        return await self.user_service.get_current_user_info(identity)

    async def initiate_password_reset(self, email: str) -> dict:
        return await self.password_reset_service.initiate(email)

    async def confirm_password_reset(self, email: str, otp: str, new_password: str) -> dict:
        return await self.password_reset_service.confirm(email, otp, new_password)

    # Phương thức register_user cũ không còn cần thiết với luồng OTP mới
    # Nếu vẫn cần, nó sẽ nằm trong RegistrationService
