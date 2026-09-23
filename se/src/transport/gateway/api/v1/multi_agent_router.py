from fastapi import APIRouter, Depends, HTTPException, status

from .....application.container import ApplicationContainer
from .....domain.schemas.identity import Identity
from .....domain.schemas.multi_agent import (
    AgentJoinRequest,
    AgentMessageRequest,
    AgentSessionCreateRequest,
    AgentTaskCreateRequest,
    AgentTaskForkRequest,
    AgentTaskRetryRequest,
    AgentTaskBranchResolutionRequest,
    AgentTaskAggregateRequest,
)
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container
from .....application.connection_affinity import (
    ConnectionAffinityError,
    validate_connection_affinity,
)
from .....runtimes.agent.fork_planning import (
    ForkPlanDeferred,
    ForkPlanError,
)
from .....runtimes.agent.persistence import (
    AggregateControlError,
    ForkControlError,
    RetryControlError,
)
from .....runtimes.agent.retry_planning import RetryPlanError, RetryPlanDeferred
from .....runtimes.agent.task_budget import (
    AggregateAdmissionError,
    BranchResolutionError,
    ForkConsumeDeferred,
    ForkConsumeError,
    RetryConsumeDeferred,
    RetryConsumeError,
)

router = APIRouter(prefix="/v1/multi-agent", tags=["Multi-Agent"])


def get_coordinator(
    container: ApplicationContainer = Depends(get_container),
):
    if container is None or container.multi_agent_coordinator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Multi-agent runtime is unavailable.",
        )
    return container.multi_agent_coordinator


def _fork_error_http_detail(error: Exception):
    if not isinstance(
        error,
        (ForkPlanError, ForkConsumeError, ForkControlError),
    ):
        return None

    code = str(error.code)
    if isinstance(error, ForkControlError):
        retryable = bool(error.retryable)
    else:
        retryable = isinstance(
            error,
            (ForkPlanDeferred, ForkConsumeDeferred),
        )

    if code == "FORK_FOREIGN_PRINCIPAL":
        status_code = status.HTTP_403_FORBIDDEN
    elif retryable or isinstance(error, ForkConsumeError) and "CONFLICT" in code:
        status_code = status.HTTP_409_CONFLICT
    elif any(
        token in code
        for token in ("CONFLICT", "TERMINAL", "CLOSED", "CHANGED")
    ):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    message = str(error)
    prefix = f"{code}: "
    if message.startswith(prefix):
        message = message[len(prefix):]

    return status_code, {
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))

    fork_detail = _fork_error_http_detail(error)
    if fork_detail is not None:
        status_code, detail = fork_detail
        return HTTPException(status_code=status_code, detail=detail)

    if isinstance(
        error,
        (
            RetryPlanError,
            RetryConsumeError,
            RetryControlError,
            AggregateControlError,
            BranchResolutionError,
            AggregateAdmissionError,
        ),
    ):
        code = str(error.code)
        retryable = isinstance(
            error, (RetryPlanDeferred, RetryConsumeDeferred)
        ) or bool(getattr(error, "retryable", False))
        if "FOREIGN_PRINCIPAL" in code:
            status_code = status.HTTP_403_FORBIDDEN
        elif retryable or any(
            token in code
            for token in (
                "CONFLICT",
                "TERMINAL",
                "RESOLVED",
                "NOT_OPEN",
                "FORBIDDEN",
                "EXCEEDED",
                "EXECUTION_ACTIVE",
            )
        ):
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        message = str(error)
        prefix = f"{code}: "
        if message.startswith(prefix):
            message = message[len(prefix):]
        return HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        )

    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_agent_session(
    body: AgentSessionCreateRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.create_session_async(identity, body.agent_ids)
    except Exception as error:
        raise map_error(error) from error


@router.post("/sessions/{session_id}/agents")
async def add_agent_to_session(
    session_id: str,
    body: AgentJoinRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator.add_agent(session_id, body.agent_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.get("/sessions/{session_id}/messages")
async def list_agent_messages(
    session_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator.list_messages(session_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def send_agent_message(
    body: AgentMessageRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.send_message_async(
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
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    try:
        connection_snapshot = None
        if body.connection_id:
            connection_snapshot = validate_connection_affinity(
                container.connection_runtime.registry,
                body.connection_id,
                identity.user_id or "",
            )
        return await coordinator.create_task_async(
            session_id=body.session_id,
            assigned_agent_id=body.assigned_agent_id,
            task_input=body.input,
            identity=identity,
            parent_task_id=body.parent_task_id,
            connection_id=body.connection_id,
            client_id=(
                connection_snapshot.metadata.get("client_id")
                if connection_snapshot is not None
                else None
            ),
        )
    except ConnectionAffinityError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    except Exception as error:
        raise map_error(error) from error


@router.get("/tasks/{task_id}")
async def get_agent_task(
    task_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator.get_task(task_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/fork")
async def fork_agent_task(
    task_id: str,
    body: AgentTaskForkRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.fork_task(task_id, body, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/retry")
async def retry_agent_task(
    task_id: str,
    body: AgentTaskRetryRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.retry_task(task_id, body, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/branches/discard")
async def discard_agent_task_branch(
    task_id: str,
    body: AgentTaskBranchResolutionRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.discard_task_branch(task_id, body, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/branches/adopt")
async def adopt_agent_task_branch(
    task_id: str,
    body: AgentTaskBranchResolutionRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.adopt_task_branch(task_id, body, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/aggregate")
async def aggregate_agent_task_branches(
    task_id: str,
    body: AgentTaskAggregateRequest,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.aggregate_task_branches(
            task_id, body, identity
        )
    except Exception as error:
        raise map_error(error) from error


@router.get("/tasks/{task_id}/branches")
async def list_agent_task_branches(
    task_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.list_task_branches_durable(
            task_id,
            identity,
        )
    except Exception as error:
        raise map_error(error) from error


@router.get("/branches/{branch_id}")
async def get_agent_task_branch(
    branch_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return await coordinator.get_task_branch_durable(
            branch_id,
            identity,
        )
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(
    task_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        cancel_and_wait = getattr(
            coordinator,
            "cancel_task_and_wait",
            None,
        )
        if callable(cancel_and_wait):
            return await cancel_and_wait(task_id, identity)
        return coordinator.cancel_task(task_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/sessions/{session_id}/close")
async def close_agent_session(
    session_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator.close_session(session_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/execute")
async def execute_agent_task(
    task_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    """Execute a task through an opt-in application callback when configured."""
    executor = getattr(coordinator, "executor", None)
    if executor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent execution runtime is unavailable.",
        )
    try:
        return await coordinator.execute_task(task_id, identity, executor)
    except Exception as error:
        raise map_error(error) from error


@router.get("/executions/{execution_id}")
async def get_agent_execution(
    execution_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    try:
        return coordinator.get_execution(execution_id, identity)
    except Exception as error:
        raise map_error(error) from error


@router.post("/tasks/{task_id}/start")
async def start_agent_task(
    task_id: str,
    coordinator=Depends(get_coordinator),
    identity: Identity = Depends(get_current_identity),
):
    """Start an Agent task in the background for UI polling/cancellation."""
    executor = getattr(coordinator, "executor", None)
    if executor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent execution runtime is unavailable.",
        )
    try:
        return await coordinator.start_task(task_id, identity, executor)
    except Exception as error:
        raise map_error(error) from error
