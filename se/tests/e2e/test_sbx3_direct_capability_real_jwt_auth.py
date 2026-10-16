"""DIRECT-JWT-DB-T1: signed JWT + SQL Role/Permission + real Server terminal ingress.

AUTH_FIXTURE_LEVEL=REAL_SIGNED_JWT_DB_ROLE_TEST_APP.
No dependency override of get_current_identity, JWTAuthenticator, or terminal driver.
The HTTP adapter is in-process ASGI; this is NOT external TCP/production proof.
RED-first J03 must remain physically RED until separately authorized T2.
"""
from __future__ import annotations

import secrets
import shlex
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI

from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.config.schemas import AuthenticationSettings, DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.user_data.member import (
    Member, member_role_association,
)
from se.src.infrastructure.storage.models.sql.user_data.organization import Organization
from se.src.infrastructure.storage.models.sql.user_data.permission import (
    Permission, Role, role_permission_association,
)
from se.src.infrastructure.storage.models.sql.user_data.user import User
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage, InferenceResponse, InferenceToolCall,
)
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.chat.direct import DirectChatRuntime
from se.src.tool.registry import ToolRegistry
from se.src.transport.gateway.api.v1 import capability_router
from se.src.transport.gateway.authentication.authenticators.jwt_authenticator import (
    JWTAuthenticator,
)
from se.src.transport.gateway.authentication.dependency import get_current_identity
from se.src.transport.gateway.authentication.manager import AuthenticationManager
from se.src.transport.gateway.authentication.middleware import AuthenticationMiddleware
from se.src.transport.gateway.authentication.services.token_service import TokenService


_MEMBER_ID = "direct-jwt-member"
_ADMIN_ID = "direct-jwt-admin"


class _NoopRefreshStore:
    """Only refresh-token persistence is out of scope; access JWTs are real."""
    async def save_token(self, *_args, **_kwargs):
        return None


@dataclass(frozen=True)
class _Gateway:
    app: FastAPI
    token_service: TokenService
    jwt_authenticator: JWTAuthenticator
    uow_factory: object
    tokens: dict[str, str]
    runtime: CapabilityRuntime

    def headers(self, principal: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[principal]}"}


def _harmless_command(marker: Path) -> str:
    argv = [
        sys.executable, "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('DIRECT_JWT_OK', encoding='utf-8')",
    ]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


async def _seed_roles(driver: SQLiteDriver) -> None:
    """Real persisted membership -> role -> permission graph, never an Identity override."""
    async with driver._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(User.__table__.insert(), [
            {"id": _MEMBER_ID, "email": "direct-member@example.invalid",
             "password_hash": "offline-test-no-password-login", "status": "active"},
            {"id": _ADMIN_ID, "email": "direct-admin@example.invalid",
             "password_hash": "offline-test-no-password-login", "status": "active"},
        ])
        await conn.execute(Organization.__table__.insert(), {
            "id": "direct-jwt-org", "owner_id": _ADMIN_ID,
            "name": "Direct JWT isolated test", "plan": "free", "status": "active",
        })
        await conn.execute(Role.__table__.insert(), [
            {"id": "direct-role-member", "name": "member", "organization_id": None},
            {"id": "direct-role-admin", "name": "admin", "organization_id": None},
        ])
        await conn.execute(Permission.__table__.insert(), [
            {"id": "direct-perm-read", "name": "read:public"},
            {"id": "direct-perm-write", "name": "admin:write"},
        ])
        await conn.execute(Member.__table__.insert(), [
            {"organization_id": "direct-jwt-org", "user_id": _MEMBER_ID},
            {"organization_id": "direct-jwt-org", "user_id": _ADMIN_ID},
        ])
        await conn.execute(member_role_association.insert(), [
            {"member_organization_id": "direct-jwt-org", "member_user_id": _MEMBER_ID,
             "role_id": "direct-role-member"},
            {"member_organization_id": "direct-jwt-org", "member_user_id": _ADMIN_ID,
             "role_id": "direct-role-admin"},
        ])
        await conn.execute(role_permission_association.insert(), [
            {"role_id": "direct-role-member", "permission_id": "direct-perm-read"},
            {"role_id": "direct-role-admin", "permission_id": "direct-perm-write"},
        ])


@pytest_asyncio.fixture
async def real_jwt_gateway(tmp_path):
    db_path = tmp_path / "direct-jwt-roles.sqlite3"
    driver = SQLiteDriver(DriverConfig(
        enabled=True, required=True, options={"path": str(db_path)}
    ))
    await _seed_roles(driver)
    uow_factory = lambda: SqlAlchemyUnitOfWork(driver)
    jwt_secret = secrets.token_urlsafe(48)
    session_secret = secrets.token_urlsafe(48)
    auth_settings = AuthenticationSettings(
        enable=True,
        allow_guest=False,
        jwt_secret_key=jwt_secret,
        session_secret_key=session_secret,
    )
    auth_settings.validate_runtime_secrets()
    token_service = TokenService(
        uow_factory=uow_factory,
        session_repo=_NoopRefreshStore(),
        config=auth_settings,
    )
    jwt_auth = JWTAuthenticator(token_service, uow_factory)
    member_jwt = (await token_service.create_user_tokens(
        _MEMBER_ID, "direct-member@example.invalid"
    )).access_token
    admin_jwt = (await token_service.create_user_tokens(
        _ADMIN_ID, "direct-admin@example.invalid"
    )).access_token

    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        catalog=CapabilityCatalog(),
        routing_policy=CapabilityRoutingPolicy(),
    )
    tool_registry = ToolRegistry()  # Metadata-only, no competing registry projection.
    root = Path(__file__).resolve().parents[3]
    registration = register_local_tools(runtime, tool_registry, root / "tools" / "v1")
    assert registration.get("terminal.run") == "registered", registration

    app = FastAPI()
    container = SimpleNamespace(
        config=SimpleNamespace(auth=auth_settings),
        auth_manager=AuthenticationManager([jwt_auth]),
        capability_runtime=runtime,
        tool_registry=tool_registry,
    )
    container.require = lambda name: getattr(container, name)
    app.state.container = container
    app.include_router(capability_router.router)

    @app.get("/__direct_jwt_test_identity")
    async def identity_probe(identity: Identity = Depends(get_current_identity)):
        return {
            "user_id": identity.user_id,
            "auth_type": identity.auth_type,
            "roles": identity.roles,
            "permissions": identity.permissions,
        }

    assert get_current_identity not in app.dependency_overrides
    app.add_middleware(AuthenticationMiddleware, public_paths=[])
    try:
        yield _Gateway(
            app=app, token_service=token_service, jwt_authenticator=jwt_auth,
            uow_factory=uow_factory, tokens={"member": member_jwt, "admin": admin_jwt},
            runtime=runtime,
        )
    finally:
        await driver.disconnect()


@pytest.mark.asyncio
async def test_j01_j06_signed_jwt_roles_and_permissions_are_persisted(real_jwt_gateway):
    gateway = real_jwt_gateway
    async with gateway.uow_factory() as uow:
        assert await uow.users.get_user_roles(_MEMBER_ID) == ["member"]
        assert await uow.users.get_user_roles(_ADMIN_ID) == ["admin"]
        assert await uow.permissions.get_permissions_for_role_names(["member"]) == {"read:public"}
        assert await uow.permissions.get_permissions_for_role_names(["admin"]) == {"admin:write"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        for principal, user_id, roles, permissions in [
            ("member", _MEMBER_ID, ["member"], ["read:public"]),
            ("admin", _ADMIN_ID, ["admin"], ["admin:write"]),
        ]:
            identity = await gateway.jwt_authenticator.authenticate(gateway.tokens[principal])
            assert identity.auth_type == "jwt"
            assert identity.user_id == user_id
            assert identity.roles == roles
            assert identity.permissions == permissions
            response = await client.get(
                "/__direct_jwt_test_identity", headers=gateway.headers(principal)
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["user_id"] == user_id and body["auth_type"] == "jwt"
            assert body["roles"] == roles and set(body["permissions"]) == set(permissions)


@pytest.mark.asyncio
async def test_j02_unsigned_expired_unknown_subject_and_missing_bearer_fail_closed(
    real_jwt_gateway, tmp_path,
):
    gateway = real_jwt_gateway
    marker = tmp_path / "invalid-jwt-must-not-run.txt"
    command = _harmless_command(marker)
    expired = gateway.token_service.jwt.create_access_token(
        {"sub": _MEMBER_ID, "roles": ["member"], "scopes": ["profile"]},
        expires_delta=timedelta(seconds=-60),
    )
    unknown = gateway.token_service.jwt.create_access_token(
        {"sub": "direct-jwt-nonexistent", "roles": ["member"], "scopes": ["profile"]}
    )
    headers_cases = [
        {},
        {"Authorization": "Bearer definitely-not-a-signed-jwt"},
        {"Authorization": f"Bearer {expired}"},
        {"Authorization": f"Bearer {unknown}"},
    ]
    original_popen = subprocess.Popen
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        with patch("tools.v1.terminal_tool.subprocess.Popen", wraps=original_popen) as spy:
            responses = [
                await client.post(
                    "/v1/capabilities/terminal.run/execute", headers=headers,
                    json={"arguments": {"command": command, "cwd": str(tmp_path), "timeout": 8}},
                )
                for headers in headers_cases
            ]
    witness = {
        "statuses": [response.status_code for response in responses],
        "popen_count": spy.call_count,
        "marker_created": marker.exists(),
    }
    assert witness == {
        "statuses": [401, 401, 401, 401], "popen_count": 0,
        "marker_created": False,
    }, witness


@pytest.mark.asyncio
async def test_j03_signed_unprivileged_jwt_cannot_reach_real_terminal_popen(
    real_jwt_gateway, tmp_path,
):
    """RED-FIRST: an authenticated member must not receive host EXECUTE authority."""
    gateway = real_jwt_gateway
    marker = tmp_path / "unprivileged-must-not-run.txt"
    command = _harmless_command(marker)
    original_popen = subprocess.Popen
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        probe = await client.get(
            "/__direct_jwt_test_identity", headers=gateway.headers("member")
        )
        assert probe.status_code == 200, probe.text
        assert probe.json()["permissions"] == ["read:public"]
        with patch("tools.v1.terminal_tool.subprocess.Popen", wraps=original_popen) as spy:
            response = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers=gateway.headers("member"),
                json={"arguments": {"command": command, "cwd": str(tmp_path), "timeout": 8}},
            )
    witness = {
        "http_status": response.status_code,
        "popen_count": spy.call_count,
        "marker_created": marker.exists(),
    }
    assert witness == {
        "http_status": 403, "popen_count": 0, "marker_created": False,
    }, witness


@pytest.mark.asyncio
async def test_j04_signed_privileged_db_role_preserves_real_terminal_positive(
    real_jwt_gateway, tmp_path,
):
    gateway = real_jwt_gateway
    marker = tmp_path / "signed-admin-positive.txt"
    command = _harmless_command(marker)
    original_popen = subprocess.Popen
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        with patch("tools.v1.terminal_tool.subprocess.Popen", wraps=original_popen) as spy:
            response = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers=gateway.headers("admin"),
                json={"arguments": {"command": command, "cwd": str(tmp_path), "timeout": 8}},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["capability_id"] == "terminal.run" and body["success"] is True
    assert body["output"]["ok"] is True
    assert body["output"]["data"]["exit_code"] == 0
    assert spy.call_count == 1
    assert marker.read_text(encoding="utf-8") == "DIRECT_JWT_OK"


@pytest.mark.asyncio
async def test_j05_signed_privileged_jwt_cannot_promote_model_origin_direct_to_execute(
    real_jwt_gateway, tmp_path,
):
    gateway = real_jwt_gateway
    marker = tmp_path / "forged-direct-must-not-run.txt"
    command = _harmless_command(marker)
    privileged = await gateway.jwt_authenticator.authenticate(gateway.tokens["admin"])

    class _HostileInference:
        async def complete(self, request):
            assert all(item.name != "terminal.run" for item in request.tools)
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                model=request.model or "mock-chat",
                provider="direct-jwt-test",
                message=InferenceMessage(
                    role="assistant", content="",
                    tool_calls=(InferenceToolCall(
                        id="forged-model-call", name="terminal.run",
                        arguments={"command": command, "cwd": str(tmp_path)},
                    ),),
                ),
            )

    direct = DirectChatRuntime(
        inference=_HostileInference(), capability_runtime=gateway.runtime
    )
    original_popen = subprocess.Popen
    with patch("tools.v1.terminal_tool.subprocess.Popen", wraps=original_popen) as spy:
        with pytest.raises(PermissionError, match="not allowed in DIRECT mode"):
            await direct.execute(
                messages=[InferenceMessage(role="user", content="run host command")],
                identity=privileged, session_id="direct-jwt-session", model="mock-chat",
            )
    assert spy.call_count == 0 and not marker.exists()
