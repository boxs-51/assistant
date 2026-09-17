from fastapi import APIRouter, Depends, HTTPException, status, Request
import structlog

from .....domain.schemas.agent import AgentDefinition, AgentRegistrationResponse
from .....domain.schemas.identity import Identity
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container
from .....application.container import ApplicationContainer
from .....agent.registry import AgentRegistry
from .....tool.registry import ToolRegistry # Đảm bảo import từ gateway.tool.registry

router = APIRouter(prefix="/v1/agents", tags=["Agents"])
logger = structlog.get_logger(__name__)


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown agent: {agent_name}")
    return agent


@router.post(
    "/",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký một Agent mới"
)
async def register_agent(
    agent_definition: AgentDefinition,
    container: ApplicationContainer = Depends(get_container),
    identity: Identity = Depends(get_current_identity),
):
    """
    Endpoint cho phép Client đăng ký một "bản thiết kế" của Agent với Gateway.

    - **Gateway** sẽ xác thực và lưu trữ định nghĩa này.
    - **Gateway** sẽ kiểm tra xem các `tools` mà Agent yêu cầu có tồn tại trong hệ thống không.
    - Trong tương lai, `AgentRuntime` của Gateway sẽ sử dụng định nghĩa này để thực thi Agent.
    """
    agent_registry: AgentRegistry = container.agent_registry
    tool_registry: ToolRegistry = container.tool_registry

    # Xác thực: Kiểm tra xem các tool mà agent cần có tồn tại không
    for tool_name in agent_definition.tools:
        if not tool_registry.get(tool_name):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Tool '{tool_name}' is not registered in the Gateway.")

    catalog = getattr(container.capability_runtime, "catalog", None)
    for skill_name in agent_definition.skills:
        if catalog is None or not catalog.contains_definition(skill_name):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Skill '{skill_name}' is not registered in the Gateway.",
            )
        definition = catalog.get_definition(skill_name)
        if str(definition.metadata.get("kind", "")).upper() != "SKILL":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Capability '{skill_name}' is not a Skill.",
            )

    agent_registry.register(agent_definition)

    return AgentRegistrationResponse(
        name=agent_definition.name,
        message=f"Agent '{agent_definition.name}' has been registered successfully."
    )
