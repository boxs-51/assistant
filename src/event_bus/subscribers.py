import structlog

from ..schemas.event import BaseEvent
from .ws_manager import WebSocketConnectionManager
from ..context.manager import ContextEngine
from ..storage.repositories.sessions import SessionRepository
from .registry import EventRegistry

logger = structlog.get_logger(__name__)

# Khởi tạo một instance registry toàn cục để các decorator có thể sử dụng
registry = EventRegistry()

@registry.subscribe("system.event.failed")
async def handle_failed_event_dlq(event: BaseEvent):
    """
    Dead Letter Queue (DLQ) handler.
    Lắng nghe các sự kiện thất bại và ghi log chi tiết để phân tích sau.
    """
    payload = event.payload
    logger.error(
        "☠️ DEAD LETTER QUEUE: Event processing failed permanently",
        failed_event_name=payload.get("failed_event", {}).get("event_name"),
        failed_handler=payload.get("failed_handler"),
        error_details=payload, # Ghi lại toàn bộ payload của sự kiện lỗi
    )

@registry.subscribe("user.created")
async def handle_user_created(event: BaseEvent, session_repo: SessionRepository):
    """
    Một ví dụ về hàm xử lý sự kiện (event handler).
    Hàm này sẽ được gọi bất đồng bộ khi có sự kiện 'user.created' được phát ra.
    Nó tự động nhận `session_repo` nhờ Dependency Injection.
    """
    logger.info("🎉 New user has registered!", user_id=event.payload.get("user_id"), repo_instance=session_repo.__class__.__name__)

@registry.subscribe("chat.session.started")
async def handle_chat_session_started(event: BaseEvent):
    """
    Hàm xử lý khi một phiên hội thoại mới bắt đầu.
    Trong tương lai, có thể dùng để khởi tạo các tài nguyên cho Agent,
    gửi thông báo real-time cho client, v.v.
    """
    logger.info("🚀 New chat session started", session_id=event.payload.get("session_id"), user_id=event.payload.get("user_id"))

@registry.subscribe("tool.execution.started")
async def handle_tool_execution_started(event: BaseEvent):
    """Lắng nghe sự kiện khi một tool bắt đầu được thực thi."""
    logger.info(
        "🛠️ Tool execution started",
        tool_name=event.payload.get("tool_name"),
        session_id=event.payload.get("session_id"),
    )

@registry.subscribe("tool.execution.completed")
async def handle_tool_execution_completed(event: BaseEvent):
    """Lắng nghe sự kiện khi một tool thực thi xong."""
    logger.info(
        "✅ Tool execution completed",
        tool_name=event.payload.get("tool_name"),
        session_id=event.payload.get("session_id"),
    )

@registry.subscribe_to_all()
async def broadcast_to_websockets(event: BaseEvent, ws_manager: WebSocketConnectionManager):
    """
    Handler này lắng nghe TẤT CẢ các sự kiện và chuyển tiếp đến WebSocket clients.
    Nó tự động nhận `ws_manager` nhờ Dependency Injection.
    """
    logger.debug("Sending event to subscribed WebSocket clients", event_name=event.event_name)
    await ws_manager.send_to_subscribers(event.event_name, event.model_dump())
@registry.subscribe("session.summary.needed")
async def handle_session_summary_needed(event: BaseEvent, context_engine: ContextEngine):
    """
    Lắng nghe sự kiện cần tóm tắt session và ủy quyền cho ContextEngine.
    Nó tự động nhận `context_engine` nhờ Dependency Injection.
    """
    session_id = event.payload.get("session_id")
    if session_id:
        logger.info("Triggering session summary", session_id=session_id)
        # Giả sử ContextEngine có phương thức này
        # await context_engine.summarize_session(session_id)