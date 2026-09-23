from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
from typing import Dict, Any, Tuple
import uuid
from fastapi import FastAPI
import httpx
import structlog
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from .infrastructure.event_bus.manager import EventingManager

from .kernel.kernel import RuntimeKernel

# Config & Observability
from .infrastructure.config import ConfigurationRegistry, ConfigManager, ConfigSchema
from .infrastructure.observability import ObservabilityConfig, LoggingConfig, TracingConfig

from .transport.gateway.middleware.metris import setup_gateway_observability
from .transport.gateway.middleware.factory import create_middleware_stack

# Storage & UoW
from .infrastructure.storage.core.manager import StorageEngine
from .infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .infrastructure.storage.repositories.capability_invocations import SqlCapabilityInvocationStore
from .infrastructure.mcp.mcp_manager import GatewayMcpManager

# Security & Gateway Infrastructure
from .transport.gateway.limiter import RateLimiterManager
from .circuit_breaker import CircuitBreakerManager

from .transport.gateway.authentication.oauth import create_oauth_client
from .transport.gateway.authentication.manager import AuthenticationManager
from .transport.gateway.authentication.authentication import Authentication

from .transport.gateway.authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .transport.gateway.authentication.authenticators.jwt_authenticator import JWTAuthenticator

from .transport.gateway.authentication.services import (APIKeyService, LoginService, OAuthService, PasswordResetService,
OTPStorageService, RegistrationService, TokenService, UserService, GuestSessionService)

from .runtimes.connection.runtime import ConnectionRuntime
from .runtimes.session.runtime import SessionRuntime
from .runtimes.workflow.runtime import WorkflowRuntime
from .runtimes.capability.runtime import CapabilityRuntime
from .runtimes.provider.runtime import ProviderRuntime
from .runtimes.event.runtime import EventRuntime
from .runtimes.context.runtime import ContextRuntime

# Canonical versioned HTTP transport routers
from .transport.gateway.api.v1 import (
    admin as admin_router,
    agent_router,
    auth_router,
    capability_router,
    chat_router,
    embeddings_router,
    events_router,
    files_router,
    health_router,
    models_router,
    multi_agent_router,
    session_router,
    tool_router,
)
from .application.container import ApplicationContainer
from .application.policy.authorization import AuthorizationService
from .agent.registry import AgentRegistry
from .tool.registry import ToolRegistry
from .runtimes.capability.registry import CapabilityRegistry
from .runtimes.capability.catalog import CapabilityCatalog
from .runtimes.capability.registration import ClientCapabilityRegistrationService
from .runtimes.capability.policy import CapabilityRoutingPolicy
from .runtimes.capability.local_tool_loader import register_local_tools
from .runtimes.capability.builtins import register_builtin_support
from .runtimes.capability.invocation import CapabilityInvocationLifecycle
from .runtimes.agent.coordinator import MultiAgentCoordinator
from .runtimes.agent.persistence import (
    AggregateControlError,
    DurableAgentStore,
    ForkControlError,
    RetryControlError,
)
from .runtimes.agent.runtime import AgentRuntime
from .runtimes.agent.supervisor import AgentExecutionSupervisor
from .runtimes.agent.task_budget import TaskBudgetService
from .runtimes.agent.resume_planning import AgentResumePlanningService
from .runtimes.agent.fork_planning import AgentForkPlanningService
from .runtimes.agent.retry_planning import AgentRetryPlanningService
from .runtimes.agent.ids import AgentExecutionIdFactory
from .runtimes.agent.assembly import DefaultAgentContextAssembler
from .runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from .runtimes.agent.capabilities import RegistryAgentCapabilityResolver, RegistryAgentSkillResolver
from .runtimes.agent.events import EventBusAgentEventPublisher
from .runtimes.agent.adapters import (
    ContextBuilderAdapter,
    ProviderInferenceAdapter,
    CapabilityToolExecutionAdapter,
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from .runtimes.agent.tool_execution import AgentToolExecutionCoordinator
from .runtimes.agent.contracts.context import AgentExecutionContext
from .runtimes.chat import DirectChatRuntime
from .domain.schemas.agent_execution import AgentExecutionLimits
from .domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from .domain.schemas.event import BaseEvent
from .version import __version__
logger = structlog.get_logger(__name__)

def _classify_fork_replay_execution(execution) -> str:
    """Classify only durable R8-F lifecycle shapes that may be replayed."""

    state = str(getattr(execution, "state", ""))
    try:
        revision = int(getattr(execution, "revision"))
    except (TypeError, ValueError) as exc:
        raise ForkControlError(
            "FORK_ADMISSION_CORRUPT",
            "Fork execution has an invalid durable revision.",
        ) from exc

    if state == "RUNNING" and revision == 1:
        return "PREACTIVATION"
    if state == "RUNNING" and revision >= 2:
        return "IDENTITY_REPLAY"
    if state == "WAITING" and revision >= 3:
        return "IDENTITY_REPLAY"
    if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"} and revision >= 2:
        return "IDENTITY_REPLAY"

    raise ForkControlError(
        "FORK_ADMISSION_CORRUPT",
        (
            "ForkAdmission execution has an invalid lifecycle shape: "
            f"{state}@{revision}."
        ),
    )


async def _activate_fork_owned(
    *,
    store,
    runtime,
    supervisor,
    token,
    bootstrap,
    identity,
):
    """Resolve durable activation to a known outcome across caller cancellation."""

    activation_task = asyncio.create_task(
        store.activate_fork_execution(
            bootstrap,
            identity=identity,
        ),
        name=f"fork-activation:{bootstrap.execution_id}",
    )
    try:
        return await asyncio.shield(activation_task)
    except asyncio.CancelledError:
        outcome = (
            await asyncio.gather(
                activation_task,
                return_exceptions=True,
            )
        )[0]
        if not isinstance(outcome, BaseException):
            try:
                await runtime.cancel_activated_fork_execution(
                    bootstrap.context,
                    outcome.activated_execution_revision,
                    error_message="FORK_ACTIVATION_CALLER_CANCELLED",
                )
            finally:
                await supervisor.release_reserved(token)
        else:
            await supervisor.release_reserved(token)
        raise


async def execute_forked_agent_task_control_plane(
    container,
    task_id,
    request,
    identity,
):
    """R8-F durable replay/activation path for an already-created E2."""

    store = container.agent_durable_store
    planner = container.fork_planning_service
    budget_service = container.task_budget_service
    supervisor = container.agent_execution_supervisor
    runtime = container.agent_runtime
    principal = str(identity.user_id or "")
    if not principal:
        raise PermissionError("Authenticated principal is required.")

    replay = await store.load_fork_replay(
        task_id=task_id,
        fork_request_id=request.fork_request_id,
        source_branch_id=request.source_branch_id,
        source_execution_id=request.source_execution_id,
        source_checkpoint_id=request.source_checkpoint_id,
        target_user_id=principal,
        overlay_messages=request.overlay_messages,
    )
    if replay is not None:
        admission = replay.admission
    else:
        plan = await planner.build_fork_plan(
            fork_request_id=request.fork_request_id,
            task_id=task_id,
            source_branch_id=request.source_branch_id,
            source_execution_id=request.source_execution_id,
            source_checkpoint_id=request.source_checkpoint_id,
            target_user_id=principal,
            overlay_messages=tuple(request.overlay_messages),
        )
        admission = await budget_service.consume_fork_plan(plan)

    current = await store.load_execution(admission.execution_id)
    if current is None:
        raise ForkControlError(
            "FORK_ADMISSION_CORRUPT",
            "ForkAdmission execution is missing.",
        )

    lifecycle = _classify_fork_replay_execution(current)
    if lifecycle == "IDENTITY_REPLAY":
        return {
            "task_id": task_id,
            "fork_request_id": request.fork_request_id,
            "branch_id": admission.branch_id,
            "execution_id": admission.execution_id,
            "execution_state": str(current.state),
            "execution_revision": int(current.revision),
            "started": False,
        }

    agent = container.agent_registry.get(current.agent_id)
    if agent is None:
        raise LookupError(
            f"Agent '{current.agent_id}' is not registered."
        )

    bootstrap = await store.prepare_fork_execution_context(
        admission.execution_id,
        identity=identity,
        agent=agent,
    )

    token = await supervisor.reserve(bootstrap.context)
    activation = None
    try:
        try:
            activation = await _activate_fork_owned(
                store=store,
                runtime=runtime,
                supervisor=supervisor,
                token=token,
                bootstrap=bootstrap,
                identity=identity,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            # Every ordinary pre-WIN exit releases process-local ownership.
            await supervisor.release_reserved(token)
            latest = await store.load_execution(admission.execution_id)
            if latest is None:
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "ForkAdmission execution disappeared during activation.",
                )
            latest_lifecycle = _classify_fork_replay_execution(latest)
            if latest_lifecycle == "IDENTITY_REPLAY":
                return {
                    "task_id": task_id,
                    "fork_request_id": request.fork_request_id,
                    "branch_id": admission.branch_id,
                    "execution_id": admission.execution_id,
                    "execution_state": str(latest.state),
                    "execution_revision": int(latest.revision),
                    "started": False,
                }
            raise

        try:
            bootstrap.context.restore_active_budget(
                activation.remaining_active_budget_seconds
            )
            owned_task = await supervisor.start_reserved(
                token,
                bootstrap.context,
                lambda: runtime.execute(
                    bootstrap.context,
                    durable_revision=(
                        activation.activated_execution_revision
                    ),
                ),
            )
        except BaseException as handoff_error:
            try:
                await runtime.cancel_activated_fork_execution(
                    bootstrap.context,
                    activation.activated_execution_revision,
                    error_message=(
                        "FORK_RUNTIME_HANDOFF_FAILED: "
                        f"{type(handoff_error).__name__}: {handoff_error}"
                    ),
                )
            finally:
                await supervisor.release_reserved(token)
            raise

        def observe_fork_runner(completed):
            if completed.cancelled():
                return
            # Retrieve the exception so a detached execution-scoped task
            # is never left as an unobserved asyncio failure.
            completed.exception()

        owned_task.add_done_callback(observe_fork_runner)
    except BaseException:
        # Pre-WIN cancellation/failure and post-WIN handoff cleanup are
        # explicitly owned above.
        raise

    latest = await store.load_execution(admission.execution_id)
    if latest is None:
        raise ForkControlError(
            "FORK_ADMISSION_CORRUPT",
            "ForkAdmission execution disappeared after activation.",
        )
    _classify_fork_replay_execution(latest)
    return {
        "task_id": task_id,
        "fork_request_id": request.fork_request_id,
        "branch_id": admission.branch_id,
        "execution_id": admission.execution_id,
        "execution_state": str(latest.state),
        "execution_revision": int(latest.revision),
        "started": True,
    }


def _classify_retry_replay_execution(execution) -> str:
    state = str(getattr(execution, "state", ""))
    try:
        revision = int(getattr(execution, "revision"))
    except (TypeError, ValueError) as exc:
        raise RetryControlError(
            "RETRY_ADMISSION_CORRUPT",
            "Retry execution has an invalid durable revision.",
        ) from exc
    if state == "RUNNING" and revision == 1:
        return "PREACTIVATION"
    if state == "RUNNING" and revision >= 2:
        return "IDENTITY_REPLAY"
    if state == "WAITING" and revision >= 3:
        return "IDENTITY_REPLAY"
    if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"} and revision >= 2:
        return "IDENTITY_REPLAY"
    raise RetryControlError(
        "RETRY_ADMISSION_CORRUPT",
        f"Retry execution has an invalid lifecycle shape: {state}@{revision}.",
    )


async def execute_retried_agent_task_control_plane(
    container,
    task_id,
    request,
    identity,
):
    """R9-G explicit retry replay/admission/activation command."""

    store = container.agent_durable_store
    principal = str(identity.user_id or "")
    if not principal:
        raise PermissionError("Authenticated principal is required.")
    replay = await store.load_retry_replay(
        task_id=task_id,
        retry_request_id=request.retry_request_id,
        branch_id=request.branch_id,
        source_execution_id=request.source_execution_id,
        source_checkpoint_id=request.source_checkpoint_id,
        target_user_id=principal,
    )
    if replay is not None:
        admission = replay.admission
    else:
        plan = await container.retry_planning_service.build_retry_plan(
            retry_request_id=request.retry_request_id,
            task_id=task_id,
            branch_id=request.branch_id,
            source_execution_id=request.source_execution_id,
            source_checkpoint_id=request.source_checkpoint_id,
            target_user_id=principal,
        )
        admission = await container.task_budget_service.consume_retry_plan(plan)
    current = await store.load_execution(admission.execution_id)
    if current is None:
        raise RetryControlError(
            "RETRY_ADMISSION_CORRUPT", "Retry execution is missing."
        )
    lifecycle = _classify_retry_replay_execution(current)
    if lifecycle == "IDENTITY_REPLAY":
        return {
            "task_id": task_id,
            "retry_request_id": request.retry_request_id,
            "branch_id": admission.branch_id,
            "execution_id": admission.execution_id,
            "execution_state": str(current.state),
            "execution_revision": int(current.revision),
            "started": False,
        }
    agent = container.agent_registry.get(current.agent_id)
    if agent is None:
        raise LookupError(f"Agent '{current.agent_id}' is not registered.")
    bootstrap = await store.prepare_retry_execution_context(
        admission.execution_id, identity=identity, agent=agent
    )
    supervisor = container.agent_execution_supervisor
    runtime = container.agent_runtime
    token = await supervisor.reserve(bootstrap.context)
    activation = None
    try:
        try:
            activation = await store.activate_retry_execution(
                bootstrap, identity=identity
            )
        except BaseException:
            await supervisor.release_reserved(token)
            latest = await store.load_execution(admission.execution_id)
            if latest is not None and (
                _classify_retry_replay_execution(latest)
                == "IDENTITY_REPLAY"
            ):
                return {
                    "task_id": task_id,
                    "retry_request_id": request.retry_request_id,
                    "branch_id": admission.branch_id,
                    "execution_id": admission.execution_id,
                    "execution_state": str(latest.state),
                    "execution_revision": int(latest.revision),
                    "started": False,
                }
            raise
        try:
            bootstrap.context.restore_active_budget(
                activation.remaining_active_budget_seconds
            )
            owned_task = await supervisor.start_reserved(
                token,
                bootstrap.context,
                lambda: runtime.execute(
                    bootstrap.context,
                    durable_revision=activation.activated_execution_revision,
                ),
            )
        except BaseException as handoff_error:
            try:
                await runtime.cancel_activated_retry_execution(
                    bootstrap.context,
                    activation.activated_execution_revision,
                    error_message=(
                        "RETRY_RUNTIME_HANDOFF_FAILED: "
                        f"{type(handoff_error).__name__}: {handoff_error}"
                    ),
                )
            finally:
                await supervisor.release_reserved(token)
            raise
        owned_task.add_done_callback(
            lambda completed: (
                None if completed.cancelled() else completed.exception()
            )
        )
    except BaseException:
        raise
    latest = await store.load_execution(admission.execution_id)
    if latest is None:
        raise RetryControlError(
            "RETRY_ADMISSION_CORRUPT",
            "Retry execution disappeared after activation.",
        )
    _classify_retry_replay_execution(latest)
    return {
        "task_id": task_id,
        "retry_request_id": request.retry_request_id,
        "branch_id": admission.branch_id,
        "execution_id": admission.execution_id,
        "execution_state": str(latest.state),
        "execution_revision": int(latest.revision),
        "started": True,
    }


def _classify_aggregate_replay_execution(execution) -> str:
    state = str(getattr(execution, "state", ""))
    try:
        revision = int(getattr(execution, "revision"))
    except (TypeError, ValueError) as exc:
        raise AggregateControlError(
            "AGGREGATE_ADMISSION_CORRUPT",
            "Aggregate execution has an invalid durable revision.",
        ) from exc
    if state == "RUNNING" and revision == 1:
        return "PREACTIVATION"
    if state == "RUNNING" and revision >= 2:
        return "IDENTITY_REPLAY"
    if state == "WAITING" and revision >= 3:
        return "IDENTITY_REPLAY"
    if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"} and revision >= 2:
        return "IDENTITY_REPLAY"
    raise AggregateControlError(
        "AGGREGATE_ADMISSION_CORRUPT",
        (
            "Aggregate execution has an invalid lifecycle shape: "
            f"{state}@{revision}."
        ),
    )


async def _activate_aggregate_owned(
    *,
    store,
    runtime,
    supervisor,
    token,
    bootstrap,
    identity,
):
    """Resolve aggregate activation to a known outcome across caller cancellation."""

    activation_task = asyncio.create_task(
        store.activate_aggregate_execution(
            bootstrap,
            identity=identity,
        ),
        name=f"aggregate-activation:{bootstrap.execution_id}",
    )
    try:
        return await asyncio.shield(activation_task)
    except asyncio.CancelledError:
        outcome = (
            await asyncio.gather(
                activation_task,
                return_exceptions=True,
            )
        )[0]
        if not isinstance(outcome, BaseException):
            try:
                await runtime.cancel_activated_aggregate_execution(
                    bootstrap.context,
                    outcome.activated_execution_revision,
                    error_message="AGGREGATE_ACTIVATION_CALLER_CANCELLED",
                )
            finally:
                await supervisor.release_reserved(token)
        else:
            await supervisor.release_reserved(token)
        raise


async def execute_aggregated_agent_task_control_plane(
    container,
    task_id,
    request,
    identity,
):
    """R9-F/G durable AGGREGATE admission/replay/activation/handoff command."""

    store = container.agent_durable_store
    service = container.task_budget_service
    supervisor = container.agent_execution_supervisor
    runtime = container.agent_runtime
    principal = str(identity.user_id or "")
    if not principal:
        raise PermissionError("Authenticated principal is required.")

    admission = await service.aggregate_branches(
        task_id,
        aggregate_request_id=request.aggregate_request_id,
        target_branch_id=request.target_branch_id,
        source_branch_ids=tuple(request.source_branch_ids),
        target_user_id=principal,
    )
    current = await store.load_execution(admission.execution_id)
    if current is None:
        raise AggregateControlError(
            "AGGREGATE_ADMISSION_CORRUPT",
            "Aggregate execution is missing.",
        )

    lifecycle = _classify_aggregate_replay_execution(current)
    if lifecycle == "IDENTITY_REPLAY":
        return {
            "task_id": task_id,
            "aggregate_request_id": request.aggregate_request_id,
            "target_branch_id": admission.target_branch_id,
            "source_branch_ids": list(admission.source_branch_ids),
            "execution_id": admission.execution_id,
            "execution_state": str(current.state),
            "execution_revision": int(current.revision),
            "started": False,
        }

    agent = container.agent_registry.get(current.agent_id)
    if agent is None:
        raise LookupError(f"Agent '{current.agent_id}' is not registered.")

    bootstrap = await store.prepare_aggregate_execution_context(
        admission.execution_id,
        identity=identity,
        agent=agent,
    )
    token = await supervisor.reserve(bootstrap.context)
    activation = None
    try:
        try:
            activation = await _activate_aggregate_owned(
                store=store,
                runtime=runtime,
                supervisor=supervisor,
                token=token,
                bootstrap=bootstrap,
                identity=identity,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            await supervisor.release_reserved(token)
            latest = await store.load_execution(admission.execution_id)
            if latest is None:
                raise AggregateControlError(
                    "AGGREGATE_ADMISSION_CORRUPT",
                    "Aggregate execution disappeared during activation.",
                )
            if (
                _classify_aggregate_replay_execution(latest)
                == "IDENTITY_REPLAY"
            ):
                return {
                    "task_id": task_id,
                    "aggregate_request_id": request.aggregate_request_id,
                    "target_branch_id": admission.target_branch_id,
                    "source_branch_ids": list(admission.source_branch_ids),
                    "execution_id": admission.execution_id,
                    "execution_state": str(latest.state),
                    "execution_revision": int(latest.revision),
                    "started": False,
                }
            raise

        try:
            bootstrap.context.restore_active_budget(
                activation.remaining_active_budget_seconds
            )
            owned_task = await supervisor.start_reserved(
                token,
                bootstrap.context,
                lambda: runtime.execute(
                    bootstrap.context,
                    durable_revision=(
                        activation.activated_execution_revision
                    ),
                ),
            )
        except BaseException as handoff_error:
            try:
                await runtime.cancel_activated_aggregate_execution(
                    bootstrap.context,
                    activation.activated_execution_revision,
                    error_message=(
                        "AGGREGATE_RUNTIME_HANDOFF_FAILED: "
                        f"{type(handoff_error).__name__}: {handoff_error}"
                    ),
                )
            finally:
                await supervisor.release_reserved(token)
            raise

        def observe_aggregate_runner(completed):
            if completed.cancelled():
                return
            completed.exception()

        owned_task.add_done_callback(observe_aggregate_runner)
    except BaseException:
        raise

    latest = await store.load_execution(admission.execution_id)
    if latest is None:
        raise AggregateControlError(
            "AGGREGATE_ADMISSION_CORRUPT",
            "Aggregate execution disappeared after activation.",
        )
    _classify_aggregate_replay_execution(latest)
    return {
        "task_id": task_id,
        "aggregate_request_id": request.aggregate_request_id,
        "target_branch_id": admission.target_branch_id,
        "source_branch_ids": list(admission.source_branch_ids),
        "execution_id": admission.execution_id,
        "execution_state": str(latest.state),
        "execution_revision": int(latest.revision),
        "started": True,
    }


# ==============================================================================
# BOOTSTRAP FACTORIES
# ==============================================================================

def bootstrap_observability(config: ConfigSchema | None = None) -> ConfigSchema:
    """Tải cấu hình gateway và kích hoạt hệ thống Observability (Metrics & Tracing)."""
    _config = config
    if _config is None:
        _config_manager = ConfigManager().get_instance("se/config/default.yaml")
        _config = _config_manager.initialize()
    _config.auth.validate_runtime_secrets()
    ConfigurationRegistry.set_config(_config)

    obs_config = ObservabilityConfig(
        service_name=_config.gateway.name,
        service_version=_config.gateway.version,
        logging=LoggingConfig(level=_config.logging.level),
        tracing=TracingConfig(
            enable=_config.tracing.enable,
            otlp_endpoint=_config.tracing.otlp_endpoint,
        ),
    )
    setup_gateway_observability(obs_config)
    return _config


async def bootstrap_storage(config: ConfigSchema) -> Tuple[StorageEngine, Any]:
    """Kết nối cơ sở dữ liệu và tạo Unit of Work Factory."""
    storage_engine = StorageEngine(config)
    await storage_engine.connect()
    
    db_driver = storage_engine.drivers.get("sqlite")
    uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
    
    logger.info("Storage Engine connected successfully.")
    return storage_engine, uow_factory


def bootstrap_security(
    config: ConfigSchema,
    storage_engine: StorageEngine, 
    uow_factory: Any, 
    cb_manager: CircuitBreakerManager,
    eventing_manager: EventingManager,
) -> Dict[str, Any]:
    """Khởi tạo các dịch vụ Xác thực, OAuth và Rate Limiting."""
    cache_driver = storage_engine.get_cache_driver()
    
    limiter = RateLimiterManager(
        cache_driver=cache_driver,
        circuit_breaker_manager=cb_manager,
        config=config.rate_limit
    )

    session_repo = storage_engine.repositories.get("sessions")
    token_service = TokenService(uow_factory=uow_factory, session_repo=session_repo,config=config.auth)
    guest_session_service = GuestSessionService(uow_factory, token_service)
    api_key_service = APIKeyService(uow_factory=uow_factory)

    auth_manager = AuthenticationManager(
        authenticators=[
            APIKeyAuthenticator(api_key_service),
            JWTAuthenticator(token_service, uow_factory),
        ]
    )
    redis_driver = storage_engine.get_cache_driver()
    otp_service = OTPStorageService(redis_driver if redis_driver else None, uow_factory)
    registration_service = RegistrationService(
        uow_factory,
        otp_service,
        token_service,
        eventing_manager.bus,
        guest_session_service,
    )
    login_service = LoginService(uow_factory, token_service, guest_session_service)
    oauth_service = OAuthService(
        uow_factory,
        token_service,
        eventing_manager.bus,
        guest_session_service,
    )
    user_service = UserService(uow_factory)
    password_reset_service = PasswordResetService(uow_factory, otp_service)

    auth = Authentication(
        registration_service=registration_service,
        login_service=login_service,
        oauth_service=oauth_service,
        token_service=token_service,
        user_service=user_service,
        password_reset_service=password_reset_service,
    )

    oauth = create_oauth_client(config.oauth)
    logger.info("Authentication & Security Managers initialized.")
    return {
        "auth_manager": auth_manager,
        "oauth": oauth,
        "limiter": limiter,
        "auth": auth,
        "api_key_service": api_key_service,
        "guest_session_service": guest_session_service,
    }


async def bootstrap_runtime_kernel(
    config: Any,
    storage_engine: StorageEngine,
    uow_factory: Any,
    http_client: httpx.AsyncClient,
    cb_manager: CircuitBreakerManager,
    eventing_manager: EventingManager,

    security_services: Dict[str, Any] = None,
) -> ApplicationContainer:
    """Khởi tạo EventBus, Container, các Runtimes và kích hoạt Boot Sequence cho Kernel."""
    eventing_manager.register_subscribers()
    agent_registry = AgentRegistry()
    capability_registry = CapabilityRegistry()
    capability_catalog = CapabilityCatalog()
    authorization_service = AuthorizationService()
    agent_execution_id_factory = AgentExecutionIdFactory()
    agent_execution_supervisor = AgentExecutionSupervisor()
    task_budget_settings = config.agent.task_budget
    task_budget_limits = TaskBudgetLimits(
        max_total_executions=task_budget_settings.max_total_executions,
        max_active_executions=task_budget_settings.max_active_executions,
        max_active_branches=task_budget_settings.max_active_branches,
        max_parallel_agents=task_budget_settings.max_parallel_agents,
        max_total_tool_calls=task_budget_settings.max_total_tool_calls,
        max_total_inference_calls=(
            task_budget_settings.max_total_inference_calls
        ),
        max_total_tokens=task_budget_settings.max_total_tokens,
        max_total_cost_usd=task_budget_settings.max_total_cost_usd,
        max_delegation_depth=task_budget_settings.max_delegation_depth,
    )
    task_budget_policy = TaskBudgetPolicy(
        version=task_budget_settings.policy_version,
        deny_recursive_agent_cycle=(
            task_budget_settings.deny_recursive_agent_cycle
        ),
    )
    task_budget_service = TaskBudgetService(
        eventing_manager.uow_factory,
        default_limits=task_budget_limits,
        default_policy=task_budget_policy,
    )

    # 1. Tạo ApplicationContainer trước
    container = ApplicationContainer(
        config=config,
        storage=storage_engine,
        uow_factory=uow_factory,
        http_client=http_client,
        eventing_manager=eventing_manager,
        mcp_manager=GatewayMcpManager(),
        event_bus=eventing_manager.bus,
        circuit_breaker_manager=cb_manager,
        agent_registry=agent_registry,
        tool_registry=ToolRegistry(),
        capability_registry=capability_registry,
        authorization_service=authorization_service,
        agent_execution_id_factory=agent_execution_id_factory,
        agent_execution_supervisor=agent_execution_supervisor,
        task_budget_service=task_budget_service,
        task_budget_policy=task_budget_policy,
        multi_agent_coordinator=MultiAgentCoordinator(
            agent_registry,
            durable_store=DurableAgentStore(eventing_manager.uow_factory),
            execution_supervisor=agent_execution_supervisor,
            execution_id_factory=agent_execution_id_factory,
            task_budget_service=task_budget_service,
        ),
        **(security_services or {}),
    )
    eventing_manager.set_dependency_container(container)
    await container.mcp_manager.start_health_checker()

    # 2. Tạo RuntimeKernel nhận container
    kernel = RuntimeKernel(eventing_manager, container)
    container.runtime_kernel = kernel

    # 3. Tạo các instance Runtimes và Bind vào Container trước khi Kernel Bootstrap
    connection_runtime = ConnectionRuntime()
    connection_runtime.registration_service = ClientCapabilityRegistrationService(
        capability_catalog,
        connection_runtime.registry,
    )
    capability_routing_policy = CapabilityRoutingPolicy(
        connection_availability=connection_runtime.registry,
    )

    async def publish_capability_invocation(event):
        await container.event_bus.publish(
            BaseEvent(
                event_id=event.event_id,
                event_name=event.event_name,
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload=event.model_dump(mode="json"),
            )
        )

    capability_invocation_lifecycle = CapabilityInvocationLifecycle(
        SqlCapabilityInvocationStore(eventing_manager.uow_factory),
        publish_capability_invocation,
    )

    runtimes = [
        ("event_runtime", EventRuntime()),
        ("context_runtime", ContextRuntime()),
        ("connection_runtime", connection_runtime),
        ("session_runtime", SessionRuntime()),
        ("workflow_runtime", WorkflowRuntime(agent_execution_id_factory)),
        (
            "capability_runtime",
            CapabilityRuntime(
                registry=capability_registry,
                authorization=authorization_service,
                catalog=capability_catalog,
                routing_policy=capability_routing_policy,
                connection_registry=connection_runtime.registry,
                realtime=connection_runtime.realtime,
                invocation_lifecycle=capability_invocation_lifecycle,
            ),
        ),
        ("provider_runtime", ProviderRuntime(cb_manager)),
    ]

    for runtime_id, runtime_instance in runtimes:
        container.bind_runtime(runtime_id, runtime_instance)
        kernel.register_runtime(runtime_instance)

    if container.capability_runtime and hasattr(container.capability_runtime, "registry"):
        container.tool_registry = ToolRegistry(container.capability_runtime.registry)
        tools_dir = Path(__file__).resolve().parents[2] / "tools" / "v1"
        loaded_tools = register_local_tools(
            container.capability_runtime, container.tool_registry, tools_dir
        )
        logger.info("Local tools discovered", tools=loaded_tools)

    # 4. Bootstrap Kernel (RuntimeContext tự động được khởi tạo bên trong)
    await kernel.bootstrap()

    # Establish canonical agent ports while preserving the legacy coordinator path.
    agent_tool_policy = RegistryAgentToolPolicy(
        agent_registry=container.agent_registry,
        capability_registry=container.capability_registry,
        authorization=container.authorization_service,
        capability_catalog=capability_catalog,
    )
    agent_execution_policy = DefaultAgentExecutionPolicy()
    container.agent_tool_policy = agent_tool_policy
    container.agent_execution_policy = agent_execution_policy
    container.context_assembler = DefaultAgentContextAssembler(
        DefaultAgentSystemPromptProvider(),
        RegistryAgentCapabilityResolver(
            agent_registry=container.agent_registry,
            capability_registry=container.capability_registry,
            capability_catalog=capability_catalog,
            tool_policy=agent_tool_policy,
        ),
        RegistryAgentSkillResolver(
            agent_registry=container.agent_registry,
            capability_catalog=capability_catalog,
        ),
    )
    container.context_builder_port = ContextBuilderAdapter(
        container.context_runtime,
        container.capability_runtime,
        agent_tool_policy,
        context_assembler=container.context_assembler,
    )
    container.inference_port = ProviderInferenceAdapter(
        container.provider_runtime,
        container.http_client,
    )
    container.direct_chat_runtime = DirectChatRuntime(
        inference=container.inference_port,
        capability_runtime=container.capability_runtime,
    )
    container.tool_execution_port = CapabilityToolExecutionAdapter(
        container.capability_runtime,
        agent_tool_policy,
        agent_execution_policy,
    )
    container.tool_execution_port = AgentToolExecutionCoordinator(
        container.tool_execution_port,
    )
    container.agent_durable_store = DurableAgentStore(eventing_manager.uow_factory)
    container.resume_planning_service = AgentResumePlanningService(
        container.agent_durable_store,
        container.capability_runtime,
    )
    container.fork_planning_service = AgentForkPlanningService(
        container.agent_durable_store
    )
    container.retry_planning_service = AgentRetryPlanningService(
        container.agent_durable_store
    )
    container.agent_runtime = AgentRuntime(
        context_builder=container.context_builder_port,
        inference=container.inference_port,
        tool_execution=container.tool_execution_port,
        execution_policy=container.agent_execution_policy,
        durable_store=container.agent_durable_store,
        event_publisher=EventBusAgentEventPublisher(container.event_bus),
        task_budget_service=container.task_budget_service,
    )
    builtin_support = register_builtin_support(container)
    container.multi_agent_coordinator.agent_authorizer = (
        lambda identity, agent_id: (
            capability_catalog.contains_definition(agent_id)
            and container.authorization_service.is_allowed(
                identity, capability_catalog.get_definition(agent_id)
            )
        )
    )
    logger.info("Built-in agent support registered", support=builtin_support)

    # Cấu hình Multi-Agent Executor
    async def execute_registered_agent_task(
        task,
        *,
        identity,
        execution_id,
        correlation_id,
        parent_execution_id=None,
    ):
        agent = container.agent_registry.get(task.assigned_agent_id)
        if agent is None:
            raise LookupError(f"Agent '{task.assigned_agent_id}' is not registered.")
        # Agent context assembly reads canonical conversation history. A
        # multi-agent session has its own durable record, so ensure the paired
        # conversation record exists before entering AgentRuntime.
        async with container.uow_factory() as uow:
            conversation = await uow.sessions.get_by_id(task.session_id)
            if conversation is None:
                await uow.sessions.create_session(
                    user_id=identity.user_id,
                    organization_id=identity.organization_id,
                    session_id=task.session_id,
                )
                await uow.commit()
            elif conversation.user_id != identity.user_id:
                raise PermissionError(
                    f"Session '{task.session_id}' is not owned by this identity."
                )
        task_metadata = (
            dict(task.input.get("metadata", {}))
            if isinstance(task.input.get("metadata"), dict)
            else {}
        )
        if task.input.get("model"):
            task_metadata["model"] = task.input["model"]
        if task.client_id:
            task_metadata["client_id"] = task.client_id
        execution_context = AgentExecutionContext.create(
            execution_id=execution_id,
            agent_id=agent.name,
            session_id=task.session_id,
            correlation_id=correlation_id,
            identity=identity,
            limits=AgentExecutionLimits(),
            task_id=task.task_id,
            parent_execution_id=parent_execution_id,
            connection_id=task.connection_id,
            agent=agent,
            input=dict(task.input),
            metadata=task_metadata,
        )
        result = await container.agent_execution_supervisor.run(
            execution_context,
            lambda: container.agent_runtime.execute(execution_context),
        )
        return result.model_dump(mode="json")

    async def execute_forked_agent_task(
        task_id,
        request,
        identity,
    ):
        return await execute_forked_agent_task_control_plane(
            container,
            task_id,
            request,
            identity,
        )

    async def execute_retried_agent_task(task_id, request, identity):
        return await execute_retried_agent_task_control_plane(
            container, task_id, request, identity
        )

    async def discard_agent_task_branch(task_id, request, identity):
        result = await container.task_budget_service.discard_branch(
            task_id,
            request.branch_id,
            target_user_id=str(identity.user_id or ""),
        )
        task = await container.agent_durable_store.load_task(task_id)
        branch = await container.agent_durable_store.load_task_branch(
            result.branch_id
        )
        return {
            "task_id": task_id,
            "branch_id": result.branch_id,
            "resolution_state": str(branch.resolution_state),
            "task_status": str(task.status),
            "execution_id": branch.current_execution_id,
        }

    async def adopt_agent_task_branch(task_id, request, identity):
        result = await container.task_budget_service.adopt_branch(
            task_id,
            request.branch_id,
            target_user_id=str(identity.user_id or ""),
        )
        task = await container.agent_durable_store.load_task(task_id)
        branch = await container.agent_durable_store.load_task_branch(
            result.selected_branch_id
        )
        return {
            "task_id": task_id,
            "branch_id": result.selected_branch_id,
            "resolution_state": str(branch.resolution_state),
            "task_status": str(task.status),
            "execution_id": result.selected_execution_id,
        }

    async def aggregate_agent_task_branches(task_id, request, identity):
        return await execute_aggregated_agent_task_control_plane(
            container,
            task_id,
            request,
            identity,
        )

    # Multi-agent HTTP tasks enter the canonical AgentRuntime loop.
    container.multi_agent_coordinator.executor = execute_registered_agent_task
    container.multi_agent_coordinator.fork_executor = (
        execute_forked_agent_task
    )
    container.multi_agent_coordinator.retry_executor = (
        execute_retried_agent_task
    )
    container.multi_agent_coordinator.discard_executor = (
        discard_agent_task_branch
    )
    container.multi_agent_coordinator.adopt_executor = (
        adopt_agent_task_branch
    )
    container.multi_agent_coordinator.aggregate_executor = (
        aggregate_agent_task_branches
    )

    logger.info("AI Runtime Kernel & Runtimes booted successfully.")
    return container


# ==============================================================================
# APPLICATION LIFESPAN & CREATION
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý tập trung toàn bộ vòng đời ứng dụng (Startup & Graceful Shutdown)."""
    logger.info("Starting AI Gateway Application...")

    # 1. Startup Sequence
    config = bootstrap_observability(app.state.bootstrap_config)
    FastAPIInstrumentor.instrument_app(app)

    # Local resources
    cb_manager = CircuitBreakerManager(config=config.circuit_breaker)
    http_client = httpx.AsyncClient(timeout=config.provider.timeout)
    storage_engine, uow_factory = await bootstrap_storage(config)
    eventing_manager = EventingManager(storage_engine=storage_engine)
    security_services = bootstrap_security(config=config, storage_engine=storage_engine, 
                                           uow_factory=uow_factory, cb_manager=cb_manager,
                                           eventing_manager=eventing_manager)
    
    container = await bootstrap_runtime_kernel(
        config=config, storage_engine=storage_engine, uow_factory=uow_factory, 
        http_client=http_client, cb_manager=cb_manager, security_services=security_services, 
        eventing_manager=eventing_manager
    )

    # Chỉ gán duy nhất app.state.container
    app.state.container = container

    try:
        yield  # --- APPLICATION IS RUNNING AND SERVING TRAFFIC ---
    finally:
        # 2. Shutdown Sequence (Dọn dẹp trong try...finally)
        logger.info("Initiating Application Shutdown sequence...")

        if container.agent_execution_supervisor:
            await container.agent_execution_supervisor.quiesce()

        await eventing_manager.quiesce()

        if container.multi_agent_coordinator:
            await container.multi_agent_coordinator.shutdown()

        if container.agent_execution_supervisor:
            await container.agent_execution_supervisor.shutdown()

        if container.runtime_kernel:
            await container.runtime_kernel.shutdown()

        if container.mcp_manager:
            await container.mcp_manager.stop()

        await http_client.aclose()
        await storage_engine.disconnect()

        logger.info("Shutdown sequence completed cleanly.")


def create_app(config: ConfigSchema | None = None) -> FastAPI:
    """Tạo instance FastAPI và đăng ký Middlewares, Routers."""
    if config is None:
        config = ConfigManager().get_instance("se/config/default.yaml").initialize()
    app_instance = FastAPI(
        title=config.gateway.name,
        version=__version__,
        lifespan=lifespan,
    )
    app_instance.state.bootstrap_config = config

    # Middleware Stack
    create_middleware_stack(app_instance, config.auth)

    # Route Registrations: api/v1 is the sole HTTP router surface.
    app_instance.include_router(auth_router.router)
    app_instance.include_router(files_router.router)
    app_instance.include_router(models_router.router)
    app_instance.include_router(chat_router.router)
    app_instance.include_router(embeddings_router.router)
    app_instance.include_router(admin_router.router)
    app_instance.include_router(agent_router.router)
    app_instance.include_router(tool_router.router)
    app_instance.include_router(capability_router.router)
    app_instance.include_router(session_router.router)
    app_instance.include_router(events_router.router)
    app_instance.include_router(multi_agent_router.router)
    app_instance.include_router(health_router.router)

    return app_instance


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "se.src.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
