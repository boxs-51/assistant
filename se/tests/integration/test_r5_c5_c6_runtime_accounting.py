from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentExecutionContext,
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
    InferenceUsage,
    PolicyDecision,
    ToolExecutionResult,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import (
    TaskBudgetService,
)


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


class _Builder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(
                InferenceMessage(role="user", content="hello"),
            ),
        )


class _Inference:
    def __init__(self, budget_service, task_id):
        self.calls = 0
        self.budget_service = budget_service
        self.task_id = task_id

    async def complete(self, request):
        self.calls += 1
        budget = await self.budget_service.get_budget(self.task_id)
        assert budget.used_inference_calls == self.calls
        if self.calls == 1:
            message = InferenceMessage(
                role="assistant",
                content="use tool",
                tool_calls=(
                    InferenceToolCall(
                        id="tool-call-stable",
                        name="tool.echo",
                        arguments={"value": 1},
                    ),
                ),
            )
            usage = InferenceUsage(
                prompt_tokens=2,
                completion_tokens=1,
                total_tokens=3,
                estimated_cost_usd=0.10,
            )
        else:
            message = InferenceMessage(
                role="assistant",
                content="done",
            )
            usage = InferenceUsage(
                prompt_tokens=3,
                completion_tokens=1,
                total_tokens=4,
                estimated_cost_usd=0.20,
            )
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            finish_reason="stop",
            usage=usage,
            provider="test",
            model="test",
        )


class _Tool:
    def __init__(self, budget_service, task_id):
        self.budget_service = budget_service
        self.task_id = task_id

    async def execute_many(self, context, requests, *, max_parallel):
        budget = await self.budget_service.get_budget(self.task_id)
        assert budget.used_tool_calls == len(requests)
        return [
            ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=True,
                output={"ok": True},
            )
            for request in requests
        ]

    def can_continue_server_side(self, capability_id):
        return False


class _Allow:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW


async def _mark_running(service: TaskBudgetService, task_id: str):
    await service.transition_task(
        task_id,
        allowed_source_states=("ASSIGNED",),
        target_state="RUNNING",
        values={},
    )


async def _setup(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r5c-runtime.sqlite').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = lambda: _Uow(sessions)
    limits = TaskBudgetLimits(
        max_total_executions=4,
        max_active_executions=2,
        max_active_branches=2,
        max_parallel_agents=1,
        max_total_tool_calls=4,
        max_total_inference_calls=4,
        max_total_tokens=100,
        max_total_cost_usd="2",
        max_delegation_depth=4,
    )
    service = TaskBudgetService(
        uow_factory,
        default_limits=limits,
        default_policy=TaskBudgetPolicy(version="r5-c-runtime"),
    )
    store = DurableAgentStore(uow_factory)
    return engine, sessions, service, store


@pytest.mark.asyncio
async def test_r5_c5_c6_runtime_charges_logical_inference_tool_and_usage(
    tmp_path,
):
    engine, sessions, service, store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            {
                "id": "task-runtime",
                "session_id": "session-runtime",
                "created_by": "user-runtime",
                "assigned_agent_id": "agent-runtime",
                "revision": 0,
                "status": "ASSIGNED",
                "wait_reasons": [],
                "input": {"prompt": "hello"},
            }
        )
        await _mark_running(service, "task-runtime")
        runtime = AgentRuntime(
            context_builder=_Builder(),
            inference=_Inference(service, "task-runtime"),
            tool_execution=_Tool(service, "task-runtime"),
            execution_policy=_Allow(),
            durable_store=store,
            task_budget_service=service,
        )
        context = AgentExecutionContext.create(
            execution_id="exec-runtime",
            agent_id="agent-runtime",
            session_id="session-runtime",
            correlation_id="corr-runtime",
            identity=Identity(
                user_id="user-runtime",
                auth_type="api_key",
                scopes={"*"},
            ),
            limits=AgentExecutionLimits(timeout_seconds=10),
            task_id="task-runtime",
            input={"prompt": "hello"},
        )

        result = await runtime.execute(context)
        assert result.state.value == "COMPLETED"

        budget = await service.get_budget("task-runtime")
        assert budget.used_executions == 1
        assert budget.active_executions == 0
        assert budget.used_inference_calls == 2
        assert budget.used_tool_calls == 1
        assert budget.used_tokens == 7
        assert budget.used_cost_usd == Decimal("0.30000000")

        async with sessions() as session:
            execution = await AgentRepository(session).get_execution(
                "exec-runtime"
            )
            assert execution.state == "COMPLETED"
            assert execution.revision == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_c5_failed_provider_dispatch_keeps_inference_reservation(
    tmp_path,
):
    class FailingInference:
        def __init__(self, budget_service):
            self.budget_service = budget_service

        async def complete(self, request):
            budget = await self.budget_service.get_budget("task-failed-inf")
            assert budget.used_inference_calls == 1
            raise RuntimeError("provider failed")

    engine, _sessions, service, store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            {
                "id": "task-failed-inf",
                "session_id": "session-failed-inf",
                "created_by": "user-failed-inf",
                "assigned_agent_id": "agent-failed-inf",
                "revision": 0,
                "status": "ASSIGNED",
                "wait_reasons": [],
                "input": {"prompt": "hello"},
            }
        )
        await _mark_running(service, "task-failed-inf")
        runtime = AgentRuntime(
            context_builder=_Builder(),
            inference=FailingInference(service),
            tool_execution=None,
            execution_policy=_Allow(),
            durable_store=store,
            task_budget_service=service,
        )
        context = AgentExecutionContext.create(
            execution_id="exec-failed-inf",
            agent_id="agent-failed-inf",
            session_id="session-failed-inf",
            correlation_id="corr-failed-inf",
            identity=Identity(
                user_id="user-failed-inf",
                auth_type="api_key",
                scopes={"*"},
            ),
            limits=AgentExecutionLimits(timeout_seconds=10),
            task_id="task-failed-inf",
            input={"prompt": "hello"},
        )

        result = await runtime.execute(context)
        assert result.state.value == "FAILED"
        budget = await service.get_budget("task-failed-inf")
        assert budget.used_inference_calls == 1
        assert budget.used_tokens == 0
        assert budget.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_c6_tool_batch_replay_is_idempotent_and_payload_safe(
    tmp_path,
):
    engine, _sessions, service, _store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            {
                "id": "task-tools",
                "session_id": "session-tools",
                "created_by": "user-tools",
                "assigned_agent_id": "agent-tools",
                "revision": 0,
                "status": "ASSIGNED",
                "wait_reasons": [],
                "input": {},
            }
        )
        calls = [
            {
                "execution_id": "exec-tools",
                "tool_call_id": "tool-stable",
                "capability_id": "tool.echo",
                "arguments": {"x": 1},
            }
        ]
        first = await service.reserve_tool_call_batch(
            "task-tools",
            calls,
        )
        second = await service.reserve_tool_call_batch(
            "task-tools",
            calls,
        )
        assert first.used_tool_calls == second.used_tool_calls == 1

        with pytest.raises(Exception, match="different payload"):
            await service.reserve_tool_call_batch(
                "task-tools",
                [
                    {
                        **calls[0],
                        "arguments": {"x": 2},
                    }
                ],
            )
    finally:
        await engine.dispose()
