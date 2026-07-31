from fastapi import APIRouter, Depends, Request, status
import structlog
from typing import List

from ....domain.schemas.tool import GatewayToolDefinition
from ....domain.schemas.identity import Identity
from ..authentication.dependency import get_current_identity
from ....tool.registry import ToolRegistry

router = APIRouter(prefix="/v1/tools", tags=["Tools"])
logger = structlog.get_logger(__name__)

class ToolRegistrationResponse(GatewayToolDefinition):
    status: str = "success"

@router.post(
    "/",
    response_model=ToolRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký một Tool mới với Gateway"
)
async def register_tool(
    tool_definition: GatewayToolDefinition,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    """
    Endpoint cho phép Client (ví dụ: một plugin CRM, một agent game)
    đăng ký một tool với Gateway để các Agent khác có thể sử dụng.
    """
    tool_registry: ToolRegistry = request.app.state.tool_registry
    tool_registry.register(tool_definition)
    return ToolRegistrationResponse(status="success", **tool_definition.model_dump())