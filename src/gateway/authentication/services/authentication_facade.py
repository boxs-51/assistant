from ...schemas.auth import (
    UserCreateSchema, LoginRequestSchema, TokenSchema,
    OAuthUserInfoSchema, AccessTokenSchema, UserMeSchema
)
from ...schemas.identity import Identity

from .registration_service import RegistrationService
from .login_service import LoginService
from .oauth_service import OAuthService
from .token_service import TokenService
from .user_service import UserService

class AuthenticationFacade:
    def __init__(
        self,
        registration_service: RegistrationService,
        login_service: LoginService,
        oauth_service: OAuthService,
        token_service: TokenService,
        user_service: UserService,
    ):
        self.registration_service = registration_service
        self.login_service = login_service
        self.oauth_service = oauth_service
        self.token_service = token_service
        self.user_service = user_service

    async def initiate_registration(self, user_data: UserCreateSchema) -> dict:
        return await self.registration_service.initiate_registration(user_data)

    async def confirm_registration(self, email: str, otp: str) -> TokenSchema:
        return await self.registration_service.confirm_registration(email, otp)

    async def login(self, login_data: LoginRequestSchema) -> TokenSchema:
        return await self.login_service.login(login_data)

    async def handle_oauth_callback(self, provider: str, oauth_user_info: OAuthUserInfoSchema) -> TokenSchema:
        return await self.oauth_service.handle_oauth_callback(provider, oauth_user_info)

    async def refresh_access_token(self, refresh_token: str) -> AccessTokenSchema:
        return await self.token_service.refresh_access_token(refresh_token)

    async def logout(self, refresh_token: str):
        await self.token_service.logout(refresh_token)

    async def get_current_user_info(self, identity: Identity) -> UserMeSchema:
        return await self.user_service.get_current_user_info(identity)

    # Phương thức register_user cũ không còn cần thiết với luồng OTP mới
    # Nếu vẫn cần, nó sẽ nằm trong RegistrationService