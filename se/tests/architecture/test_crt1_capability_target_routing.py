from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.target import (
    CapabilityInvocationTarget,
    FallbackPolicy,
    ResourceScope,
    canonical_target_payload,
)
from se.src.domain.schemas.capability import CapabilityExecutionRequest
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.contracts.error import CapabilityError
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.capability.policy import (
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.transport.gateway.api.v1.capability_router import (
    execute_capability as execute_capability_endpoint,
)


def _definition(capability_id: str = "crt.echo") -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="CRT-1 target routing fixture",
        input_schema={"type": "object"},
    )


def _client(definition, implementation_id, connection_id, stable_client_id):
    return CapabilityImplementation.from_definition(
        definition,
        implementation_id=implementation_id,
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id="user-crt",
        connection_id=connection_id,
        metadata={
            "client_id": stable_client_id,
            "resource_scopes": ["CLIENT_LOCAL", "EXTERNAL"],
        },
    )


def _server(definition, implementation_id):
    return CapabilityImplementation.from_definition(
        definition,
        implementation_id=implementation_id,
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="SERVER_REGISTRY",
        owner_type=CapabilityOwnerType.SYSTEM,
        metadata={"resource_scopes": ["CLIENT_LOCAL", "EXTERNAL"]},
    )


def _enabled_catalog(*implementations):
    catalog = CapabilityCatalog()
    definition = _definition()
    catalog.register_definition(definition)
    for item in implementations:
        catalog.register_implementation(item)
        catalog.transition_implementation(
            item.implementation_id,
            CapabilityImplementationState.ENABLED,
        )
    return catalog


def test_target_none_preserves_exact_legacy_fingerprint_payload():
    arguments = {"b": 2, "a": {"x": 1}}
    legacy_payload = {
        "capability_id": "crt.echo",
        "capability_version": "1.0",
        "arguments": arguments,
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    assert capability_request_fingerprint(
        capability_id="crt.echo",
        capability_version="1.0",
        arguments=arguments,
        target=None,
    ) == expected


def test_semantic_target_fields_change_fingerprint_but_physical_ids_do_not_exist_in_target():
    base = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        resource_ref="client://client-a/report.txt",
        stable_client_id="client-a",
        fallback_policy=FallbackPolicy.NONE,
    )
    variants = [
        base.model_copy(update={"resource_scope": ResourceScope.EXTERNAL}),
        base.model_copy(update={"resource_ref": "client://client-a/other.txt"}),
        base.model_copy(update={"stable_client_id": "client-b"}),
        base.model_copy(
            update={
                "fallback_policy": FallbackPolicy.SEMANTICALLY_EQUIVALENT_ONLY
            }
        ),
    ]
    base_fp = capability_request_fingerprint(
        capability_id="crt.echo",
        capability_version="1.0",
        arguments={"value": 1},
        target=base,
    )
    assert all(
        capability_request_fingerprint(
            capability_id="crt.echo",
            capability_version="1.0",
            arguments={"value": 1},
            target=item,
        ) != base_fp
        for item in variants
    )
    payload = canonical_target_payload(base)
    assert set(payload) == {
        "resource_scope",
        "resource_ref",
        "stable_client_id",
        "fallback_policy",
    }
    assert "connection_id" not in payload
    assert "implementation_id" not in payload


def test_server_and_client_dispatcher_use_identical_target_fingerprint():
    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        resource_ref="client://client-a/report.txt",
        stable_client_id="client-a",
        fallback_policy=FallbackPolicy.NONE,
    )
    arguments = {"path": "report.txt"}
    server_fp = capability_request_fingerprint(
        capability_id="crt.echo",
        capability_version="1.0",
        arguments=arguments,
        target=target,
    )
    client_fp = CapabilityDispatcher._request_fingerprint(
        "crt.echo",
        "1.0",
        arguments,
        canonical_target_payload(target),
    )
    assert client_fp == server_fp


def test_client_local_routes_only_same_stable_client_and_never_server_fallback():
    definition = _definition()
    client_a = _client(definition, "a-client", "conn-a", "client-a")
    client_b = _client(definition, "b-client", "conn-b", "client-b")
    server = _server(definition, "z-server")
    catalog = _enabled_catalog(client_a, client_b, server)

    connections = ConnectionRegistry()
    connections.register("sess-a", "user-crt", connection_id="conn-a")
    connections.activate("conn-a")
    connections.register("sess-b", "user-crt", connection_id="conn-b")
    connections.activate("conn-b")

    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        stable_client_id="client-a",
        fallback_policy=FallbackPolicy.NONE,
    )
    policy = CapabilityRoutingPolicy(connection_availability=connections)
    selected = policy.select(
        catalog,
        "crt.echo",
        context=CapabilityRequestContext(
            owner_id="user-crt",
            connection_id="conn-b",
            target=target,
        ),
    )
    assert selected.implementation_id == "a-client"

    connections.disconnect("conn-a")
    with pytest.raises(PermissionError):
        policy.select(
            catalog,
            "crt.echo",
            context=CapabilityRequestContext(
                owner_id="user-crt",
                connection_id="conn-b",
                target=target,
            ),
        )


def test_same_stable_client_new_connection_preserves_target_and_equivalent_fallback_is_explicit():
    definition = _definition()
    old_client = _client(definition, "a-old", "conn-old", "client-a")
    new_client = _client(definition, "b-new", "conn-new", "client-a")
    catalog = _enabled_catalog(old_client, new_client)
    connections = ConnectionRegistry()
    connections.register("sess-old", "user-crt", connection_id="conn-old")
    connections.register("sess-new", "user-crt", connection_id="conn-new")
    connections.activate("conn-new")
    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        stable_client_id="client-a",
    )
    selected = CapabilityRoutingPolicy(
        connection_availability=connections
    ).select(
        catalog,
        "crt.echo",
        context=CapabilityRequestContext(owner_id="user-crt", target=target),
    )
    assert selected.implementation_id == "b-new"

    ext_definition = _definition()
    server_a = _server(ext_definition, "a-server")
    server_b = _server(ext_definition, "b-server")
    ext_catalog = _enabled_catalog(server_a, server_b)
    equivalent = CapabilityInvocationTarget(
        resource_scope=ResourceScope.EXTERNAL,
        resource_ref="https://example.invalid/resource",
        fallback_policy=FallbackPolicy.SEMANTICALLY_EQUIVALENT_ONLY,
    )
    selected_fallback = CapabilityRoutingPolicy().select(
        ext_catalog,
        "crt.echo",
        context=CapabilityRequestContext(target=equivalent),
        excluded_implementation_ids=frozenset({"a-server"}),
    )
    assert selected_fallback.implementation_id == "b-server"

    no_fallback = equivalent.model_copy(
        update={"fallback_policy": FallbackPolicy.NONE}
    )
    with pytest.raises(PermissionError):
        CapabilityRoutingPolicy().select(
            ext_catalog,
            "crt.echo",
            context=CapabilityRequestContext(target=no_fallback),
            excluded_implementation_ids=frozenset({"a-server"}),
        )

@pytest.mark.asyncio
async def test_malformed_metadata_target_is_safe_validation_error():
    runtime = CapabilityRuntime()
    with pytest.raises(CapabilityError) as exc_info:
        await runtime.execute_capability(
            "crt.echo",
            {"value": "x"},
            Identity(user_id="user-crt", auth_type="jwt"),
            metadata={"target": "CLIENT_LOCAL"},
        )

    error = exc_info.value
    assert error.code == "CAPABILITY_INVALID_TARGET"
    assert error.category == "VALIDATION"
    assert error.retryable is False
    assert error.safe_for_client is True
    assert error.cause_type == "TypeError"

@pytest.mark.asyncio
async def test_http_malformed_metadata_target_returns_422():
    runtime = CapabilityRuntime()
    body = CapabilityExecutionRequest(
        arguments={"value": "x"},
        metadata={"target": "CLIENT_LOCAL"},
    )
    container = SimpleNamespace(capability_runtime=runtime)

    with pytest.raises(HTTPException) as exc_info:
        await execute_capability_endpoint(
            "crt.echo",
            body,
            Identity(user_id="user-crt", auth_type="jwt"),
            container,
        )

    error = exc_info.value
    assert error.status_code == 422
    assert error.detail["code"] == "CAPABILITY_INVALID_TARGET"
    assert error.detail["category"] == "VALIDATION"

