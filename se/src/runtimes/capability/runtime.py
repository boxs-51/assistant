# src/runtime/runtimes/capability/runtime.py
import asyncio
import structlog
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Mapping, Optional

from ...kernel.base import BaseRuntime, HealthStatus, RuntimeContext, RuntimeManifest
from .registry import CapabilityRegistry, CapabilityState
from .drivers.base import BaseCapabilityDriver
from .contracts.context import CapabilityExecutionContext
from .contracts.error import (
    CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT,
    CAPABILITY_CONTINUATION_INVALID_STATE,
    CAPABILITY_CONTINUATION_NOT_FOUND,
    CAPABILITY_CONTINUATION_STALE,
    CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE,
    CAPABILITY_CONTINUATION_UNSAFE,
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
    REMOTE_OUTCOME_UNKNOWN,
)
from .contracts.result import CapabilityResult
from .contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from .drivers.mcp_driver import McpCapabilityDriver
from .drivers.remote_client_driver import RemoteClientDriver
from .driver_registry import CapabilityDriverRegistry
from .fingerprint import capability_request_fingerprint
from .invocation import CapabilityInvocationLifecycle
from .reconciliation import RemoteInvocationReconciliationService
from .contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    CapabilityWaitReason,
    ExistingInvocationContinuationMode,
    RemoteOutcomeState,
)
from .catalog import CapabilityCatalog
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from .policy import (
    CapabilityAccessPolicy,
    CapabilityAccessProfile,
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)
from ...runtimes.connection.registry import (
    ConnectionRegistry,
    ConnectionStateError,
)
from ...runtimes.connection.realtime import (
    RealtimeMultiplexer,
    RemoteCapabilityError,
)
from ...runtimes.connection.multiplexer import RemoteConnectionLost
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
        driver_registry: CapabilityDriverRegistry | None = None,
        invocation_lifecycle: CapabilityInvocationLifecycle | None = None,
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
        self.driver_registry = driver_registry or CapabilityDriverRegistry()
        self.invocation_lifecycle = invocation_lifecycle or CapabilityInvocationLifecycle()
        effective_connections = (
            connection_registry
            or getattr(realtime, "registry", None)
        )
        self.reconciliation_service = (
            RemoteInvocationReconciliationService(
                self.invocation_lifecycle,
                effective_connections,
                realtime,
            )
            if effective_connections is not None and realtime is not None
            else None
        )
        
        self._subscribed = False
        self.mcp_manager = None

    async def reconcile_remote_invocation(
        self,
        invocation_id: str,
        connection_id: str,
        *,
        timeout: float | None = None,
    ):
        if self.reconciliation_service is None:
            raise RuntimeError(
                "Remote invocation reconciliation is unavailable."
            )
        return await self.reconciliation_service.reconcile(
            invocation_id,
            connection_id,
            timeout=timeout,
        )

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
                caller_agent_execution_id=event.payload.get(
                    "caller_agent_execution_id"
                ),
                invocation_id=event.payload.get("invocation_id"),
                turn_id=event.turn_id,
                request_id=event.payload.get("request_id"),
                session_id=event.session_id,
                task_id=event.payload.get("task_id"),
                branch_id=event.payload.get("branch_id"),
                correlation_id=event.payload.get("correlation_id"),
                trace_id=event.payload.get("trace_id"),
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

    async def get_available_capabilities(
        self,
        identity: Identity,
        access_profile: CapabilityAccessProfile = CapabilityAccessProfile.AGENT_POLICY,
    ):
        definitions = [
            driver.definition
            for driver in self.registry.get_all_drivers()
            if self.authorization.is_allowed(identity, driver)
            and CapabilityAccessPolicy.allows(driver.definition, access_profile)
        ]
        known_ids = {item.capability_id for item in definitions}
        if self.catalog is not None:
            definitions.extend(
                definition
                for definition in self.catalog.list_definitions()
                if definition.capability_id not in known_ids
                and definition.kind is CapabilityKind.SKILL
                and definition.execution_mode is CapabilityExecutionMode.CONTEXT_ONLY
                and self.authorization.is_allowed(identity, definition)
                and CapabilityAccessPolicy.allows(definition, access_profile)
            )
        return sorted(definitions, key=lambda item: item.capability_id)

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
            driver = McpCapabilityDriver(definition, self.mcp_manager)
            self.register_capability(driver)
            if self.catalog is not None:
                self.catalog.register_definition(definition)
                implementation_id = (
                    f"mcp:{descriptor.server_name}:{descriptor.name}"
                )
                if not self.catalog.contains_implementation(implementation_id):
                    implementation = CapabilityImplementation.from_definition(
                        definition,
                        implementation_id=implementation_id,
                        location=CapabilityExecutionLocation.MCP,
                        driver_kind="MCP",
                        owner_type=CapabilityOwnerType.SYSTEM,
                    )
                    self.catalog.register_implementation(implementation)
                    self.catalog.transition_implementation(
                        implementation_id,
                        CapabilityImplementationState.ENABLED,
                    )
                self.driver_registry.bind(
                    implementation_id,
                    driver,
                    replace=True,
                )
        return len(descriptors)

    async def continue_invocation(
        self,
        invocation_id: str,
        *,
        target_connection_id: str | None,
        mode: ExistingInvocationContinuationMode,
        expected_revision: int,
        expected_request_fingerprint: str,
        cancellation_event: asyncio.Event | None = None,
    ) -> CapabilityResult:
        """Continue one durable logical invocation by creating exactly one attempt.

        This is intentionally distinct from execute_capability: that method
        creates a new logical CapabilityInvocation, while this method reuses
        the existing row and preserves its semantic identity.
        """
        started = time.perf_counter()
        cancellation_event = cancellation_event or asyncio.Event()
        if cancellation_event.is_set():
            raise asyncio.CancelledError()

        try:
            mode = ExistingInvocationContinuationMode(mode)
        except ValueError as exc:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_UNSAFE,
                message=f"Unsupported continuation mode: {mode!r}",
                category="CONTINUATION",
                retryable=False,
                safe_for_client=True,
                invocation_id=invocation_id,
            ) from exc

        store = self.invocation_lifecycle.store
        invocation = await store.get(invocation_id)
        if invocation is None:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_NOT_FOUND,
                message=f"Unknown capability invocation: {invocation_id}",
                category="CONTINUATION",
                retryable=False,
                safe_for_client=True,
                invocation_id=invocation_id,
            )

        self._validate_existing_continuation(
            invocation,
            mode=mode,
            expected_revision=expected_revision,
            expected_request_fingerprint=expected_request_fingerprint,
        )
        await self._validate_attempt_history(invocation)

        driver, implementation = self._resolve_continuation_target(
            invocation,
            target_connection_id=target_connection_id,
        )

        context = CapabilityExecutionContext.create(
            identity=None,
            execution_id=invocation.execution_id,
            invocation_id=invocation.invocation_id,
            caller_agent_execution_id=invocation.execution_id,
            session_id=invocation.session_id,
            correlation_id=invocation.correlation_id,
            trace_id=invocation.trace_id,
            connection_id=target_connection_id,
            workflow_id=invocation.workflow_id,
            attempt=invocation.attempt + 1,
            cancellation_event=cancellation_event,
            metadata={
                "tool_call_id": invocation.tool_call_id,
                "continuation_mode": mode.value,
                "request_fingerprint": invocation.request_fingerprint,
            },
        )

        try:
            invocation, attempt = (
                await self.invocation_lifecycle.begin_continuation_attempt(
                    invocation,
                    implementation_id=implementation.implementation_id,
                    driver_kind=implementation.driver_kind,
                    connection_id=target_connection_id,
                    continuation_mode=mode.value,
                )
            )
        except RuntimeError as exc:
            current = await store.get(invocation_id)
            code = (
                CAPABILITY_CONTINUATION_STALE
                if current is not None
                and current.revision != expected_revision
                else CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT
            )
            raise CapabilityError(
                code=code,
                message=str(exc),
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "expected_revision": expected_revision,
                    "current_revision": (
                        current.revision if current is not None else None
                    ),
                },
            ) from exc

        context.attempt = invocation.attempt
        try:
            invocation, attempt = (
                await self.invocation_lifecycle.start_continuation_attempt(
                    invocation,
                    attempt,
                )
            )
        except RuntimeError as exc:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_STALE,
                message=str(exc),
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            ) from exc
        # Bind only after the atomic RUNNING transition so the dispatch
        # callback closes over the current invocation revision.
        self._bind_remote_dispatch_started(driver, invocation)

        return await self._run_invocation_attempt(
            invocation=invocation,
            attempt=attempt,
            driver=driver,
            selected_implementation_id=implementation.implementation_id,
            selected_implementation=implementation,
            effective_implementation_id=implementation.implementation_id,
            effective_driver_kind=implementation.driver_kind,
            context=context,
            capability_id=invocation.capability_id,
            arguments=invocation.arguments,
            identity=None,
            request_metadata=dict(context.metadata),
            routing_connection_id=target_connection_id,
            started=started,
            allow_internal_retry=False,
            continuation_mode=mode,
        )

    def _validate_existing_continuation(
        self,
        invocation: CapabilityInvocation,
        *,
        mode: ExistingInvocationContinuationMode,
        expected_revision: int,
        expected_request_fingerprint: str,
    ) -> None:
        if invocation.revision != expected_revision:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_STALE,
                message="CapabilityInvocation revision changed after planning.",
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "expected_revision": expected_revision,
                    "current_revision": invocation.revision,
                },
            )
        if (
            not invocation.capability_version
            or not invocation.request_fingerprint
            or invocation.request_fingerprint
            != expected_request_fingerprint
        ):
            raise CapabilityError(
                code=REMOTE_INVOCATION_CONFLICT,
                message="Continuation semantic fingerprint does not match.",
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        recomputed = capability_request_fingerprint(
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            arguments=invocation.arguments,
        )
        if recomputed != invocation.request_fingerprint:
            raise CapabilityError(
                code=REMOTE_INVOCATION_CONFLICT,
                message="Durable invocation arguments no longer match fingerprint.",
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        if not invocation.execution_id or not invocation.tool_call_id:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_INVALID_STATE,
                message=(
                    "Agent-owned continuation requires durable execution_id "
                    "and tool_call_id lineage."
                ),
                category="CONTINUATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        if (
            invocation.state is not CapabilityInvocationState.WAITING
            or invocation.wait_reason is not CapabilityWaitReason.CONNECTION
        ):
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_INVALID_STATE,
                message=(
                    "Existing invocation continuation requires "
                    "WAITING(CONNECTION)."
                ),
                category="CONTINUATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "state": invocation.state.value,
                    "wait_reason": (
                        invocation.wait_reason.value
                        if invocation.wait_reason is not None
                        else None
                    ),
                },
            )

        if mode is ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED:
            allowed = (
                invocation.remote_outcome_state
                is RemoteOutcomeState.NOT_DISPATCHED
            )
        else:
            allowed = (
                invocation.remote_outcome_state
                in {
                    RemoteOutcomeState.IN_FLIGHT,
                    RemoteOutcomeState.OUTCOME_UNKNOWN,
                }
                and invocation.idempotency
                in {
                    CapabilityIdempotency.IDEMPOTENT,
                    CapabilityIdempotency.DEDUPLICATED,
                }
            )
        if not allowed:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_UNSAFE,
                message=(
                    f"Continuation mode {mode.value} is incompatible with "
                    "the durable R6 outcome/idempotency snapshot."
                ),
                category="CONTINUATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "remote_outcome_state": (
                        invocation.remote_outcome_state.value
                        if invocation.remote_outcome_state is not None
                        else None
                    ),
                    "idempotency": invocation.idempotency.value,
                },
            )

    async def _validate_attempt_history(
        self,
        invocation: CapabilityInvocation,
    ) -> None:
        attempts = await self.invocation_lifecycle.store.list_attempts(
            invocation.invocation_id
        )
        numbers = sorted(item.attempt_number for item in attempts)
        expected = list(range(1, invocation.attempt + 1))
        if numbers != expected:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT,
                message="CapabilityInvocation attempt history is inconsistent.",
                category="CONTINUATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "invocation_attempt": invocation.attempt,
                    "attempt_numbers": numbers,
                },
            )

    def _resolve_continuation_target(
        self,
        invocation: CapabilityInvocation,
        *,
        target_connection_id: str | None,
    ) -> tuple[RemoteClientDriver, CapabilityImplementation]:
        if (
            not target_connection_id
            or self.catalog is None
            or self.connection_registry is None
            or self.realtime is None
        ):
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE,
                message="Continuation target connection is unavailable.",
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        if target_connection_id == invocation.connection_id:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE,
                message="Continuation requires a new connection generation.",
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        try:
            snapshot = self.connection_registry.get(target_connection_id)
        except Exception as exc:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE,
                message="Continuation target connection does not exist.",
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            ) from exc
        if not snapshot.is_usable:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE,
                message="Continuation target connection is not ACTIVE.",
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        client_id = str(snapshot.metadata.get("client_id") or "")
        if (
            not invocation.owner_user_id
            or snapshot.user_id != invocation.owner_user_id
            or not invocation.origin_client_id
            or client_id != invocation.origin_client_id
        ):
            raise CapabilityError(
                code="CAPABILITY_UNAUTHORIZED",
                message=(
                    "Continuation target must be the same user and stable "
                    "client installation."
                ),
                category="AUTHORIZATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )

        try:
            definition = self.catalog.get_definition(
                invocation.capability_id
            )
            candidates = [
                item
                for item in self.catalog.list_implementations(
                    invocation.capability_id,
                    routable_only=True,
                )
                if (
                    item.location is CapabilityExecutionLocation.CLIENT
                    and item.owner_type is CapabilityOwnerType.CLIENT
                    and item.driver_kind == "REMOTE_CLIENT"
                    and item.connection_id == target_connection_id
                    and item.owner_id == invocation.owner_user_id
                    and str(item.metadata.get("client_id") or "")
                    == invocation.origin_client_id
                    and item.version == invocation.capability_version
                )
            ]
        except Exception as exc:
            raise CapabilityError(
                code=CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE,
                message="Continuation capability is not available on target.",
                category="CONTINUATION",
                retryable=True,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            ) from exc

        if (
            definition.version != invocation.capability_version
            or definition.kind is not invocation.kind
            or definition.execution_mode is not invocation.execution_mode
            or definition.idempotency is not invocation.idempotency
        ):
            raise CapabilityError(
                code=REMOTE_INVOCATION_CONFLICT,
                message="Continuation capability contract changed.",
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )
        if len(candidates) != 1:
            code = (
                REMOTE_INVOCATION_CONFLICT
                if len(candidates) > 1
                else CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE
            )
            raise CapabilityError(
                code=code,
                message=(
                    "Continuation requires exactly one matching client "
                    "implementation on the target connection."
                ),
                category="CONTINUATION",
                retryable=(len(candidates) == 0),
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={"candidate_count": len(candidates)},
            )
        implementation = candidates[0]
        return (
            RemoteClientDriver(
                definition,
                self.realtime,
                target_connection_id,
            ),
            implementation,
        )

    async def execute_capability(
        self, 
        capability_id: str,
        arguments: Mapping[str, Any],
        identity: Identity,
        *,
        execution_id: str | None = None,
        caller_agent_execution_id: str | None = None,
        caller_execution_remaining_seconds: float | None = None,
        caller_iteration_remaining_seconds: float | None = None,
        invocation_id: str | None = None,
        turn_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        branch_id: str | None = None,
        correlation_id: str | None = None,
        trace_id: str | None = None,
        connection_id: str | None = None,
        workflow_id: str | None = None,
        timeout_seconds: float | None = None,
        cancellation_event: asyncio.Event | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CapabilityResult:
        started = time.perf_counter()
        if caller_agent_execution_id is not None:
            if execution_id is None:
                execution_id = caller_agent_execution_id
            elif execution_id != caller_agent_execution_id:
                raise ValueError(
                    "caller_agent_execution_id must match execution_id "
                    "for Agent-owned capability invocations."
                )
        request_metadata = dict(metadata or {})
        effective_correlation_id = (
            correlation_id
            if correlation_id is not None
            else request_metadata.get("correlation_id")
        )
        effective_trace_id = (
            trace_id if trace_id is not None else request_metadata.get("trace_id")
        )
        metadata_connection_id = request_metadata.get("connection_id")
        if (
            connection_id is not None
            and metadata_connection_id is not None
            and connection_id != metadata_connection_id
        ):
            raise ValueError(
                "Explicit connection_id does not match metadata['connection_id']."
            )
        driver, selected_implementation_id = self._resolve_execution_driver(
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
            invocation_id=invocation_id,
            caller_agent_execution_id=caller_agent_execution_id,
            caller_execution_remaining_seconds=(
                caller_execution_remaining_seconds
            ),
            caller_iteration_remaining_seconds=(
                caller_iteration_remaining_seconds
            ),
            request_id=request_id,
            session_id=session_id,
            task_id=task_id,
            branch_id=branch_id,
            correlation_id=effective_correlation_id,
            trace_id=effective_trace_id,
            connection_id=(
                connection_id
                or metadata_connection_id
            ),
            workflow_id=workflow_id,
            timeout_seconds=timeout_seconds,
            cancellation_event=cancellation_event,
            metadata=request_metadata,
        )

        selected_implementation = (
            self.catalog.get_implementation(selected_implementation_id)
            if self.catalog is not None and selected_implementation_id is not None
            else None
        )
        effective_implementation_id = (
            selected_implementation_id or f"legacy:{capability_id}"
        )
        effective_driver_kind = (
            selected_implementation.driver_kind
            if selected_implementation is not None
            else type(driver).__name__
        )
        origin_client_id = None
        if (
            selected_implementation is not None
            and selected_implementation.location
            is CapabilityExecutionLocation.CLIENT
        ):
            origin_client_id = selected_implementation.metadata.get(
                "client_id"
            )
        elif isinstance(driver, RemoteClientDriver):
            origin_client_id = request_metadata.get("client_id")

        invocation = CapabilityInvocation(
            invocation_id=context.invocation_id,
            capability_id=capability_id,
            capability_version=driver.definition.version,
            kind=driver.definition.kind,
            execution_mode=driver.definition.execution_mode,
            idempotency=driver.definition.idempotency,
            request_fingerprint=capability_request_fingerprint(
                capability_id=capability_id,
                capability_version=driver.definition.version,
                arguments=arguments,
            ),
            owner_user_id=identity.user_id,
            origin_client_id=origin_client_id,
            remote_outcome_state=(
                RemoteOutcomeState.NOT_DISPATCHED
                if isinstance(driver, RemoteClientDriver)
                else None
            ),
            implementation_id=effective_implementation_id,
            driver_kind=effective_driver_kind,
            session_id=context.session_id,
            turn_id=turn_id or request_metadata.get("turn_id"),
            execution_id=context.execution_id,
            workflow_id=context.workflow_id,
            tool_call_id=request_metadata.get("tool_call_id"),
            connection_id=context.connection_id,
            max_attempts=max(1, int(request_metadata.get("max_attempts", 1))),
            arguments=dict(arguments),
            deadline_at=(
                datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
                if timeout_seconds is not None
                else None
            ),
            correlation_id=context.correlation_id,
            trace_id=context.trace_id,
        )
        await self.invocation_lifecycle.create(invocation)
        self._bind_remote_dispatch_started(driver, invocation)
        invocation.attempt = 1
        attempt = await self.invocation_lifecycle.start_attempt(
            invocation,
            implementation_id=effective_implementation_id,
            driver_kind=effective_driver_kind,
            connection_id=context.connection_id,
        )
        await self.invocation_lifecycle.transition(
            invocation,
            CapabilityInvocationState.DISPATCHING,
            attempt_id=attempt.attempt_id,
        )
        await self.invocation_lifecycle.transition(
            invocation,
            CapabilityInvocationState.RUNNING,
            attempt_id=attempt.attempt_id,
        )

        return await self._run_invocation_attempt(
            invocation=invocation,
            attempt=attempt,
            driver=driver,
            selected_implementation_id=selected_implementation_id,
            selected_implementation=selected_implementation,
            effective_implementation_id=effective_implementation_id,
            effective_driver_kind=effective_driver_kind,
            context=context,
            capability_id=capability_id,
            arguments=arguments,
            identity=identity,
            request_metadata=request_metadata,
            routing_connection_id=connection_id,
            started=started,
            allow_internal_retry=True,
        )

    async def _run_invocation_attempt(
        self,
        *,
        invocation: CapabilityInvocation,
        attempt: CapabilityInvocationAttempt,
        driver: BaseCapabilityDriver,
        selected_implementation_id: str | None,
        selected_implementation: CapabilityImplementation | None,
        effective_implementation_id: str,
        effective_driver_kind: str,
        context: CapabilityExecutionContext,
        capability_id: str,
        arguments: Mapping[str, Any],
        identity: Identity | None,
        request_metadata: Dict[str, Any],
        routing_connection_id: str | None,
        started: float,
        allow_internal_retry: bool,
        continuation_mode: ExistingInvocationContinuationMode | None = None,
    ) -> CapabilityResult:
        logger.info(
            "Executing capability",
            capability_id=capability_id,
            invocation_id=context.invocation_id,
        )
        started_at = datetime.now(timezone.utc)
        excluded_implementation_ids: set[str] = set()

        async def try_remote_retry(
            retry_error: dict[str, Any],
            *,
            allow_retry: bool,
        ) -> bool:
            nonlocal driver
            nonlocal selected_implementation_id
            nonlocal selected_implementation
            nonlocal effective_implementation_id
            nonlocal effective_driver_kind
            nonlocal attempt

            can_retry = (
                allow_internal_retry
                and allow_retry
                and invocation.attempt < invocation.max_attempts
                and self.catalog is not None
                and selected_implementation_id is not None
            )
            if not can_retry:
                return False

            if attempt.completed_at is None:
                await self.invocation_lifecycle.finish_attempt(
                    attempt,
                    CapabilityInvocationState.FAILED,
                    error=retry_error,
                )
            await self.invocation_lifecycle.transition(
                invocation,
                CapabilityInvocationState.RETRYING,
                attempt_id=attempt.attempt_id,
            )
            excluded_implementation_ids.add(selected_implementation_id)
            retry_metadata = {
                **request_metadata,
                "excluded_implementation_ids": sorted(
                    excluded_implementation_ids
                ),
            }
            try:
                driver, selected_implementation_id = (
                    self._resolve_execution_driver(
                        capability_id,
                        identity,
                        retry_metadata,
                        connection_id=routing_connection_id,
                    )
                )
            except Exception:
                driver = None
                return False

            if driver is not None and not self.authorization.is_allowed(
                identity,
                driver,
            ):
                driver = None
                return False

            assert driver is not None
            assert selected_implementation_id is not None
            selected_implementation = self.catalog.get_implementation(
                selected_implementation_id
            )
            effective_implementation_id = selected_implementation_id
            effective_driver_kind = selected_implementation.driver_kind
            invocation.implementation_id = effective_implementation_id
            invocation.driver_kind = effective_driver_kind
            invocation.connection_id = selected_implementation.connection_id
            invocation.attempt += 1
            context.attempt = invocation.attempt
            self._bind_remote_dispatch_started(driver, invocation)
            attempt = await self.invocation_lifecycle.start_attempt(
                invocation,
                implementation_id=effective_implementation_id,
                driver_kind=effective_driver_kind,
                connection_id=selected_implementation.connection_id,
            )
            await self.invocation_lifecycle.transition(
                invocation,
                CapabilityInvocationState.DISPATCHING,
                attempt_id=attempt.attempt_id,
            )
            await self.invocation_lifecycle.transition(
                invocation,
                CapabilityInvocationState.RUNNING,
                attempt_id=attempt.attempt_id,
            )
            return True

        while True:
            try:
                raw_output = await self._execute_driver_once(
                    driver,
                    context,
                    capability_id=capability_id,
                    arguments=arguments,
                )
                break
            except ConnectionStateError as exc:
                # The transport rejected the connection before remote invoke
                # could enter the send boundary.  Restore the durable proof
                # that this attempt did not dispatch.
                if (
                    isinstance(driver, RemoteClientDriver)
                    and invocation.remote_outcome_state
                    is RemoteOutcomeState.IN_FLIGHT
                    and continuation_mode
                    is not ExistingInvocationContinuationMode.REPLAY_SAFE
                ):
                    await self.invocation_lifecycle.update_remote_outcome(
                        invocation,
                        RemoteOutcomeState.NOT_DISPATCHED,
                    )
                details = {
                    "connection_id": context.connection_id,
                    "invocation_id": context.invocation_id,
                    "remote_outcome_state": (
                        invocation.remote_outcome_state.value
                        if invocation.remote_outcome_state is not None
                        else None
                    ),
                    "idempotency": invocation.idempotency.value,
                    "request_fingerprint": invocation.request_fingerprint,
                }
                retry_error = {
                    "code": "REMOTE_CONNECTION_LOST",
                    "message": str(exc),
                    "details": details,
                }
                if await try_remote_retry(
                    retry_error,
                    allow_retry=True,
                ):
                    continue

                normalized = CapabilityError(
                    code="REMOTE_CONNECTION_LOST",
                    message=str(exc),
                    category="CONNECTION",
                    retryable=False,
                    safe_for_client=True,
                    cause_type=type(exc).__name__,
                    capability_id=capability_id,
                    invocation_id=context.invocation_id,
                    details=details,
                )
                error = normalized.model_dump()
                if attempt.completed_at is None:
                    await self.invocation_lifecycle.finish_attempt(
                        attempt,
                        CapabilityInvocationState.FAILED,
                        error=error,
                    )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.WAITING,
                    wait_reason=CapabilityWaitReason.CONNECTION,
                    attempt_id=attempt.attempt_id,
                )
                raise normalized from exc
            except RemoteConnectionLost as exc:
                if (
                    isinstance(driver, RemoteClientDriver)
                    and invocation.remote_outcome_state
                    is not RemoteOutcomeState.OUTCOME_UNKNOWN
                ):
                    await self.invocation_lifecycle.update_remote_outcome(
                        invocation,
                        RemoteOutcomeState.OUTCOME_UNKNOWN,
                    )
                details = {
                    "connection_id": exc.connection_id,
                    "invocation_id": exc.invocation_id,
                    "remote_outcome_state": RemoteOutcomeState.OUTCOME_UNKNOWN.value,
                    "idempotency": invocation.idempotency.value,
                    "request_fingerprint": invocation.request_fingerprint,
                }
                retry_error = {
                    "code": REMOTE_OUTCOME_UNKNOWN,
                    "message": str(exc),
                    "details": details,
                }
                replay_safe = invocation.idempotency in {
                    CapabilityIdempotency.IDEMPOTENT,
                    CapabilityIdempotency.DEDUPLICATED,
                }
                if await try_remote_retry(
                    retry_error,
                    allow_retry=replay_safe,
                ):
                    continue

                normalized = CapabilityError(
                    code=REMOTE_OUTCOME_UNKNOWN,
                    message=str(exc),
                    category="RECONCILIATION",
                    retryable=False,
                    safe_for_client=True,
                    cause_type=type(exc).__name__,
                    capability_id=capability_id,
                    invocation_id=context.invocation_id,
                    details=details,
                )
                error = normalized.model_dump()
                if attempt.completed_at is None:
                    await self.invocation_lifecycle.finish_attempt(
                        attempt, CapabilityInvocationState.FAILED, error=error
                    )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.WAITING,
                    wait_reason=CapabilityWaitReason.CONNECTION,
                    attempt_id=attempt.attempt_id,
                )
                raise normalized from exc
            except RemoteCapabilityError as exc:
                details = {
                    "remote_details": exc.details,
                    "remote_outcome_state": (
                        RemoteOutcomeState.TERMINAL_COMMITTED.value
                    ),
                }
                normalized = CapabilityError(
                    code=exc.code,
                    message=str(exc),
                    category="REMOTE",
                    retryable=exc.retryable,
                    safe_for_client=True,
                    cause_type=type(exc).__name__,
                    capability_id=capability_id,
                    invocation_id=context.invocation_id,
                    details=details,
                )
                error = normalized.model_dump()
                await self.invocation_lifecycle.finish_attempt(
                    attempt,
                    CapabilityInvocationState.FAILED,
                    error=error,
                )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.FAILED,
                    attempt_id=attempt.attempt_id,
                    error=error,
                    remote_outcome_state=(
                        RemoteOutcomeState.TERMINAL_COMMITTED
                    ),
                )
                raise normalized from exc
            except asyncio.TimeoutError as exc:
                if (
                    isinstance(driver, RemoteClientDriver)
                    and invocation.remote_outcome_state
                    in {
                        RemoteOutcomeState.NOT_DISPATCHED,
                        RemoteOutcomeState.IN_FLIGHT,
                    }
                ):
                    await self.invocation_lifecycle.update_remote_outcome(
                        invocation,
                        RemoteOutcomeState.OUTCOME_UNKNOWN,
                    )
                error = {"code": "CAPABILITY_TIMEOUT", "message": str(exc)}
                await self.invocation_lifecycle.finish_attempt(
                    attempt, CapabilityInvocationState.TIMED_OUT, error=error
                )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.TIMED_OUT,
                    attempt_id=attempt.attempt_id,
                    error=error,
                )
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
                if (
                    isinstance(driver, RemoteClientDriver)
                    and invocation.remote_outcome_state
                    in {
                        RemoteOutcomeState.NOT_DISPATCHED,
                        RemoteOutcomeState.IN_FLIGHT,
                    }
                ):
                    await self.invocation_lifecycle.update_remote_outcome(
                        invocation,
                        RemoteOutcomeState.OUTCOME_UNKNOWN,
                    )
                error = {"code": "CAPABILITY_CANCELLED", "message": str(exc)}
                await self.invocation_lifecycle.finish_attempt(
                    attempt, CapabilityInvocationState.CANCELLED, error=error
                )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.CANCELLED,
                    attempt_id=attempt.attempt_id,
                    error=error,
                )
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
            except CapabilityError as exc:
                error = exc.model_dump()
                await self.invocation_lifecycle.finish_attempt(
                    attempt, CapabilityInvocationState.FAILED, error=error
                )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.FAILED,
                    attempt_id=attempt.attempt_id,
                    error=error,
                )
                raise
            except Exception as exc:
                if (
                    isinstance(driver, RemoteClientDriver)
                    and invocation.remote_outcome_state
                    is RemoteOutcomeState.IN_FLIGHT
                ):
                    await self.invocation_lifecycle.update_remote_outcome(
                        invocation,
                        RemoteOutcomeState.OUTCOME_UNKNOWN,
                    )
                original_code = getattr(exc, "code", None)
                details: Dict[str, Any] = {}
                if original_code:
                    details["original_error_code"] = str(original_code)
                remote_details = getattr(exc, "details", None)
                if remote_details is not None:
                    details["remote_details"] = remote_details
                normalized = CapabilityError(
                    code="CAPABILITY_EXECUTION_FAILED",
                    message=str(exc),
                    category="EXECUTION",
                    retryable=bool(getattr(exc, "retryable", False)),
                    safe_for_client=False,
                    cause_type=type(exc).__name__,
                    capability_id=capability_id,
                    invocation_id=context.invocation_id,
                    details=details,
                )
                error = normalized.model_dump()
                await self.invocation_lifecycle.finish_attempt(
                    attempt, CapabilityInvocationState.FAILED, error=error
                )
                await self.invocation_lifecycle.transition(
                    invocation,
                    CapabilityInvocationState.FAILED,
                    attempt_id=attempt.attempt_id,
                    error=error,
                )
                raise normalized from exc

        completed_at = time.perf_counter()
        completed_at_utc = datetime.now(timezone.utc)
        await self.invocation_lifecycle.finish_attempt(
            attempt, CapabilityInvocationState.COMPLETED
        )
        await self.invocation_lifecycle.transition(
            invocation,
            CapabilityInvocationState.COMPLETED,
            attempt_id=attempt.attempt_id,
            output=raw_output,
            remote_outcome_state=(
                RemoteOutcomeState.TERMINAL_COMMITTED
                if isinstance(driver, RemoteClientDriver)
                else None
            ),
        )
        return CapabilityResult(
            invocation_id=context.invocation_id,
            capability_id=capability_id,
            output=raw_output,
            output_type=type(raw_output).__name__,
            started_at=started_at,
            completed_at=completed_at_utc,
            duration_ms=(completed_at - started) * 1000,
            metadata={
                "attempt": invocation.attempt,
                **(
                    {"implementation_id": selected_implementation_id}
                    if selected_implementation_id is not None
                    else {}
                ),
                **(
                    {
                        "continuation_mode": continuation_mode.value,
                        "request_fingerprint": invocation.request_fingerprint,
                    }
                    if continuation_mode is not None
                    else {}
                ),
            },
        )

    def _bind_remote_dispatch_started(
        self,
        driver: BaseCapabilityDriver,
        invocation: CapabilityInvocation,
    ) -> None:
        if not isinstance(driver, RemoteClientDriver):
            return

        async def mark_started(_envelope) -> None:
            if (
                invocation.remote_outcome_state
                is RemoteOutcomeState.NOT_DISPATCHED
            ):
                await self.invocation_lifecycle.update_remote_outcome(
                    invocation,
                    RemoteOutcomeState.IN_FLIGHT,
                )

        driver.set_dispatch_started_handler(mark_started)

    def _resolve_execution_driver(
        self,
        capability_id: str,
        identity: Identity,
        metadata: Dict[str, Any],
        *,
        connection_id: str | None = None,
    ) -> tuple[BaseCapabilityDriver | None, str | None]:
        legacy_driver = self.registry.get_driver(capability_id)
        if self.catalog is None or not self.catalog.contains_definition(capability_id):
            return legacy_driver, None
        if self.routing_policy is None:
            raise RuntimeError(
                "Capability catalog is configured without routing policy."
            )

        effective_connection_id = connection_id or metadata.get("connection_id")

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
            excluded_implementation_ids=frozenset(
                metadata.get("excluded_implementation_ids", ())
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
            ), selected.implementation_id

        bound_driver = self.driver_registry.get(selected.implementation_id)
        if bound_driver is not None:
            if bound_driver.definition.capability_id != selected.capability_id:
                raise RuntimeError(
                    f"Implementation '{selected.implementation_id}' is bound to "
                    f"capability '{bound_driver.definition.capability_id}', not "
                    f"'{selected.capability_id}'."
                )
            return bound_driver, selected.implementation_id

        # Temporary migration bridge for a single legacy server driver. New
        # registrations bind explicitly by implementation_id.
        if selected.location == CapabilityExecutionLocation.SERVER and legacy_driver:
            self.driver_registry.bind(selected.implementation_id, legacy_driver)
            return legacy_driver, selected.implementation_id

        raise RuntimeError(
            f"No driver bound for implementation '{selected.implementation_id}'"
        )

    @staticmethod
    async def _execute_driver_once(
        driver: BaseCapabilityDriver,
        context: CapabilityExecutionContext,
        *,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        if context.cancelled:
            raise asyncio.CancelledError()
        driver_task = asyncio.create_task(
            driver.execute(context, dict(arguments)),
            name=f"capability:{capability_id}:{context.invocation_id}",
        )
        cancellation_task = asyncio.create_task(
            context.cancellation_event.wait(),
            name=f"capability-cancel:{context.invocation_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {driver_task, cancellation_task},
                timeout=context.remaining_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if driver_task in done:
                return await driver_task
            if cancellation_task in done:
                driver_task.cancel()
                await asyncio.gather(driver_task, return_exceptions=True)
                raise asyncio.CancelledError()
            driver_task.cancel()
            await asyncio.gather(driver_task, return_exceptions=True)
            raise asyncio.TimeoutError()
        except BaseException:
            if not driver_task.done():
                driver_task.cancel()
            await asyncio.gather(
                driver_task,
                return_exceptions=True,
            )
            raise
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

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
            caller_agent_execution_id=context.get(
                "caller_agent_execution_id"
            ),
            invocation_id=context.get("invocation_id"),
            turn_id=context.get("turn_id"),
            request_id=context.get("request_id"),
            session_id=context.get("session_id"),
            connection_id=context.get("connection_id"),
            workflow_id=context.get("workflow_id"),
            metadata={
                key: value
                for key, value in context.items()
                if key not in {
                    "execution_id",
                    "caller_agent_execution_id",
                    "invocation_id",
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
