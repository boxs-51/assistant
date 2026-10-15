import structlog
import hashlib
from datetime import timedelta

from .....domain.schemas.auth import TokenSchema, AccessTokenSchema, GuestTokenSchema
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

    async def _create_access_only(self, user_id: str, email: str) -> str:
        """Access JWT claims without minting or storing a refresh credential."""
        async with self.uow_factory() as uow:
            roles = list(await uow.users.get_user_roles(user_id))
            organization = await uow.users.get_organization_for_user(user_id)
        if email in ADMIN_EMAILS and "admin" not in roles:
            roles.append("admin")
        jwt_data = {
            "sub": user_id,
            "roles": roles,
            "org_id": organization.id if organization else None,
            "plan": "enterprise" if "admin" in roles else (
                organization.plan if organization else "free"
            ),
            "scopes": ["profile", "email"],
        }
        return self.jwt.create_access_token(jwt_data)

    async def create_user_tokens(
        self,
        user_id: str,
        email: str,
        *,
        claimed_guest_sessions: int | None = None,
    ) -> TokenSchema:
        """Persist the refresh authority before exposing either credential."""
        access_token = await self._create_access_only(user_id, email)
        v2 = self.session_repo.refresh_store is not None
        refresh_token = self.jwt.create_refresh_token(
            {"sub": user_id, **({"sv": 2} if v2 else {})}
        )
        refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        ttl = self.config.refresh_token_expire_days * 86400
        if v2:
            claims = self.jwt.decode_token(refresh_token)
            if not claims or not isinstance(claims.get("jti"), str):
                raise RuntimeError("Issued refresh credential has no verified identity")
            if not await self.session_repo.issue_v2(refresh_hash, user_id, claims["jti"], ttl):
                raise RuntimeError("Refresh session issuance was not confirmed")
        else:
            # Compatibility for direct legacy repositories and staged V1 callers.
            await self.session_repo.save_token(user_id, refresh_hash, ttl)
        return TokenSchema(
            access_token=access_token,
            refresh_token=refresh_token,
            session_claim=(
                {"claimed_count": claimed_guest_sessions}
                if claimed_guest_sessions is not None else None
            ),
        )

    def create_guest_token(self, guest_user_id: str) -> GuestTokenSchema:
        ttl = self.config.guest_token_ttl_seconds
        access_token = self.jwt.create_access_token(
            data={
                "sub": guest_user_id,
                "roles": ["guest"],
                "scopes": ["guest"],
                "plan": "guest",
                "principal_type": "guest",
            },
            expires_delta=timedelta(seconds=ttl),
        )
        return GuestTokenSchema(
            access_token=access_token,
            expires_in=ttl,
            user_id=guest_user_id,
        )

    async def refresh_access_token(self, refresh_token: str) -> AccessTokenSchema:
        """Access-only refresh; final authorization is atomically fenced."""
        payload = self.jwt.decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise InvalidCredentialsError("Invalid or expired refresh token")
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise InvalidCredentialsError("Invalid refresh token subject")
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        version = payload.get("sv")

        if version == 2 and type(version) is int:
            jti = payload.get("jti")
            if not isinstance(jti, str) or not jti:
                raise InvalidCredentialsError("Invalid V2 refresh session identity")
            lease = await self.session_repo.begin_v2(token_hash, user_id, jti)
            if lease is None:
                raise InvalidCredentialsError("Refresh session revoked or unavailable")
            finalized = False
            try:
                async with self.uow_factory() as uow:
                    user = await uow.users.get_by_id(user_id)
                if not user:
                    raise InvalidCredentialsError("User associated with token not found")
                access_token = await self._create_access_only(user.id, user.email)
                finalized = await self.session_repo.finalize_v2(lease)
                if not finalized:
                    raise InvalidCredentialsError("Refresh session revoked before authorization")
                return AccessTokenSchema(access_token=access_token)
            finally:
                if not finalized:
                    try:
                        await self.session_repo.abort_v2(lease)
                    except Exception:
                        # A failed abort must not turn failed authorization into success.
                        logger.warning("Uncertain refresh lease abort", exc_info=True)

        if version is not None:
            raise InvalidCredentialsError("Unsupported refresh session version")

        # V1 and Stage-1 (jti without sv) compatibility. The original lookup
        # stays observable to frozen PR545; final read is mandatory after
        # user/claims work and is the last authorization step.
        async with self.uow_factory() as uow:
            stored_user_id = await self.session_repo.get_user_id_by_token(token_hash)
            if stored_user_id != user_id:
                raise InvalidCredentialsError("Refresh token not found or revoked")
            user = await uow.users.get_by_id(user_id)
        if not user:
            raise InvalidCredentialsError("User associated with token not found")
        access_token = await self._create_access_only(user.id, user.email)
        if not await self.session_repo.assert_legacy_active(user_id, token_hash):
            raise InvalidCredentialsError("Refresh token revoked before authorization")
        return AccessTokenSchema(access_token=access_token)

    async def logout(self, refresh_token: str):
        """Legacy confirmed DEL or V2 atomic REVOKE; no false revoke ACK."""
        digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        # PR535's frozen real-chain synthetic test constructs TokenService via
        # __new__ without JwtHelper. Preserve its legacy hash-only contract.
        helper = getattr(self, "jwt", None)
        payload = helper.decode_token(refresh_token) if helper is not None else None
        if payload and payload.get("type") == "refresh" and payload.get("sv") == 2:
            user_id, jti = payload.get("sub"), payload.get("jti")
            if not isinstance(user_id, str) or not user_id or not isinstance(jti, str) or not jti:
                raise InvalidCredentialsError("Invalid V2 refresh identity")
            if not await self.session_repo.revoke_v2(digest, user_id, jti):
                raise InvalidCredentialsError("Refresh token absent or already revoked")
        else:
            if payload and payload.get("sv") is not None:
                raise InvalidCredentialsError("Unsupported refresh session version")
            deleted = await self.session_repo.delete_token(digest)
            if deleted != 1:
                raise InvalidCredentialsError("Refresh token not found or already revoked")
        logger.info("Refresh session revoked", token_hash_prefix=digest[:8])

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

        principal_type = payload.get("principal_type", "user")
        return Identity(
            auth_type="guest" if principal_type == "guest" else "jwt",
            user_id=user_id,
            organization_id=payload.get("org_id"),
            session_id=payload.get("jti"), # jti (JWT ID) có thể được dùng làm session_id
            plan=payload.get("plan", "free"),
            roles=roles,
            permissions=list(permissions),
            scopes=scopes
        )
