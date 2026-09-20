from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.agent.adapters.policy import (
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentExecutionContext,
    AgentLoopState,
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
    InferenceUsage,
)
from se.src.runtimes.agent.ids import AgentExecutionIdFactory
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.agent_driver import AgentCapabilityDriver
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.runtimes.capability.invocation import CapabilityInvocationLifecycle
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime


PARENT_E1 = "exec-parent"
CHILD_E2 = "exec_child"


class _SqliteUow:
    def __init__(self, sessions) -> None:
        self._sessions = sessions
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self.session = self._sessions()
        self.agents = AgentRepository(self.session)
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


class _ContextBuilder:
    async def build(self, context, request):
        if request.prior_messages:
            messages = tuple(
                InferenceMessage.model_validate(item)
                for item in request.prior_messages
            )
        else:
            messages = (
                InferenceMessage(
                    role="user",
                    content=context.input.get("prompt", ""),
                ),
            )
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=messages,
            tools=(),
            metadata={"agent_id": context.agent_id},
        )


class _NestedInference:
    def __init__(self) -> None:
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)

        if request.execution_id == PARENT_E1 and request.iteration == 1:
            message = InferenceMessage(
                role="assistant",
                tool_calls=(
                    InferenceToolCall(
                        id="tc-parent-agent",
                        name="agent-child",
                        arguments={"prompt": "delegate"},
                    ),
                ),
            )
        elif request.execution_id == CHILD_E2 and request.iteration == 1:
            message = InferenceMessage(
                role="assistant",
                tool_calls=(
                    InferenceToolCall(
                        id="tc-child-echo",
                        name="tool.echo",
                        arguments={"value": "child-value"},
                    ),
                ),
            )
        elif request.execution_id == CHILD_E2 and request.iteration == 2:
            message = InferenceMessage(
                role="assistant",
                content="child-complete",
            )
        elif request.execution_id == PARENT_E1 and request.iteration == 2:
            message = InferenceMessage(
                role="assistant",
                content="parent-complete",
            )
        else:
            raise AssertionError(
                f"Unexpected inference turn: "
                f"{request.execution_id}@{request.iteration}"
            )

        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            provider="r3-test",
            model="r3-test",
            usage=InferenceUsage(total_tokens=1),
        )


def _identity() -> Identity:
    return Identity(
        user_id="user-r3-d2",
        auth_type="api_key",
        scopes={"*"},
    )


@pytest.mark.asyncio
async def test_r3_real_sqlite_nested_agent_graph_survives_reconstruction(tmp_path: Path):
    db_path = tmp_path / "r3_nested.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = lambda: _SqliteUow(sessions)
    durable_store = DurableAgentStore(uow_factory)
    invocation_store = SqlCapabilityInvocationStore(uow_factory)

    capability_registry = CapabilityRegistry()
    authorization = AuthorizationService()
    capability_runtime = CapabilityRuntime(
        registry=capability_registry,
        authorization=authorization,
        invocation_lifecycle=CapabilityInvocationLifecycle(invocation_store),
    )

    parent_agent = AgentDefinition(
        name="agent-parent",
        goal="parent",
        instruction="delegate",
        tools=["agent-child"],
    )
    child_agent = AgentDefinition(
        name="agent-child",
        goal="child",
        instruction="use echo",
        tools=["tool.echo"],
    )
    agents = AgentRegistry()
    agents.register(parent_agent)
    agents.register(child_agent)

    tool_policy = RegistryAgentToolPolicy(
        agents,
        capability_registry,
        authorization,
    )
    execution_policy = DefaultAgentExecutionPolicy()
    tool_port = CapabilityToolExecutionAdapter(
        capability_runtime,
        tool_policy,
        execution_policy,
    )
    inference = _NestedInference()
    agent_runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=inference,
        tool_execution=tool_port,
        execution_policy=execution_policy,
        durable_store=durable_store,
    )

    child_definition = CapabilityDefinition(
        id=child_agent.name,
        name=child_agent.name,
        description=child_agent.goal,
        input_schema={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
        kind=CapabilityKind.AGENT,
        execution_kind="AGENT",
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
    )
    capability_runtime.register_capability(
        AgentCapabilityDriver(
            child_definition,
            child_agent,
            agent_runtime,
            execution_id_factory=AgentExecutionIdFactory(
                prefix="exec_",
                token_factory=lambda: "child",
            ),
        )
    )

    echo_definition = CapabilityDefinition(
        id="tool.echo",
        name="tool.echo",
        description="echo",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )
    capability_runtime.register_capability(
        PythonCapabilityDriver(
            echo_definition,
            lambda value: {"echo": value},
        )
    )

    parent_context = AgentExecutionContext.create(
        execution_id=PARENT_E1,
        agent_id=parent_agent.name,
        session_id="session-r3-d2",
        correlation_id="corr-r3-d2",
        identity=_identity(),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=4,
            max_parallel_tools=1,
            timeout_seconds=10,
            inference_timeout_seconds=2,
            tool_timeout_seconds=5,
        ),
        request_id="request-r3-d2",
        task_id="task-r3-d2",
        branch_id="branch-r3-d2",
        workflow_id="workflow-r3-d2",
        trace_id="trace-r3-d2",
        agent=parent_agent,
        input={"prompt": "start"},
    )

    try:
        result = await agent_runtime.execute(parent_context)

        assert result.state is AgentLoopState.COMPLETED
        assert result.execution_id == PARENT_E1
        assert result.output == "parent-complete"

        async with sessions() as session:
            executions = list(
                (
                    await session.execute(
                        select(AgentExecutionRecord).where(
                            AgentExecutionRecord.id.in_([PARENT_E1, CHILD_E2])
                        )
                    )
                ).scalars()
            )
            by_execution = {item.id: item for item in executions}
            assert set(by_execution) == {PARENT_E1, CHILD_E2}

            parent_row = by_execution[PARENT_E1]
            child_row = by_execution[CHILD_E2]

            assert parent_row.parent_execution_id is None
            assert child_row.parent_execution_id == PARENT_E1
            assert child_row.task_id == parent_row.task_id == "task-r3-d2"
            assert child_row.branch_id == parent_row.branch_id == "branch-r3-d2"
            assert (
                child_row.correlation_id
                == parent_row.correlation_id
                == "corr-r3-d2"
            )
            assert child_row.context_state["causation_id"]
            parent_invocation_id = child_row.context_state["causation_id"]
            assert child_row.context_state["trace_id"] == "trace-r3-d2"
            assert child_row.context_state["request_id"] == "request-r3-d2"
            assert child_row.context_state["workflow_id"] == "workflow-r3-d2"

            invocation = (
                await session.execute(
                    select(CapabilityInvocationRecord).where(
                        CapabilityInvocationRecord.invocation_id
                        == parent_invocation_id
                    )
                )
            ).scalar_one()
            assert invocation.execution_id == PARENT_E1
            assert invocation.capability_id == "agent-child"
            assert invocation.correlation_id == "corr-r3-d2"
            assert invocation.trace_id == "trace-r3-d2"

            iterations = list(
                (
                    await session.execute(
                        select(AgentIterationRecord).where(
                            AgentIterationRecord.execution_id.in_(
                                [PARENT_E1, CHILD_E2]
                            )
                        )
                    )
                ).scalars()
            )
            assert {
                (item.execution_id, item.iteration)
                for item in iterations
            } == {
                (PARENT_E1, 1),
                (PARENT_E1, 2),
                (CHILD_E2, 1),
                (CHILD_E2, 2),
            }

            tool_calls = list(
                (
                    await session.execute(
                        select(AgentToolCallRecord).where(
                            AgentToolCallRecord.execution_id.in_(
                                [PARENT_E1, CHILD_E2]
                            )
                        )
                    )
                ).scalars()
            )
            assert {
                (item.execution_id, item.tool_call_id, item.capability_id)
                for item in tool_calls
            } == {
                (PARENT_E1, "tc-parent-agent", "agent-child"),
                (CHILD_E2, "tc-child-echo", "tool.echo"),
            }

            tool_results = list(
                (
                    await session.execute(
                        select(AgentToolResultRecord).where(
                            AgentToolResultRecord.execution_id.in_(
                                [PARENT_E1, CHILD_E2]
                            )
                        )
                    )
                ).scalars()
            )
            assert {
                (item.execution_id, item.tool_call_id, item.capability_id)
                for item in tool_results
            } == {
                (PARENT_E1, "tc-parent-agent", "agent-child"),
                (CHILD_E2, "tc-child-echo", "tool.echo"),
            }

        restarted_store = DurableAgentStore(uow_factory)
        restored_child = await restarted_store.resume_execution(
            CHILD_E2,
            identity=_identity(),
            agent=child_agent,
        )

        assert restored_child is not None
        assert restored_child.execution_id == CHILD_E2
        assert restored_child.parent_execution_id == PARENT_E1
        assert restored_child.task_id == "task-r3-d2"
        assert restored_child.branch_id == "branch-r3-d2"
        assert restored_child.correlation_id == "corr-r3-d2"
        assert restored_child.trace_id == "trace-r3-d2"
        assert restored_child.causation_id == parent_invocation_id
        assert restored_child.request_id == "request-r3-d2"
        assert restored_child.workflow_id == "workflow-r3-d2"
        assert restored_child.retry_of_execution_id is None
        assert restored_child.base_execution_id is None
        assert restored_child.base_checkpoint_id is None
    finally:
        await engine.dispose()
