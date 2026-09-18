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
from .....runtimes.capability.contracts.error import CapabilityError
from .....runtimes.capability.contracts.result import CapabilityResult
from .....runtimes.capability.drivers.agent_driver import AgentCapabilityDriver
from .....runtimes.capability.drivers.skill_driver import ExecutableSkillCapabilityDriver
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


@router.get("/", response_model=list[CapabilityRegistrationResponse])
async def list_capabilities(
    kind: CapabilityKind | None = None,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    catalog = _catalog(container)
    result = []
    for definition in catalog.list_definitions():
        if not _authorization(container).is_allowed(identity, definition):
            continue
        try:
            definition_kind = definition.kind
        except ValueError:
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
    return result


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
        if str(catalog.get_definition(name).metadata.get("kind", "")).upper() != "SKILL"
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
        definition = catalog.register_definition(definition)
        container.agent_registry.register(body)
        implementations = []
        agent_runtime = getattr(container, "agent_runtime", None)
        if agent_runtime is not None:
            driver = AgentCapabilityDriver(definition, body, agent_runtime)
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/skills", response_model=CapabilityRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_skill_capability(body: SkillDefinition, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    catalog = _catalog(container)
    definition = CapabilityDefinition(
        id=body.name, version=body.version, name=body.name, description=body.description,
        input_schema=body.input_schema, execution_kind="SKILL",
        kind=CapabilityKind.SKILL,
        execution_mode=CapabilityExecutionMode(body.execution_mode),
        effects=set(body.effects),
        metadata={"kind": "SKILL", "instruction": body.instruction, **body.metadata},
    )
    try:
        definition = catalog.register_definition(definition, allow_update=True)
        container.capability_runtime.registry.register_definition(definition)
        implementations = []
        if definition.execution_mode is not CapabilityExecutionMode.CONTEXT_ONLY:
            inference_port = getattr(container, "inference_port", None)
            if inference_port is None:
                raise ValueError("Executable skills require the inference runtime.")
            driver = ExecutableSkillCapabilityDriver(
                definition, body.instruction, inference_port
            )
            container.capability_runtime.register_capability(driver)
            implementation_id = f"server:skill:{definition.capability_id}"
            implementation = CapabilityImplementation.from_definition(
                definition,
                implementation_id=implementation_id,
                location=CapabilityExecutionLocation.SERVER,
                driver_kind="SKILL_RUNTIME",
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
        return _response(CapabilityKind.SKILL, definition, implementations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    try:
        catalog = _catalog(container)
        definition = catalog.get_definition(capability_id)
        if not _authorization(container).is_allowed(identity, definition):
            raise HTTPException(status_code=404, detail=f"Unknown capability: {capability_id}")
        kind = definition.kind
        return _response(kind, definition, catalog.list_implementations(capability_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
