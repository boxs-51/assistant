import structlog

from ...domain.schemas.event import BaseEvent
from .ws_manager import WebSocketConnectionManager
from ...context.manager import ContextEngine
from ..storage.repositories.sessions import SessionRepository

logger = structlog.get_logger(__name__)

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

async def  handle_failed_event(event: BaseEvent):
    pass

async def handle_user_created(event: BaseEvent, session_repo: SessionRepository):
    """
    Một ví dụ về hàm xử lý sự kiện (event handler).
    Hàm này sẽ được gọi bất đồng bộ khi có sự kiện 'user.created' được phát ra.
    Nó tự động nhận `session_repo` nhờ Dependency Injection.
    """
    logger.info("🎉 New user has registered!", user_id=event.payload.get("user_id"), repo_instance=session_repo.__class__.__name__)

async def handle_user_welcome_email(event: BaseEvent):
    pass

async def handle_chat_session_started(event: BaseEvent):
    """
    Hàm xử lý khi một phiên hội thoại mới bắt đầu.
    Trong tương lai, có thể dùng để khởi tạo các tài nguyên cho Agent,
    gửi thông báo real-time cho client, v.v.
    """
    logger.info("🚀 New chat session started", session_id=event.payload.get("session_id"), user_id=event.payload.get("user_id"))

async def handle_tool_execution_started(event: BaseEvent):
    """Lắng nghe sự kiện khi một tool bắt đầu được thực thi."""
    logger.info(
        "🛠️ Tool execution started",
        tool_name=event.payload.get("tool_name"),
        session_id=event.payload.get("session_id"),
    )

async def handle_tool_execution_completed(event: BaseEvent):
    """Lắng nghe sự kiện khi một tool thực thi xong."""
    logger.info(
        "✅ Tool execution completed",
        tool_name=event.payload.get("tool_name"),
        session_id=event.payload.get("session_id"),
    )

async def broadcast_to_websockets(event: BaseEvent, ws_manager: WebSocketConnectionManager = None):
    """
    Handler này lắng nghe TẤT CẢ các sự kiện và chuyển tiếp đến WebSocket clients.
    Nó tự động nhận `ws_manager` nhờ Dependency Injection.
    """
    if not ws_manager:
        # Fallback to fetching manager instance or log warning
        ...
    logger.debug("Sending event to subscribed WebSocket clients", event_name=event.event_name)
    await ws_manager.send_to_subscribers(event.event_name, event.model_dump())
async def handle_session_summary_needed(event: BaseEvent, context_engine: ContextEngine):
    """
    Lắng nghe sự kiện cần tóm tắt session và ủy quyền cho ContextEngine.
    Nó tự động nhận `context_engine` nhờ Dependency Injection.
    """
    session_id = event.payload.get("session_id")
    if session_id:
        logger.info("Triggering session summary", session_id=session_id)

async def  handle_unknown_event(event: BaseEvent):
    pass

def register_subscribers(registry):
    registry.register("system.event.failed", handle_failed_event_dlq)
    registry.register("system.event.failed", handle_failed_event)
    registry.register("user.created", handle_user_created)
    registry.register("user.created", handle_user_welcome_email)
    registry.register("chat.session.started", handle_chat_session_started)
    registry.register("tool.execution.started", handle_tool_execution_started)
    registry.register("tool.execution.completed", handle_tool_execution_completed)
    registry.register("unknown.event", handle_unknown_event)
    #registry.register_for_all(broadcast_to_websockets)
    registry.register("session.summary.needed", handle_session_summary_needed)
        # Giả sử ContextEngine có phương thức này
        # await context_engine.summarize_session(session_id)