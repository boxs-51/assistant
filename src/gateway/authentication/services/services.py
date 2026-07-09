import structlog
import hashlib

from typing import Dict, Any
from ...schemas.auth import UserCreateSchema, LoginRequestSchema, TokenSchema, OAuthUserInfoSchema, AccessTokenSchema, UserMeSchema
from ..exceptions import InvalidCredentialsError
from ...schemas.identity import Identity
from ...storage.repositories.users import UserRepository
from ...storage.repositories.sessions import SessionRepository
from ...storage.repositories.oauth_accounts import OAuthAccountRepository
from ...storage.repositories.organizations import OrganizationRepository
from ...storage.repositories.members import MemberRepository
from .. import password as PwdHelper
from ..jwt import JwtHelper
from ...config import settings

logger = structlog.get_logger(__name__)

class AuthenticationService:
    def __init__(
        self,
        user_repo: UserRepository,
        session_repo: SessionRepository,
        oauth_repo: OAuthAccountRepository,
        org_repo: OrganizationRepository,
        member_repo: MemberRepository,
        config: Dict[str, Any]
    ):
        self.config = config
        self.user_repo = user_repo
        self.session_repo = session_repo
        self.oauth_repo = oauth_repo
        self.org_repo = org_repo
        self.member_repo = member_repo
        self.jwt = JwtHelper(config)

    async def register_user(self, user_data: UserCreateSchema) -> TokenSchema:
        """
        Đăng ký một người dùng mới, hash mật khẩu và trả về token.
        """
        logger.info("Registering new user", email=user_data.email)
        # Kiểm tra xem user đã tồn tại chưa
        existing_user = await self.user_repo.get_by_email(user_data.email)
        if existing_user:
            # Nếu user đã tồn tại và đã liên kết với OAuth, không cho đăng ký lại bằng mật khẩu.
            is_oauth_linked = await self.user_repo.is_linked_to_oauth(existing_user.id)
            if is_oauth_linked:
                raise InvalidCredentialsError("This email is registered with an OAuth provider. Please log in using Google or another social account.")
            else:
                raise InvalidCredentialsError("Email already registered. Please log in.")

        hashed_password = PwdHelper.get_password_hash(user_data.password)
        
        # Tạo user mới trong DB
        new_user = await self.user_repo.create(
            email=user_data.email, 
            hashed_password=hashed_password
        )
        
        # Tự động tạo một Organization mặc định và gán vai trò admin cho người dùng mới
        org_name = f"{user_data.email.split('@')[0]}'s Organization"
        new_org = await self.org_repo.create(name=org_name, owner_id=new_user.id)
        await self.member_repo.create(organization_id=new_org.id, user_id=new_user.id, role="admin")

        # Tạo token sau khi đăng ký thành công
        return await self._create_user_tokens(new_user.id)

    async def login(self, login_data: LoginRequestSchema) -> TokenSchema:
        """
        Xác thực người dùng và trả về token.
        """
        user = await self.user_repo.get_by_email(login_data.email)
        if not user or not PwdHelper.verify_password(login_data.password, user.password_hash):
            raise InvalidCredentialsError()

        logger.info("User logged in successfully", user_id=user.id)
        return await self._create_user_tokens(user.id)

    async def handle_oauth_callback(self, provider: str, oauth_user_info: OAuthUserInfoSchema) -> TokenSchema:
        """
        Xử lý callback từ OAuth provider.
        1. Tìm kiếm liên kết OAuth đã tồn tại.
        2. Nếu có, đăng nhập cho user tương ứng.
        3. Nếu không, tìm user bằng email.
        4. Nếu có user, tạo liên kết mới.
        5. Nếu không có user, tạo user mới và tạo liên kết.
        6. Trả về token.
        """
        # 1. Tìm kiếm liên kết OAuth đã tồn tại
        oauth_account = await self.oauth_repo.get_by_provider_user_id(
            provider=provider,
            provider_user_id=oauth_user_info.provider_user_id
        )

        if oauth_account:
            # 2. Nếu có, đăng nhập cho user tương ứng.
            logger.info("OAuth account found, logging in user", user_id=oauth_account.user_id, provider=provider)
            return await self._create_user_tokens(oauth_account.user_id)

        # 3. Nếu không, tìm user bằng email.
        user = await self.user_repo.get_by_email(oauth_user_info.email)

        if user:
            # 4. Nếu có user, tạo liên kết mới và đăng nhập.
            logger.info("User found by email, creating new OAuth link for existing account.", user_id=user.id, provider=provider)
            await self.oauth_repo.create(user.id, provider, oauth_user_info.provider_user_id)
            return await self._create_user_tokens(user.id)
        else:
            # 5. Nếu không có user, tạo user mới và tạo liên kết.
            logger.info("No existing user or OAuth link found. Creating new user and link.", email=oauth_user_info.email, provider=provider)
            # Tạo mật khẩu ngẫu nhiên vì người dùng đăng nhập qua OAuth
            random_password = PwdHelper.get_password_hash(hashlib.sha256(oauth_user_info.email.encode()).hexdigest())
            new_user = await self.user_repo.create(
                email=oauth_user_info.email,
                hashed_password=random_password
            )
            # Tạo Organization và gán vai trò admin cho người dùng mới từ OAuth
            org_name = f"{oauth_user_info.email.split('@')[0]}'s Organization"
            new_org = await self.org_repo.create(name=org_name, owner_id=new_user.id)
            await self.member_repo.create(organization_id=new_org.id, user_id=new_user.id, role="admin")

            await self.oauth_repo.create(new_user.id, provider, oauth_user_info.provider_user_id)
            return await self._create_user_tokens(new_user.id)

    async def refresh_access_token(self, refresh_token: str) -> AccessTokenSchema:
        """
        Làm mới access token bằng một refresh token hợp lệ.
        1. Giải mã refresh token.
        2. Xác thực hash của nó với session đã lưu trong cache (Redis).
        3. Nếu hợp lệ, tạo một access token mới.
        """
        payload = self.jwt.decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise InvalidCredentialsError("Invalid or expired refresh token")

        user_id = payload.get("sub")
        if not user_id:
            raise InvalidCredentialsError("Invalid refresh token payload")

        # Xác thực với session đã lưu
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        stored_user_id = await self.session_repo.get_user_id_by_token(token_hash)
        if not stored_user_id or stored_user_id != user_id:
            raise InvalidCredentialsError("Refresh token not found or has been revoked")

        new_access_token = self.jwt.create_access_token(data={"sub": user_id})
        return AccessTokenSchema(access_token=new_access_token)

    async def logout(self, refresh_token: str):
        """
        Thu hồi một refresh token bằng cách xóa nó khỏi session store (Redis).
        Điều này ngăn chặn việc sử dụng token đã bị thu hồi để refresh.
        """
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        await self.session_repo.delete_token(token_hash)
        logger.info("User logged out, refresh token revoked.", token_hash_prefix=token_hash[:8])

    async def _create_user_tokens(self, user_id: str) -> TokenSchema:
        """Tạo và lưu trữ access và refresh token."""
        # TỐI ƯU HÓA: Lấy thông tin roles và org để nhúng vào JWT
        roles = await self.user_repo.get_user_roles(user_id)
        organization = await self.user_repo.get_organization_for_user(user_id)

        jwt_data = {
            "sub": user_id,
            "roles": roles,
            "org_id": organization.id if organization else None,
            "plan": organization.plan if organization else "free"
        }
        access_token = self.jwt.create_access_token(data=jwt_data)
        refresh_token = self.jwt.create_refresh_token(data={"sub": user_id})

        # Lưu hash của refresh token vào Redis để có thể thu hồi sau này
        refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        await self.session_repo.save_token(user_id, refresh_token_hash, settings.auth.refresh_token_expire_days * 86400)

        return TokenSchema(access_token=access_token, refresh_token=refresh_token)

    async def get_current_user_info(self, identity: Identity) -> UserMeSchema:
        """Lấy thông tin chi tiết của người dùng hiện tại dựa trên Identity."""
        if not identity.user_id:
            raise InvalidCredentialsError("Cannot get user info without a valid user session (JWT).")

        user = await self.user_repo.get_by_id(identity.user_id)
        if not user:
            raise InvalidCredentialsError("User not found.")

        return UserMeSchema(
            id=user.id,
            email=user.email,
            roles=identity.roles # Lấy roles trực tiếp từ identity đã được làm giàu
        )