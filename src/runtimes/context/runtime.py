# src/runtimes/context/runtime.py

from typing import Any, List
import structlog

from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from ...domain.schemas.event import BaseEvent
from ...context.manager import ContextEngine
from ...domain.schemas.identity import Identity

logger = structlog.get_logger(__name__)


class ContextRuntime(BaseRuntime):
    def __init__(self):
        manifest = RuntimeManifest(
            id="context_runtime",
            name="ContextRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.context_engine = None
        self._subscribed = False

    async def initialize(self, context: RuntimeContext) -> None:
        await super().initialize(context)
        # Hỗ trợ cả Dict hoặc Dependency Container Object
        self.event_bus = context.event_bus
        if not self.event_bus:
            raise ValueError("ContextRuntime requires 'event_bus' in initialization context.")

        if context.storage is None or context.uow_factory is None:
            raise ValueError("ContextRuntime requires storage and uow_factory.")
        
        self.context_engine = ContextEngine(context.storage, context.uow_factory)

        if not self._subscribed:
            self.event_bus.subscribe("context.command.build", self._handle_build_context)
            self._subscribed = True

        self._is_initialized = True
        logger.info("ContextRuntime initialized successfully")

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False
        if self.event_bus is not None and self._subscribed:
            self.event_bus.unsubscribe("context.command.build", self._handle_build_context)
            self._subscribed = False

    async def _handle_build_context(self, event: BaseEvent):
        """Xử lý Command yêu cầu dựng Prompt/Context từ Session/History và Request mới."""
        session_id = event.session_id or event.payload.get("session_id")
        identity_data = event.payload.get("identity")
        try:
            if session_id and identity_data:
                identity = identity_data if isinstance(identity_data, Identity) else Identity.model_validate(identity_data)
                context = await self.context_engine.load_context(session_id, identity)
                payload = {**event.payload, "context": context.model_dump()}
                request_body = dict(event.payload.get("request_body", {}))
                request_body["messages"] = [
                    message.model_dump(exclude_none=True)
                    for message in context.session.messages
                ]
                payload["request_body"] = request_body
            else:
                payload = event.payload
            await self.event_bus.publish(BaseEvent(
                event_name="context.event.built",
                session_id=session_id,
                payload=payload
            ))
        except Exception as exc:
            await self.event_bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=session_id,
                payload={"error": str(exc), "status_code": 404 if isinstance(exc, ValueError) else 500},
            ))