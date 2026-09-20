import asyncio
import json
import uuid
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from pydantic import ValidationError

from .....infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from .....runtimes.capability.contracts.registration import ClientCapabilityRegistration
from .....runtimes.connection.protocol import RealtimeEnvelope
from ...authentication.dependency import get_current_identity, get_websocket_identity
from .....domain.schemas.identity import Identity
from ...dependencies import get_container
from .....application.container import ApplicationContainer

router = APIRouter(prefix="/v1/events", tags=["Events"])
logger = structlog.get_logger(__name__)


async def _send_realtime(websocket: WebSocket, envelope: RealtimeEnvelope):
    await websocket.send_json(envelope.model_dump(mode="json"))


async def _ensure_connection(websocket, identity, connection_runtime, envelope):
    if connection_runtime is None:
        raise RuntimeError("Connection runtime is unavailable.")

    connection_id = envelope.connection_id or envelope.payload.get("connection_id")
    if not connection_id:
        raise ValueError("connection.register requires connection_id")

    session_id = (
        envelope.session_id
        or envelope.payload.get("session_id")
        or getattr(identity, "session_id", None)
        or f"ws-{uuid.uuid4().hex}"
    )
    snapshot = connection_runtime.registry.register(
        session_id=session_id,
        user_id=identity.user_id,
        socket=websocket,
        metadata={
            "client_id": envelope.payload.get("client_id", ""),
            "transport": "websocket",
        },
        connection_id=connection_id,
    )
    snapshot = connection_runtime.registry.activate(connection_id)
    await _send_realtime(
        websocket,
        RealtimeEnvelope(
            type="connection.registered",
            message_id=f"registered-{uuid.uuid4().hex}",
            session_id=snapshot.session_id,
            connection_id=snapshot.connection_id,
            payload={"state": snapshot.state.value},
        ),
    )
    return connection_id


async def _resume_execution(websocket, identity, container, connection_id, envelope):
    payload = envelope.payload
    execution_id = str(payload.get("execution_id") or "")
    checkpoint_id = str(payload.get("checkpoint_id") or "")
    if not execution_id or not checkpoint_id:
        raise ValueError("execution.resume requires execution_id and checkpoint_id")
    if envelope.connection_id != connection_id:
        raise ValueError("execution.resume connection_id does not match active connection")

    snapshot = container.connection_runtime.registry.get(connection_id)
    if not snapshot.is_usable or snapshot.user_id != identity.user_id:
        raise PermissionError("Resume connection is not active for this principal")
    service = container.continuation_service
    checkpoint = await service.ensure_loaded(execution_id)
    if checkpoint is None or checkpoint.checkpoint_id != checkpoint_id:
        raise ValueError("STALE_CONTINUATION_CHECKPOINT")

    implementations = container.capability_runtime.catalog.list_implementations_for_connection(
        connection_id
    )
    if not any(
        item.capability_id == checkpoint.pending_capability_id
        and item.state.value == "ENABLED"
        for item in implementations
    ):
        raise ValueError("PENDING_CAPABILITY_NOT_READY")

    # Rehydrate and validate the durable execution before advancing the
    # continuation transaction.  A missing/corrupt durable record must not
    # leave the checkpoint marked RUNNING.
    context = await container.agent_durable_store.resume_execution(
        execution_id,
        identity=identity,
    )
    if context is None:
        raise LookupError(f"Unknown agent execution: {execution_id}")
    context.agent = container.agent_registry.get(context.agent_id)
    if context.agent is None:
        raise LookupError(f"Agent '{context.agent_id}' is not registered")

    # Branch creation does not advance the immutable checkpoint.  The durable
    # WAITING -> RUNNING claim must win before confirm_merge() and before ACK.
    branch = await service.reconnect(
        execution_id=execution_id,
        connection_id=connection_id,
        user_id=identity.user_id,
        metadata={"client_id": snapshot.metadata.get("client_id")},
    )
    durable_revision = await container.agent_runtime.claim_resume(context)
    merged = await service.confirm_merge(
        execution_id=execution_id,
        branch_id=branch.branch_id,
        user_id=identity.user_id,
    )
    context.connection_id = connection_id
    context.metadata["client_id"] = snapshot.metadata.get("client_id")

    await _send_realtime(
        websocket,
        RealtimeEnvelope(
            type="execution.resume.accepted",
            message_id=f"resume-{uuid.uuid4().hex}",
            connection_id=connection_id,
            execution_id=execution_id,
            payload={
                "execution_id": execution_id,
                "checkpoint_id": merged.checkpoint_id,
                "state": "RUNNING",
            },
        ),
    )
    task = asyncio.create_task(
        container.agent_runtime.execute(
            context,
            durable_revision=durable_revision,
        ),
        name=f"resume:{execution_id}",
    )
    task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    identity: Identity = Depends(get_websocket_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """Endpoint cho phép client kết nối để nhận các sự kiện hệ thống theo thời gian thực."""
    ws_manager: WebSocketConnectionManager = container.eventing_manager.ws_manager
    connection_runtime = getattr(container, "connection_runtime", None)
    await ws_manager.connect(websocket)
    logger.info("WebSocket client connected", client_host=websocket.client.host, user_id=identity.user_id)
    try:
        active_connection_id = None
        # Vòng lặp để nhận tin nhắn từ client (ví dụ: yêu cầu subscribe)
        while True:
            data = await websocket.receive_text()
            if data:
                logger.info("data tu ws", data=data)
            try:
                message = json.loads(data)
                action = message.get("action")
                event_name = message.get("event_name")

                if action == "subscribe" and event_name:
                    await ws_manager.subscribe(websocket, event_name)
                    await websocket.send_text(json.dumps({"status": "success", "message": f"Subscribed to {event_name}"}))
                elif action == "unsubscribe" and event_name:
                    await ws_manager.unsubscribe(websocket, event_name)
                    await websocket.send_text(json.dumps({"status": "success", "message": f"Unsubscribed from {event_name}"}))
                else:
                    envelope = RealtimeEnvelope.model_validate(message)
                    if envelope.type == "connection.register":
                        active_connection_id = await _ensure_connection(
                            websocket,
                            identity,
                            connection_runtime,
                            envelope,
                        )
                    elif connection_runtime is None or active_connection_id is None:
                        raise ValueError("Client must send connection.register first")
                    elif envelope.type == "capability.register":
                        registration_service = connection_runtime.registration_service
                        if registration_service is None:
                            raise RuntimeError("Capability registration is unavailable.")
                        request = ClientCapabilityRegistration.model_validate(
                            envelope.payload
                        )
                        if request.connection_id != active_connection_id:
                            raise ValueError("Capability connection_id does not match active connection")
                        registered = registration_service.register(request)
                        await _send_realtime(
                            websocket,
                            RealtimeEnvelope(
                                type="capability.registered",
                                message_id=f"capabilities-{uuid.uuid4().hex}",
                                connection_id=active_connection_id,
                                payload={
                                    "capabilities": [
                                        item.model_dump(mode="json")
                                        for item in registered
                                    ]
                                },
                            ),
                        )
                    elif envelope.type == "execution.resume":
                        await _resume_execution(
                            websocket,
                            identity,
                            container,
                            active_connection_id,
                            envelope,
                        )
                    else:
                        await connection_runtime.handle_realtime_message(
                            active_connection_id,
                            envelope,
                        )
            except (ValidationError, ValueError, RuntimeError, LookupError, PermissionError) as error:
                logger.exception("Validation/Runtime error during WebSocket message processing", error=str(error))
                await websocket.send_text(json.dumps({"status": "error", "message": str(error)}))
            except json.JSONDecodeError:
                logger.error("Invalid JSON received from WebSocket client")
                await websocket.send_text(json.dumps({"status": "error", "message": "Invalid JSON format"}))

    except WebSocketDisconnect:
        if active_connection_id and connection_runtime is not None:
            await connection_runtime.disconnect_connection(active_connection_id)
        ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected", client_host=websocket.client.host, user_id=identity.user_id)
