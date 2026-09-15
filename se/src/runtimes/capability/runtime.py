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
from .drivers.remote_client_driver import RemoteClientDriver
from .catalog import CapabilityCatalog
from .contracts.implementation import CapabilityExecutionLocation
from .policy import CapabilityRequestContext, CapabilityRoutingPolicy
from ...runtimes.connection.registry import ConnectionRegistry
from ...runtimes.connection.realtime import RealtimeMultiplexer
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
        catalog: CapabilityCatalog | None = None,
        routing_policy: CapabilityRoutingPolicy | None = None,
        connection_registry: ConnectionRegistry | None = None,
        realtime: RealtimeMultiplexer | None = None,
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
        self.catalog = catalog
        self.routing_policy = routing_policy
        self.connection_registry = connection_registry
        self.realtime = realtime
        
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
        connection_id: str | None = None,
        workflow_id: str | None = None,
        timeout_seconds: float | None = None,
        cancellation_event: asyncio.Event | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CapabilityResult:
        started = time.perf_counter()
        request_metadata = dict(metadata or {})
        metadata_connection_id = request_metadata.get("connection_id")
        if (
            connection_id is not None
            and metadata_connection_id is not None
            and connection_id != metadata_connection_id
        ):
            raise ValueError(
                "Explicit connection_id does not match metadata['connection_id']."
            )
        driver = self._resolve_execution_driver(
            capability_id,
            identity,
            request_metadata,
            connection_id=connection_id,
        )
        if not driver:
            raise ValueError(f"Capability '{capability_id}' not found or unavailable.")
        if not self.authorization.is_allowed(identity, driver):
            raise PermissionError(f"Capability '{capability_id}' is not authorized.")

        context = CapabilityExecutionContext.create(
            identity=identity,
            execution_id=execution_id,
            request_id=request_id,
            session_id=session_id,
            connection_id=(
                connection_id
                or metadata_connection_id
                or (
                    self.connection_registry.resolve_connection_id(
                        getattr(identity, "session_id", "") or ""
                    )
                    if self.connection_registry is not None
                    else None
                )
            ),
            workflow_id=workflow_id,
            timeout_seconds=timeout_seconds,
            cancellation_event=cancellation_event,
            metadata=request_metadata,
        )

        logger.info(
            "Executing capability",
            capability_id=capability_id,
            invocation_id=context.invocation_id,
        )
        started_at = datetime.now(timezone.utc)
        driver_task = asyncio.create_task(
            driver.execute(context, dict(arguments)),
            name=f"capability:{capability_id}:{context.invocation_id}",
        )
        cancellation_task = asyncio.create_task(
            context.cancellation_event.wait(),
            name=f"capability-cancel:{context.invocation_id}",
        )
        try:
            if context.cancelled:
                raise asyncio.CancelledError()
            done, pending = await asyncio.wait(
                {driver_task, cancellation_task},
                timeout=context.remaining_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if driver_task in done:
                raw_output = await driver_task
            elif cancellation_task in done:
                driver_task.cancel()
                await asyncio.gather(driver_task, return_exceptions=True)
                raise asyncio.CancelledError()
            else:
                driver_task.cancel()
                await asyncio.gather(driver_task, return_exceptions=True)
                raise asyncio.TimeoutError()
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
            original_code = getattr(exc, "code", None)
            details: Dict[str, Any] = {}
            if original_code:
                details["original_error_code"] = str(original_code)
            raise CapabilityError(
                code="CAPABILITY_EXECUTION_FAILED",
                message=str(exc),
                category="EXECUTION",
                retryable=bool(getattr(exc, "retryable", False)),
                safe_for_client=False,
                cause_type=type(exc).__name__,
                capability_id=capability_id,
                invocation_id=context.invocation_id,
                details=details,
            ) from exc
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

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

    def _resolve_execution_driver(
        self,
        capability_id: str,
        identity: Identity,
        metadata: Dict[str, Any],
        *,
        connection_id: str | None = None,
    ) -> BaseCapabilityDriver | None:
        legacy_driver = self.registry.get_driver(capability_id)
        if self.catalog is None or not self.catalog.contains_definition(capability_id):
            return legacy_driver
        if self.routing_policy is None:
            raise RuntimeError(
                "Capability catalog is configured without routing policy."
            )

        effective_connection_id = connection_id or metadata.get("connection_id")
        if effective_connection_id is None and self.connection_registry is not None:
            effective_connection_id = self.connection_registry.resolve_connection_id(
                identity.session_id or ""
            )

        selected = self.routing_policy.select(
            self.catalog,
            capability_id,
            context=CapabilityRequestContext(
                owner_id=identity.user_id,
                connection_id=effective_connection_id,
                scopes=frozenset(identity.scopes),
            ),
            preferred_implementation_id=metadata.get(
                "implementation_id"
            ),
        )

        if selected.location == CapabilityExecutionLocation.CLIENT:
            if self.realtime is None or not selected.connection_id:
                raise RuntimeError(
                    "Client capability execution requires realtime and connection."
                )
            return RemoteClientDriver(
                self.catalog.get_definition(capability_id),
                self.realtime,
                selected.connection_id,
            )

        if selected.location == CapabilityExecutionLocation.SERVER:
            return legacy_driver

        raise RuntimeError(
            f"No compatibility driver for location {selected.location.value}"
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
            connection_id=context.get("connection_id"),
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