from fastapi import APIRouter, Depends, HTTPException, Request, status

from ....domain.schemas.identity import Identity
from ....domain.schemas.multi_agent import (
    AgentJoinRequest,
    AgentMessageRequest,
    AgentSessionCreateRequest,
    AgentTaskCreateRequest,
)
from ..authentication.dependency import get_current_identity

router = APIRouter(prefix="/v1/multi-agent", tags=["Multi-Agent"])


def coordinator(request: Request):
    value = getattr(request.app.state, "container", None)
    if value is None or value.multi_agent_coordinator is None:
        raise HTTPException(status_code=503, detail="Multi-agent runtime is unavailable.")
    return value.multi_agent_coordinator


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_agent_session(
    body: AgentSessionCreateRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator(request).create_session_async(identity, body.agent_ids)
    except Exception as error:
        raise map_error(error) from error


@router.post("/sessions/{session_id}/agents")
async def add_agent_to_session(
    session_id: str,
    body: AgentJoinRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator(request).add_agent(session_id, body.agent_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.get("/sessions/{session_id}/messages")
async def list_agent_messages(
    session_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator(request).list_messages(session_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def send_agent_message(
    body: AgentMessageRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator(request).send_message_async(
            session_id=body.session_id,
            sender_id=body.sender_id,
            message_type=body.message_type,
            payload=body.payload,
            identity=identity,
            recipient_id=body.recipient_id,
        )
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_agent_task(
    body: AgentTaskCreateRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator(request).create_task_async(
            session_id=body.session_id,
            assigned_agent_id=body.assigned_agent_id,
            task_input=body.input,
            identity=identity,
            parent_task_id=body.parent_task_id,
        )
    except Exception as error:
        raise map_error(error) from error


@router.get("/tasks/{task_id}")
async def get_agent_task(
    task_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator(request).get_task(task_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(
    task_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator(request).cancel_task(task_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/sessions/{session_id}/close")
async def close_agent_session(
    session_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator(request).close_session(session_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/execute")
async def execute_agent_task(
    task_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    """Execute a task through an opt-in application callback when configured."""
    runtime = coordinator(request)
    executor = getattr(runtime, "executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Agent execution runtime is unavailable.")
    try:
        return await runtime.execute_task(task_id, identity, executor)
    except Exception as error:
        raise map_error(error) from error


@router.get("/executions/{execution_id}")
async def get_agent_execution(
    execution_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator(request).get_execution(execution_id, identity)
    except Exception as error:
        raise map_error(error) from error