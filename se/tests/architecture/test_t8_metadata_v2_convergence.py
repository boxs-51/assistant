from __future__ import annotations

import pytest

from se.src.runtimes.capability.catalog import (
    CapabilityCatalog,
    CapabilityDefinitionConflictError,
    CapabilityImplementationConflictError,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityEffect,
    CapabilityIdempotency,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.registration import (
    CapabilityRegistration,
    ClientCapabilityRegistration,
)
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
    ClientRegistrationError,
)
from se.src.runtimes.connection.registry import ConnectionRegistry


def _definition(
    capability_id: str = "logical.shared",
    *,
    source: str = "LOCAL",
    execution_kind: str = "PYTHON",
    version: str = "3.0",
    description: str = "shared",
    input_schema=None,
    output_schema=None,
    effects=None,
    idempotency: CapabilityIdempotency = CapabilityIdempotency.UNKNOWN,
    required_scopes=None,
    metadata=None,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        version=version,
        name=capability_id,
        description=description,
        input_schema=input_schema
        or {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema=output_schema
        or {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
        },
        source=source,
        execution_kind=execution_kind,
        kind="TOOL",
        execution_mode="ONE_SHOT",
        idempotency=idempotency,
        effects=effects
        or {
            CapabilityEffect.READ,
            CapabilityEffect.EXTERNAL_SIDE_EFFECT,
        },
        require_auth=False,
        required_scopes=required_scopes
        or ["scope.b", "scope.a"],
        metadata=metadata
        or {
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.b", "perm.a"],
            "danger_patterns": ["danger.b", "danger.a"],
        },
    )


def _request(
    definition: CapabilityDefinition,
    *,
    implementation_id: str | None = None,
    metadata=None,
) -> ClientCapabilityRegistration:
    capability_id = definition.capability_id
    return ClientCapabilityRegistration(
        connection_id="conn-1",
        client_id="desktop-01",
        owner_id="user-1",
        capabilities=[
            CapabilityRegistration(
                definition=definition,
                location=CapabilityExecutionLocation.CLIENT,
                driver_kind="REMOTE_CLIENT",
                owner_type=CapabilityOwnerType.CLIENT,
                owner_id="user-1",
                connection_id="conn-1",
                implementation_id=implementation_id
                or f"conn-1:{capability_id}",
                metadata=metadata
                or {
                    "physical_tool": "pkg",
                    "physical_version": "9.0.0",
                    "bind": {"action": "run"},
                    "manifest_version": "2.0",
                },
            )
        ],
    )


def _service():
    catalog = CapabilityCatalog()
    connections = ConnectionRegistry()
    connections.register(
        "sess-1",
        "user-1",
        connection_id="conn-1",
    )
    connections.activate("conn-1")
    return (
        ClientCapabilityRegistrationService(catalog, connections),
        catalog,
    )


def _register_server(
    catalog: CapabilityCatalog,
    definition: CapabilityDefinition,
) -> CapabilityImplementation:
    catalog.register_definition(definition)
    implementation = CapabilityImplementation.from_definition(
        definition,
        implementation_id=f"server:{definition.capability_id}",
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="PYTHON",
        owner_type=CapabilityOwnerType.SYSTEM,
        metadata={
            "physical_tool": "server_pkg",
            "physical_version": "1.0.0",
            "bind": {"action": "run"},
        },
    )
    catalog.register_implementation(implementation)
    return catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )


def test_matching_server_and_client_definitions_coexist_without_rewrite():
    service, catalog = _service()
    server_definition = _definition(
        required_scopes=["scope.a", "scope.b"],
        metadata={
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.a", "perm.b"],
            "danger_patterns": ["danger.a", "danger.b"],
        },
    )
    _register_server(catalog, server_definition)

    client_definition = _definition(
        required_scopes=["scope.b", "scope.a"],
        metadata={
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.b", "perm.a"],
            "danger_patterns": ["danger.b", "danger.a"],
        },
    )

    registered = service.register(_request(client_definition))

    assert len(registered) == 1
    assert registered[0].location == CapabilityExecutionLocation.CLIENT
    assert catalog.get_definition("logical.shared") == server_definition
    implementations = catalog.list_implementations("logical.shared")
    assert {
        item.location
        for item in implementations
    } == {
        CapabilityExecutionLocation.SERVER,
        CapabilityExecutionLocation.CLIENT,
    }


def test_client_first_then_server_keeps_same_canonical_definition():
    service, catalog = _service()
    canonical_client = _definition(
        required_scopes=["scope.a", "scope.b"],
        metadata={
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.a", "perm.b"],
            "danger_patterns": ["danger.a", "danger.b"],
        },
    )

    service.register(_request(canonical_client))
    client_first_dump = catalog.get_definition(
        "logical.shared"
    ).model_dump(mode="json")

    server_definition = _definition(
        required_scopes=["scope.a", "scope.b"],
        metadata={
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.a", "perm.b"],
            "danger_patterns": ["danger.a", "danger.b"],
        },
    )
    _register_server(catalog, server_definition)

    assert catalog.get_definition(
        "logical.shared"
    ).model_dump(mode="json") == client_first_dump
    assert catalog.get_definition("logical.shared") == server_definition
    assert {
        item.location
        for item in catalog.list_implementations("logical.shared")
    } == {
        CapabilityExecutionLocation.SERVER,
        CapabilityExecutionLocation.CLIENT,
    }


@pytest.mark.parametrize(
    ("source", "execution_kind"),
    [
        ("CLIENT", "PYTHON"),
        ("LOCAL", "REMOTE_CLIENT"),
        ("CLIENT", "REMOTE_CLIENT"),
    ],
)
def test_noncanonical_v2_source_or_execution_kind_cannot_be_catalog_authority(
    source,
    execution_kind,
):
    service, catalog = _service()
    definition = _definition(
        source=source,
        execution_kind=execution_kind,
    )

    with pytest.raises(
        ClientRegistrationError,
        match="source=LOCAL and execution_kind=PYTHON",
    ):
        service.register(_request(definition))

    assert not catalog.contains_definition("logical.shared")
    assert not catalog.contains_implementation("conn-1:logical.shared")


def test_v2_definition_provenance_leak_is_rejected_before_mutation():
    service, catalog = _service()
    definition = _definition(
        metadata={
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.a", "perm.b"],
            "danger_patterns": ["danger.a", "danger.b"],
            "client_id": "desktop-01",
            "physical_tool": "pkg",
            "bind": {"action": "run"},
        },
    )

    with pytest.raises(
        ClientRegistrationError,
        match="implementation provenance",
    ):
        service.register(_request(definition))

    assert not catalog.contains_definition("logical.shared")
    assert not catalog.contains_implementation("conn-1:logical.shared")


@pytest.mark.parametrize(
    ("mutator", "label"),
    [
        (
            lambda d: d.model_copy(update={"version": "4.0"}),
            "version",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "input_schema": {
                        "type": "object",
                        "properties": {"other": {"type": "string"}},
                        "additionalProperties": False,
                    }
                }
            ),
            "input schema",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "output_schema": {
                        "type": "object",
                        "properties": {"different": {"type": "boolean"}},
                    }
                }
            ),
            "output schema",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "effects": {CapabilityEffect.READ},
                }
            ),
            "effects",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "idempotency": CapabilityIdempotency.IDEMPOTENT,
                }
            ),
            "idempotency",
        ),
        (
            lambda d: d.model_copy(
                update={"required_scopes": ["scope.a"]},
            ),
            "scopes",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "metadata": {
                        **d.metadata,
                        "base_risk": "HIGH",
                    }
                }
            ),
            "risk",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "metadata": {
                        **d.metadata,
                        "required_permissions": ["perm.a"],
                    }
                }
            ),
            "permissions",
        ),
        (
            lambda d: d.model_copy(
                update={
                    "metadata": {
                        **d.metadata,
                        "danger_patterns": ["danger.a"],
                    }
                }
            ),
            "danger patterns",
        ),
    ],
)
def test_divergent_existing_definition_is_rejected_before_mutation(
    mutator,
    label,
):
    service, catalog = _service()
    existing = _definition(
        required_scopes=["scope.a", "scope.b"],
        metadata={
            "base_risk": "MEDIUM",
            "required_permissions": ["perm.a", "perm.b"],
            "danger_patterns": ["danger.a", "danger.b"],
        },
    )
    _register_server(catalog, existing)

    request = _request(mutator(existing))

    with pytest.raises(
        CapabilityDefinitionConflictError,
        match="different contract",
    ):
        service.register(request)

    assert catalog.get_definition("logical.shared") == existing
    assert not catalog.contains_implementation("conn-1:logical.shared")
    assert len(catalog.list_implementations("logical.shared")) == 1


def test_divergent_batch_has_zero_mutation_for_earlier_valid_entry():
    service, catalog = _service()
    existing = _definition()
    _register_server(catalog, existing)

    valid = _definition("logical.new")
    conflicting = existing.model_copy(
        update={"description": "divergent"}
    )
    request = ClientCapabilityRegistration(
        connection_id="conn-1",
        client_id="desktop-01",
        owner_id="user-1",
        capabilities=[
            _request(valid).capabilities[0],
            _request(conflicting).capabilities[0].model_copy(
                update={
                    "implementation_id": "conn-1:logical.shared",
                }
            ),
        ],
    )

    with pytest.raises(CapabilityDefinitionConflictError):
        service.register(request)

    assert not catalog.contains_definition("logical.new")
    assert not catalog.contains_implementation("conn-1:logical.new")
    assert not catalog.contains_implementation("conn-1:logical.shared")


def test_exact_implementation_id_reuse_is_idempotent():
    service, catalog = _service()
    request = _request(_definition())

    first = service.register(request)
    second = service.register(request)

    assert second == first
    assert len(catalog.list_implementations("logical.shared")) == 1


def test_divergent_implementation_id_reuse_is_rejected():
    service, catalog = _service()
    definition = _definition()
    service.register(_request(definition))

    divergent = _request(
        definition,
        metadata={
            "physical_tool": "pkg",
            "physical_version": "9.0.0",
            "bind": {"action": "different"},
            "manifest_version": "2.0",
        },
    )

    with pytest.raises(
        CapabilityImplementationConflictError,
        match="different contract",
    ):
        service.register(divergent)

    stored = catalog.get_implementation("conn-1:logical.shared")
    assert stored.metadata["bind"] == {"action": "run"}


def test_removed_implementation_id_cannot_be_revived():
    service, catalog = _service()
    request = _request(_definition())
    service.register(request)
    service.unregister_connection("conn-1")

    with pytest.raises(
        CapabilityImplementationConflictError,
        match="Removed implementation",
    ):
        service.register(request)

    assert (
        catalog.get_implementation("conn-1:logical.shared").state
        == CapabilityImplementationState.REMOVED
    )


def test_registration_deep_owns_nested_implementation_metadata():
    service, catalog = _service()
    request = _request(
        _definition(),
        metadata={
            "physical_tool": "pkg",
            "physical_version": "9.0.0",
            "bind": {
                "action": "run",
                "options": {"tags": ["original"]},
            },
            "manifest_version": "2.0",
        },
    )

    service.register(request)
    request.capabilities[0].metadata["bind"]["options"]["tags"].append(
        "mutated"
    )

    stored = catalog.get_implementation("conn-1:logical.shared")
    assert stored.metadata["bind"] == {
        "action": "run",
        "options": {"tags": ["original"]},
    }


def test_registration_deep_owns_logical_definition_snapshot():
    service, catalog = _service()
    definition = _definition()
    request = _request(definition)

    service.register(request)

    request_definition = request.capabilities[0].definition
    request_definition.input_schema["properties"]["value"]["type"] = "integer"
    request_definition.required_scopes.append("scope.mutated")
    request_definition.metadata["required_permissions"].append(
        "perm.mutated"
    )

    stored = catalog.get_definition("logical.shared")
    assert stored.input_schema["properties"]["value"]["type"] == "string"
    assert stored.required_scopes == ["scope.b", "scope.a"]
    assert stored.metadata["required_permissions"] == [
        "perm.b",
        "perm.a",
    ]


def test_duplicate_logical_ids_in_one_batch_are_rejected_before_mutation():
    service, catalog = _service()
    definition = _definition()
    first = _request(
        definition,
        implementation_id="conn-1:logical.shared:first",
    ).capabilities[0]
    second = _request(
        definition.model_copy(deep=True),
        implementation_id="conn-1:logical.shared:second",
    ).capabilities[0]
    request = ClientCapabilityRegistration(
        connection_id="conn-1",
        client_id="desktop-01",
        owner_id="user-1",
        capabilities=[first, second],
    )

    with pytest.raises(
        ClientRegistrationError,
        match="duplicate capability_id",
    ):
        service.register(request)

    assert not catalog.contains_definition("logical.shared")
    assert not catalog.contains_implementation(
        "conn-1:logical.shared:first"
    )
    assert not catalog.contains_implementation(
        "conn-1:logical.shared:second"
    )


@pytest.mark.parametrize("manifest_version", ["3.0", 2, True])
def test_explicit_unsupported_manifest_version_is_rejected_before_mutation(
    manifest_version,
):
    service, catalog = _service()
    request = _request(
        _definition(),
        metadata={
            "physical_tool": "pkg",
            "physical_version": "9.0.0",
            "bind": {"action": "run"},
            "manifest_version": manifest_version,
        },
    )

    with pytest.raises(
        ClientRegistrationError,
        match="Unsupported client capability manifest_version",
    ):
        service.register(request)

    assert not catalog.contains_definition("logical.shared")
    assert not catalog.contains_implementation("conn-1:logical.shared")
