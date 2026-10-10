"""Canonical HTTP control plane for declarative capabilities.

Client capabilities register through the WebSocket handshake, where they can be
bound to a live connection before an agent invokes them.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from .....application.container import ApplicationContainer
from .....domain.schemas.agent import AgentDefinition
from .....domain.schemas.capability import (
    CapabilityExecutionRequest,
    CapabilityRegistrationResponse,
    SkillDefinition,
)
from .....domain.schemas.identity import Identity
from .....domain.schemas.tool import GatewayToolDefinition
from .....runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from .....runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from .....runtimes.capability.contracts.registration import CapabilityRegistration
from .....runtimes.capability.contracts.skill_manifest import (
    SKILL_RUNTIME_RESERVED_METADATA_KEYS,
)
from .....runtimes.capability.contracts.error import CapabilityError
from .....infrastructure.storage.repositories.capability_publications import (
    PublicationAuthorityUnavailable,
    PublicationConflict,
    PublicationPermissionDenied,
    PublicationStaleRevision,
)
from .....runtimes.capability.contracts.result import CapabilityResult
from .....runtimes.capability.drivers.agent_driver import AgentCapabilityDriver
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container

router = APIRouter(prefix="/v1/capabilities", tags=["Capabilities"])


def _catalog(container: ApplicationContainer):
    runtime = container.capability_runtime
    if runtime is None or runtime.catalog is None:
        raise HTTPException(status_code=503, detail="Capability control plane is unavailable.")
    return runtime.catalog


def _authorization(container: ApplicationContainer):
    return (
        getattr(container, "authorization_service", None)
        or container.capability_runtime.authorization
    )


def _response(kind: CapabilityKind, definition: CapabilityDefinition, implementations=()):
    return CapabilityRegistrationResponse(
        capability_id=definition.capability_id, kind=kind.value,
        definition=definition.model_dump(mode="json", by_alias=True),
        implementations=[item.model_dump(mode="json") for item in implementations],
    )


def _raise_publication_http(exc: Exception):
    if isinstance(exc, PublicationAuthorityUnavailable):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Durable Skill publication authority is unavailable.",
        ) from exc
    if isinstance(exc, PublicationPermissionDenied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, (PublicationConflict, PublicationStaleRevision)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


def _validated_generic_caller_skill(body: CapabilityRegistration) -> CapabilityDefinition:
    definition = body.definition
    if body.kind is not definition.kind:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Capability kind must match definition.kind.",
        )
    if definition.kind is not CapabilityKind.SKILL:
        raise HTTPException(status_code=422, detail="Expected a Skill definition.")
    if body.owner_type is not CapabilityOwnerType.USER:
        raise HTTPException(
            status_code=422,
            detail="Caller Skill owner_type must be USER.",
        )
    if definition.source != "HTTP_CALLER":
        raise HTTPException(
            status_code=422,
            detail="Caller Skill source must be HTTP_CALLER.",
        )
    if definition.execution_mode is not CapabilityExecutionMode.CONTEXT_ONLY:
        raise HTTPException(
            status_code=422,
            detail="Executable caller Skills are outside the P1A publication boundary.",
        )
    allowed_location = (
        body.location is CapabilityExecutionLocation.DECLARATIVE
        and body.driver_kind == "DECLARATIVE"
    ) or (
        body.location is CapabilityExecutionLocation.SERVER
        and body.driver_kind == "DECLARATIVE"
    )
    if not allowed_location:
        raise HTTPException(
            status_code=422,
            detail="Caller context Skill requires DECLARATIVE driver semantics.",
        )
    metadata = dict(definition.metadata or {})
    allowed_caller_keys = {"instruction"}
    reserved = sorted(
        set(metadata).intersection(
            SKILL_RUNTIME_RESERVED_METADATA_KEYS.difference(allowed_caller_keys)
        )
    )
    if reserved:
        raise HTTPException(
            status_code=422,
            detail="Skill metadata contains runtime-reserved keys: " + ", ".join(reserved),
        )
    instruction = str(metadata.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="Skill instruction must not be empty.")
    return definition.model_copy(
        update={
            "execution_kind": "SKILL",
            "metadata": {
                **metadata,
                "instruction": instruction,
                "kind": "SKILL",
                "server_managed": False,
                "runtime_owned": True,
                "lazy": False,
                "loaded": True,
                "schema_version": "1",
                "skill_id": definition.capability_id,
                "provenance": "HTTP_CALLER",
                "ownership": "CALLER_REGISTERED",
            },
        }
    )


def _ensure_server_tool_implementation(container: ApplicationContainer, definition: CapabilityDefinition):
    """Attach a catalog implementation only when a real executable server driver exists.

    The legacy ToolRegistry is metadata-only.  A capability is not considered
    executable merely because its definition is present.
    """
    runtime = container.capability_runtime
    driver = runtime.registry.get_driver(definition.capability_id)
    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Tool '{definition.capability_id}' has no executable server driver. "
                "Register/load the server tool first, or use the client WebSocket "
                "capability.register flow."
            ),
        )

    catalog = _catalog(container)
    implementation_id = f"server:{definition.capability_id}"
    if catalog.contains_implementation(implementation_id):
        implementation = catalog.get_implementation(implementation_id)
        if implementation.state == CapabilityImplementationState.REMOVED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Server implementation '{implementation_id}' was removed.",
            )
        runtime.driver_registry.bind(implementation_id, driver, replace=True)
        return implementation

    implementation = CapabilityImplementation.from_definition(
        definition,
        implementation_id=implementation_id,
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="SERVER_REGISTRY",
        owner_type=CapabilityOwnerType.SYSTEM,
        owner_id=identity.user_id if False else None,
        metadata={"kind": "TOOL", "driver": type(driver).__name__},
    )
    catalog.register_implementation(implementation)
    implementation = catalog.transition_implementation(
        implementation_id,
        CapabilityImplementationState.ENABLED,
    )
    runtime.driver_registry.bind(implementation_id, driver, replace=True)
    return implementation


@router.post("/", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_capability(body: CapabilityRegistration, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    if body.location is CapabilityExecutionLocation.CLIENT:
        raise HTTPException(status_code=422, detail="Client capabilities must register through /v1/events/ws.")
    if body.owner_id not in (None, identity.user_id):
        raise HTTPException(status_code=403, detail="Cannot register a capability for another owner.")
    if body.kind is not body.definition.kind:
        raise HTTPException(status_code=422, detail="Capability kind must match definition.kind.")

    runtime = container.capability_runtime
    if body.definition.kind is CapabilityKind.SKILL:
        definition = _validated_generic_caller_skill(body)
        try:
            await runtime.publish_caller_context_skill(definition, identity=identity)
            return _response(CapabilityKind.SKILL, definition, [])
        except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
            _raise_publication_http(exc)

    try:
        # When the durable namespace authority is configured, caller-created
        # non-Skill IDs are fenced as USER-origin before local side effects.
        await runtime.reserve_caller_namespace(body.definition, identity=identity)
        catalog = _catalog(container)
        definition = catalog.register_definition(body.definition)
        implementation = CapabilityImplementation.from_definition(
            definition, implementation_id=body.implementation_id, location=body.location,
            driver_kind=body.driver_kind, owner_type=body.owner_type,
            owner_id=body.owner_id or identity.user_id, connection_id=body.connection_id,
            metadata={**body.metadata, "kind": body.kind.value},
        )
        if catalog.contains_implementation(implementation.implementation_id):
            implementation = catalog.get_implementation(implementation.implementation_id)
        else:
            implementation = catalog.register_implementation(implementation)
        runtime.registry.register_definition(definition)
        return _response(body.kind, definition, [implementation])
    except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
        _raise_publication_http(exc)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/", response_model=list[CapabilityRegistrationResponse])
async def list_capabilities(
    kind: CapabilityKind | None = None,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    catalog = _catalog(container)
    runtime = container.capability_runtime
    result = []
    durable_skill_authority = runtime.publication_authority is not None
    for definition in catalog.list_definitions():
        try:
            definition_kind = definition.kind
        except ValueError:
            continue
        if definition_kind is CapabilityKind.SKILL and durable_skill_authority:
            continue
        if not _authorization(container).is_allowed(identity, definition):
            continue
        if kind is not None and definition_kind is not kind:
            continue
        result.append(
            _response(
                definition_kind,
                definition,
                catalog.list_implementations(definition.capability_id),
            )
        )
    if durable_skill_authority and kind in (None, CapabilityKind.SKILL):
        try:
            for definition in await runtime.list_visible_context_skills(identity):
                result.append(_response(CapabilityKind.SKILL, definition, []))
        except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
            _raise_publication_http(exc)
    return sorted(result, key=lambda item: item.capability_id)


@router.post("/tools", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_tool_capability(body: GatewayToolDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    definition = CapabilityDefinition(
        id=body.name, name=body.name, description=body.description,
        input_schema=body.parameters or {"type": "object"}, require_auth=body.require_auth,
        required_scopes=body.required_scopes, metadata={"kind": "TOOL"},
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode(body.execution_mode),
        effects=set(body.effects),
    )
    try:
        # Never mutate the catalog before we know an executable server driver exists.
        if container.capability_runtime.registry.get_driver(definition.capability_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Tool '{definition.capability_id}' has no executable server driver. "
                    "Use the client WebSocket registration path for remote tools."
                ),
            )
        await container.capability_runtime.reserve_caller_namespace(
            definition, identity=identity
        )
        definition = _catalog(container).register_definition(definition)
        container.tool_registry.register(body)
        implementation = _ensure_server_tool_implementation(container, definition)
        return _response(CapabilityKind.TOOL, definition, [implementation])
    except HTTPException:
        raise
    except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
        _raise_publication_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/agents", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_agent_capability(body: AgentDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    catalog = _catalog(container)

    def known_tool(name: str) -> bool:
        if container.tool_registry.get(name) is not None:
            return True
        if not catalog.contains_definition(name):
            return False
        definition = catalog.get_definition(name)
        return (
            definition.kind in {CapabilityKind.TOOL, CapabilityKind.AGENT}
            and bool(catalog.list_implementations(name, routable_only=True))
            and _authorization(container).is_allowed(identity, definition)
        )

    missing = [name for name in body.tools if not known_tool(name)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown tools/agents: {', '.join(missing)}")
    missing_skills = [
        name for name in body.skills
        if not catalog.contains_definition(name)
        or not _authorization(container).is_allowed(
            identity, catalog.get_definition(name)
        )
    ]
    if missing_skills:
        raise HTTPException(status_code=422, detail=f"Unknown skills: {', '.join(missing_skills)}")
    invalid_skills = [
        name for name in body.skills
        if (
            str(catalog.get_definition(name).metadata.get("kind", "")).upper() != "SKILL"
            or catalog.get_definition(name).metadata.get("server_managed") is not True
        )
    ]
    if invalid_skills:
        raise HTTPException(status_code=422, detail=f"Not skill capabilities: {', '.join(invalid_skills)}")
    definition = CapabilityDefinition(
        id=body.name, name=body.name, description=body.goal, input_schema={"type": "object"},
        execution_kind="AGENT", kind=CapabilityKind.AGENT,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
        metadata={"kind": "AGENT", "agent": body.model_dump(mode="json")},
    )
    try:
        await container.capability_runtime.reserve_caller_namespace(
            definition, identity=identity
        )
        definition = catalog.register_definition(definition)
        container.agent_registry.register(body)
        implementations = []
        agent_runtime = getattr(container, "agent_runtime", None)
        if agent_runtime is not None:
            driver = AgentCapabilityDriver(
                definition,
                body,
                agent_runtime,
                execution_supervisor=getattr(
                    container,
                    "agent_execution_supervisor",
                    None,
                ),
                execution_id_factory=getattr(
                    container, "agent_execution_id_factory", None
                ),
            )
            container.capability_runtime.register_capability(driver)
            implementation_id = f"server:agent:{definition.capability_id}"
            implementation = CapabilityImplementation.from_definition(
                definition,
                implementation_id=implementation_id,
                location=CapabilityExecutionLocation.SERVER,
                driver_kind="AGENT_RUNTIME",
                owner_type=CapabilityOwnerType.SYSTEM,
            )
            if not catalog.contains_implementation(implementation_id):
                catalog.register_implementation(implementation)
                implementation = catalog.transition_implementation(
                    implementation_id, CapabilityImplementationState.ENABLED
                )
            else:
                implementation = catalog.get_implementation(implementation_id)
            container.capability_runtime.driver_registry.bind(
                implementation_id, driver, replace=True
            )
            implementations.append(implementation)
        return _response(CapabilityKind.AGENT, definition, implementations)
    except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
        _raise_publication_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _trusted_legacy_skill_metadata(body: SkillDefinition) -> dict:
    caller_metadata = dict(body.metadata or {})
    reserved = sorted(
        set(caller_metadata).intersection(SKILL_RUNTIME_RESERVED_METADATA_KEYS)
    )
    if reserved:
        raise ValueError(
            "Skill metadata contains runtime-reserved keys: " + ", ".join(reserved)
        )
    return {
        **caller_metadata,
        "kind": "SKILL",
        "instruction": body.instruction,
        "server_managed": False,
        "runtime_owned": True,
        "lazy": False,
        "loaded": True,
        "schema_version": "1",
        "skill_id": body.name,
        "provenance": "HTTP_CALLER",
        "ownership": "CALLER_REGISTERED",
    }


@router.post("/skills", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_skill_capability(body: SkillDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    try:
        metadata = _trusted_legacy_skill_metadata(body)
        definition = CapabilityDefinition(
            id=body.name, version=body.version, name=body.name, description=body.description,
            input_schema=body.input_schema, execution_kind="SKILL",
            kind=CapabilityKind.SKILL,
            execution_mode=CapabilityExecutionMode(body.execution_mode),
            effects=set(body.effects),
            source="HTTP_CALLER",
            metadata=metadata,
        )
        if definition.execution_mode is not CapabilityExecutionMode.CONTEXT_ONLY:
            raise HTTPException(
                status_code=422,
                detail="Executable caller Skills are outside the P1A publication boundary.",
            )
        await container.capability_runtime.publish_caller_context_skill(
            definition, identity=identity
        )
        return _response(CapabilityKind.SKILL, definition, [])
    except HTTPException:
        raise
    except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
        _raise_publication_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/skills/{capability_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_skill_capability(
    capability_id: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    try:
        await container.capability_runtime.revoke_caller_context_skill(
            capability_id, identity=identity
        )
    except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
        _raise_publication_http(exc)
    return None


@router.post("/{capability_id}/execute", response_model=CapabilityResult)
async def execute_capability(
    capability_id: str,
    body: CapabilityExecutionRequest,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    try:
        return await container.capability_runtime.execute_capability(
            capability_id=capability_id,
            arguments=body.arguments,
            identity=identity,
            invocation_id=body.invocation_id,
            session_id=body.session_id,
            connection_id=body.connection_id,
            timeout_seconds=body.timeout_seconds,
            metadata=body.metadata,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except CapabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.model_dump(),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{capability_id}", response_model=CapabilityRegistrationResponse)
async def get_capability(
    capability_id: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    catalog = _catalog(container)
    if catalog.contains_definition(capability_id):
        local = catalog.get_definition(capability_id)
        if local.kind is not CapabilityKind.SKILL:
            if not _authorization(container).is_allowed(identity, local):
                raise HTTPException(status_code=404, detail=f"Unknown capability: {capability_id}")
            return _response(
                local.kind,
                local,
                catalog.list_implementations(capability_id),
            )
    try:
        definition = await container.capability_runtime.get_visible_context_skill(
            capability_id, identity
        )
    except (PublicationAuthorityUnavailable, PublicationConflict, PublicationPermissionDenied, PublicationStaleRevision) as exc:
        _raise_publication_http(exc)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Unknown capability: {capability_id}")
    return _response(CapabilityKind.SKILL, definition, [])
