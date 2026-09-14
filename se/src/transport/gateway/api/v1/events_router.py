import json
import uuid
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from pydantic import ValidationError

from .....infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from .....runtimes.capability.contracts.registration import ClientCapabilityRegistration
from .....runtimes.connection.protocol import RealtimeEnvelope
from ...authentication.dependency import get_current_identity
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


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    identity: Identity = Depends(get_current_identity),
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
                                type="connection.registered",
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
                    else:
                        await connection_runtime.handle_realtime_message(
                            active_connection_id,
                            envelope,
                        )
            except (ValidationError, ValueError, RuntimeError) as error:
                await websocket.send_text(json.dumps({"status": "error", "message": str(error)}))
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"status": "error", "message": "Invalid JSON format"}))

    except WebSocketDisconnect:
        if active_connection_id and connection_runtime is not None:
            await connection_runtime.disconnect_connection(active_connection_id)
        ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected", client_host=websocket.client.host, user_id=identity.user_id)