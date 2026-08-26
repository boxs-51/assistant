# src/runtime/runtimes/capability/runtime.py
import structlog
from typing import Any, Dict, Optional

from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from .registry import CapabilityRegistry
from .drivers.base import BaseCapabilityDriver
from ...domain.schemas.identity import Identity
from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent
from ...application.policy.authorization import AuthorizationService

logger = structlog.get_logger(__name__)


class CapabilityRuntime(BaseRuntime):
    """Runtime quản lý toàn bộ vòng đời và thực thi các Capability/Tools."""

    def __init__(self):
        manifest = RuntimeManifest(
            id="capability_runtime",
            name="CapabilityRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.registry = CapabilityRegistry()
        self.authorization = AuthorizationService()

    async def initialize(self, context: RuntimeContext) -> None:
        # Lắng nghe Command yêu cầu thực thi Tool từ EventBus
        self.event_bus = context.event_bus
        self.event_bus.subscribe("capability.command.execute", self._handle_execute_command)
        self._is_initialized = True
        logger.info("Capability Runtime initialized.")

    async def start(self) -> None:
        self._is_running = True
        logger.info("Capability Runtime started.")

    async def stop(self) -> None:
        self._is_running = False
        logger.info("Capability Runtime stopped.")

    async def _handle_execute_command(self, event: BaseEvent):
        """Handler nhận Command yêu cầu chạy Tool và phát Event thông báo kết quả."""
        tool_name = event.payload.get("tool_name")
        arguments = event.payload.get("arguments", {})
        identity = event.payload.get("identity")

        try:
            result = await self.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                identity=identity,
                context=event.payload.get("context")
            )
            
            # Phát Event thông báo Tool đã chạy xong
            await self.event_bus.publish(BaseEvent(
                event_name="capability.event.executed",
                session_id=event.session_id,
                payload={"tool_name": tool_name, "result": result}
            ))
        except Exception as e:
            logger.error("Failed to process capability execution command", tool_name=tool_name, error=str(e))
            await self.event_bus.publish(BaseEvent(
                event_name="capability.event.failed",
                session_id=event.session_id,
                payload={"tool_name": tool_name, "error": str(e)}
            ))

    def register_tool(self, driver: BaseCapabilityDriver):
        self.registry.register_capability(driver)

    async def get_available_tools(self, identity: Identity):
        return [
            {
                "type": "function",
                "function": {
                    "name": driver.definition.name,
                    "description": driver.definition.description,
                    "parameters": driver.definition.parameters,
                },
            }
            for driver in self.registry.get_all_drivers()
            if self.authorization.is_allowed(identity, driver)
        ]

    async def execute_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any], 
        identity: Identity,
        context: Optional[Dict[str, Any]] = None
    ) -> Any:
        driver = self.registry.get_driver(tool_name)
        if not driver:
            raise ValueError(f"Capability/Tool '{tool_name}' not found.")
        if not self.authorization.is_allowed(identity, driver):
            raise PermissionError(f"Capability/Tool '{tool_name}' is not authorized.")

        exec_context = context or {}
        exec_context["identity"] = identity

        logger.info("Executing capability", tool_name=tool_name, arguments=arguments)
        return await driver.execute(arguments, exec_context)