"""Canonical HTTP control plane for declarative capabilities.

Client capabilities register through the WebSocket handshake, where they can be
bound to a live connection before an agent invokes them.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from .....application.container import ApplicationContainer
from .....domain.schemas.agent import AgentDefinition
from .....domain.schemas.capability import CapabilityRegistrationResponse, SkillDefinition
from .....domain.schemas.identity import Identity
from .....domain.schemas.tool import GatewayToolDefinition
from .....runtimes.capability.contracts.definition import CapabilityDefinition
from .....runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from .....runtimes.capability.contracts.registration import CapabilityKind, CapabilityRegistration
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container

router = APIRouter(prefix="/v1/capabilities", tags=["Capabilities"])


def _catalog(container: ApplicationContainer):
    runtime = container.capability_runtime
    if runtime is None or runtime.catalog is None:
        raise HTTPException(status_code=503, detail="Capability control plane is unavailable.")
    return runtime.catalog


def _response(kind: CapabilityKind, definition: CapabilityDefinition, implementations=()):
    return CapabilityRegistrationResponse(
        capability_id=definition.capability_id, kind=kind.value,
        definition=definition.model_dump(mode="json", by_alias=True),
        implementations=[item.model_dump(mode="json") for item in implementations],
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
    return catalog.transition_implementation(
        implementation_id,
        CapabilityImplementationState.ENABLED,
    )


@router.post("/", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_capability(body: CapabilityRegistration, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    if body.location.value == "CLIENT":
        raise HTTPException(status_code=422, detail="Client capabilities must register through /v1/events/ws.")
    if body.owner_id not in (None, identity.user_id):
        raise HTTPException(status_code=403, detail="Cannot register a capability for another owner.")
    try:
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
        container.capability_runtime.registry.register_definition(definition)
        return _response(body.kind, definition, [implementation])
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tools", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_tool_capability(body: GatewayToolDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    definition = CapabilityDefinition(
        id=body.name, name=body.name, description=body.description,
        input_schema=body.parameters or {"type": "object"}, require_auth=body.require_auth,
        required_scopes=body.required_scopes, metadata={"kind": "TOOL"},
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
        definition = _catalog(container).register_definition(definition)
        container.tool_registry.register(body)
        implementation = _ensure_server_tool_implementation(container, definition)
        return _response(CapabilityKind.TOOL, definition, [implementation])
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/agents", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_agent_capability(body: AgentDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    missing = [name for name in body.tools if container.tool_registry.get(name) is None]
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown tools: {', '.join(missing)}")
    definition = CapabilityDefinition(
        id=body.name, name=body.name, description=body.goal, input_schema={"type": "object"},
        execution_kind="AGENT", metadata={"kind": "AGENT", "agent": body.model_dump(mode="json")},
    )
    try:
        definition = _catalog(container).register_definition(definition)
        container.agent_registry.register(body)
        return _response(CapabilityKind.AGENT, definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/skills", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_skill_capability(body: SkillDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    definition = CapabilityDefinition(
        id=body.name, version=body.version, name=body.name, description=body.description,
        input_schema=body.input_schema, execution_kind="SKILL",
        metadata={"kind": "SKILL", "instruction": body.instruction, **body.metadata},
    )
    try:
        definition = _catalog(container).register_definition(definition)
        container.capability_runtime.registry.register_definition(definition)
        return _response(CapabilityKind.SKILL, definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{capability_id}", response_model=CapabilityRegistrationResponse)
async def get_capability(capability_id: str, container: ApplicationContainer = Depends(get_container)):
    try:
        catalog = _catalog(container)
        definition = catalog.get_definition(capability_id)
        kind = CapabilityKind(str(definition.metadata.get("kind", "TOOL")))
        return _response(kind, definition, catalog.list_implementations(capability_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc