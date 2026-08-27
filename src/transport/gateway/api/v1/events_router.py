import json
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from .....infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from ...authentication.dependency import get_current_identity
from .....domain.schemas.identity import Identity
from ...dependencies import get_container
from .....application.container import ApplicationContainer

router = APIRouter(prefix="/v1/events", tags=["Events"])
logger = structlog.get_logger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """Endpoint cho phép client kết nối để nhận các sự kiện hệ thống theo thời gian thực."""
    ws_manager: WebSocketConnectionManager = container.eventing_manager.ws_manager
    await ws_manager.connect(websocket)
    logger.info("WebSocket client connected", client_host=websocket.client.host, user_id=identity.user_id)
    try:
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
                    await websocket.send_text(json.dumps({"status": "error", "message": "Invalid action or event_name"}))
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"status": "error", "message": "Invalid JSON format"}))

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected", client_host=websocket.client.host, user_id=identity.user_id)