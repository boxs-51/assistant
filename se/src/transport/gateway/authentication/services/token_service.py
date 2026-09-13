import structlog
import hashlib

from .....domain.schemas.auth import TokenSchema, AccessTokenSchema
from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .....infrastructure.storage.repositories.sessions import SessionRepository
from ..jwt import JwtHelper
from .....infrastructure.config import AuthenticationSettings
from ..exceptions import InvalidCredentialsError
from .....domain.schemas.identity import Identity
from typing import Callable

logger = structlog.get_logger(__name__)
ADMIN_EMAILS = ["manager.admin@gmail.com", "superdev@gmail.com"]

class TokenService:
    def __init__(self,
                 uow_factory: SqlAlchemyUnitOfWork,
                 session_repo: SessionRepository,
                 config: AuthenticationSettings,
):
        self.config = config
        self.uow_factory = uow_factory
        self.session_repo = session_repo
        self.jwt = JwtHelper(config)

    async def create_user_tokens(self, user_id: str, email: str) -> TokenSchema:
        """Tạo và lưu trữ access và refresh token cho một người dùng."""
        async with self.uow_factory() as uow:
            roles = await uow.users.get_user_roles(user_id)
            organization = await uow.users.get_organization_for_user(user_id)

        # Nâng cấp quyền admin dựa trên danh sách Email đã định sẵn
        if email in ADMIN_EMAILS and "admin" not in roles:
            roles.append("admin")

        jwt_data = {
            "sub": user_id,
            "roles": roles,
            "org_id": organization.id if organization else None,
            "plan": "enterprise" if "admin" in roles else (organization.plan if organization else "free"),
            "scopes": ["profile", "email"] # Gán scope mặc định khi đăng nhập
        }

        access_token = self.jwt.create_access_token(data=jwt_data)
        refresh_token = self.jwt.create_refresh_token(data={"sub": user_id})

        # Lưu hash của refresh token vào Redis để có thể thu hồi sau này
        refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        await self.session_repo.save_token(user_id, refresh_token_hash, self.config.auth.refresh_token_expire_days * 86400)

        return TokenSchema(access_token=access_token, refresh_token=refresh_token)

    async def refresh_access_token(self, refresh_token: str) -> AccessTokenSchema:
        """Làm mới access token bằng một refresh token hợp lệ."""
        payload = self.jwt.decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise InvalidCredentialsError("Invalid or expired refresh token")

        user_id = payload.get("sub")
        if not user_id:
            raise InvalidCredentialsError("Invalid refresh token payload")

        async with self.uow_factory() as uow:
            # Xác thực với session đã lưu
            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
            stored_user_id = await self.session_repo.get_user_id_by_token(token_hash)
            if not stored_user_id or stored_user_id != user_id:
                raise InvalidCredentialsError("Refresh token not found or has been revoked")

            # Lấy thông tin user để tạo lại access token với payload đầy đủ
            user = await uow.users.get_by_id(user_id)
            if not user:
                raise InvalidCredentialsError("User associated with token not found")

            # Chỉ tạo lại access token, không tạo lại refresh token
            new_access_token = (await self.create_user_tokens(user.id, user.email)).access_token
            return AccessTokenSchema(access_token=new_access_token)

    async def logout(self, refresh_token: str):
        """Thu hồi một refresh token bằng cách xóa nó khỏi session store."""
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        await self.session_repo.delete_token(token_hash)
        logger.info("User logged out, refresh token revoked.", token_hash_prefix=token_hash[:8])

    def verify_access_token(self, token: str) -> Identity:
        """
        Giải mã và xác thực một access token (JWT), trả về đối tượng Identity.
        Đây là một hoạt động stateless, không cần truy vấn DB.
        """
        payload = self.jwt.decode_token(token)
        if not payload or payload.get("type") != "access":
            raise InvalidCredentialsError("Invalid or expired JWT")

        user_id = payload.get("sub")
        if not user_id:
            raise InvalidCredentialsError("Invalid JWT payload: Missing 'sub' claim")

        roles = payload.get("roles", ["member"])
        scopes = set(payload.get("scopes", []))
        # Logic lấy permission giờ đây sẽ nằm trong các Authenticator, nơi có UoW
        permissions = [] # Để trống, sẽ được điền bởi Authenticator

        return Identity(
            auth_type="jwt",
            user_id=user_id,
            organization_id=payload.get("org_id"),
            session_id=payload.get("jti"), # jti (JWT ID) có thể được dùng làm session_id
            plan=payload.get("plan", "free"),
            roles=roles,
            permissions=list(permissions),
            scopes=scopes
        )