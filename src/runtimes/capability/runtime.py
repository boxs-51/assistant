# src/runtime/runtimes/capability/runtime.py
import asyncio
import structlog
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from ...kernel.base import BaseRuntime, HealthStatus, RuntimeContext, RuntimeManifest
from .registry import CapabilityRegistry, CapabilityState
from .drivers.base import BaseCapabilityDriver
from .contracts.context import CapabilityExecutionContext
from .contracts.error import CapabilityError
from .contracts.result import CapabilityResult
from .contracts.definition import CapabilityDefinition
from .drivers.mcp_driver import McpCapabilityDriver
from ...domain.schemas.identity import Identity
from ...domain.schemas.event import BaseEvent
from ...application.policy.authorization import AuthorizationService

logger = structlog.get_logger(__name__)


class CapabilityRuntime(BaseRuntime):
    """Runtime quản lý toàn bộ vòng đời và thực thi các Capability/Tools."""

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        authorization: AuthorizationService | None = None,
    ):
        manifest = RuntimeManifest(
            id="capability_runtime",
            name="CapabilityRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.registry = registry if registry is not None else CapabilityRegistry()
        self.authorization = authorization if authorization is not None else AuthorizationService()
        
        self._subscribed = False
        self.mcp_manager = None

    async def initialize(self, context: RuntimeContext) -> None:
        await super().initialize(context)
        self.event_bus = context.event_bus
        self.mcp_manager = getattr(context.container, "mcp_manager", None)
        if not self._subscribed:
            self.event_bus.subscribe(
                "capability.command.execute",
                self._handle_execute_command,
            )
            self._subscribed = True
        self._is_initialized = True
        logger.info("Capability Runtime initialized.")

    async def start(self) -> None:
        if self.state.name == "DISPOSED":
            raise RuntimeError("Cannot start a disposed CapabilityRuntime.")
        self._is_running = True
        logger.info("Capability Runtime started.")

    async def stop(self) -> None:
        if self.event_bus is not None and self._subscribed:
            self.event_bus.unsubscribe(
                "capability.command.execute",
                self._handle_execute_command,
            )
            self._subscribed = False
        self._is_running = False
        logger.info("Capability Runtime stopped.")

    async def _handle_execute_command(self, event: BaseEvent):
        """Handler nhận Command yêu cầu chạy Tool và phát Event thông báo kết quả."""
        capability_id = (
            event.payload.get("capability_id")
            or event.payload.get("tool_name")
        )
        arguments = event.payload.get("arguments", {})
        identity = event.payload.get("identity")

        try:
            result = await self.execute_capability(
                capability_id=capability_id,
                arguments=arguments,
                identity=identity,
                execution_id=event.payload.get("execution_id"),
                request_id=event.payload.get("request_id"),
                session_id=event.session_id,
                workflow_id=event.payload.get("workflow_id"),
            )

            await self.event_bus.publish(BaseEvent(
                event_name="capability.event.executed",
                session_id=event.session_id,
                payload={
                    "capability_id": capability_id,
                    "tool_name": capability_id,
                    "result": result.output,
                    "capability_result": result.model_dump(mode="json"),
                    }
            ))
        except Exception as e:
            logger.error(
                "Failed to process capability execution command",
                capability_id=capability_id,
                error=str(e),
            )
            await self.event_bus.publish(BaseEvent(
                event_name="capability.event.failed",
                session_id=event.session_id,
                payload={
                    "capability_id": capability_id,
                    "tool_name": capability_id,
                    "error": str(e),
                    "capability_error": (
                        e.model_dump() if isinstance(e, CapabilityError) else
                        CapabilityError.from_exception(
                            e,
                            capability_id=capability_id,
                        ).model_dump()
                    ),
                },
            ))

    def register_tool(self, driver: BaseCapabilityDriver):
        """Backward-compatible alias for capability registration."""
        self.registry.register_capability(driver)

    def register_capability(self, driver: BaseCapabilityDriver):
        return self.registry.register_capability(driver)

    async def get_available_capabilities(self, identity: Identity):
        return [
            driver.definition
            for driver in self.registry.get_all_drivers()
            if self.authorization.is_allowed(identity, driver)
        ]

    async def check_health(self):
        failed = False
        degraded = False
        for record in self.registry.list_records():
            driver = record.driver
            if driver is None:
                continue
            if record.state in {
                CapabilityState.DISABLED,
                CapabilityState.REMOVED,
            }:
                continue
            try:
                if not await driver.check_health():
                    if record.definition.source == "MCP":
                        degraded = True
                        self.registry.set_state(
                            record.id,
                            CapabilityState.UNAVAILABLE,
                        )
                    else:
                        failed = True
                        return self._failed_health_status()
                elif record.state in {
                    CapabilityState.UNAVAILABLE,
                    CapabilityState.DEGRADED,
                }:
                    self.registry.set_state(
                        record.id,
                        CapabilityState.ENABLED,
                    )
            except Exception:
                if record.definition.source == "MCP":
                    degraded = True
                    self.registry.set_state(
                        record.id,
                        CapabilityState.UNAVAILABLE,
                    )
                else:
                    failed = True
                    return self._failed_health_status()
        if failed:
            return self._failed_health_status()
        if degraded:
            return HealthStatus.DEGRADED
        return self._healthy_health_status()

    @staticmethod
    def _healthy_health_status():
        return HealthStatus.HEALTHY

    @staticmethod
    def _failed_health_status():
        return HealthStatus.FAILED

    async def discover_mcp_capabilities(self, server_name: str) -> int:
        """Discover remote MCP tools and register them as executable capabilities."""
        if self.mcp_manager is None:
            raise RuntimeError("MCP infrastructure is not available.")

        descriptors = await self.mcp_manager.get_tools_from_cache(server_name)
        for descriptor in descriptors:
            definition = CapabilityDefinition(
                id=f"{descriptor.server_name}:{descriptor.name}",
                version="1.0",
                name=f"{descriptor.server_name}:{descriptor.name}",
                description=descriptor.description,
                input_schema=descriptor.input_schema,
                source="MCP",
                execution_kind="MCP",
                metadata={
                    "mcp_server": descriptor.server_name,
                    "mcp_tool_name": descriptor.name,
                },
            )
            self.register_capability(
                McpCapabilityDriver(definition, self.mcp_manager)
            )
        return len(descriptors)

    async def execute_capability(
        self, 
        capability_id: str,
        arguments: Mapping[str, Any],
        identity: Identity,
        *,
        execution_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        workflow_id: str | None = None,
        timeout_seconds: float | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CapabilityResult:
        started = time.perf_counter()
        driver = self.registry.get_driver(capability_id)
        if not driver:
            raise ValueError(f"Capability '{capability_id}' not found or unavailable.")
        if not self.authorization.is_allowed(identity, driver):
            raise PermissionError(f"Capability '{capability_id}' is not authorized.")

        context = CapabilityExecutionContext.create(
            identity=identity,
            execution_id=execution_id,
            request_id=request_id,
            session_id=session_id,
            workflow_id=workflow_id,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )

        logger.info(
            "Executing capability",
            capability_id=capability_id,
            invocation_id=context.invocation_id,
        )
        started_at = datetime.now(timezone.utc)
        try:
            if context.cancelled:
                raise asyncio.CancelledError()
            driver_task = driver.execute(context, dict(arguments))
            if context.remaining_seconds is not None:
                raw_output = await asyncio.wait_for(
                    driver_task,
                    timeout=context.remaining_seconds,
                )
            else:
                raw_output = await driver_task
        except asyncio.TimeoutError as exc:
            raise CapabilityError(
                code="CAPABILITY_TIMEOUT",
                message=f"Capability '{capability_id}' timed out.",
                category="TIMEOUT",
                retryable=True,
                safe_for_client=True,
                cause_type=type(exc).__name__,
                capability_id=capability_id,
                invocation_id=context.invocation_id,
            ) from exc
        except asyncio.CancelledError as exc:
            raise CapabilityError(
                code="CAPABILITY_CANCELLED",
                message=f"Capability '{capability_id}' was cancelled.",
                category="CANCELLED",
                retryable=False,
                safe_for_client=True,
                cause_type=type(exc).__name__,
                capability_id=capability_id,
                invocation_id=context.invocation_id,
            ) from exc
        except CapabilityError:
            raise
        except Exception as exc:
            raise CapabilityError.from_exception(
                exc,
                capability_id=capability_id,
                invocation_id=context.invocation_id,
            ) from exc

        completed_at = time.perf_counter()
        completed_at_utc = datetime.now(timezone.utc)
        return CapabilityResult(
            invocation_id=context.invocation_id,
            capability_id=capability_id,
            output=raw_output,
            output_type=type(raw_output).__name__,
            started_at=started_at,
            completed_at=completed_at_utc,
            duration_ms=(completed_at - started) * 1000,
            metadata={"attempt": context.attempt},
        )

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        identity: Identity,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Compatibility wrapper for the legacy Tool API."""
        context = context or {}
        result = await self.execute_capability(
            capability_id=tool_name,
            arguments=arguments,
            identity=identity,
            execution_id=context.get("execution_id"),
            request_id=context.get("request_id"),
            session_id=context.get("session_id"),
            workflow_id=context.get("workflow_id"),
            metadata={
                key: value
                for key, value in context.items()
                if key not in {
                    "execution_id",
                    "request_id",
                    "session_id",
                    "workflow_id",
                    "identity",
                }
            },
        )
        return result.output

    async def get_available_tools(self, identity: Identity):
        """Compatibility view; provider adapters can migrate later."""
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                },
            }
            for definition in await self.get_available_capabilities(identity)
        ]