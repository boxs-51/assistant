from __future__ import annotations

import hashlib
import json

import pytest

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
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.policy import (
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)


class _Active:
    def is_active(self, connection_id: str) -> bool:
        return True


def _target(**changes) -> CapabilityInvocationTarget:
    values = {
        "resource_scope": ResourceScope.CLIENT_LOCAL,
        "resource_ref": "desktop:primary",
        "stable_client_id": "client-a",
        "fallback_policy": FallbackPolicy.NONE,
    }
    values.update(changes)
    return CapabilityInvocationTarget(**values)


def test_crt1_legacy_fingerprint_payload_is_exactly_unchanged():
    arguments = {"b": 2, "a": {"y": 2, "x": 1}}
    observed = capability_request_fingerprint(
        capability_id="tool.echo",
        capability_version="1.0",
        arguments=arguments,
        target=None,
    )
    encoded = json.dumps(
        {
            "capability_id": "tool.echo",
            "capability_version": "1.0",
            "arguments": arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert observed == hashlib.sha256(encoded).hexdigest()


def test_crt1_each_semantic_target_field_changes_fingerprint():
    base = _target()
    base_hash = capability_request_fingerprint(
        capability_id="tool.echo",
        capability_version="1.0",
        arguments={"value": "x"},
        target=base,
    )
    variants = (
        _target(resource_scope=ResourceScope.EXTERNAL),
        _target(resource_ref="desktop:secondary"),
        _target(stable_client_id="client-b"),
        _target(fallback_policy=FallbackPolicy.SEMANTICALLY_EQUIVALENT_ONLY),
    )
    for variant in variants:
        assert capability_request_fingerprint(
            capability_id="tool.echo",
            capability_version="1.0",
            arguments={"value": "x"},
            target=variant,
        ) != base_hash


def test_crt1_client_local_routes_by_stable_client_without_server_fallback():
    definition = CapabilityDefinition(
        id="tool.echo",
        name="tool.echo",
        description="echo",
    )
    catalog = CapabilityCatalog()
    catalog.register_definition(definition)
    catalog.register_implementation(
        CapabilityImplementation.from_definition(
            definition,
            implementation_id="server:tool.echo",
            location=CapabilityExecutionLocation.SERVER,
            driver_kind="LOCAL",
            metadata={"resource_scopes": ["CLIENT_LOCAL"]},
        ).model_copy(update={"state": CapabilityImplementationState.ENABLED})
    )
    catalog.register_implementation(
        CapabilityImplementation.from_definition(
            definition,
            implementation_id="client-b:tool.echo",
            location=CapabilityExecutionLocation.CLIENT,
            driver_kind="REMOTE_CLIENT",
            owner_id="user-1",
            connection_id="conn-b",
            owner_type=CapabilityOwnerType.CLIENT,
            metadata={"client_id": "client-b"},
        ).model_copy(update={"state": CapabilityImplementationState.ENABLED})
    )
    catalog.register_implementation(
        CapabilityImplementation.from_definition(
            definition,
            implementation_id="client-a:tool.echo",
            location=CapabilityExecutionLocation.CLIENT,
            driver_kind="REMOTE_CLIENT",
            owner_id="user-1",
            connection_id="conn-new",
            owner_type=CapabilityOwnerType.CLIENT,
            metadata={"client_id": "client-a"},
        ).model_copy(update={"state": CapabilityImplementationState.ENABLED})
    )

    policy = CapabilityRoutingPolicy(connection_availability=_Active())
    selected = policy.select(
        catalog,
        "tool.echo",
        context=CapabilityRequestContext(
            owner_id="user-1",
            target=_target(resource_ref=None),
        ),
    )
    assert selected.implementation_id == "client-a:tool.echo"
    assert selected.connection_id == "conn-new"

    with pytest.raises(PermissionError):
        policy.select(
            catalog,
            "tool.echo",
            context=CapabilityRequestContext(
                owner_id="user-1",
                target=_target(resource_ref=None),
            ),
            excluded_implementation_ids=frozenset({"client-a:tool.echo"}),
        )
