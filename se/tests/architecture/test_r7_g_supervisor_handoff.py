from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimConsumeResult,
    ResumeClaimState,
)
from se.src.runtimes.agent.resume_claim import (
    ResumeActivationError,
    ResumeClaimDeferred,
)
from se.src.runtimes.agent.supervisor import AgentExecutionSupervisor
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.transport.gateway.api.v1.events_router import _resume_execution


EXECUTION = "exec-r7g"
CHECKPOINT = "cp-r7g"
REQUEST = "rr-r7g"
USER = "user-r7g"
CLIENT = "client-r7g"
K2 = "conn-r7g-k2"
AGENT = "agent-r7g"


def _plan():
    return SimpleNamespace(
        execution_id=EXECUTION,
        checkpoint_id=CHECKPOINT,
        expected_execution_revision=7,
        plan_fingerprint="f" * 64,
        target_user_id=USER,
        target_client_id=CLIENT,
        target_connection_id=K2,
        wait_expires_at=None,
        trace_id="trace-r7g",
        request_id="request-r7g",
        agent_id=AGENT,
        invocation_actions=(),
    )


def _identity():
    return Identity(
        user_id=USER,
        auth_type="jwt",
        scopes={"*"},
    )


def _envelope(connection_id=K2):
    return RealtimeEnvelope(
        type="execution.resume",
        message_id="resume-r7g",
        connection_id=connection_id,
        execution_id=EXECUTION,
        payload={
            "execution_id": EXECUTION,
            "checkpoint_id": CHECKPOINT,
            "resume_request_id": REQUEST,
        },
    )


class _Planner:
    def __init__(self, plan, *, fail_after_first=False):
        self.plan = plan
        self.calls = 0
        self.fail_after_first = fail_after_first

    async def build_resume_plan(self, *args, **kwargs):
        self.calls += 1
        if self.fail_after_first and self.calls > 1:
            raise AssertionError(
                "lost-ACK replay must not rebuild ResumePlan"
            )
        return self.plan


class _ReconnectPlanner:
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    async def build_resume_plan(self, *args, **kwargs):
        self.calls += 1
        connection_id = kwargs["target_connection_id"]
        if connection_id == self.plan.target_connection_id:
            return self.plan
        values = dict(vars(self.plan))
        values["target_connection_id"] = connection_id
        values["plan_fingerprint"] = "3" * 64
        return SimpleNamespace(**values)


class _Store:
    def __init__(self, plan):
        self.plan = plan
        self.claim = None
        self.prepare_calls = 0
        self.create_calls = 0
        self.consume_calls = 0
        self.handoff_calls = []

    async def load_resume_claim_by_request_id(self, resume_request_id):
        if self.claim is None:
            return None
        assert resume_request_id == self.claim.resume_request_id
        return self.claim

    async def prepare_resume_plan_context(self, plan, *, identity, agent=None):
        self.prepare_calls += 1
        assert plan.execution_id == self.plan.execution_id
        assert plan.checkpoint_id == self.plan.checkpoint_id
        assert identity.user_id == USER
        return SimpleNamespace(
            execution_id=EXECUTION,
            task_id=None,
            parent_execution_id=None,
            cancellation_event=asyncio.Event(),
            identity=identity,
            agent=agent,
        )

    async def get_or_create_resume_claim(self, intent):
        self.create_calls += 1
        if self.claim is None:
            self.claim = SimpleNamespace(
                claim_id="claim-r7g",
                resume_request_id=intent.resume_request_id,
                execution_id=intent.execution_id,
                checkpoint_id=intent.checkpoint_id,
                expected_execution_revision=intent.expected_execution_revision,
                plan_fingerprint=intent.plan_fingerprint,
                user_id=intent.user_id,
                client_id=intent.client_id,
                connection_id=intent.connection_id,
                state=ResumeClaimState.CREATED,
                revision=0,
                rejection_code=None,
                metadata={},
            )
        return self.claim

    async def rebind_created_resume_claim(
        self,
        claim_id,
        *,
        plan,
        resume_request_id,
    ):
        assert self.claim is not None
        assert self.claim.claim_id == claim_id
        assert self.claim.resume_request_id == resume_request_id
        assert self.claim.state is ResumeClaimState.CREATED
        assert self.claim.user_id == plan.target_user_id
        assert self.claim.client_id == plan.target_client_id
        self.claim.connection_id = plan.target_connection_id
        self.claim.plan_fingerprint = plan.plan_fingerprint
        self.claim.revision += 1
        return self.claim

    async def consume_resume_claim(self, spec):
        self.consume_calls += 1
        assert self.claim is not None
        self.claim.state = ResumeClaimState.CONSUMED
        self.claim.consumed_execution_revision = (
            self.plan.expected_execution_revision + 1
        )
        return ResumeClaimConsumeResult(
            claim_id=self.claim.claim_id,
            resume_request_id=self.claim.resume_request_id,
            execution_id=EXECUTION,
            checkpoint_id=CHECKPOINT,
            source_execution_revision=self.plan.expected_execution_revision,
            consumed_execution_revision=(
                self.plan.expected_execution_revision + 1
            ),
            remaining_active_budget_seconds=20.0,
            bound_client_id=CLIENT,
            bound_connection_id=self.claim.connection_id,
            already_consumed=False,
        )

    async def record_resume_claim_handoff(self, claim_id, *, status, payload):
        assert self.claim is not None
        assert claim_id == self.claim.claim_id
        handoff = {"status": status, **dict(payload)}
        existing = self.claim.metadata.get("r7_g_handoff")
        if existing is not None:
            assert existing == handoff
        else:
            self.claim.metadata["r7_g_handoff"] = handoff
        self.handoff_calls.append((status, dict(payload)))
        return self.claim


class _DeferredConsumeStore(_Store):
    async def consume_resume_claim(self, spec):
        self.consume_calls += 1
        assert self.claim is not None
        raise ResumeClaimDeferred(
            "RESUME_CONFLICT",
            "transient Task activity epoch conflict",
            retryable=True,
        )


class _DeferredOnceConsumeStore(_Store):
    def __init__(self, plan):
        super().__init__(plan)
        self.deferred_once = False

    async def consume_resume_claim(self, spec):
        if not self.deferred_once:
            self.deferred_once = True
            self.consume_calls += 1
            raise ResumeClaimDeferred(
                "RESUME_CONFLICT",
                "transient Task activity epoch conflict",
                retryable=True,
            )
        return await super().consume_resume_claim(spec)


class _BlockingHandoffStore(_Store):
    def __init__(self, plan):
        super().__init__(plan)
        self.handoff_started = asyncio.Event()
        self.release_handoff = asyncio.Event()

    async def record_resume_claim_handoff(self, claim_id, *, status, payload):
        self.handoff_started.set()
        await self.release_handoff.wait()
        return await super().record_resume_claim_handoff(
            claim_id,
            status=status,
            payload=payload,
        )


class _Runtime:
    def __init__(self, *, activation_error=None):
        self.activation_error = activation_error
        self.activation_calls = 0
        self.execute_calls = 0
        self.recover_calls = 0
        self.fail_calls = 0

    async def prepare_claimed_resume_activation(self, context, *, plan, consumed):
        self.activation_calls += 1
        if self.activation_error is not None:
            raise self.activation_error
        return ()

    async def execute(
        self,
        context,
        *,
        durable_revision,
        initial_tool_results,
    ):
        self.execute_calls += 1
        return SimpleNamespace(execution_id=EXECUTION, state="COMPLETED")

    async def recover_claimed_resume(
        self,
        context,
        *,
        plan,
        consumed,
        error_message,
    ):
        self.recover_calls += 1
        return (
            f"{EXECUTION}:checkpoint:{consumed.consumed_execution_revision + 1}",
            consumed.consumed_execution_revision + 1,
        )

    async def fail_claimed_resume(
        self,
        context,
        *,
        plan,
        consumed,
        error_message,
    ):
        self.fail_calls += 1
        return True


class _BlockingRuntime(_Runtime):
    def __init__(self):
        super().__init__()
        self.activation_started = asyncio.Event()

    async def prepare_claimed_resume_activation(self, context, *, plan, consumed):
        self.activation_calls += 1
        self.activation_started.set()
        await asyncio.Event().wait()


class _Socket:
    def __init__(
        self,
        *,
        supervisor=None,
        runtime=None,
        drop_first_accepted=False,
    ):
        self.messages = []
        self.supervisor = supervisor
        self.runtime = runtime
        self.drop_first_accepted = drop_first_accepted
        self.owned_when_accepted = None
        self.activated_when_accepted = None

    async def send_json(self, message):
        if message["type"] == "execution.resume.accepted":
            if self.supervisor is not None:
                self.owned_when_accepted = self.supervisor.is_running(EXECUTION)
            if self.runtime is not None:
                self.activated_when_accepted = self.runtime.activation_calls > 0
            if self.drop_first_accepted:
                self.drop_first_accepted = False
                raise ConnectionError("simulated lost resume ACK")
        self.messages.append(message)


def _container(plan, store, runtime, supervisor, *, planner=None):
    snapshot = SimpleNamespace(
        is_usable=True,
        user_id=USER,
        metadata={"client_id": CLIENT},
    )
    return SimpleNamespace(
        connection_runtime=SimpleNamespace(
            registry=SimpleNamespace(get=lambda _: snapshot)
        ),
        resume_planning_service=planner or _Planner(plan),
        agent_durable_store=store,
        agent_runtime=runtime,
        agent_execution_supervisor=supervisor,
        agent_registry=SimpleNamespace(get=lambda _: object()),
        continuation_service=SimpleNamespace(),
    )


async def _drain_owned_task():
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_r7_g_accepted_ack_requires_supervisor_ownership_and_activation_barrier():
    plan = _plan()
    store = _Store(plan)
    runtime = _Runtime()
    supervisor = AgentExecutionSupervisor()
    socket = _Socket(supervisor=supervisor, runtime=runtime)
    container = _container(plan, store, runtime, supervisor)

    await _resume_execution(
        socket,
        _identity(),
        container,
        K2,
        _envelope(),
    )
    await _drain_owned_task()

    accepted = [
        item
        for item in socket.messages
        if item["type"] == "execution.resume.accepted"
    ]
    assert len(accepted) == 1
    assert socket.owned_when_accepted is True
    assert socket.activated_when_accepted is True
    assert accepted[0]["payload"] == {
        "execution_id": EXECUTION,
        "checkpoint_id": CHECKPOINT,
        "resume_request_id": REQUEST,
        "claim_id": "claim-r7g",
        "accepted_revision": 8,
        "state_at_accept": "RUNNING",
    }
    assert store.consume_calls == 1
    assert [item[0] for item in store.handoff_calls] == ["ACCEPTED"]
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r7_g_postclaim_activation_failure_recovers_without_accepted_ack():
    plan = _plan()
    store = _Store(plan)
    runtime = _Runtime(
        activation_error=ResumeActivationError(
            "RESUME_ACTION_NOT_COMMITTED",
            "continued invocation remained provisional",
            retryable=True,
        )
    )
    supervisor = AgentExecutionSupervisor()
    socket = _Socket(supervisor=supervisor, runtime=runtime)
    container = _container(plan, store, runtime, supervisor)

    await _resume_execution(
        socket,
        _identity(),
        container,
        K2,
        _envelope(),
    )

    assert not any(
        item["type"] == "execution.resume.accepted"
        for item in socket.messages
    )
    failed = [
        item
        for item in socket.messages
        if item["type"] == "execution.resume.failed"
    ]
    assert len(failed) == 1
    assert failed[0]["payload"]["code"] == "RUNTIME_HANDOFF_FAILED"
    assert failed[0]["payload"]["state"] == "WAITING"
    assert failed[0]["payload"]["wait_reason"] == "RECOVERY"
    assert failed[0]["payload"]["recovery_checkpoint_id"] == (
        f"{EXECUTION}:checkpoint:9"
    )
    assert failed[0]["payload"]["recovery_revision"] == 9
    assert store.claim.state is ResumeClaimState.CONSUMED
    assert [item[0] for item in store.handoff_calls] == ["FAILED"]
    assert runtime.recover_calls == 1
    assert runtime.execute_calls == 0
    assert supervisor.is_running(EXECUTION) is False
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r7_g_lost_accepted_ack_replays_durable_outcome_without_second_claim_or_task():
    plan = _plan()
    store = _Store(plan)
    runtime = _Runtime()
    supervisor = AgentExecutionSupervisor()
    planner = _Planner(plan, fail_after_first=True)
    container = _container(
        plan,
        store,
        runtime,
        supervisor,
        planner=planner,
    )

    first_socket = _Socket(
        supervisor=supervisor,
        runtime=runtime,
        drop_first_accepted=True,
    )
    with pytest.raises(ConnectionError, match="lost resume ACK"):
        await _resume_execution(
            first_socket,
            _identity(),
            container,
            K2,
            _envelope(),
        )
    await _drain_owned_task()

    assert store.claim.metadata["r7_g_handoff"]["status"] == "ACCEPTED"
    assert store.consume_calls == 1
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1

    second_socket = _Socket(
        supervisor=supervisor,
        runtime=runtime,
    )
    await _resume_execution(
        second_socket,
        _identity(),
        container,
        K2,
        _envelope(),
    )

    replayed = [
        item
        for item in second_socket.messages
        if item["type"] == "execution.resume.accepted"
    ]
    assert len(replayed) == 1
    assert replayed[0]["payload"]["claim_id"] == "claim-r7g"
    assert replayed[0]["payload"]["accepted_revision"] == 8
    assert store.consume_calls == 1
    assert store.create_calls == 1
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1
    assert len(store.handoff_calls) == 1
    assert planner.calls == 1
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r7_g_lost_ack_replays_on_new_connection_generation_for_same_stable_client():
    plan = _plan()
    store = _Store(plan)
    runtime = _Runtime()
    supervisor = AgentExecutionSupervisor()
    planner = _Planner(plan, fail_after_first=True)
    container = _container(
        plan,
        store,
        runtime,
        supervisor,
        planner=planner,
    )

    first_socket = _Socket(
        supervisor=supervisor,
        runtime=runtime,
        drop_first_accepted=True,
    )
    with pytest.raises(ConnectionError, match="lost resume ACK"):
        await _resume_execution(
            first_socket,
            _identity(),
            container,
            K2,
            _envelope(K2),
        )
    await _drain_owned_task()

    assert store.claim.metadata["r7_g_handoff"]["status"] == "ACCEPTED"
    assert store.claim.connection_id == K2
    assert store.consume_calls == 1
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1

    replacement_connection = "conn-r7g-k3"
    retry_socket = _Socket(supervisor=supervisor, runtime=runtime)
    await _resume_execution(
        retry_socket,
        _identity(),
        container,
        replacement_connection,
        _envelope(replacement_connection),
    )

    replayed = [
        item
        for item in retry_socket.messages
        if item["type"] == "execution.resume.accepted"
    ]
    assert len(replayed) == 1
    assert replayed[0]["connection_id"] == replacement_connection
    assert replayed[0]["payload"]["claim_id"] == "claim-r7g"
    assert replayed[0]["payload"]["accepted_revision"] == 8
    assert store.claim.connection_id == K2
    assert store.consume_calls == 1
    assert store.create_calls == 1
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1
    assert len(store.handoff_calls) == 1
    assert planner.calls == 1
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r7_g_request_cancellation_after_claim_recovers_and_does_not_leak_owned_task():
    plan = _plan()
    store = _Store(plan)
    runtime = _BlockingRuntime()
    supervisor = AgentExecutionSupervisor()
    socket = _Socket(supervisor=supervisor, runtime=runtime)
    container = _container(plan, store, runtime, supervisor)

    request_task = asyncio.create_task(
        _resume_execution(
            socket,
            _identity(),
            container,
            K2,
            _envelope(),
        )
    )
    await runtime.activation_started.wait()
    assert store.claim.state is ResumeClaimState.CONSUMED
    assert supervisor.is_running(EXECUTION) is True

    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert runtime.recover_calls == 1
    assert [item[0] for item in store.handoff_calls] == ["FAILED"]
    assert store.claim.metadata["r7_g_handoff"]["status"] == "FAILED"
    assert supervisor.is_running(EXECUTION) is False
    assert not any(
        item["type"] == "execution.resume.accepted"
        for item in socket.messages
    )
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r7_g_cancellation_during_accepted_handoff_drains_commit_then_replays():
    plan = _plan()
    store = _BlockingHandoffStore(plan)
    runtime = _Runtime()
    supervisor = AgentExecutionSupervisor()
    planner = _Planner(plan, fail_after_first=True)
    socket = _Socket(supervisor=supervisor, runtime=runtime)
    container = _container(
        plan,
        store,
        runtime,
        supervisor,
        planner=planner,
    )

    request_task = asyncio.create_task(
        _resume_execution(
            socket,
            _identity(),
            container,
            K2,
            _envelope(),
        )
    )
    await store.handoff_started.wait()
    assert supervisor.is_running(EXECUTION) is True
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 0

    request_task.cancel()
    await asyncio.sleep(0)
    store.release_handoff.set()

    with pytest.raises(asyncio.CancelledError):
        await request_task
    await _drain_owned_task()

    assert store.claim.metadata["r7_g_handoff"]["status"] == "ACCEPTED"
    assert runtime.recover_calls == 0
    assert runtime.execute_calls == 1
    assert not any(
        item["type"] == "execution.resume.accepted"
        for item in socket.messages
    )

    retry_socket = _Socket(supervisor=supervisor, runtime=runtime)
    await _resume_execution(
        retry_socket,
        _identity(),
        container,
        K2,
        _envelope(),
    )
    replayed = [
        item
        for item in retry_socket.messages
        if item["type"] == "execution.resume.accepted"
    ]
    assert len(replayed) == 1
    assert replayed[0]["payload"]["claim_id"] == "claim-r7g"
    assert store.consume_calls == 1
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1
    assert planner.calls == 1
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r8_f_retryable_resume_conflict_preserves_same_claim_id_on_wire():
    plan = _plan()
    store = _DeferredConsumeStore(plan)
    runtime = _Runtime()
    supervisor = AgentExecutionSupervisor()
    socket = _Socket(supervisor=supervisor, runtime=runtime)
    container = _container(plan, store, runtime, supervisor)

    await _resume_execution(
        socket,
        _identity(),
        container,
        K2,
        _envelope(),
    )

    rejected = [
        item
        for item in socket.messages
        if item["type"] == "execution.resume.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["code"] == "RESUME_CONFLICT"
    assert rejected[0]["payload"]["retryable"] is True
    assert rejected[0]["payload"]["claim_id"] == "claim-r7g"
    assert rejected[0]["payload"]["resume_request_id"] == REQUEST
    assert store.claim.state is ResumeClaimState.CREATED
    assert store.consume_calls == 1
    assert runtime.activation_calls == 0
    assert runtime.execute_calls == 0
    assert supervisor.is_running(EXECUTION) is False
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_r8_f_retryable_created_claim_rebinds_on_new_connection_generation():
    plan_k2 = _plan()
    store = _DeferredOnceConsumeStore(plan_k2)
    runtime = _Runtime()
    supervisor = AgentExecutionSupervisor()
    planner = _ReconnectPlanner(plan_k2)
    container = _container(
        plan_k2,
        store,
        runtime,
        supervisor,
        planner=planner,
    )

    first_socket = _Socket(supervisor=supervisor, runtime=runtime)
    await _resume_execution(
        first_socket,
        _identity(),
        container,
        K2,
        _envelope(K2),
    )

    first_rejected = [
        item
        for item in first_socket.messages
        if item["type"] == "execution.resume.rejected"
    ]
    assert len(first_rejected) == 1
    assert first_rejected[0]["payload"]["code"] == "RESUME_CONFLICT"
    assert first_rejected[0]["payload"]["retryable"] is True
    assert first_rejected[0]["payload"]["claim_id"] == "claim-r7g"
    assert store.claim.state is ResumeClaimState.CREATED
    assert store.claim.connection_id == K2

    replacement_connection = "conn-r7g-k3"
    retry_socket = _Socket(supervisor=supervisor, runtime=runtime)
    await _resume_execution(
        retry_socket,
        _identity(),
        container,
        replacement_connection,
        _envelope(replacement_connection),
    )
    await _drain_owned_task()

    accepted = [
        item
        for item in retry_socket.messages
        if item["type"] == "execution.resume.accepted"
    ]
    assert len(accepted) == 1
    assert accepted[0]["connection_id"] == replacement_connection
    assert accepted[0]["payload"]["resume_request_id"] == REQUEST
    assert accepted[0]["payload"]["claim_id"] == "claim-r7g"
    assert store.claim.state is ResumeClaimState.CONSUMED
    assert store.claim.connection_id == replacement_connection
    assert store.consume_calls == 2
    assert runtime.activation_calls == 1
    assert runtime.execute_calls == 1
    assert planner.calls == 2
    await supervisor.shutdown()
