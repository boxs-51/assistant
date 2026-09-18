from typing import List
import structlog
from fastapi import APIRouter, Depends, status

from .....application.container import ApplicationContainer
from .....domain.schemas.identity import Identity
from .....domain.schemas.tool import GatewayToolDefinition
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container

router = APIRouter(prefix="/v1/tools", tags=["Tools"])
logger = structlog.get_logger(__name__)


def _authorization(container):
    return (
        getattr(container, "authorization_service", None)
        or container.capability_runtime.authorization
    )


@router.get("/", response_model=List[GatewayToolDefinition])
async def list_tools(
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    catalog = getattr(container.capability_runtime, "catalog", None)
    result = []
    for tool in container.tool_registry.get_all():
        if catalog is not None and catalog.contains_definition(tool.name):
            definition = catalog.get_definition(tool.name)
            if not _authorization(container).is_allowed(identity, definition):
                continue
        result.append(tool)
    return result


class ToolRegistrationResponse(GatewayToolDefinition):
    status: str = "success"


@router.post(
    "/",
    response_model=ToolRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký một Tool mới với Gateway",
)
async def register_tool(
    tool_definition: GatewayToolDefinition,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Endpoint cho phép Client (ví dụ: một plugin CRM, một agent game)
    đăng ký một tool với Gateway để các Agent khác có thể sử dụng.
    """
    # Compatibility transport only; execution metadata and driver validation
    # are owned by the canonical capability control plane.
    from .capability_router import register_tool_capability

    await register_tool_capability(tool_definition, identity, container)
    return ToolRegistrationResponse(status="success", **tool_definition.model_dump())
