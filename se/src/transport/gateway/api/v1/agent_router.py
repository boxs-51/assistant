from fastapi import APIRouter, Depends, HTTPException, status

from .....application.container import ApplicationContainer
from .....domain.schemas.agent import AgentDefinition, AgentRegistrationResponse
from .....domain.schemas.identity import Identity
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container


router = APIRouter(prefix="/v1/agents", tags=["Agents"])


def _authorization(container):
    return (
        getattr(container, "authorization_service", None)
        or container.capability_runtime.authorization
    )


@router.get("/", response_model=list[AgentDefinition])
async def list_agents(
    container: ApplicationContainer = Depends(get_container),
    identity: Identity = Depends(get_current_identity),
):
    loader = getattr(container, "support_loader", None)
    agents = loader.list_agent_summaries(identity) if loader is not None else []
    known = {agent.name for agent in agents}
    catalog = getattr(container.capability_runtime, "catalog", None)
    for agent in container.agent_registry.list_all():
        if agent.name in known:
            continue
        if catalog is not None and catalog.contains_definition(agent.name):
            definition = catalog.get_definition(agent.name)
            if not _authorization(container).is_allowed(identity, definition):
                continue
        agents.append(agent)
    return agents


@router.get("/{agent_name}", response_model=AgentDefinition)
async def get_agent(
    agent_name: str,
    container: ApplicationContainer = Depends(get_container),
    identity: Identity = Depends(get_current_identity),
):
    catalog = getattr(container.capability_runtime, "catalog", None)
    if catalog is not None and catalog.contains_definition(agent_name):
        definition = catalog.get_definition(agent_name)
        if not _authorization(container).is_allowed(identity, definition):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown agent: {agent_name}",
            )
    agent = container.agent_registry.get(agent_name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown agent: {agent_name}",
        )
    return agent


@router.post(
    "/",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an Agent",
)
async def register_agent(
    agent_definition: AgentDefinition,
    container: ApplicationContainer = Depends(get_container),
    identity: Identity = Depends(get_current_identity),
):
    """Compatibility endpoint delegating to the capability control plane."""
    from .capability_router import register_agent_capability

    await register_agent_capability(agent_definition, identity, container)
    return AgentRegistrationResponse(
        name=agent_definition.name,
        message=f"Agent '{agent_definition.name}' has been registered successfully.",
    )
