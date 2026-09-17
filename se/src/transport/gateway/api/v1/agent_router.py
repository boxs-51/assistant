from fastapi import APIRouter, Depends, HTTPException, status

from .....application.container import ApplicationContainer
from .....domain.schemas.agent import AgentDefinition, AgentRegistrationResponse
from .....domain.schemas.identity import Identity
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container


router = APIRouter(prefix="/v1/agents", tags=["Agents"])


@router.get("/", response_model=list[AgentDefinition])
async def list_agents(
    container: ApplicationContainer = Depends(get_container),
    identity: Identity = Depends(get_current_identity),
):
    return container.agent_registry.list_all()


@router.get("/{agent_name}", response_model=AgentDefinition)
async def get_agent(
    agent_name: str,
    container: ApplicationContainer = Depends(get_container),
    identity: Identity = Depends(get_current_identity),
):
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
