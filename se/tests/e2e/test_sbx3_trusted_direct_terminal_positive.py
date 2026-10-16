"""SBX3 DIRECT-T1: authenticated capability positive and model-origin DIRECT denial.

AUTH_FIXTURE_LEVEL=STRATEGY_SIMULATED: synthetic Bearer credentials pass
through production AuthenticationMiddleware, AuthenticationManager and the
unmodified get_current_identity dependency. JWT cryptographic security is
outside this bounded regression. Benign subprocess writes only into tmp_path.
"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.message import GatewayMessage
from se.src.domain.schemas.response import GatewayChoice, GatewayResponse
from se.src.domain.schemas.tool import FunctionCall, GatewayToolCall
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.transport.gateway.authentication.authenticators.base import AuthenticatorInterface
from se.src.transport.gateway.authentication.dependency import get_current_identity
from se.src.transport.gateway.authentication.exceptions import InvalidCredentialsError
from se.src.transport.gateway.authentication.manager import AuthenticationManager
from se.src.transport.gateway.authentication.middleware import AuthenticationMiddleware
from se.tests.e2e import test_v1_offline as _offline


class _IsolatedBearerAuthenticator(AuthenticatorInterface):
    def __init__(self, token: str, untrusted_token: str):
        self._token = token
        self._untrusted_token = untrusted_token

    def can_handle(self, token: str) -> bool:
        return token.startswith("sbx3_direct_t1_")

    async def authenticate(self, token: str) -> Identity:
        if token == self._token:
            return Identity(auth_type="api_key", user_id="sbx3-trusted-owner", permissions=["admin:write", "terminal:execute"])
        if token == self._untrusted_token:
            return Identity(auth_type="api_key", user_id="sbx3-untrusted-principal", permissions=[])
        raise InvalidCredentialsError("Invalid isolated DIRECT-T1 credential")


@dataclass(repr=False)
class _Harness:
    app: object
    token: str
    untrusted_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def untrusted_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.untrusted_token}"}


@pytest.fixture
def trusted_direct_gateway():
    scaffold = _offline.offline_app.__wrapped__()
    app = next(scaffold)
    try:
        # This fixture must exercise real middleware and the unmodified identity
        # dependency; the old single-identity offline fixture override is removed.
        assert get_current_identity in app.dependency_overrides
        del app.dependency_overrides[get_current_identity]
        assert get_current_identity not in app.dependency_overrides
        container = app.state.container
        container.config.auth.enable = True
        container.config.auth.allow_guest = False
        token = "sbx3_direct_t1_" + secrets.token_urlsafe(24)
        untrusted_token = "sbx3_direct_t1_" + secrets.token_urlsafe(24)
        assert token != untrusted_token
        container.auth_manager = AuthenticationManager([_IsolatedBearerAuthenticator(token, untrusted_token)])
        app.add_middleware(AuthenticationMiddleware, public_paths=[])
        # Register canonical physical Metadata V2 tools, not a fake capability.
        root = Path(__file__).resolve().parents[3]
        registration = register_local_tools(
            container.capability_runtime,
            container.tool_registry,
            root / "tools" / "v1",
        )
        assert registration.get("terminal.run") == "registered", registration
        yield _Harness(app=app, token=token, untrusted_token=untrusted_token)
    finally:
        next(scaffold, None)


def _harmless_command(marker: Path) -> str:
    argv = [
        sys.executable, "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('DIRECT_OK', encoding='utf-8')",
    ]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


@pytest.mark.asyncio
async def test_trusted_direct_authenticated_capability_http_reaches_real_popen(
    trusted_direct_gateway, tmp_path,
):
    marker = tmp_path / "trusted-direct-marker.txt"
    command = _harmless_command(marker)
    app = trusted_direct_gateway.app

    # Nonexistent/invalid Bearer is rejected by real auth middleware.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for headers in ({}, {"Authorization": "Bearer sbx3_direct_t1_invalid"}):
            denied = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers=headers,
                json={"arguments": {"command": command, "cwd": str(tmp_path), "timeout": 8}},
            )
            assert denied.status_code == 401, denied.text
            assert not marker.exists(), "Unauthenticated caller created a process marker"

        # Spy wraps the original Popen and executes the real benign command.
        original_popen = subprocess.Popen
        with patch("tools.v1.terminal_tool.subprocess.Popen", wraps=original_popen) as popen:
            response = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers=trusted_direct_gateway.headers,
                json={"arguments": {"command": command, "cwd": str(tmp_path), "timeout": 8}},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["capability_id"] == "terminal.run"
        assert body["success"] is True
        output = body["output"]
        assert output["ok"] is True and output["action"] == "run", output
        assert output["data"]["exit_code"] == 0
        assert popen.call_count == 1, "Trusted capability did not reach real Popen"
        assert marker.read_text(encoding="utf-8") == "DIRECT_OK"


@pytest.mark.asyncio
async def test_authenticated_unprivileged_principal_cannot_execute_terminal(
    trusted_direct_gateway, tmp_path,
):
    """RED-first: a valid identity is not by itself terminal EXECUTE authority."""
    marker = tmp_path / "must-not-be-created-by-unprivileged-caller.txt"
    command = _harmless_command(marker)
    original_popen = subprocess.Popen
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=trusted_direct_gateway.app),
        base_url="http://testserver",
    ) as client:
        with patch("tools.v1.terminal_tool.subprocess.Popen", wraps=original_popen) as popen:
            denied = await client.post(
                "/v1/capabilities/terminal.run/execute",
                headers=trusted_direct_gateway.untrusted_headers,
                json={"arguments": {"command": command, "cwd": str(tmp_path), "timeout": 8}},
            )
    witness = {
        "http_status": denied.status_code,
        "popen_count": popen.call_count,
        "marker_created": marker.exists(),
    }
    assert witness == {
        "http_status": 403, "popen_count": 0, "marker_created": False,
    }, witness


@pytest.mark.asyncio
async def test_model_origin_direct_chat_cannot_promote_terminal_into_trusted_execute(
    trusted_direct_gateway, tmp_path,
):
    marker = tmp_path / "must-not-exist.txt"
    command = _harmless_command(marker)
    app = trusted_direct_gateway.app
    provider = app.state.container.provider_runtime.providers["mock"]
    original_chat = provider.chat.chat
    observed_tools = []

    async def hostile_provider(**kwargs):
        body = kwargs.get("body") or {}
        observed_tools.append(tuple(body.get("tools") or ()))
        return GatewayResponse(
            id="sbx3-direct-t1-forbidden-tool",
            model=body.get("model") or "mock-chat",
            choices=[
                GatewayChoice(
                    index=0,
                    message=GatewayMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            GatewayToolCall(
                                id="sbx3-forged-model-tool",
                                function=FunctionCall(
                                    name="terminal.run",
                                    arguments=json.dumps({
                                        "command": command, "cwd": str(tmp_path)
                                    }),
                                ),
                            )
                        ],
                    ),
                    finish_reason="stop",
                )
            ],
            metadata={"provider": "mock"},
        )

    provider.chat.chat = hostile_provider
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            denied = await client.post(
                "/v1/chat/completions",
                headers=trusted_direct_gateway.headers,
                json={
                    "model": "mock-chat",
                    "agent_enabled": False,
                    "messages": [{"role": "user", "content": "Run a command"}],
                    "metadata": {"routing": {"prefer_provider": "mock"}},
                },
            )
        assert observed_tools, "Provider was never invoked"
        assert all("terminal.run" not in str(tools) for tools in observed_tools)
        assert denied.status_code != 200, denied.text
        assert not marker.exists(), "Model-origin DIRECT launched a subprocess"
    finally:
        provider.chat.chat = original_chat

@pytest.mark.asyncio
async def test_launch_missing_or_invalid_bearer_cannot_reach_popen(
    trusted_direct_gateway, tmp_path,
):
    marker = tmp_path / "launch-no-auth-must-not-exist.txt"
    command = _harmless_command(marker)
    observations = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=trusted_direct_gateway.app),
        base_url="http://testserver",
    ) as client:
        for headers in ({}, {"Authorization": "Bearer sbx3_direct_t1_invalid"}):
            with patch(
                "tools.v1.terminal_tool.subprocess.Popen",
                side_effect=OSError("unexpected host launch"),
            ) as popen:
                response = await client.post(
                    "/v1/capabilities/terminal.launch/execute",
                    headers=headers,
                    json={"arguments": {"command": command, "cwd": str(tmp_path)}},
                )
            observations.append({
                "http_status": response.status_code,
                "popen_count": popen.call_count,
                "marker_created": marker.exists(),
            })
    expected = {"http_status": 401, "popen_count": 0, "marker_created": False}
    assert observations == [expected, expected], observations


@pytest.mark.asyncio
async def test_authenticated_unprivileged_principal_cannot_launch_terminal(
    trusted_direct_gateway, tmp_path,
):
    """RED-first: authenticated unprivileged caller never reaches launch Popen."""
    marker = tmp_path / "launch-unprivileged-must-not-exist.txt"
    command = _harmless_command(marker)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=trusted_direct_gateway.app),
        base_url="http://testserver",
    ) as client:
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            side_effect=OSError("T1 intercepted unauthorized launch"),
        ) as popen:
            response = await client.post(
                "/v1/capabilities/terminal.launch/execute",
                headers=trusted_direct_gateway.untrusted_headers,
                json={"arguments": {"command": command, "cwd": str(tmp_path)}},
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
async def test_trusted_principal_with_explicit_execute_grant_launches_stub(
    trusted_direct_gateway, tmp_path,
):
    """Positive dispatch control: fake PID, no physical detached process."""
    marker = tmp_path / "launch-positive-must-still-not-exist.txt"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=trusted_direct_gateway.app),
        base_url="http://testserver",
    ) as client:
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            return_value=SimpleNamespace(pid=424242),
        ) as popen:
            response = await client.post(
                "/v1/capabilities/terminal.launch/execute",
                headers=trusted_direct_gateway.headers,
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
