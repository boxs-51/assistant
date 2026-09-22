from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import CapabilityRuntime as ClientCapabilityRuntime
from cl.src.core.client_invocation_ledger import ClientInvocationLedger
from cl.src.core.client_runtime import ClientRuntime
from cl.src.core.realtime_client import GatewayRealtimeClient

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.infrastructure.storage.models.sql.agent import (
    AgentResumeClaimRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.chat_data.session import (
    Session as ChatSessionRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.supervisor import AgentExecutionSupervisor
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.invocation import CapabilityInvocationLifecycle
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import (
    get_websocket_identity,
)
from se.src.transport.gateway.dependencies import get_container


USER_ID = "r7j-user"
SESSION_ID = "r7j-session"
CLIENT_ID = "r7j-client"
EXECUTION_ID = "r7j-execution"
AGENT_ID = "r7j-agent"
CAPABILITY_ID = "desktop.r7j"
K1 = "r7j-k1"


class _SqliteUow:
    def __init__(self, sessions):
        self._sessions = sessions
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self.session = self._sessions()
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(
            self.session
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


class _AllowExecutionPolicy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW

    def check_tool_call(self, context, request):
        return PolicyDecision.ALLOW


class _AllowToolPolicy:
    def is_visible(self, *, agent_id, capability_id):
        return capability_id == CAPABILITY_ID

    def authorize(self, *, identity, agent_id, capability_id):
        return (
            PolicyDecision.ALLOW
            if capability_id == CAPABILITY_ID
            else PolicyDecision.DENY
        )


class _ContextBuilder:
    async def build(self, context, request):
        return SimpleNamespace(
            messages=(
                InferenceMessage(
                    role="user",
                    content="Run the durable R7-J remote tool.",
                ),
            ),
            tools=(),
            metadata={},
        )


class _DeterministicInference:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if request.iteration == 1:
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=InferenceMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": "call-r7j-1",
                            "name": CAPABILITY_ID,
                            "arguments": {"value": "r7-j"},
                        }
                    ],
                ),
                finish_reason="tool_calls",
                usage=InferenceUsage(),
                provider="r7j-test",
                model=request.model or "r7j-test",
                metadata={},
            )
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role="assistant",
                content="r7-j-complete",
                tool_calls=[],
            ),
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="r7j-test",
            model=request.model or "r7j-test",
            metadata={},
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_async(predicate, *, timeout=12.0, interval=0.02):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        last = value
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError(f"Timed out waiting for R7-J condition; last={last!r}")


async def _start_gateway(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=2.0,
        )
    )
    task = asyncio.create_task(
        server.serve(),
        name="r7-j-real-tcp-gateway",
    )
    await _wait_async(lambda: server.started or task.done(), timeout=10.0)
    if task.done():
        await task
        raise AssertionError("R7-J gateway exited before startup")
    return server, task, port


async def _stop_gateway(server, task):
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    server.should_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        return
    except asyncio.TimeoutError:
        server.force_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _client_registry(tool):
    return SimpleNamespace(
        settings={"loaded": True},
        tools={
            CAPABILITY_ID: {
                "func": tool,
                "metadata": {
                    "name": CAPABILITY_ID,
                    "description": "R7-J real-TCP durable reconnect tool",
                    "version": "1.0",
                    "idempotency": "IDEMPOTENT",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                        "required": ["value"],
                    },
                },
            }
        },
    )


async def _connect_generation(
    port: int,
    connection_id: str,
    registry,
    ledger_path: Path,
):
    holder = {}
    realtime = GatewayRealtimeClient(
        f"http://127.0.0.1:{port}",
        {"Authorization": "Bearer r7-j"},
        connection_id=connection_id,
        session_id=SESSION_ID,
        client_id=CLIENT_ID,
        heartbeat_interval=60.0,
        on_message=lambda envelope: holder["capabilities"].handle_message(
            envelope
        ),
    )
    dispatcher = CapabilityDispatcher(
        registry,
        realtime,
        invocation_ledger=ClientInvocationLedger(ledger_path),
        client_id=CLIENT_ID,
        principal_id=USER_ID,
    )
    capabilities = ClientCapabilityRuntime(
        registry,
        realtime,
        client_id=CLIENT_ID,
        owner_id=USER_ID,
        dispatcher=dispatcher,
    )
    holder["capabilities"] = capabilities
    registered = await asyncio.to_thread(
        realtime.connect,
        wait_timeout=5.0,
    )
    assert registered["type"] == "connection.registered"
    capability_registered = await asyncio.to_thread(
        capabilities.register,
        timeout=5.0,
    )
    assert capability_registered["type"] == "capability.registered"
    return SimpleNamespace(
        realtime=realtime,
        dispatcher=dispatcher,
        capabilities=capabilities,
        connection_id=connection_id,
    )


async def _connect_k1(port: int, registry, ledger_path: Path):
    return await _connect_generation(
        port,
        K1,
        registry,
        ledger_path,
    )


async def _close_k1(generation):
    if generation is None:
        return
    await asyncio.to_thread(generation.realtime.close)
    generation.dispatcher.shutdown()


async def _build_server(
    tmp_path,
    *,
    db_path: Path | None = None,
    initialize_schema: bool = True,
    seed_session: bool = True,
):
    db_path = db_path or (tmp_path / "r7-j-server.sqlite3")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"timeout": 5},
    )
    if initialize_schema:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = lambda: _SqliteUow(sessions)
    durable_store = DurableAgentStore(uow_factory)

    if seed_session:
        async with sessions() as session:
            session.add(
                ChatSessionRecord(
                    id=SESSION_ID,
                    user_id=USER_ID,
                    organization_id=None,
                    status="active",
                )
            )
            await session.commit()

    catalog = CapabilityCatalog()
    registration = ClientCapabilityRegistrationService(catalog, None)
    connections = ConnectionRuntime(registration)
    registration.connections = connections.registry

    invocation_store = SqlCapabilityInvocationStore(uow_factory)
    capability_runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(
            connection_availability=connections.registry,
        ),
        connection_registry=connections.registry,
        realtime=connections.realtime,
        invocation_lifecycle=CapabilityInvocationLifecycle(
            invocation_store
        ),
    )

    agent_registry = AgentRegistry()
    agent = AgentDefinition(
        name=AGENT_ID,
        goal="Prove durable reconnect/resume.",
        instruction="Use desktop.r7j once, then finish.",
        tools=[CAPABILITY_ID],
    )
    agent_registry.register(agent)

    inference = _DeterministicInference()
    execution_policy = _AllowExecutionPolicy()
    tool_execution = AgentToolExecutionCoordinator(
        CapabilityToolExecutionAdapter(
            capability_runtime,
            _AllowToolPolicy(),
            execution_policy,
        )
    )
    agent_runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=inference,
        tool_execution=tool_execution,
        execution_policy=execution_policy,
        durable_store=durable_store,
    )
    supervisor = AgentExecutionSupervisor()

    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(
            ws_manager=WebSocketConnectionManager(),
        ),
        connection_runtime=connections,
        agent_durable_store=durable_store,
        resume_planning_service=AgentResumePlanningService(
            durable_store,
            capability_runtime,
        ),
        agent_execution_supervisor=supervisor,
        agent_runtime=agent_runtime,
        agent_registry=agent_registry,
    )
    container.require = lambda key: getattr(container, key)

    identity = Identity(
        user_id=USER_ID,
        session_id=SESSION_ID,
        auth_type="jwt",
        scopes={"*"},
    )

    app = FastAPI()
    app.include_router(events_router.router)
    app.state.container = container
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_websocket_identity] = lambda: identity

    return SimpleNamespace(
        db_path=db_path,
        engine=engine,
        sessions=sessions,
        durable_store=durable_store,
        invocation_store=invocation_store,
        capability_runtime=capability_runtime,
        connections=connections,
        supervisor=supervisor,
        agent_runtime=agent_runtime,
        inference=inference,
        agent=agent,
        identity=identity,
        app=app,
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r7_j_real_tcp_k1_disconnect_k2_auto_resume_same_execution(
    tmp_path,
):
    server_state = await _build_server(tmp_path)
    server, server_task, port = await _start_gateway(server_state.app)

    k1_started = threading.Event()
    k1_release = threading.Event()
    external_effects = []

    def k1_tool(value: str, **kwargs):
        k1_started.set()
        k1_release.wait(timeout=15.0)
        return {"source": "k1-late", "value": value}

    def k2_tool(value: str, **kwargs):
        external_effects.append(
            {
                "value": value,
                "connection_id": kwargs.get("connection_id"),
            }
        )
        return {
            "source": "k2",
            "value": value,
            "effect_count": len(external_effects),
        }

    ledger_path = tmp_path / "r7-j-client-ledger.sqlite3"
    k1 = None
    k2 = None
    try:
        k1 = await _connect_k1(
            port,
            _client_registry(k1_tool),
            ledger_path,
        )

        context = AgentExecutionContext.create(
            execution_id=EXECUTION_ID,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            correlation_id="r7j-correlation",
            identity=server_state.identity,
            limits=AgentExecutionLimits(
                max_iterations=4,
                max_tool_calls=4,
                timeout_seconds=30,
            ),
            connection_id=K1,
            agent=server_state.agent,
            metadata={
                "client_id": CLIENT_ID,
                "model": "r7j-test",
            },
        )

        first_run = asyncio.create_task(
            server_state.supervisor.run(
                context,
                lambda: server_state.agent_runtime.execute(context),
            ),
            name="r7-j-initial-execution",
        )

        assert await asyncio.to_thread(k1_started.wait, 5.0)
        await asyncio.to_thread(k1.realtime.close)

        first_result = await asyncio.wait_for(first_run, timeout=10.0)
        assert first_result.state.value == "WAITING"
        assert first_result.error_code == "WAITING_FOR_CONNECTION"
        assert first_result.execution_id == EXECUTION_ID
        assert first_result.checkpoint_id

        waiting_execution = await server_state.durable_store.load_execution(
            EXECUTION_ID
        )
        checkpoint = await server_state.durable_store.load_current_checkpoint(
            EXECUTION_ID
        )
        assert waiting_execution.state == "WAITING"
        assert waiting_execution.wait_reason == "CONNECTION"
        assert checkpoint is not None
        assert checkpoint.checkpoint_id == first_result.checkpoint_id
        assert checkpoint.execution_revision == waiting_execution.revision
        assert checkpoint.origin_connection_id == K1
        assert checkpoint.origin_client_id == CLIENT_ID

        pending = (
            await server_state.durable_store.load_checkpoint_pending_invocations(
                checkpoint.checkpoint_id
            )
        )
        assert len(pending) == 1
        invocation_id = pending[0].invocation_id
        invocation_before = await server_state.invocation_store.get(
            invocation_id
        )
        assert invocation_before is not None
        original_fingerprint = invocation_before.request_fingerprint
        assert invocation_before.execution_id == EXECUTION_ID
        assert invocation_before.tool_call_id == "call-r7j-1"
        assert invocation_before.state.value == "WAITING"
        assert invocation_before.remote_outcome_state.value == "OUTCOME_UNKNOWN"

        # Finish the K1 process-death simulation deterministically. Resetting
        # the dispatcher bumps its epoch before the blocked local worker is
        # released, so that late completion cannot mutate the durable client
        # ledger from RUNNING to TERMINAL after the socket is already gone.
        await _close_k1(k1)
        k1 = None
        k1_release.set()

        # K2 is a fresh process-style ClientRuntime generation using the same
        # stable installation identity and durable client invocation ledger.
        k2 = ClientRuntime(
            f"http://127.0.0.1:{port}",
            _client_registry(k2_tool),
            api_key="r7-j",
            client_id=CLIENT_ID,
            owner_id=USER_ID,
            invocation_ledger=ClientInvocationLedger(ledger_path),
        )
        await asyncio.to_thread(k2.start)
        k2_connection_id = k2.connection_id
        assert k2_connection_id != K1
        assert k2.client_id == CLIENT_ID

        async def _completed():
            record = await server_state.durable_store.load_execution(
                EXECUTION_ID
            )
            return record if record.state == "COMPLETED" else None

        completed = await _wait_async(_completed, timeout=15.0)
        assert completed.id == EXECUTION_ID
        assert completed.bound_client_id == CLIENT_ID
        assert completed.bound_connection_id == k2_connection_id
        assert completed.current_checkpoint_id == checkpoint.checkpoint_id

        invocation_after = await server_state.invocation_store.get(
            invocation_id
        )
        attempts = await server_state.invocation_store.list_attempts(
            invocation_id
        )
        assert invocation_after is not None
        assert invocation_after.invocation_id == invocation_id
        assert invocation_after.request_fingerprint == original_fingerprint
        assert invocation_after.state.value == "COMPLETED"
        assert invocation_after.remote_outcome_state.value == "TERMINAL_COMMITTED"
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert attempts[0].connection_id == K1
        assert attempts[1].connection_id == k2_connection_id

        assert external_effects == [
            {
                "value": "r7-j",
                "connection_id": k2_connection_id,
            }
        ]

        committed = (
            await server_state.durable_store.load_committed_tool_result(
                EXECUTION_ID,
                "call-r7j-1",
            )
        )
        assert committed is not None
        assert committed.invocation_id == invocation_id
        assert committed.commit_state == "COMMITTED"
        assert committed.output == {
            "source": "k2",
            "value": "r7-j",
            "effect_count": 1,
        }

        assert len(server_state.inference.requests) == 2
        second_request = server_state.inference.requests[1]
        serialized = repr(second_request.messages)
        assert "effect_count" in serialized
        assert "REMOTE_OUTCOME_UNKNOWN" not in serialized
        assert "REMOTE_CONNECTION_LOST" not in serialized

        async with server_state.sessions() as session:
            claims = (
                await session.execute(
                    select(AgentResumeClaimRecord).where(
                        AgentResumeClaimRecord.execution_id
                        == EXECUTION_ID
                    )
                )
            ).scalars().all()
        assert len(claims) == 1
        assert claims[0].state == "CONSUMED"
        assert claims[0].checkpoint_id == checkpoint.checkpoint_id
        assert claims[0].client_id == CLIENT_ID
        assert claims[0].connection_id == k2_connection_id
        assert claims[0].consumed_execution_revision == (
            waiting_execution.revision + 1
        )

        assert not server_state.supervisor.is_running(EXECUTION_ID)
        assert server_state.supervisor.active_execution_ids() == ()
        assert (
            await server_state.connections.realtime.multiplexer.pending_count()
            == 0
        )
        assert (
            await server_state.connections.realtime.reconciliation_multiplexer.pending_count()
            == 0
        )
        assert k2.pending_resume_tickets == ()
    finally:
        if k2 is not None:
            await asyncio.to_thread(k2.stop)
        k1_release.set()
        if k1 is not None:
            await _close_k1(k1)
        await server_state.supervisor.shutdown()
        await _stop_gateway(server, server_task)
        await server_state.engine.dispose()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r7_j_server_restart_while_waiting_resumes_from_sql_only(
    tmp_path,
):
    db_path = tmp_path / "r7-j-server-restart.sqlite3"
    ledger_path = tmp_path / "r7-j-restart-client-ledger.sqlite3"

    first_state = await _build_server(
        tmp_path,
        db_path=db_path,
    )
    first_server, first_server_task, first_port = await _start_gateway(
        first_state.app
    )

    k1_started = threading.Event()
    k1_release = threading.Event()
    k1 = None
    k2 = None
    restarted_state = None
    restarted_server = None
    restarted_server_task = None
    external_effects = []

    def k1_tool(value: str, **kwargs):
        k1_started.set()
        k1_release.wait(timeout=15.0)
        return {"source": "k1-late", "value": value}

    def k2_tool(value: str, **kwargs):
        external_effects.append(value)
        return {
            "source": "k2-after-server-restart",
            "value": value,
            "effect_count": len(external_effects),
        }

    try:
        k1 = await _connect_k1(
            first_port,
            _client_registry(k1_tool),
            ledger_path,
        )

        context = AgentExecutionContext.create(
            execution_id=EXECUTION_ID,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            correlation_id="r7j-restart-correlation",
            identity=first_state.identity,
            limits=AgentExecutionLimits(
                max_iterations=4,
                max_tool_calls=4,
                timeout_seconds=30,
            ),
            connection_id=K1,
            agent=first_state.agent,
            metadata={
                "client_id": CLIENT_ID,
                "model": "r7j-test",
            },
        )

        first_run = asyncio.create_task(
            first_state.supervisor.run(
                context,
                lambda: first_state.agent_runtime.execute(context),
            ),
            name="r7-j-restart-initial",
        )
        assert await asyncio.to_thread(k1_started.wait, 5.0)
        await asyncio.to_thread(k1.realtime.close)

        waiting_result = await asyncio.wait_for(first_run, timeout=10.0)
        assert waiting_result.state.value == "WAITING"
        assert waiting_result.checkpoint_id

        waiting_execution = await first_state.durable_store.load_execution(
            EXECUTION_ID
        )
        waiting_checkpoint = (
            await first_state.durable_store.load_current_checkpoint(
                EXECUTION_ID
            )
        )
        pending = (
            await first_state.durable_store.load_checkpoint_pending_invocations(
                waiting_checkpoint.checkpoint_id
            )
        )
        assert len(pending) == 1
        invocation_id = pending[0].invocation_id
        before = await first_state.invocation_store.get(invocation_id)
        assert before is not None
        original_fingerprint = before.request_fingerprint
        assert waiting_execution.state == "WAITING"
        assert waiting_checkpoint.execution_revision == waiting_execution.revision

        # Tear down K1 and the entire server process-local runtime. The client
        # ledger intentionally remains RUNNING so K2 reconciliation sees
        # durable UNKNOWN and may choose REPLAY_SAFE for IDEMPOTENT work.
        await _close_k1(k1)
        k1 = None
        k1_release.set()

        await first_state.supervisor.shutdown()
        await _stop_gateway(first_server, first_server_task)
        await first_state.engine.dispose()

        # New process-local runtime objects, same durable SQL database.
        restarted_state = await _build_server(
            tmp_path,
            db_path=db_path,
            initialize_schema=False,
            seed_session=False,
        )
        assert restarted_state.supervisor is not first_state.supervisor
        assert restarted_state.connections is not first_state.connections
        assert restarted_state.capability_runtime is not first_state.capability_runtime

        restarted_server, restarted_server_task, restarted_port = (
            await _start_gateway(restarted_state.app)
        )

        restarted_waiting = (
            await restarted_state.durable_store.load_execution(EXECUTION_ID)
        )
        restarted_checkpoint = (
            await restarted_state.durable_store.load_current_checkpoint(
                EXECUTION_ID
            )
        )
        assert restarted_waiting.state == "WAITING"
        assert restarted_waiting.revision == waiting_execution.revision
        assert restarted_checkpoint.checkpoint_id == waiting_checkpoint.checkpoint_id
        assert restarted_checkpoint.execution_revision == restarted_waiting.revision

        k2 = ClientRuntime(
            f"http://127.0.0.1:{restarted_port}",
            _client_registry(k2_tool),
            api_key="r7-j",
            client_id=CLIENT_ID,
            owner_id=USER_ID,
            invocation_ledger=ClientInvocationLedger(ledger_path),
        )
        await asyncio.to_thread(k2.start)
        k2_connection_id = k2.connection_id
        assert k2_connection_id != K1

        async def _completed_after_restart():
            record = await restarted_state.durable_store.load_execution(
                EXECUTION_ID
            )
            return record if record.state == "COMPLETED" else None

        completed = await _wait_async(
            _completed_after_restart,
            timeout=15.0,
        )
        assert completed.id == EXECUTION_ID
        assert completed.bound_client_id == CLIENT_ID
        assert completed.bound_connection_id == k2_connection_id

        after = await restarted_state.invocation_store.get(invocation_id)
        attempts = await restarted_state.invocation_store.list_attempts(
            invocation_id
        )
        assert after is not None
        assert after.invocation_id == invocation_id
        assert after.request_fingerprint == original_fingerprint
        assert after.state.value == "COMPLETED"
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert attempts[0].connection_id == K1
        assert attempts[1].connection_id == k2_connection_id
        assert external_effects == ["r7-j"]

        committed = (
            await restarted_state.durable_store.load_committed_tool_result(
                EXECUTION_ID,
                "call-r7j-1",
            )
        )
        assert committed is not None
        assert committed.commit_state == "COMMITTED"
        assert committed.invocation_id == invocation_id
        assert committed.output["effect_count"] == 1

        # The restarted inference service has no in-memory history from K1.
        # It receives only the resumed iteration and must still finish E1.
        assert len(first_state.inference.requests) == 1
        assert len(restarted_state.inference.requests) == 1
        resumed_request = restarted_state.inference.requests[0]
        assert resumed_request.iteration == 2
        serialized = repr(resumed_request.messages)
        assert "k2-after-server-restart" in serialized
        assert "REMOTE_OUTCOME_UNKNOWN" not in serialized

        async with restarted_state.sessions() as session:
            claims = (
                await session.execute(
                    select(AgentResumeClaimRecord).where(
                        AgentResumeClaimRecord.execution_id
                        == EXECUTION_ID
                    )
                )
            ).scalars().all()
        assert len(claims) == 1
        assert claims[0].state == "CONSUMED"
        assert claims[0].connection_id == k2_connection_id

        assert restarted_state.supervisor.active_execution_ids() == ()
        assert (
            await restarted_state.connections.realtime.multiplexer.pending_count()
            == 0
        )
        assert (
            await restarted_state.connections.realtime.reconciliation_multiplexer.pending_count()
            == 0
        )
    finally:
        k1_release.set()
        if k2 is not None:
            await asyncio.to_thread(k2.stop)
        if k1 is not None:
            await _close_k1(k1)

        # Only clean up first process resources if restart did not already do so.
        try:
            await first_state.supervisor.shutdown()
        except Exception:
            pass
        try:
            await _stop_gateway(first_server, first_server_task)
        except Exception:
            pass
        try:
            await first_state.engine.dispose()
        except Exception:
            pass

        if restarted_state is not None:
            await restarted_state.supervisor.shutdown()
        if restarted_server is not None:
            await _stop_gateway(
                restarted_server,
                restarted_server_task,
            )
        if restarted_state is not None:
            await restarted_state.engine.dispose()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r7_j_lost_accepted_ack_retries_same_request_on_k3(
    tmp_path,
    monkeypatch,
):
    server_state = await _build_server(
        tmp_path,
        db_path=tmp_path / "r7-j-lost-ack.sqlite3",
    )

    original_resume = events_router._resume_execution
    original_send = events_router._send_realtime
    resume_requests = []
    accepted_writes = []
    dropped_first_accepted = {"value": False}

    async def recording_resume(
        websocket,
        identity,
        container,
        connection_id,
        envelope,
    ):
        request_id = str(
            (envelope.payload or {}).get("resume_request_id") or ""
        )
        if request_id:
            resume_requests.append((connection_id, request_id))
        return await original_resume(
            websocket,
            identity,
            container,
            connection_id,
            envelope,
        )

    async def drop_first_accepted(websocket, envelope):
        if envelope.type == "execution.resume.accepted":
            payload = dict(envelope.payload or {})
            accepted_writes.append(
                (
                    envelope.connection_id,
                    str(payload.get("resume_request_id") or ""),
                )
            )
            if not dropped_first_accepted["value"]:
                dropped_first_accepted["value"] = True
                # Durable handoff ACCEPTED has already committed before this
                # call. Close the real socket instead of sending the ACK.
                await websocket.close(code=1011)
                return
        await original_send(websocket, envelope)

    monkeypatch.setattr(
        events_router,
        "_resume_execution",
        recording_resume,
    )
    monkeypatch.setattr(
        events_router,
        "_send_realtime",
        drop_first_accepted,
    )

    server, server_task, port = await _start_gateway(server_state.app)

    k1_started = threading.Event()
    k1_release = threading.Event()
    k1 = None
    client = None
    effects = []

    def k1_tool(value: str, **kwargs):
        k1_started.set()
        k1_release.wait(timeout=15.0)
        return {"source": "k1-late", "value": value}

    def resumed_tool(value: str, **kwargs):
        effects.append(value)
        return {
            "source": "resume-client",
            "value": value,
            "effect_count": len(effects),
        }

    try:
        ledger_path = tmp_path / "r7-j-lost-ack-client.sqlite3"
        k1 = await _connect_k1(
            port,
            _client_registry(k1_tool),
            ledger_path,
        )
        context = AgentExecutionContext.create(
            execution_id=EXECUTION_ID,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            correlation_id="r7j-lost-ack-correlation",
            identity=server_state.identity,
            limits=AgentExecutionLimits(
                max_iterations=4,
                max_tool_calls=4,
                timeout_seconds=30,
            ),
            connection_id=K1,
            agent=server_state.agent,
            metadata={
                "client_id": CLIENT_ID,
                "model": "r7j-test",
            },
        )
        first_run = asyncio.create_task(
            server_state.supervisor.run(
                context,
                lambda: server_state.agent_runtime.execute(context),
            )
        )
        assert await asyncio.to_thread(k1_started.wait, 5.0)
        await asyncio.to_thread(k1.realtime.close)
        waiting_result = await asyncio.wait_for(first_run, timeout=10.0)
        assert waiting_result.state.value == "WAITING"

        waiting_execution = await server_state.durable_store.load_execution(
            EXECUTION_ID
        )
        checkpoint = await server_state.durable_store.load_current_checkpoint(
            EXECUTION_ID
        )
        assert checkpoint is not None
        assert checkpoint.execution_revision == waiting_execution.revision

        # Make K1 process death deterministic before K2/K3 recovery.
        await _close_k1(k1)
        k1 = None
        k1_release.set()

        resume_starts = []
        original_start_reserved = server_state.supervisor.start_reserved

        async def recording_start_reserved(
            token,
            resume_context,
            runner,
        ):
            resume_starts.append(
                (
                    resume_context.execution_id,
                    resume_context.connection_id,
                )
            )
            return await original_start_reserved(
                token,
                resume_context,
                runner,
            )

        server_state.supervisor.start_reserved = recording_start_reserved

        client = ClientRuntime(
            f"http://127.0.0.1:{port}",
            _client_registry(resumed_tool),
            api_key="r7-j",
            client_id=CLIENT_ID,
            owner_id=USER_ID,
            invocation_ledger=ClientInvocationLedger(ledger_path),
        )
        await asyncio.to_thread(client.start)

        await _wait_async(
            lambda: len(resume_requests) >= 2,
            timeout=15.0,
        )
        first_connection, first_request_id = resume_requests[0]
        second_connection, second_request_id = resume_requests[1]

        assert dropped_first_accepted["value"] is True
        assert first_connection != second_connection
        assert first_request_id == second_request_id
        assert first_request_id
        assert client.client_id == CLIENT_ID

        async def _completed_after_lost_ack():
            record = await server_state.durable_store.load_execution(
                EXECUTION_ID
            )
            return record if record.state == "COMPLETED" else None

        completed = await _wait_async(
            _completed_after_lost_ack,
            timeout=15.0,
        )
        assert completed.id == EXECUTION_ID

        await _wait_async(
            lambda: client.pending_resume_tickets == (),
            timeout=10.0,
        )
        assert client.connection_id == second_connection
        assert effects == ["r7-j"]

        async with server_state.sessions() as session:
            claims = (
                await session.execute(
                    select(AgentResumeClaimRecord).where(
                        AgentResumeClaimRecord.execution_id
                        == EXECUTION_ID
                    )
                )
            ).scalars().all()
        assert len(claims) == 1
        claim = claims[0]
        assert claim.state == "CONSUMED"
        assert claim.resume_request_id == first_request_id
        assert claim.connection_id == first_connection
        assert claim.consumed_execution_revision == (
            waiting_execution.revision + 1
        )

        # The replayed ACK on K3 must not reserve/start a second runtime task.
        assert len(resume_starts) == 1
        assert resume_starts[0][0] == EXECUTION_ID
        assert resume_starts[0][1] == first_connection

        assert len(accepted_writes) == 2
        assert accepted_writes[0] == (
            first_connection,
            first_request_id,
        )
        assert accepted_writes[1] == (
            second_connection,
            first_request_id,
        )

        assert server_state.supervisor.active_execution_ids() == ()
        assert (
            await server_state.connections.realtime.multiplexer.pending_count()
            == 0
        )
        assert (
            await server_state.connections.realtime.reconciliation_multiplexer.pending_count()
            == 0
        )
    finally:
        k1_release.set()
        if client is not None:
            await asyncio.to_thread(client.stop)
        if k1 is not None:
            await _close_k1(k1)
        await server_state.supervisor.shutdown()
        await _stop_gateway(server, server_task)
        await server_state.engine.dispose()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r7_j_real_tcp_two_resume_requests_have_one_authority_winner(
    tmp_path,
):
    server_state = await _build_server(
        tmp_path,
        db_path=tmp_path / "r7-j-resume-race.sqlite3",
    )
    server, server_task, port = await _start_gateway(server_state.app)

    k1_started = threading.Event()
    k1_release = threading.Event()
    k1 = None
    generation_a = None
    generation_b = None
    effects = []

    def k1_tool(value: str, **kwargs):
        k1_started.set()
        k1_release.wait(timeout=15.0)
        return {"source": "k1-late", "value": value}

    def race_tool(value: str, **kwargs):
        effects.append(
            {
                "value": value,
                "connection_id": kwargs.get("connection_id"),
            }
        )
        return {
            "source": "race-winner",
            "value": value,
            "effect_count": len(effects),
        }

    try:
        ledger_path = tmp_path / "r7-j-race-client.sqlite3"
        k1 = await _connect_k1(
            port,
            _client_registry(k1_tool),
            ledger_path,
        )
        context = AgentExecutionContext.create(
            execution_id=EXECUTION_ID,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            correlation_id="r7j-race-correlation",
            identity=server_state.identity,
            limits=AgentExecutionLimits(
                max_iterations=4,
                max_tool_calls=4,
                timeout_seconds=30,
            ),
            connection_id=K1,
            agent=server_state.agent,
            metadata={
                "client_id": CLIENT_ID,
                "model": "r7j-test",
            },
        )
        first_run = asyncio.create_task(
            server_state.supervisor.run(
                context,
                lambda: server_state.agent_runtime.execute(context),
            )
        )
        assert await asyncio.to_thread(k1_started.wait, 5.0)
        await asyncio.to_thread(k1.realtime.close)
        waiting_result = await asyncio.wait_for(first_run, timeout=10.0)
        assert waiting_result.state.value == "WAITING"

        waiting_execution = await server_state.durable_store.load_execution(
            EXECUTION_ID
        )
        checkpoint = await server_state.durable_store.load_current_checkpoint(
            EXECUTION_ID
        )
        assert checkpoint is not None

        await _close_k1(k1)
        k1 = None
        k1_release.set()

        generation_a = await _connect_generation(
            port,
            "r7j-k2a",
            _client_registry(race_tool),
            ledger_path,
        )
        generation_b = await _connect_generation(
            port,
            "r7j-k2b",
            _client_registry(race_tool),
            ledger_path,
        )

        original_build = (
            server_state.container.resume_planning_service.build_resume_plan
            if hasattr(server_state, "container")
            else None
        )
        planning_service = server_state.app.state.container.resume_planning_service
        original_build = planning_service.build_resume_plan
        plans_ready = []
        release_plans = asyncio.Event()

        async def barrier_build(*args, **kwargs):
            plan = await original_build(*args, **kwargs)
            plans_ready.append(
                (
                    plan.target_connection_id,
                    plan.checkpoint_id,
                    plan.expected_execution_revision,
                )
            )
            if len(plans_ready) >= 2:
                release_plans.set()
            await asyncio.wait_for(release_plans.wait(), timeout=8.0)
            return plan

        planning_service.build_resume_plan = barrier_build

        request_a = "rr-r7j-race-a"
        request_b = "rr-r7j-race-b"
        response_a, response_b = await asyncio.gather(
            asyncio.to_thread(
                generation_a.realtime.resume_execution,
                EXECUTION_ID,
                checkpoint.checkpoint_id,
                request_a,
                timeout=12.0,
            ),
            asyncio.to_thread(
                generation_b.realtime.resume_execution,
                EXECUTION_ID,
                checkpoint.checkpoint_id,
                request_b,
                timeout=12.0,
            ),
        )

        responses = [response_a, response_b]
        accepted = [
            item
            for item in responses
            if item["type"] == "execution.resume.accepted"
        ]
        rejected = [
            item
            for item in responses
            if item["type"] == "execution.resume.rejected"
        ]
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert rejected[0]["payload"]["code"] == "RESUME_CONFLICT"
        assert rejected[0]["payload"]["retryable"] is True
        assert len(plans_ready) == 2
        assert plans_ready[0][0] != plans_ready[1][0]
        assert plans_ready[0][1] == plans_ready[1][1] == checkpoint.checkpoint_id
        assert (
            plans_ready[0][2]
            == plans_ready[1][2]
            == waiting_execution.revision
        )

        winner_connection = accepted[0]["connection_id"]
        winner_request_id = accepted[0]["payload"]["resume_request_id"]
        assert winner_connection in {"r7j-k2a", "r7j-k2b"}
        assert winner_request_id in {request_a, request_b}

        async def _completed_after_race():
            record = await server_state.durable_store.load_execution(
                EXECUTION_ID
            )
            return record if record.state == "COMPLETED" else None

        completed = await _wait_async(
            _completed_after_race,
            timeout=15.0,
        )
        assert completed.id == EXECUTION_ID
        assert completed.bound_connection_id == winner_connection
        assert completed.bound_client_id == CLIENT_ID

        assert len(effects) == 1
        assert effects[0]["connection_id"] == winner_connection

        async with server_state.sessions() as session:
            claims = (
                await session.execute(
                    select(AgentResumeClaimRecord).where(
                        AgentResumeClaimRecord.execution_id
                        == EXECUTION_ID
                    )
                )
            ).scalars().all()
        assert len(claims) == 1
        assert claims[0].state == "CONSUMED"
        assert claims[0].resume_request_id == winner_request_id
        assert claims[0].connection_id == winner_connection
        assert claims[0].consumed_execution_revision == (
            waiting_execution.revision + 1
        )

        assert server_state.supervisor.active_execution_ids() == ()
        assert (
            await server_state.connections.realtime.multiplexer.pending_count()
            == 0
        )
        assert (
            await server_state.connections.realtime.reconciliation_multiplexer.pending_count()
            == 0
        )
    finally:
        k1_release.set()
        if generation_a is not None:
            await _close_k1(generation_a)
        if generation_b is not None:
            await _close_k1(generation_b)
        if k1 is not None:
            await _close_k1(k1)
        await server_state.supervisor.shutdown()
        await _stop_gateway(server, server_task)
        await server_state.engine.dispose()
