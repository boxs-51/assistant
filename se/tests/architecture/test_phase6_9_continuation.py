import asyncio

import pytest

from se.src.runtimes.agent.continuation import (
    AgentContinuationService,
    ContinuationAuthorizationError,
    ContinuationConflictError,
)
from se.src.runtimes.agent.contracts.continuation import ContinuationState
from se.src.runtimes.connection.multiplexer import (
    ConnectionMultiplexer,
    RemoteConnectionLost,
)
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.contracts.tool import ToolExecutionResult
from se.src.runtimes.agent.runtime import AgentRuntime


def test_disconnect_fails_each_invocation_with_immutable_identity_and_rejects_late_result():
    async def scenario():
        multiplexer = ConnectionMultiplexer()
        first = await multiplexer.register("inv-1", "conn-1")
        second = await multiplexer.register("inv-2", "conn-1")

        assert await multiplexer.fail_connection("conn-1") == 2

        for invocation_id, future in (("inv-1", first), ("inv-2", second)):
            with pytest.raises(RemoteConnectionLost) as caught:
                await future
            assert caught.value.connection_id == "conn-1"
            assert caught.value.invocation_id == invocation_id

        assert await multiplexer.resolve("inv-1", {"late": True}, "conn-1") is False
        assert await multiplexer.resolve("inv-1", {"foreign": True}, "conn-2") is False

    asyncio.run(scenario())


def test_disconnect_checkpoint_waits_and_reconnect_branch_is_isolated_until_merge():
    async def scenario():
        service = AgentContinuationService()
        checkpoint = await service.checkpoint_disconnect(
            execution_id="exec-1",
            session_id="session-1",
            owner_user_id="user-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="desktop.echo",
            iteration=2,
            transcript=[{"role": "user", "content": "hello"}],
        )
        assert checkpoint.state is ContinuationState.WAITING_FOR_CONNECTION
        assert checkpoint.current_connection_id is None

        branch = await service.reconnect(
            execution_id="exec-1",
            connection_id="conn-2",
            user_id="user-1",
        )
        assert branch.base_checkpoint_id == checkpoint.checkpoint_id
        assert branch.connection_id == "conn-2"
        assert service.current_checkpoint("exec-1") == checkpoint

        merged = await service.confirm_merge(
            execution_id="exec-1",
            branch_id=branch.branch_id,
            user_id="user-1",
        )
        assert merged.parent_checkpoint_id == checkpoint.checkpoint_id
        assert merged.current_connection_id == "conn-2"
        assert merged.state is ContinuationState.RUNNING
        assert await service.confirm_merge(
            execution_id="exec-1",
            branch_id=branch.branch_id,
            user_id="user-1",
        ) is merged

    asyncio.run(scenario())


def test_continuation_state_rehydrates_after_process_restart():
    async def scenario():
        class Persistence:
            def __init__(self):
                self.states = {}

            async def save_continuation_state(self, execution_id, state):
                self.states[execution_id] = state

            async def load_continuation_state(self, execution_id):
                return self.states.get(execution_id)

        persistence = Persistence()
        before_restart = AgentContinuationService(persistence)
        waiting = await before_restart.checkpoint_disconnect(
            execution_id="exec-durable",
            session_id="session-1",
            owner_user_id="user-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="desktop.echo",
            iteration=2,
            transcript=[{"role": "user", "content": "hello"}],
            metadata={"origin_client_id": "client-1"},
        )

        after_restart = AgentContinuationService(persistence)
        restored = await after_restart.ensure_loaded("exec-durable")
        assert restored == waiting
        branch = await after_restart.reconnect(
            execution_id="exec-durable",
            connection_id="conn-2",
            user_id="user-1",
            metadata={"client_id": "client-1"},
        )
        merged = await after_restart.confirm_merge(
            execution_id="exec-durable",
            branch_id=branch.branch_id,
            user_id="user-1",
        )
        assert merged.state is ContinuationState.RUNNING
        assert merged.current_connection_id == "conn-2"

    asyncio.run(scenario())


def test_merge_requires_owner_and_stale_branch_cannot_overwrite_main():
    async def scenario():
        service = AgentContinuationService()
        first = await service.checkpoint_disconnect(
            execution_id="exec-1",
            session_id="session-1",
            owner_user_id="user-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="desktop.echo",
            iteration=1,
            transcript=[],
        )
        branch = await service.reconnect(
            execution_id="exec-1",
            connection_id="conn-2",
            user_id="user-1",
        )
        with pytest.raises(ContinuationAuthorizationError):
            await service.confirm_merge(
                execution_id="exec-1",
                branch_id=branch.branch_id,
                user_id="user-2",
            )

        newer = await service.checkpoint_disconnect(
            execution_id="exec-1",
            session_id="session-1",
            owner_user_id="user-1",
            connection_id="conn-3",
            invocation_id="inv-2",
            tool_call_id="call-2",
            capability_id="desktop.echo",
            iteration=2,
            transcript=[],
        )
        assert newer.parent_checkpoint_id == first.checkpoint_id
        with pytest.raises(ContinuationConflictError, match="Stale"):
            await service.confirm_merge(
                execution_id="exec-1",
                branch_id=branch.branch_id,
                user_id="user-1",
            )

    asyncio.run(scenario())


def test_server_availability_does_not_make_disconnect_replay_safe():
    async def scenario():
        service = AgentContinuationService()
        checkpoint = await service.checkpoint_disconnect(
            execution_id="exec-1",
            session_id="session-1",
            owner_user_id="user-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="hybrid.echo",
            iteration=1,
            transcript=[],
            server_continuation_available=True,
        )
        assert checkpoint.state is ContinuationState.WAITING_FOR_CONNECTION
        assert checkpoint.metadata["server_continuation_available"] is True
        branch = await service.reconnect(
            execution_id="exec-1",
            connection_id="conn-2",
            user_id="user-1",
        )
        assert branch.base_checkpoint_id == checkpoint.checkpoint_id

    asyncio.run(scenario())


class _ContextBuilder:
    async def build(self, context, request):
        return type(
            "Snapshot",
            (),
            {"messages": [], "tools": [], "metadata": {}},
        )()


class _Inference:
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        tool_calls = (
            [InferenceToolCall(id="call-1", name="desktop.echo", arguments={})]
            if self.calls == 1
            else []
        )
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role="assistant",
                content="done" if self.calls > 1 else "",
                tool_calls=tool_calls,
            ),
            usage=InferenceUsage(),
            provider="test",
            model="test",
        )


class _Policy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW


class _DisconnectedToolPort:
    def __init__(self, server_continuation):
        self.server_continuation = server_continuation

    async def execute_many(self, context, requests, *, max_parallel):
        request = requests[0]
        return [
            ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_EXECUTION_FAILED",
                error_message="connection lost",
                metadata={
                    "original_error_code": "REMOTE_CONNECTION_LOST",
                    "connection_id": "conn-1",
                    "invocation_id": request.invocation_id,
                },
            )
        ]

    def can_continue_server_side(self, capability_id):
        return self.server_continuation


def _agent_context():
    return AgentExecutionContext.create(
        execution_id="exec-runtime",
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="correlation-1",
        identity=Identity(user_id="user-1", auth_type="jwt"),
        limits=AgentExecutionLimits(max_iterations=3),
        connection_id="conn-1",
    )


def test_agent_enters_waiting_for_connection_when_remote_is_still_required():
    async def scenario():
        service = AgentContinuationService()
        runtime = AgentRuntime(
            context_builder=_ContextBuilder(),
            inference=_Inference(),
            tool_execution=_DisconnectedToolPort(False),
            execution_policy=_Policy(),
            continuation_service=service,
        )
        result = await runtime.execute(_agent_context())
        assert result.error_code == "WAITING_FOR_CONNECTION"
        assert result.continuation_state is ContinuationState.WAITING_FOR_CONNECTION
        assert result.checkpoint_id is not None

    asyncio.run(scenario())


def test_agent_does_not_treat_server_availability_as_replay_safety():
    async def scenario():
        service = AgentContinuationService()
        inference = _Inference()
        runtime = AgentRuntime(
            context_builder=_ContextBuilder(),
            inference=inference,
            tool_execution=_DisconnectedToolPort(True),
            execution_policy=_Policy(),
            continuation_service=service,
        )
        result = await runtime.execute(_agent_context())
        assert result.state.value == "WAITING"
        assert result.error_code == "WAITING_FOR_CONNECTION"
        assert inference.calls == 1
        assert (
            service.current_checkpoint("exec-runtime").state
            is ContinuationState.WAITING_FOR_CONNECTION
        )

    asyncio.run(scenario())
