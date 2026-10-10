"""SEC-R0 (#430): authenticated two-principal Skill -> DIRECT prompt regression.

TEST-ONLY diagnostic.  AUTH_FIXTURE_LEVEL=STRATEGY_SIMULATED: the synthetic
tokens go through the real Gateway authentication middleware and manager,
but do NOT establish production JWT-cryptographic assurance.  No external
provider, credentials, network listener, authorization monkeypatch, skip,
xfail, or intentionally forced failure is used.

A failing "marker not in B system prompt" assertion is genuine RED evidence
of prompt contamination, NOT a mergeable regression test or a P0 verdict.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import Depends

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.message import GatewayMessage
from se.src.domain.schemas.response import GatewayChoice, GatewayResponse
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.transport.gateway.authentication.authenticators.base import (
    AuthenticatorInterface,
)
from se.src.transport.gateway.authentication.dependency import get_current_identity
from se.src.transport.gateway.authentication.exceptions import InvalidCredentialsError
from se.src.transport.gateway.authentication.manager import AuthenticationManager
from se.src.transport.gateway.authentication.middleware import AuthenticationMiddleware

# Reuse existing OFFLINE provider/event-bus/container scaffolding, NOT its
# single-identity auth override.  This test removes that override before any
# request, installs real middleware + manager, and tests the auth boundary.
from se.tests.e2e import test_v1_offline as _offline

AUTH_FIXTURE_LEVEL = "STRATEGY_SIMULATED"
_PRINCIPALS = {"A": "sec-r0-test-principal-a", "B": "sec-r0-test-principal-b"}


class _TwoTokenAuthenticator(AuthenticatorInterface):
    def __init__(self, token_to_user: dict[str, str]) -> None:
        self._token_to_user = dict(token_to_user)

    def can_handle(self, token: str) -> bool:
        return token.startswith("r0_")

    async def authenticate(self, token: str) -> Identity:
        user_id = self._token_to_user.get(token)
        if user_id is None:
            raise InvalidCredentialsError("Invalid isolated R0 test credential")
        return Identity(auth_type="api_key", user_id=user_id, permissions=[])


# Keep synthetic Bearer credentials out of pytest argument/fixture reprs.
@dataclass(repr=False)
class _Harness:
    app: object
    tokens: dict[str, str]

    def headers(self, principal: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[principal]}"}


@pytest.fixture
def two_principal_gateway() -> Iterator[_Harness]:
    # The underlying scaffold is a generator fixture. It has the same routes,
    # mock inference provider and async bus as the existing positive E2E.
    scaffold = _offline.offline_app.__wrapped__()
    app = next(scaffold)
    try:
        # Explicitly refuse the original fixed offline-user dependency override.
        assert get_current_identity in app.dependency_overrides
        del app.dependency_overrides[get_current_identity]
        container = app.state.container
        assert get_current_identity not in app.dependency_overrides

        container.config.auth.enable = True
        container.config.auth.allow_guest = False
        tokens = {
            label: "r0_" + secrets.token_urlsafe(24)
            for label in _PRINCIPALS
        }
        assert tokens["A"] != tokens["B"]
        identity_map = {
            tokens[label]: user_id
            for label, user_id in _PRINCIPALS.items()
        }
        container.auth_manager = AuthenticationManager(
            [_TwoTokenAuthenticator(identity_map)]
        )

        # Every test endpoint is protected. The manager reads Bearer per call,
        # the middleware writes request.state.identity, and this original
        # dependency reads that state; identity itself is never overridden.
        @app.get("/__sec_r0/authenticated_identity")
        async def _identity_probe(identity: Identity = Depends(get_current_identity)):
            return {"user_id": identity.user_id, "auth_type": identity.auth_type}

        app.add_middleware(AuthenticationMiddleware, public_paths=[])
        yield _Harness(app=app, tokens=tokens)
    finally:
        next(scaffold, None)


async def _prove_authentication(client: httpx.AsyncClient, harness: _Harness):
    for label, user_id in _PRINCIPALS.items():
        response = await client.get(
            "/__sec_r0/authenticated_identity",
            headers=harness.headers(label),
        )
        assert response.status_code == 200, (label, response.status_code)
        assert response.json() == {"user_id": user_id, "auth_type": "api_key"}
    for headers in ({}, {"Authorization": "Bearer r0_unknown-test-token"}):
        response = await client.get(
            "/__sec_r0/authenticated_identity", headers=headers
        )
        assert response.status_code == 401
    assert _PRINCIPALS["A"] != _PRINCIPALS["B"]


async def _direct(
    client: httpx.AsyncClient,
    harness: _Harness,
    label: str,
    provider_calls: list[dict],
) -> list[dict]:
    start = len(provider_calls)
    response = await client.post(
        "/v1/chat/completions",
        headers=harness.headers(label),
        json={
            "model": "mock-chat",
            "agent_enabled": False,
            "messages": [{"role": "user", "content": "Please answer briefly."}],
            "metadata": {"routing": {"prefer_provider": "mock"}},
        },
    )
    assert response.status_code == 200, (
        "DIRECT HTTP request did not complete",
        label,
        response.status_code,
        response.text[:240],
    )
    request_batches = provider_calls[start:]
    assert request_batches, ("No provider-bound request observed", label)
    assert all(isinstance(item.get("messages"), list) for item in request_batches)
    return request_batches


def _system_text(provider_requests: list[dict]) -> str:
    # Check EVERY provider-bound role=system segment, not the final user reply.
    return "\n".join(
        str(message.get("content") or "")
        for request in provider_requests
        for message in request["messages"]
        if message.get("role") == "system"
    )


def _capture_mock_provider(harness: _Harness, monkeypatch: pytest.MonkeyPatch):
    captured: list[dict] = []

    async def capture_chat(**kwargs):
        body = dict(kwargs.get("body") or {})
        captured.append(body)
        return GatewayResponse(
            id="sec-r0-offline-provider",
            model=body.get("model") or "mock-chat",
            choices=[
                GatewayChoice(
                    index=0,
                    message=GatewayMessage(role="assistant", content="ok"),
                    finish_reason="stop",
                )
            ],
            metadata={"provider": "mock"},
        )

    provider = harness.app.state.container.provider_runtime.providers["mock"]
    monkeypatch.setattr(provider.chat, "chat", capture_chat)
    return captured


def _server_published_positive(harness: _Harness, suffix: str) -> str:
    # Server-side catalog construction, never an HTTP client's claimed
    # BUILTIN flag. This proves the projection/capture positive, *not* a
    # signature/manifest validation claim.
    marker = "SERVER_PUBLISHED_R0_" + suffix
    definition = CapabilityDefinition(
        id="sec-r0-server-" + suffix,
        name="sec-r0-server-" + suffix,
        description="Inert server-origin positive control",
        input_schema={"type": "object"},
        kind=CapabilityKind.SKILL,
        execution_mode=CapabilityExecutionMode.CONTEXT_ONLY,
        source="BUILTIN",
        metadata={
            "instruction": marker,
            "kind": "SKILL",
            "server_managed": True,
            "provenance": "SERVER_TEST_FIXTURE",
        },
    )
    harness.app.state.container.capability_runtime.catalog.register_definition(
        definition
    )
    return marker


async def _register_skill(
    client: httpx.AsyncClient,
    harness: _Harness,
    *,
    label: str,
    ingress: str,
    name: str,
    marker: str,
) -> httpx.Response:
    if ingress == "specialized":
        path = "/v1/capabilities/skills"
        body = {
            "name": name,
            "description": "Inert caller-registered R0 skill",
            "instruction": marker,
            "execution_mode": "CONTEXT_ONLY",
        }
    else:
        assert ingress == "generic"
        path = "/v1/capabilities/"
        body = {
            "kind": "SKILL",
            "location": "DECLARATIVE",
            "driver_kind": "DECLARATIVE",
            "owner_type": "USER",
            "owner_id": _PRINCIPALS[label],
            "implementation_id": "sec-r0:" + name,
            "definition": {
                "id": name,
                "name": name,
                "description": "Inert declarative Skill R0",
                "parameters": {"type": "object"},
                "kind": "SKILL",
                "execution_kind": "SKILL",
                "execution_mode": "CONTEXT_ONLY",
                "source": "HTTP_CALLER",
                "metadata": {"instruction": marker},
            },
        }
    return await client.post(path, headers=harness.headers(label), json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("ingress", ["specialized", "generic"])
@pytest.mark.parametrize("publisher,consumer", [("A", "B"), ("B", "A")])
async def test_caller_skill_never_enters_other_principal_direct_system_prompt(
    two_principal_gateway: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    ingress: str,
    publisher: str,
    consumer: str,
):
    """Unmocked middleware, manager, catalog and DIRECT; only provider mocked."""
    harness = two_principal_gateway
    provider_calls = _capture_mock_provider(harness, monkeypatch)
    suffix = uuid.uuid4().hex
    marker = "A_PRIVATE_SKILL_R0_" + suffix
    name = "sec-r0-caller-" + suffix
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app),
        base_url="http://testserver",
    ) as client:
        await _prove_authentication(client, harness)
        server_positive = _server_published_positive(harness, suffix)

        baseline = await _direct(client, harness, consumer, provider_calls)
        assert server_positive in _system_text(baseline), "Provider capture positive missing"
        assert marker not in _system_text(baseline)

        result = await _register_skill(
            client, harness,
            label=publisher, ingress=ingress, name=name, marker=marker,
        )
        assert result.status_code == 201, (
            "Skill registration/setup blocked",
            ingress,
            result.status_code,
            result.text[:260],
        )
        stored = harness.app.state.container.capability_runtime.catalog.get_definition(
            name
        )
        assert stored.kind is CapabilityKind.SKILL
        assert stored.execution_mode is CapabilityExecutionMode.CONTEXT_ONLY
        assert stored.metadata.get("instruction") == marker

        own = await _direct(client, harness, publisher, provider_calls)
        assert marker in _system_text(own), "Own-principal positive control failed"

        # Discovery and detail are observed on B, not confused with a
        # hypothetical owner-private contract which is not implemented yet.
        listing = await client.get(
            "/v1/capabilities/", headers=harness.headers(consumer)
        )
        detail = await client.get(
            "/v1/capabilities/" + name, headers=harness.headers(consumer)
        )
        assert listing.status_code == 200, listing.status_code
        assert detail.status_code in (200, 403, 404), detail.status_code

        other = await _direct(client, harness, consumer, provider_calls)
        # This is the actual falsifiable expected-RED security assertion.
        # Never xfail, skip or intentionally fabricate the marker.
        assert marker not in _system_text(other), (
            "A_TO_B_REPRO_CONFIRMED",
            "A verified distinct authenticated publisher's caller Skill "
            "reached B provider-bound DIRECT role=system; "
            f"ingress={ingress}; direction={publisher}->{consumer}; "
            f"fixture={AUTH_FIXTURE_LEVEL}; principal labels only.",
        )


@pytest.mark.asyncio
async def test_specialized_reserved_metadata_is_rejected_without_mutation(
    two_principal_gateway: _Harness,
):
    harness = two_principal_gateway
    name = "sec-r0-reserved-" + uuid.uuid4().hex
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app),
        base_url="http://testserver",
    ) as client:
        await _prove_authentication(client, harness)
        response = await client.post(
            "/v1/capabilities/skills",
            headers=harness.headers("A"),
            json={
                "name": name,
                "description": "Spoof attempt",
                "instruction": "INERT_R0_RESERVED_CONTROL",
                "execution_mode": "CONTEXT_ONLY",
                "metadata": {"server_managed": True, "owner_type": "SYSTEM"},
            },
        )
        assert response.status_code == 422, response.status_code
    assert not harness.app.state.container.capability_runtime.catalog.contains_definition(name)


@pytest.mark.asyncio
async def test_cross_principal_skill_id_collision_cannot_replace_original(
    two_principal_gateway: _Harness,
):
    harness = two_principal_gateway
    name = "sec-r0-collision-" + uuid.uuid4().hex
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app),
        base_url="http://testserver",
    ) as client:
        await _prove_authentication(client, harness)
        first = await _register_skill(
            client, harness,
            label="A", ingress="specialized", name=name, marker="R0_A_ORIGINAL",
        )
        assert first.status_code == 201, first.status_code
        catalog = harness.app.state.container.capability_runtime.catalog
        original = catalog.get_definition(name).model_dump(mode="json")
        replacement = await _register_skill(
            client, harness,
            label="B", ingress="specialized", name=name, marker="R0_B_REPLACEMENT",
        )
        after = catalog.get_definition(name).model_dump(mode="json")
        assert after == original, (
            "CROSS_PRINCIPAL_SKILL_DEFINITION_REPLACED",
            replacement.status_code,
        )
        assert replacement.status_code in (403, 409, 422), replacement.status_code
