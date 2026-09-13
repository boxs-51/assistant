# src/runtime/runtimes/connection/runtime.py
import asyncio
import structlog
from typing import Dict, Any, Optional

from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from .registry import ConnectionRegistry
from .protocol import RealtimeEnvelope
from .realtime import RealtimeMultiplexer
from ..capability.registration import ClientCapabilityRegistrationService
from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent

logger = structlog.get_logger(__name__)


class ConnectionRuntime(BaseRuntime):
    """Runtime quản lý toàn bộ kết nối active (WebSocket, SSE, Transport Sessions)."""

    def __init__(
        self,
        registration_service: ClientCapabilityRegistrationService | None = None,
    ):
        manifest = RuntimeManifest(
            id="connection_runtime",
            name="ConnectionRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.registry = ConnectionRegistry()
        self.realtime = RealtimeMultiplexer(self.registry)
        self.registration_service = registration_service
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._subscribed = False

    async def initialize(self, context: RuntimeContext) -> None:
        await super().initialize(context)
        # Subscribe Command gửi tin nhắn tới Client
        self.event_bus = context.event_bus
        if not self._subscribed:
            self.event_bus.subscribe("connection.command.send", self._handle_send_command)
            self._subscribed = True
        self._is_initialized = True
        logger.info("Connection Runtime initialized.")

    async def start(self) -> None:
        self._is_running = True
        self._heartbeat_task = asyncio.create_task(self._monitor_heartbeats())
        logger.info("Connection Runtime started.")

    async def stop(self) -> None:
        self._is_running = False
        if self.event_bus is not None and self._subscribed:
            self.event_bus.unsubscribe("connection.command.send", self._handle_send_command)
            self._subscribed = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        logger.info("Connection Runtime stopped.")

    async def _handle_send_command(self, event: BaseEvent):
        """Lắng nghe Command gửi tin nhắn xuống client qua WebSocket."""
        success = await self.send_to_client(event.session_id, event.payload)
        if not success:
            logger.warning("Failed to deliver message: client socket not found", session_id=event.session_id)

    async def send_to_client(self, session_id: str, data: Dict[str, Any]) -> bool:
        socket = self.registry.get_socket(session_id)
        if socket:
            await socket.send_json(data)
            return True
        return False

    async def send_realtime(
        self,
        envelope: RealtimeEnvelope,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send one correlated realtime invocation and await its terminal result."""
        return await self.realtime.invoke(envelope, timeout=timeout)

    async def handle_realtime_message(
        self,
        connection_id: str,
        data: Dict[str, Any],
    ) -> bool:
        """Validate and dispatch one inbound realtime envelope."""
        envelope = RealtimeEnvelope.model_validate(data)
        return await self.realtime.handle_inbound(connection_id, envelope)

    async def disconnect_connection(self, connection_id: str) -> int:
        """Close lifecycle state and fail pending invocations deterministically."""
        snapshot = self.registry.disconnect(connection_id)
        del snapshot
        failed = await self.realtime.disconnect(connection_id)
        if self.registration_service is not None:
            self.registration_service.unregister_connection(connection_id)
        return failed

    def evict_stale_connections(self):
        """Synchronously evict stale transport connections.

        Remote invocation is intentionally not implemented here.
        """
        stale = self.registry.evict_stale()
        if self.registration_service is not None:
            for connection in stale:
                self.registration_service.unregister_connection(
                    connection.connection_id
                )
        return stale

    async def _monitor_heartbeats(self):
        while self._is_running:
            await asyncio.sleep(30)
            stale = self.evict_stale_connections()
            if stale:
                logger.info(
                    "Stale connections evicted",
                    count=len(stale),
                    connection_ids=[
                        connection.connection_id
                        for connection in stale
                    ],
                )