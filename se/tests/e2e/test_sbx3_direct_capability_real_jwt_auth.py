"""DIRECT-JWT-DB-T1: signed JWT + SQL Role/Permission + real Server terminal ingress.

AUTH_FIXTURE_LEVEL=REAL_SIGNED_JWT_DB_ROLE_TEST_APP.
No dependency override of get_current_identity, JWTAuthenticator, or terminal driver.
The HTTP adapter is in-process ASGI; this is NOT external TCP/production proof.
RED-first J03 must remain physically RED until separately authorized T2.
"""
from __future__ import annotations

import secrets
import hashlib
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
from se.src.infrastructure.storage.models.sql.user_data.application import Application
from se.src.infrastructure.storage.models.sql.user_data.api_key import APIKey
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
from se.src.transport.gateway.authentication.services.api_key_service import APIKeyService
from se.src.transport.gateway.authentication.dependency import get_current_identity
from se.src.transport.gateway.authentication.manager import AuthenticationManager
from se.src.transport.gateway.authentication.middleware import AuthenticationMiddleware
from se.src.transport.gateway.authentication.services.token_service import TokenService


_MEMBER_ID = "direct-jwt-member"
_ADMIN_ID = "direct-jwt-admin"
_WRITE_ONLY_ID = "direct-jwt-write-only"


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
            {"id": _WRITE_ONLY_ID, "email": "direct-write-only@example.invalid",
             "password_hash": "offline-test-no-password-login", "status": "active"},
        ])
        await conn.execute(Organization.__table__.insert(), {
            "id": "direct-jwt-org", "owner_id": _ADMIN_ID,
            "name": "Direct JWT isolated test", "plan": "free", "status": "active",
        })
        await conn.execute(Role.__table__.insert(), [
            {"id": "direct-role-member", "name": "member", "organization_id": None},
            {"id": "direct-role-admin", "name": "admin", "organization_id": None},
            {"id": "direct-role-write-only", "name": "direct-write-only", "organization_id": None},
        ])
        await conn.execute(Permission.__table__.insert(), [
            {"id": "direct-perm-read", "name": "read:public"},
            {"id": "direct-perm-write", "name": "admin:write"},
            {"id": "direct-perm-terminal", "name": "terminal:execute"},
        ])
        await conn.execute(Member.__table__.insert(), [
            {"organization_id": "direct-jwt-org", "user_id": _MEMBER_ID},
            {"organization_id": "direct-jwt-org", "user_id": _ADMIN_ID},
            {"organization_id": "direct-jwt-org", "user_id": _WRITE_ONLY_ID},
        ])
        await conn.execute(member_role_association.insert(), [
            {"member_organization_id": "direct-jwt-org", "member_user_id": _MEMBER_ID,
             "role_id": "direct-role-member"},
            {"member_organization_id": "direct-jwt-org", "member_user_id": _ADMIN_ID,
             "role_id": "direct-role-admin"},
            {"member_organization_id": "direct-jwt-org", "member_user_id": _WRITE_ONLY_ID,
             "role_id": "direct-role-write-only"},
        ])
        await conn.execute(role_permission_association.insert(), [
            {"role_id": "direct-role-member", "permission_id": "direct-perm-read"},
            {"role_id": "direct-role-admin", "permission_id": "direct-perm-write"},
            {"role_id": "direct-role-admin", "permission_id": "direct-perm-terminal"},
            {"role_id": "direct-role-write-only", "permission_id": "direct-perm-write"},
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
    write_only_jwt = (await token_service.create_user_tokens(
        _WRITE_ONLY_ID, "direct-write-only@example.invalid"
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
            uow_factory=uow_factory, tokens={"member": member_jwt, "admin": admin_jwt, "write_only": write_only_jwt},
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
        assert await uow.users.get_user_roles(_WRITE_ONLY_ID) == ["direct-write-only"]
        assert await uow.permissions.get_permissions_for_role_names(["member"]) == {"read:public"}
        assert await uow.permissions.get_permissions_for_role_names(["admin"]) == {"admin:write", "terminal:execute"}
        assert await uow.permissions.get_permissions_for_role_names(["direct-write-only"]) == {"admin:write"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        for principal, user_id, roles, permissions in [
            ("member", _MEMBER_ID, ["member"], ["read:public"]),
            ("admin", _ADMIN_ID, ["admin"], ["admin:write", "terminal:execute"]),
            ("write_only", _WRITE_ONLY_ID, ["direct-write-only"], ["admin:write"]),
        ]:
            identity = await gateway.jwt_authenticator.authenticate(gateway.tokens[principal])
            assert identity.auth_type == "jwt"
            assert identity.user_id == user_id
            assert identity.roles == roles
            assert set(identity.permissions) == set(permissions)
            assert len(identity.permissions) == len(permissions)
            response = await client.get(
                "/__direct_jwt_test_identity", headers=gateway.headers(principal)
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["user_id"] == user_id and body["auth_type"] == "jwt"
            assert body["roles"] == roles and set(body["permissions"]) == set(permissions)
            assert len(body["permissions"]) == len(permissions)


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
    privileged = await gateway.jwt_authenticator.authenticate(gateway.tokens["admin"])
    assert set(privileged.permissions) == {"admin:write", "terminal:execute"}
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


@pytest.mark.parametrize("capability_id", ("terminal.run", "terminal.launch"))
@pytest.mark.asyncio
async def test_j05_signed_privileged_jwt_cannot_promote_model_origin_direct_to_execute(
    real_jwt_gateway, tmp_path, capability_id,
):
    gateway = real_jwt_gateway
    marker = tmp_path / "forged-direct-must-not-run.txt"
    command = _harmless_command(marker)
    privileged = await gateway.jwt_authenticator.authenticate(gateway.tokens["admin"])

    class _HostileInference:
        async def complete(self, request):
            assert all(item.name != capability_id for item in request.tools)
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                model=request.model or "mock-chat",
                provider="direct-jwt-test",
                message=InferenceMessage(
                    role="assistant", content="",
                    tool_calls=(InferenceToolCall(
                        id="forged-model-call", name=capability_id,
                        arguments={"command": command, "cwd": str(tmp_path)},
                    ),),
                ),
            )

    direct = DirectChatRuntime(
        inference=_HostileInference(), capability_runtime=gateway.runtime
    )
    with patch(
        "tools.v1.terminal_tool.subprocess.Popen",
        side_effect=OSError("T1 intercepted model-origin terminal dispatch"),
    ) as spy:
        with pytest.raises(PermissionError, match="not allowed in DIRECT mode"):
            await direct.execute(
                messages=[InferenceMessage(role="user", content="run host command")],
                identity=privileged, session_id="direct-jwt-session", model="mock-chat",
            )
    assert spy.call_count == 0 and not marker.exists()

@pytest.mark.asyncio
async def test_j07_jwt_launch_missing_and_invalid_bearer_never_reach_popen(
    real_jwt_gateway, tmp_path,
):
    """Authentication failures must stop terminal.launch before physical dispatch."""
    gateway = real_jwt_gateway
    marker = tmp_path / "j07-launch-must-not-exist.txt"
    command = _harmless_command(marker)
    witnesses = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        for headers in ({}, {"Authorization": "Bearer invalid-signed-jwt"}):
            with patch(
                "tools.v1.terminal_tool.subprocess.Popen",
                side_effect=OSError("unexpected host launch"),
            ) as popen:
                response = await client.post(
                    "/v1/capabilities/terminal.launch/execute",
                    headers=headers,
                    json={"arguments": {"command": command, "cwd": str(tmp_path)}},
                )
            witnesses.append({
                "http_status": response.status_code,
                "popen_count": popen.call_count,
                "marker_created": marker.exists(),
            })
    assert witnesses == [
        {"http_status": 401, "popen_count": 0, "marker_created": False},
        {"http_status": 401, "popen_count": 0, "marker_created": False},
    ], witnesses


@pytest.mark.asyncio
async def test_j08_jwt_member_and_write_only_principal_cannot_launch(
    real_jwt_gateway, tmp_path,
):
    """RED-first: neither read:public nor admin:write alone grants detached EXECUTE."""
    gateway = real_jwt_gateway
    witnesses = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        for principal in ("member", "write_only"):
            marker = tmp_path / f"j08-{principal}-launch-must-not-exist.txt"
            command = _harmless_command(marker)
            actual = await gateway.jwt_authenticator.authenticate(gateway.tokens[principal])
            assert "terminal:execute" not in actual.permissions
            with patch(
                "tools.v1.terminal_tool.subprocess.Popen",
                side_effect=OSError("T1 intercepted unauthorized launch"),
            ) as popen:
                response = await client.post(
                    "/v1/capabilities/terminal.launch/execute",
                    headers=gateway.headers(principal),
                    json={"arguments": {"command": command, "cwd": str(tmp_path)}},
                )
            witnesses[principal] = {
                "http_status": response.status_code,
                "popen_count": popen.call_count,
                "marker_created": marker.exists(),
            }
    expected = {"http_status": 403, "popen_count": 0, "marker_created": False}
    assert witnesses == {"member": expected, "write_only": expected}, witnesses


@pytest.mark.asyncio
async def test_j09_jwt_admin_terminal_execute_launch_dispatch_stub(
    real_jwt_gateway, tmp_path,
):
    """Privileged launch reaches one dispatch; do not create a detached OS child."""
    gateway = real_jwt_gateway
    identity = await gateway.jwt_authenticator.authenticate(gateway.tokens["admin"])
    assert "terminal:execute" in identity.permissions
    marker = tmp_path / "j09-no-real-detached-child.txt"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            return_value=SimpleNamespace(pid=424242),
        ) as popen:
            response = await client.post(
                "/v1/capabilities/terminal.launch/execute",
                headers=gateway.headers("admin"),
                json={"arguments": {
                    "command": _harmless_command(marker), "cwd": str(tmp_path),
                }},
            )
    witness = {
        "http_status": response.status_code,
        "popen_count": popen.call_count,
        "marker_created": marker.exists(),
    }
    assert witness == {
        "http_status": 200, "popen_count": 1, "marker_created": False,
    }, witness
    body = response.json()
    assert body["capability_id"] == "terminal.launch"
    assert body["success"] is True and body["output"]["ok"] is True
    assert body["output"]["data"]["pid"] == 424242
    assert body["output"]["data"]["started"] is True


@pytest.mark.asyncio
async def test_j10_jwt_write_only_cannot_run_terminal(
    real_jwt_gateway, tmp_path,
):
    """RED-first: admin:write must never be an implicit terminal:execute alias."""
    gateway = real_jwt_gateway
    marker = tmp_path / "j10-write-only-run-must-not-exist.txt"
    identity = await gateway.jwt_authenticator.authenticate(gateway.tokens["write_only"])
    assert set(identity.permissions) == {"admin:write"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
    ) as client:
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            side_effect=OSError("T1 intercepted unauthorized run"),
        ) as popen:
            response = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers=gateway.headers("write_only"),
                json={"arguments": {
                    "command": _harmless_command(marker),
                    "cwd": str(tmp_path), "timeout": 8,
                }},
            )
    witness = {
        "http_status": response.status_code,
        "popen_count": popen.call_count,
        "marker_created": marker.exists(),
    }
    assert witness == {
        "http_status": 403, "popen_count": 0, "marker_created": False,
    }, witness


@pytest.mark.asyncio
async def test_j11_cross_organization_same_name_role_must_not_inherit_terminal(
    real_jwt_gateway, tmp_path,
):
    """Signed JWT + actual persisted membership: role B name=admin cannot
    borrow the terminal grant from role A with the same name."""
    gateway = real_jwt_gateway
    victim = "direct-collision-owner-b"
    org_b = "direct-collision-org-b"
    role_b = "direct-collision-admin-b"
    async with gateway.uow_factory() as uow:
        await uow.session.execute(User.__table__.insert(), {
            "id": victim, "email": "collision-b@example.invalid",
            "password_hash": "disabled-test-password", "status": "active",
        })
        await uow.session.execute(Organization.__table__.insert(), {
            "id": org_b, "owner_id": victim, "name": "Untrusted org B",
            "plan": "free", "status": "active",
        })
        # This role is created AFTER org A's role already has terminal grant.
        await uow.session.execute(Role.__table__.insert(), {
            "id": role_b, "name": "admin", "organization_id": org_b,
        })
        await uow.session.execute(Member.__table__.insert(), {
            "organization_id": org_b, "user_id": victim,
        })
        await uow.session.execute(member_role_association.insert(), {
            "member_organization_id": org_b, "member_user_id": victim,
            "role_id": role_b,
        })
        await uow.commit()

    token_b = gateway.token_service.jwt.create_access_token({
        "sub": victim, "roles": ["admin"],
        "org_id": org_b, "scopes": ["profile"],
    })
    # Role names are *identical*; only durable role IDs/memberships differ.
    identity_b = await gateway.jwt_authenticator.authenticate(token_b)
    assert identity_b.organization_id == org_b
    assert identity_b.roles == ["admin"]
    assert "terminal:execute" not in identity_b.permissions

    # Even a valid signed JWT with forged org A claim must not gain A's grant.
    forged_org = gateway.token_service.jwt.create_access_token({
        "sub": victim, "roles": ["admin"],
        "org_id": "direct-jwt-org", "scopes": ["profile"],
    })
    asserted_a = await gateway.jwt_authenticator.authenticate(forged_org)
    assert "terminal:execute" not in asserted_a.permissions

    marker = tmp_path / "cross-organization-terminal-must-not-exist"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app),
        base_url="http://testserver",
    ) as client:
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            side_effect=OSError("cross-organization execution intercepted"),
        ) as popen:
            response = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"arguments": {"command": _harmless_command(marker),
                                    "cwd": str(tmp_path), "timeout": 8}},
            )
        assert response.status_code == 403, response.text
        assert popen.call_count == 0 and not marker.exists()
    original_a = await gateway.jwt_authenticator.authenticate(gateway.tokens["admin"])
    assert "terminal:execute" in original_a.permissions


@pytest.mark.asyncio
async def test_j12_admin_and_standard_api_keys_never_union_same_name_role_grants(
    real_jwt_gateway,
):
    """An ak_/sk_ token for org B must not acquire org A's terminal grant."""
    gateway = real_jwt_gateway
    victim = "direct-api-tenant-b"
    org_b = "direct-api-org-b"
    async with gateway.uow_factory() as uow:
        await uow.session.execute(User.__table__.insert(), {
            "id": victim, "email": "api-owner-b@example.invalid",
            "password_hash": "disabled-test-password", "status": "active",
        })
        await uow.session.execute(Organization.__table__.insert(), {
            "id": org_b, "owner_id": victim, "name": "API org B",
            "plan": "free", "status": "active",
        })
        await uow.session.execute(Role.__table__.insert(), {
            "id": "direct-api-role-b", "name": "admin",
            "organization_id": org_b,
        })
        await uow.session.execute(Member.__table__.insert(), {
            "organization_id": org_b, "user_id": victim,
        })
        await uow.session.execute(member_role_association.insert(), {
            "member_organization_id": org_b, "member_user_id": victim,
            "role_id": "direct-api-role-b",
        })
        await uow.session.execute(Application.__table__.insert(), [
            {"id": "direct-api-app-b", "organization_id": org_b, "name": "B"},
            {"id": "direct-api-app-a", "organization_id": "direct-jwt-org", "name": "A"},
        ])
        for prefix, app in (
            ("ak_crossb", "direct-api-app-b"),
            ("sk_crossb", "direct-api-app-b"),
            ("ak_crossa", "direct-api-app-a"),
        ):
            await uow.session.execute(APIKey.__table__.insert(), {
                "id": prefix, "application_id": app, "prefix": prefix,
                "hashed_key": hashlib.sha256(b"proof-body").hexdigest(),
                "status": "active",
            })
        await uow.commit()

    key_service = APIKeyService(gateway.uow_factory)
    admin_b = await key_service.verify_key("ak_crossb_proof-body")
    assert admin_b.organization_id == org_b
    assert "terminal:execute" not in admin_b.permissions
    standard_b = await key_service.verify_key("sk_crossb_proof-body")
    assert standard_b.organization_id == org_b
    assert "terminal:execute" not in standard_b.permissions
    admin_a = await key_service.verify_key("ak_crossa_proof-body")
    assert admin_a.organization_id == "direct-jwt-org"
    assert "terminal:execute" in admin_a.permissions
