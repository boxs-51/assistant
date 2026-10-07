from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
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
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_planning import (
    AgentResumePlanningService,
    ResumePlanRejected,
)
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import TaskBudgetService
from se.src.runtimes.agent.waiting_ticket import build_waiting_ticket_payload


USER = "user-tbo3"
AGENT = "agent-tbo3"


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(self.session)
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
            messages=(InferenceMessage(role="user", content="tbo3"),),
        )


class _Allow:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW


class _QuotaDenied(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"{code}: exhausted")


def _canonical_inference_quota_error(code: str) -> RuntimeError:
    from se.src.application.user_inference_quota import (
        UserComputeQuotaExceededError,
        UserCostQuotaExceededError,
        UserInferenceQuotaExceededError,
        UserTokenQuotaExceededError,
    )

    error_types = {
        item.code: item
        for item in (
            UserInferenceQuotaExceededError,
            UserTokenQuotaExceededError,
            UserComputeQuotaExceededError,
            UserCostQuotaExceededError,
        )
    }
    return error_types[code]("exhausted")


class _InferenceQuotaDenied:
    def __init__(self, code: str) -> None:
        self.code = code
        self.admission_calls = 0
        self.provider_dispatches = 0

    async def complete(self, request):
        self.admission_calls += 1
        raise _canonical_inference_quota_error(self.code)


class _InferenceCodeCollision:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        raise _QuotaDenied(self.code)


class _ToolCallingInference:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            message = InferenceMessage(
                role="assistant",
                content="call tool",
                tool_calls=(
                    InferenceToolCall(
                        id="call-tbo3",
                        name="tool.tbo3",
                        arguments={"value": 1},
                    ),
                ),
            )
        else:
            message = InferenceMessage(role="assistant", content="done")
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="test",
            model="test",
        )


class _ToolQuotaDenied:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls = 0

    async def execute_many(self, context, requests, *, max_parallel):
        self.calls += 1
        return [
            ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code=self.code,
                error_message=f"{self.code}: exhausted",
                retryable=False,
            )
            for request in requests
        ]

    def can_continue_server_side(self, capability_id):
        return False


class _PostDispatchQuotaCodeCollision:
    def __init__(self, store, code: str) -> None:
        self.store = store
        self.code = code
        self.calls = 0

    async def execute_many(self, context, requests, *, max_parallel):
        self.calls += 1
        async with self.store.uow_factory() as uow:
            for request in requests:
                uow.session.add(
                    CapabilityInvocationRecord(
                        invocation_id=request.invocation_id,
                        capability_id=request.capability_id,
                        capability_version="1",
                        kind="TOOL",
                        execution_mode="SYNC",
                        idempotency="UNKNOWN",
                        request_fingerprint="post-dispatch-collision",
                        owner_user_id=USER,
                        remote_outcome_state="TERMINAL_COMMITTED",
                        state="FAILED",
                        execution_id=request.execution_id,
                        tool_call_id=request.tool_call_id,
                        attempt=1,
                        max_attempts=1,
                        arguments=dict(request.arguments),
                        error={
                            "error_code": self.code,
                            "error_message": f"{self.code}: remote collision",
                            "retryable": False,
                        },
                    )
                )
            await uow.commit()
        return [
            ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code=self.code,
                error_message=f"{self.code}: remote collision",
                retryable=False,
            )
            for request in requests
        ]

    def can_continue_server_side(self, capability_id):
        return False


class _ConnectionRegistry:
    def get(self, connection_id):
        return SimpleNamespace(
            is_usable=True,
            user_id=USER,
            metadata={"client_id": "client-tbo3"},
        )


class _Capabilities:
    connection_registry = _ConnectionRegistry()


def _identity() -> Identity:
    return Identity(
        user_id=USER,
        auth_type="api_key",
        scopes={"*"},
    )


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=8,
        max_active_executions=4,
        max_active_branches=2,
        max_parallel_agents=2,
        max_total_tool_calls=16,
        max_total_inference_calls=16,
        max_total_tokens=1000,
        max_total_cost_usd="10",
        max_delegation_depth=4,
    )


async def _setup(tmp_path: Path, name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    service = TaskBudgetService(
        factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="tbo3-lane-a"),
    )
    store = DurableAgentStore(factory)
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            name=AGENT,
            goal="TBO-3 resource deferral",
            instruction="Exercise bounded RESOURCE WAITING semantics.",
        )
    )
    coordinator = MultiAgentCoordinator(
        registry,
        durable_store=store,
        task_budget_service=service,
    )
    identity = _identity()
    session = coordinator.create_session(identity, [AGENT])
    task = await coordinator.create_task_async(
        session.session_id,
        AGENT,
        {"prompt": "tbo3"},
        identity,
    )
    return engine, sessions, service, store, coordinator, identity, task


def _runtime(store, service, inference, tool_execution=None) -> AgentRuntime:
    return AgentRuntime(
        context_builder=_Builder(),
        inference=inference,
        tool_execution=tool_execution,
        execution_policy=_Allow(),
        durable_store=store,
        task_budget_service=service,
    )


async def _execute_with_runtime(
    coordinator,
    runtime,
    task,
    identity,
):
    async def executor(
        current_task,
        *,
        identity,
        execution_id,
        correlation_id,
        parent_execution_id=None,
    ):
        context = AgentExecutionContext.create(
            execution_id=execution_id,
            agent_id=current_task.assigned_agent_id,
            session_id=current_task.session_id,
            correlation_id=correlation_id,
            identity=identity,
            limits=AgentExecutionLimits(timeout_seconds=30),
            task_id=current_task.task_id,
            input=current_task.input,
            metadata={"client_id": "client-tbo3"},
            parent_execution_id=parent_execution_id,
        )
        result = await runtime.execute(context)
        return result.model_dump(mode="json")

    return await coordinator.execute_task(
        task.task_id,
        identity,
        executor,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "USER_INFERENCE_QUOTA_EXHAUSTED",
        "USER_TOKEN_QUOTA_EXHAUSTED",
        "USER_COMPUTE_QUOTA_EXHAUSTED",
        "USER_COST_QUOTA_EXHAUSTED",
    ],
)
async def test_tbo3_task_inference_resource_exhaustion_waits_without_dispatch(
    tmp_path,
    code,
):
    engine, _sessions, service, store, coordinator, identity, task = await _setup(
        tmp_path,
        f"{code.lower()}.sqlite",
    )
    inference = _InferenceQuotaDenied(code)
    runtime = _runtime(store, service, inference)
    try:
        execution = await _execute_with_runtime(
            coordinator,
            runtime,
            task,
            identity,
        )

        durable = await store.load_execution(execution.execution_id)
        checkpoint = await store.load_current_checkpoint(execution.execution_id)
        budget = await service.get_budget(task.task_id)
        current_task = coordinator.get_task(task.task_id, identity)

        assert execution.state.value == "WAITING"
        assert durable.state == "WAITING"
        assert durable.wait_reason == "RESOURCE"
        assert durable.result["error_code"] == code
        assert durable.wait_expires_at is None
        assert checkpoint is not None
        assert checkpoint.wait_reason == "RESOURCE"
        assert await store.load_checkpoint_pending_invocations(
            checkpoint.checkpoint_id
        ) == ()
        assert current_task.status.value == "WAITING"
        assert current_task.wait_reasons == ["RESOURCE"]
        assert budget.active_executions == 0
        assert inference.admission_calls == 1
        assert inference.provider_dispatches == 0

        ticket = build_waiting_ticket_payload(durable, checkpoint, ())
        assert ticket["wait_reason"] == "RESOURCE"
        assert ticket["auto_resume_allowed"] is False

        planner = AgentResumePlanningService(store, _Capabilities())
        with pytest.raises(ResumePlanRejected) as raised:
            await planner.build_resume_plan(
                execution.execution_id,
                checkpoint.checkpoint_id,
                target_user_id=USER,
                target_client_id="client-tbo3",
                target_connection_id="conn-tbo3-new",
            )
        assert raised.value.code == "UNSUPPORTED_RESUME_TRIGGER"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo3_total_tool_quota_commits_result_then_waits_resource(tmp_path):
    engine, _sessions, service, store, coordinator, identity, task = await _setup(
        tmp_path,
        "tool-total.sqlite",
    )
    inference = _ToolCallingInference()
    tool = _ToolQuotaDenied("USER_TOOL_QUOTA_EXHAUSTED")
    runtime = _runtime(store, service, inference, tool)
    try:
        execution = await _execute_with_runtime(
            coordinator,
            runtime,
            task,
            identity,
        )

        durable = await store.load_execution(execution.execution_id)
        checkpoint = await store.load_current_checkpoint(execution.execution_id)
        budget = await service.get_budget(task.task_id)
        committed = await store.load_committed_tool_result(
            execution.execution_id,
            "call-tbo3",
        )

        assert execution.state.value == "WAITING"
        assert durable.state == "WAITING"
        assert durable.wait_reason == "RESOURCE"
        assert durable.result["error_code"] == "USER_TOOL_QUOTA_EXHAUSTED"
        assert checkpoint is not None
        assert committed is not None
        assert committed.commit_state == "COMMITTED"
        assert committed.error_code == "USER_TOOL_QUOTA_EXHAUSTED"
        assert await store.load_checkpoint_pending_invocations(
            checkpoint.checkpoint_id
        ) == ()
        assert inference.calls == 1
        assert tool.calls == 1
        assert budget.active_executions == 0

        transcript = await store.load_committed_checkpoint_transcript(
            execution.execution_id,
            checkpoint.checkpoint_id,
        )
        tool_messages = [
            item for item in transcript
            if item.get("role") == "tool"
            and item.get("tool_call_id") == "call-tbo3"
        ]
        assert len(tool_messages) == 1
        assert tool_messages[0]["content"]["error_code"] == (
            "USER_TOOL_QUOTA_EXHAUSTED"
        )

        async with store.uow_factory() as uow:
            release = await uow.agents.get_task_budget_reservation(
                task.task_id,
                "RELEASE_EXECUTION",
                f"{execution.execution_id}:2",
            )
            await uow.commit()
        assert release is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo3_capability_tool_quota_remains_capability_only(tmp_path):
    engine, _sessions, service, store, coordinator, identity, task = await _setup(
        tmp_path,
        "tool-capability.sqlite",
    )
    inference = _ToolCallingInference()
    tool = _ToolQuotaDenied("USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED")
    runtime = _runtime(store, service, inference, tool)
    try:
        execution = await _execute_with_runtime(
            coordinator,
            runtime,
            task,
            identity,
        )

        durable = await store.load_execution(execution.execution_id)
        committed = await store.load_committed_tool_result(
            execution.execution_id,
            "call-tbo3",
        )
        current_task = coordinator.get_task(task.task_id, identity)
        budget = await service.get_budget(task.task_id)

        assert execution.state.value == "COMPLETED"
        assert durable.state == "COMPLETED"
        assert durable.wait_reason is None
        assert durable.current_checkpoint_id is None
        assert current_task.status.value == "COMPLETED"
        assert current_task.wait_reasons == []
        assert committed is not None
        assert committed.commit_state == "COMMITTED"
        assert committed.error_code == "USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED"
        assert inference.calls == 2
        assert tool.calls == 1
        assert budget.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo3_inference_code_collision_is_not_resource_provenance(tmp_path):
    engine, _sessions, service, store, coordinator, identity, task = await _setup(
        tmp_path,
        "inference-code-collision.sqlite",
    )
    inference = _InferenceCodeCollision("USER_INFERENCE_QUOTA_EXHAUSTED")
    runtime = _runtime(store, service, inference)
    try:
        execution = await _execute_with_runtime(
            coordinator,
            runtime,
            task,
            identity,
        )
        durable = await store.load_execution(execution.execution_id)
        current_task = coordinator.get_task(task.task_id, identity)

        assert execution.state.value == "FAILED"
        assert durable.state == "FAILED"
        assert durable.wait_reason is None
        assert durable.current_checkpoint_id is None
        assert current_task.status.value == "FAILED"
        assert inference.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo3_post_dispatch_tool_code_collision_does_not_resource_defer(
    tmp_path,
):
    engine, _sessions, service, store, coordinator, identity, task = await _setup(
        tmp_path,
        "tool-post-dispatch-code-collision.sqlite",
    )
    inference = _ToolCallingInference()
    tool = _PostDispatchQuotaCodeCollision(
        store,
        "USER_TOOL_QUOTA_EXHAUSTED",
    )
    runtime = _runtime(store, service, inference, tool)
    try:
        execution = await _execute_with_runtime(
            coordinator,
            runtime,
            task,
            identity,
        )

        durable = await store.load_execution(execution.execution_id)
        committed = await store.load_committed_tool_result(
            execution.execution_id,
            "call-tbo3",
        )
        current_task = coordinator.get_task(task.task_id, identity)

        assert execution.state.value == "COMPLETED"
        assert durable.state == "COMPLETED"
        assert durable.wait_reason is None
        assert durable.current_checkpoint_id is None
        assert current_task.status.value == "COMPLETED"
        assert committed is not None
        assert committed.commit_state == "COMMITTED"
        assert committed.error_code == "USER_TOOL_QUOTA_EXHAUSTED"
        assert committed.extra_metadata["r7_commit_authority"] == (
            "CAPABILITY_INVOCATION"
        )
        assert inference.calls == 2
        assert tool.calls == 1
    finally:
        await engine.dispose()
