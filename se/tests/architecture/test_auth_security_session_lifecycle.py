from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import uuid

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.auth import LoginRequestSchema
from se.src.infrastructure.config.schemas import (
    AuthenticationSettings,
    DriverConfig,
)
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.chat_data.session import Message, Session
from se.src.infrastructure.storage.models.sql.user_data.user import User
from se.src.transport.gateway.authentication.middleware import AuthenticationMiddleware
from se.src.transport.gateway.authentication.services.guest_session_service import (
    GuestSessionService,
)
from se.src.transport.gateway.authentication.services.token_service import TokenService
from se.src.transport.gateway.authentication.services.login_service import LoginService
from se.src.transport.gateway.authentication import password as password_helper
from se.src.transport.gateway.authentication.authenticators.jwt_authenticator import (
    JWTAuthenticator,
)
from se.src.transport.gateway.authentication.exceptions import InvalidCredentialsError
from se.src.version import __version__


JWT_SECRET = "j" * 48
SESSION_SECRET = "s" * 48


class NoopRefreshTokenStore:
    async def save_token(self, *args, **kwargs):
        return None


def secure_auth_config(**updates) -> AuthenticationSettings:
    return AuthenticationSettings(
        jwt_secret_key=JWT_SECRET,
        session_secret_key=SESSION_SECRET,
        **updates,
    )


def test_auth_secrets_fail_closed_and_public_paths_are_precise():
    with pytest.raises(ValueError, match="JWT"):
        AuthenticationSettings().validate_runtime_secrets()
    with pytest.raises(ValueError, match="different"):
        AuthenticationSettings(
            jwt_secret_key=JWT_SECRET,
            session_secret_key=JWT_SECRET,
        ).validate_runtime_secrets()
    with pytest.raises(ValueError, match="JWT"):
        AuthenticationSettings(
            jwt_secret_key="replace-with-at-least-32-random-bytes",
            session_secret_key=SESSION_SECRET,
        ).validate_runtime_secrets()

    config = secure_auth_config()
    config.validate_runtime_secrets()
    assert "/v1/auth/login" in config.public_paths
    assert "/v1/auth/register/initiate" in config.public_paths
    assert "/v1/auth/me" not in config.public_paths
    assert "/v1/auth/api-keys" not in config.public_paths


def test_application_version_has_one_canonical_value():
    from se.src.main import create_app
    from se.src.infrastructure.config.schemas import ConfigSchema, GatewaySettings

    config = ConfigSchema(
        gateway=GatewaySettings(),
        auth=secure_auth_config(enable=True),
    )
    app = create_app(config)
    assert app.version == __version__ == config.gateway.version


@pytest.mark.asyncio
async def test_guest_claim_and_owner_scoped_delete_are_atomic():
    database_path = Path(".cache") / f"guest-lifecycle-{uuid.uuid4().hex}.db"
    database_path.parent.mkdir(exist_ok=True)
    driver = SQLiteDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"path": str(database_path)},
        )
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    uow_factory = lambda: SqlAlchemyUnitOfWork(driver)
    token_service = TokenService(
        uow_factory=uow_factory,
        session_repo=NoopRefreshTokenStore(),
        config=secure_auth_config(enable=False, allow_guest=True),
    )
    guest_service = GuestSessionService(uow_factory, token_service)

    issued = await guest_service.create_guest()
    assert issued.identity.auth_type == "guest"
    assert issued.identity.user_id

    async with uow_factory() as uow:
        target = User(
            id="target-user",
            email="target@example.com",
            password_hash=password_helper.get_password_hash("correct-password"),
            status="active",
        )
        uow.session.add(target)
        conversation = Session(
            id="guest-session",
            user_id=issued.identity.user_id,
            status="active",
        )
        uow.session.add(conversation)
        uow.session.add(
            Message(
                id="guest-message",
                session_id=conversation.id,
                role="user",
                content={"type": "text", "data": "hello"},
            )
        )
        await uow.commit()

    login_service = LoginService(uow_factory, token_service, guest_service)
    with pytest.raises(InvalidCredentialsError):
        await login_service.login(
            LoginRequestSchema(
                email="target@example.com",
                password="wrong-password",
            ),
            issued.identity,
        )
    async with uow_factory() as uow:
        assert (await uow.sessions.get_by_id("guest-session")).user_id == issued.identity.user_id

    tokens = await login_service.login(
        LoginRequestSchema(
            email="target@example.com",
            password="correct-password",
        ),
        issued.identity,
    )
    assert tokens.session_claim == {"claimed_count": 1}
    with pytest.raises(InvalidCredentialsError, match="no longer active"):
        await JWTAuthenticator(token_service, uow_factory).authenticate(
            issued.token.access_token
        )

    async with uow_factory() as uow:
        moved = await uow.sessions.get_by_id("guest-session")
        assert moved.user_id == "target-user"
        guest = await uow.users.get_by_id(issued.identity.user_id)
        assert guest.status == "claimed"
        assert not await uow.sessions.delete_owned_session(
            "guest-session", issued.identity.user_id
        )
        assert await uow.sessions.delete_owned_session(
            "guest-session", "target-user"
        )
        await uow.commit()

    async with uow_factory() as uow:
        assert await uow.sessions.get_by_id("guest-session") is None
        messages = await uow.session.execute(
            select(Message).where(Message.session_id == "guest-session")
        )
        assert messages.scalars().all() == []

    await driver.disconnect()
    database_path.unlink(missing_ok=True)


class FakeAuthManager:
    def has_credentials(self, connection):
        return bool(connection.headers.get("Authorization"))

    async def authenticate(self, connection):
        return Identity(auth_type="guest", user_id="existing-guest")


class FakeGuestService:
    async def create_guest(self):
        return SimpleNamespace(
            identity=Identity(auth_type="guest", user_id="new-guest"),
            token=SimpleNamespace(access_token="signed-guest", expires_in=60),
        )


class FakeContainer:
    def __init__(self, auth_config):
        self.config = SimpleNamespace(auth=auth_config)
        self.auth_manager = FakeAuthManager()
        self.guest_session_service = FakeGuestService()

    def require(self, name):
        return getattr(self, name)


@pytest.mark.asyncio
async def test_auth_disabled_creates_isolated_guest_and_public_login_is_optional_auth():
    auth_config = secure_auth_config(enable=False, allow_guest=True)
    app = FastAPI()
    app.state.container = FakeContainer(auth_config)
    app.add_middleware(
        AuthenticationMiddleware,
        public_paths=auth_config.public_paths,
    )

    @app.get("/protected")
    async def protected(request: Request):
        return {"user_id": request.state.identity.user_id}

    @app.post("/v1/auth/login")
    async def login(request: Request):
        identity = getattr(request.state, "identity", None)
        return {"guest_user_id": identity.user_id if identity else None}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        anonymous = await client.get("/protected")
        assert anonymous.status_code == 200
        assert anonymous.json()["user_id"] == "new-guest"
        assert "guest_access_token" in anonymous.cookies

        public_without_identity = await client.post("/v1/auth/login")
        assert public_without_identity.json()["guest_user_id"] is None

        public_with_identity = await client.post(
            "/v1/auth/login",
            headers={"Authorization": "Bearer guest"},
        )
        assert public_with_identity.json()["guest_user_id"] == "existing-guest"


@pytest.mark.asyncio
async def test_auth_enabled_rejects_anonymous_protected_request():
    auth_config = secure_auth_config(enable=True, allow_guest=False)
    app = FastAPI()
    app.state.container = FakeContainer(auth_config)
    app.add_middleware(
        AuthenticationMiddleware,
        public_paths=auth_config.public_paths,
    )

    @app.get("/protected")
    async def protected(request: Request):
        return {"user_id": request.state.identity.user_id}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or malformed Authorization header"
