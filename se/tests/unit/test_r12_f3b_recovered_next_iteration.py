from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import (
    TaskBudgetReservationKind,
    normalize_task_budget_cost,
)
from se.src.circuit_breaker import CircuitBreaker, CircuitBreakerState
from se.src.provider.exceptions import (
    ProviderDeadlineExceededError,
    ProviderRecoveryAuthorityLostError,
    ProviderRecoveryGuardError,
)
from se.src.provider.executor import ProviderExecutor
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.policies.retry import RetryPolicy
from se.src.provider.retry_contracts import ProviderCallBudget
from se.src.runtimes.agent.adapters.policy import DefaultAgentExecutionPolicy
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentExecutionContext,
    AgentEventName,
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.recovery import (
    RecoveryActivationResult,
    RecoveryInferenceDisposition,
    RecoveryPlan,
    recovery_plan_fingerprint,
)
from se.src.runtimes.agent.contracts.resume import ResumeTriggerType
from se.src.runtimes.agent.recovery_execution import (
    AgentRecoveryExecutionService,
    RecoveryExecutionError,
)
from se.src.runtimes.agent.runtime import (
    AgentRuntime,
    RecoveryProgressionDeferredError,
)
import se.src.runtimes.agent.task_budget as task_budget_module
from se.src.runtimes.agent.task_budget import (
    TaskBudgetConflictError,
    TaskBudgetIncarnationChangedError,
    TaskBudgetService,
)


EXECUTION = "exec-r12-f3b"
SESSION = "session-r12-f3b"
AGENT = "agent-r12-f3b"
USER = "user-r12-f3b"


def _plan() -> RecoveryPlan:
    values = {
        "execution_id": EXECUTION,
        "checkpoint_id": "cp-r12-f3b",
        "expected_execution_revision": 4,
        "recovery_fingerprint": "recovery-fp-r12-f3b",
        "expected_unowned_lease_generation": 8,
        "checkpoint_origin_client_id": None,
        "checkpoint_origin_connection_id": None,
        "agent_id": AGENT,
        "session_id": SESSION,
        "task_id": None,
        "task_revision": None,
        "branch_id": None,
        "branch_revision": None,
        "parent_execution_id": None,
        "retry_of_execution_id": None,
        "base_execution_id": None,
        "base_checkpoint_id": None,
        "resolved_recovery_principal": USER,
        "iteration": 1,
        "recovery_iteration_id": f"{EXECUTION}:iteration:1",
        "inference_request_id": "old-inf-r12-f3b",
        "inference_disposition": RecoveryInferenceDisposition.NO_INFERENCE,
        "ordered_tool_call_ids": (),
        "transcript_snapshot": (),
        "remaining_active_budget_seconds": 30.0,
        "wait_expires_at": None,
        "task_budget_incarnation_generation": None,
        "task_budget_revision": None,
        "target_trigger": ResumeTriggerType.SERVER_RECOVERY,
        "target_client_id": None,
        "target_connection_id": None,
        "invocation_actions": (),
    }
    return RecoveryPlan(
        **values,
        plan_fingerprint=recovery_plan_fingerprint(
            {**values, "plan_fingerprint": ""}
        ),
    )


def _activation(plan: RecoveryPlan) -> RecoveryActivationResult:
    return RecoveryActivationResult(
        claim_id="claim-r12-f3b",
        resume_request_id="rr-r12-f3b",
        execution_id=plan.execution_id,
        checkpoint_id=plan.checkpoint_id,
        source_execution_revision=plan.expected_execution_revision,
        consumed_execution_revision=plan.expected_execution_revision + 1,
        activation_owner_instance_id="worker-r12-f3b",
        lease_generation=plan.expected_unowned_lease_generation + 1,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def _context(plan: RecoveryPlan, activation: RecoveryActivationResult):
    agent = AgentDefinition(
        name=AGENT,
        goal="continue recovered execution",
        instruction="test",
        tools=[],
    )
    context = AgentExecutionContext.create(
        execution_id=EXECUTION,
        agent_id=AGENT,
        session_id=SESSION,
        correlation_id="corr-r12-f3b",
        identity=Identity(
            user_id=USER,
            session_id=SESSION,
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            max_iterations=4,
            timeout_seconds=30.0,
            iteration_timeout_seconds=10.0,
            inference_timeout_seconds=5.0,
            tool_timeout_seconds=2.0,
        ),
        agent=agent,
        remaining_active_budget_seconds=30.0,
        activate_budget=False,
    )
    context.iteration = plan.iteration
    context.resume_revision = activation.consumed_execution_revision
    return context


class _Store:
    def __init__(self, *, fence_results=None, handoff_disposition="WRITTEN"):
        self.fence_results = list(fence_results or [])
        self.handoff_disposition = handoff_disposition
        self.iterations = {}
        self.checkpoints = []
        self.compare_calls = []
        self.handoff_calls = []

    async def has_active_execution_lease_fence(self, execution_id, **kwargs):
        assert execution_id == EXECUTION
        if self.fence_results:
            return self.fence_results.pop(0)
        return True

    async def load_iteration(self, execution_id, *, iteration_number):
        return self.iterations.get(iteration_number)

    async def save_iteration(self, values, *, recovery_fence=None):
        assert recovery_fence is not None
        record = SimpleNamespace(**values)
        self.iterations[int(values["iteration"])] = record
        return record

    async def update_iteration(self, iteration_id, values, *, recovery_fence=None):
        assert recovery_fence is not None
        record = SimpleNamespace(**values)
        self.iterations[int(values["iteration"])] = record
        return record

    async def update_checkpoint(self, execution_id, values, *, recovery_fence=None):
        assert execution_id == EXECUTION
        assert recovery_fence is not None
        self.checkpoints.append(dict(values))
        return SimpleNamespace(**values)

    async def compare_and_set_execution(
        self,
        execution_id,
        expected_revision,
        values,
        *,
        recovery_fence=None,
    ):
        self.compare_calls.append(
            (execution_id, expected_revision, dict(values), recovery_fence)
        )
        return SimpleNamespace(**values)

    async def commit_recovery_inference_handoff(self, execution_id, **kwargs):
        assert execution_id == EXECUTION
        assert kwargs["recovery_fence"] is not None
        self.handoff_calls.append(kwargs)
        return self.handoff_disposition


class _ContextBuilder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(InferenceMessage(role="user", content="continue"),),
            tools=(),
        )


class _NeverTools:
    def __init__(self):
        self.calls = 0

    async def execute_many(self, context, requests, *, max_parallel):
        self.calls += 1
        raise AssertionError("F3-B must not dispatch fresh tool calls")


class _GuardOnlyInference:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        assert request.recovery_pre_attempt_guard is not None
        await request.recovery_pre_attempt_guard()
        raise AssertionError("provider send must not happen after authority loss")


class _ToolCallInference:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        assert request.recovery_pre_attempt_guard is not None
        await request.recovery_pre_attempt_guard()
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role="assistant",
                content="need tool",
                tool_calls=[
                    InferenceToolCall(
                        id="fresh-call",
                        name="tool.echo",
                        arguments={"value": "x"},
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage=InferenceUsage(total_tokens=3),
            provider="fake",
            model="fake-model",
        )


class _Events:
    def __init__(self):
        self.names = []

    async def publish(self, event):
        self.names.append(event.event_name)


@pytest.mark.asyncio
async def test_f3b_live_authority_loss_propagates_without_agent_terminalization():
    plan = _plan()
    activation = _activation(plan)
    context = _context(plan, activation)
    store = _Store(
        fence_results=[True, True, True, True, False],
    )
    inference = _GuardOnlyInference()
    runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=inference,
        tool_execution=_NeverTools(),
        execution_policy=DefaultAgentExecutionPolicy(),
        durable_store=store,
    )

    with pytest.raises(ProviderRecoveryAuthorityLostError) as raised:
        await runtime.execute_recovered_next_iteration(
            context,
            plan=plan,
            activation=activation,
            handoff_transcript=(),
            recovered_tool_results=(),
        )

    assert raised.value.retryable is False
    assert len(inference.requests) == 1
    assert store.compare_calls == []
    assert context.active_budget_running is False


@pytest.mark.asyncio
async def test_f3b_fresh_tool_calls_defer_before_logical_tool_admission():
    plan = _plan()
    activation = _activation(plan)
    context = _context(plan, activation)
    store = _Store()
    tools = _NeverTools()
    events = _Events()
    runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=_ToolCallInference(),
        tool_execution=tools,
        execution_policy=DefaultAgentExecutionPolicy(),
        durable_store=store,
        event_publisher=events,
    )

    with pytest.raises(RecoveryProgressionDeferredError) as raised:
        await runtime.execute_recovered_next_iteration(
            context,
            plan=plan,
            activation=activation,
            handoff_transcript=(),
            recovered_tool_results=(),
        )

    assert raised.value.code == "RECOVERY_FRESH_TOOL_DISPATCH_DEFERRED"
    assert tools.calls == 0
    assert store.compare_calls == []
    assert len(store.checkpoints) == 1
    assert AgentEventName.TOOL_REQUESTED not in events.names
    assert AgentEventName.TOOL_STARTED not in events.names


@pytest.mark.asyncio
async def test_f3b_reused_handoff_never_mints_or_sends_fresh_inference(monkeypatch):
    plan = _plan()
    activation = _activation(plan)
    context = _context(plan, activation)
    store = _Store(handoff_disposition="REUSED")
    service = AgentRecoveryExecutionService(store, None, None)

    async def recovered_batch(*args, **kwargs):
        return ()

    monkeypatch.setattr(
        service,
        "execute_active_tool_batch",
        recovered_batch,
    )

    calls = []

    class _Runtime:
        async def execute_recovered_next_iteration(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("reused handoff must not start inference")

    with pytest.raises(RecoveryExecutionError) as raised:
        await service.execute_recovered_next_iteration(
            context,
            plan=plan,
            activation=activation,
            runtime=_Runtime(),
        )

    assert raised.value.code == "RECOVERY_INFERENCE_CUT_UNPROVEN"
    assert calls == []
    assert len(store.handoff_calls) == 1


def test_f3b_task_budget_recovery_fence_precedes_idempotent_replay_and_cas():
    mutate_source = inspect.getsource(
        TaskBudgetService._mutate_with_reservation
    )
    assert mutate_source.index(
        "_lock_recovery_mutation_fence_in_uow"
    ) < mutate_source.index("get_task_budget_reservation")
    assert mutate_source.index(
        "_require_recovery_mutation_fence_now"
    ) < mutate_source.index("compare_and_set_task_budget")

    transition_source = inspect.getsource(
        TaskBudgetService._transition_execution_with_budget
    )
    assert transition_source.index(
        "_lock_recovery_mutation_fence_in_uow"
    ) < transition_source.index("get_task_budget_reservation")
    assert transition_source.index(
        "_require_recovery_mutation_fence_now"
    ) < transition_source.index("compare_and_set_task_budget")


def test_f3b_task_budget_exact_fence_rejects_stale_owner():
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    execution = SimpleNamespace(
        task_id="task-r12-f3b",
        state="RUNNING",
        owner_instance_id="worker-r12-f3b",
        lease_generation=9,
        lease_expires_at=expiry,
    )
    fence = {
        "owner_instance_id": "worker-r12-f3b",
        "lease_generation": 9,
        "lease_expires_at": expiry,
    }

    TaskBudgetService._require_recovery_mutation_fence_now(
        execution,
        task_id="task-r12-f3b",
        recovery_fence=fence,
    )

    execution.owner_instance_id = "other-worker"
    with pytest.raises(TaskBudgetConflictError, match="RECOVERY_ACTIVE_LEASE_FENCE_LOST"):
        TaskBudgetService._require_recovery_mutation_fence_now(
            execution,
            task_id="task-r12-f3b",
            recovery_fence=fence,
        )

class _Breaker:
    def __init__(self):
        self.before = 0
        self.success = 0
        self.failure = 0

    async def before_request(self):
        self.before += 1

    async def on_success(self):
        self.success += 1

    async def on_failure(self):
        self.failure += 1


class _BreakerManager:
    def __init__(self, breaker):
        self.breaker = breaker

    async def get_breaker(self, provider_name):
        return self.breaker


class _ExecutorProvider:
    def __init__(self, name="p1", outcomes=()):
        self.name = name
        self.chat = SimpleNamespace(chat=self.chat)
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        if not self.outcomes:
            return SimpleNamespace(provider=self.name)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_f3b_first_attempt_guard_loss_starts_zero_provider_send_or_breaker_failure():
    breaker = _Breaker()
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=time.monotonic() + 30.0,
        max_retries=2,
    )
    guard_calls = 0

    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        raise ProviderRecoveryAuthorityLostError(
            reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST"
        )

    with pytest.raises(ProviderRecoveryAuthorityLostError) as raised:
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert raised.value.retryable is False
    assert guard_calls == 1
    assert provider.calls == 0
    assert budget.retries_used == 0
    assert breaker.before == 1
    assert breaker.failure == 0
    assert breaker.success == 0


@pytest.mark.asyncio
async def test_f3b_retry_attempt_rechecks_guard_before_second_physical_send(
    monkeypatch,
):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        no_sleep,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    breaker = _Breaker()
    provider = _ExecutorProvider(
        outcomes=[httpx.ConnectError("transient")],
    )
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=time.monotonic() + 30.0,
        max_retries=2,
    )
    guard_calls = 0

    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise ProviderRecoveryAuthorityLostError(
                reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST"
            )

    with pytest.raises(ProviderRecoveryAuthorityLostError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert guard_calls == 2
    assert provider.calls == 1
    assert budget.retries_used == 1
    assert breaker.failure == 0
    assert breaker.success == 0


@pytest.mark.asyncio
async def test_f3b_provider_deadline_dominates_authority_loss_after_guard(
    monkeypatch,
):
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )

    breaker = _Breaker()
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=101.0,
        max_retries=2,
    )

    async def guard():
        clock["now"] = 102.0
        raise ProviderRecoveryAuthorityLostError(
            reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST"
        )

    with pytest.raises(ProviderDeadlineExceededError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert provider.calls == 0
    assert budget.retries_used == 0
    assert breaker.failure == 0
    assert breaker.success == 0


class _HandlerRouting:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, **kwargs):
        return list(self.providers)


class _HandlerProvider:
    def __init__(self, name, events=None):
        self.name = name
        self.events = events if events is not None else []

    async def has_capability(self, *args, **kwargs):
        self.events.append(f"probe:{self.name}")
        return True


class _FallbackGuardExecutor:
    def __init__(self):
        self.retry_policy = SimpleNamespace(max_retries=0)
        self.dispatches = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(
        self,
        *,
        provider,
        recovery_pre_attempt_guard=None,
        **kwargs,
    ):
        if recovery_pre_attempt_guard is not None:
            await recovery_pre_attempt_guard()
        self.dispatches.append(provider.name)
        if provider.name == "p1":
            raise httpx.ConnectError("first provider failed")
        return SimpleNamespace(
            model="logical-model",
            usage=SimpleNamespace(),
            metadata=SimpleNamespace(provider=provider.name),
        )


class _PassthroughCanonicalizer:
    async def canonicalize(self, response, *, owner_user_id):
        return response


@pytest.mark.asyncio
async def test_f3b_fallback_candidate_rechecks_guard_before_second_send():
    providers = [_HandlerProvider("p1"), _HandlerProvider("p2")]
    executor = _FallbackGuardExecutor()
    handler = ChatExecutionHandler(
        providers={item.name: item for item in providers},
        routing_policy=_HandlerRouting(providers),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        generated_asset_canonicalizer=_PassthroughCanonicalizer(),
    )
    guard_calls = 0

    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise ProviderRecoveryAuthorityLostError(
                reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST"
            )

    with pytest.raises(ProviderRecoveryAuthorityLostError):
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            recovery_pre_attempt_guard=guard,
        )

    assert guard_calls == 2
    assert executor.dispatches == ["p1"]


class _AssetDeferHook:
    def __init__(self):
        self.project_calls = 0

    @staticmethod
    def contains_canonical_assets(body):
        return True

    async def project_attempt(self, **kwargs):
        self.project_calls += 1
        raise AssertionError("recovered asset projection must be deferred")


class _NeverHandlerExecutor:
    def __init__(self):
        self.retry_policy = SimpleNamespace(max_retries=0)
        self.calls = 0

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, **kwargs):
        self.calls += 1
        raise AssertionError("recovered asset request must not reach executor")


@pytest.mark.asyncio
async def test_f3b_asset_bearing_recovery_defers_before_cas_projection():
    provider = _HandlerProvider("p1")
    executor = _NeverHandlerExecutor()
    hook = _AssetDeferHook()
    handler = ChatExecutionHandler(
        providers={provider.name: provider},
        routing_policy=_HandlerRouting([provider]),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        asset_projection_hook=hook,
        generated_asset_canonicalizer=_PassthroughCanonicalizer(),
    )

    async def live_guard():
        return None

    with pytest.raises(ProviderRecoveryAuthorityLostError) as raised:
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model", "messages": []},
            recovery_pre_attempt_guard=live_guard,
        )

    assert raised.value.reason_code == "RECOVERY_ASSET_PROJECTION_DEFERRED"
    assert hook.project_calls == 0
    assert executor.calls == 0


class _OrderQuota:
    enabled = True

    def __init__(self, events):
        self.events = events

    async def reserve(self, *, context, body, streaming_mode):
        self.events.append("ubq-reserve")
        return SimpleNamespace(id="admission")

    def normalize_gateway_usage(self, response):
        self.events.append("ubq-normalize")
        return SimpleNamespace(total_tokens=1)

    async def settle_success(self, admission, usage):
        self.events.append("ubq-settle")


class _OrderExecutor:
    def __init__(self, events):
        self.events = events
        self.retry_policy = SimpleNamespace(max_retries=0)

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, recovery_pre_attempt_guard=None, **kwargs):
        if recovery_pre_attempt_guard is not None:
            await recovery_pre_attempt_guard()
        self.events.append("provider-success")
        return SimpleNamespace(
            model="logical-model",
            usage=SimpleNamespace(total_tokens=1),
            metadata=SimpleNamespace(provider=provider.name),
        )


class _OrderCanonicalizer:
    def __init__(self, events):
        self.events = events

    async def canonicalize(self, response, *, owner_user_id):
        self.events.append("cas-canonicalize")
        return response


@pytest.mark.asyncio
async def test_f3b_success_preserves_provider_then_ubq_then_cas_order():
    events = []
    provider = _HandlerProvider("p1", events)
    handler = ChatExecutionHandler(
        providers={provider.name: provider},
        routing_policy=_HandlerRouting([provider]),
        executor=_OrderExecutor(events),
        circuit_breaker_manager=object(),
        timeout=30,
        inference_quota=_OrderQuota(events),
        generated_asset_canonicalizer=_OrderCanonicalizer(events),
    )

    async def live_guard():
        return None

    await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-r12-f3b",
        recovery_pre_attempt_guard=live_guard,
    )

    assert events.index("provider-success") < events.index("ubq-settle")
    assert events.index("ubq-settle") < events.index("cas-canonicalize")


class _ReplayAgents:
    def __init__(
        self,
        execution,
        budget_record,
        reservations,
    ):
        self.execution = execution
        self.budget_record = budget_record
        self.reservations = reservations
        self.reservation_lookups = 0

    async def get_execution_for_update(self, execution_id):
        assert execution_id == self.execution.id
        return self.execution

    async def get_task_budget(self, task_id):
        assert task_id == self.budget_record.task_id
        return self.budget_record

    async def get_task_budget_reservation(
        self,
        task_id,
        kind,
        reservation_key,
        *,
        expected_incarnation_generation,
    ):
        self.reservation_lookups += 1
        return self.reservations.get((kind, reservation_key))

    async def compare_and_set_task_budget(self, *args, **kwargs):
        raise AssertionError("idempotent replay must not mutate TaskBudget")


class _ReplayUow:
    def __init__(self, agents):
        self.agents = agents
        self.user_budgets = SimpleNamespace()
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_f3b_task_budget_replay_requires_live_fence_and_frozen_incarnation(
    monkeypatch,
):
    task_id = "task-r12-f3b-replay"
    execution_id = "exec-r12-f3b-replay"
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    execution = SimpleNamespace(
        id=execution_id,
        task_id=task_id,
        state="RUNNING",
        owner_instance_id="worker-r12-f3b",
        lease_generation=9,
        lease_expires_at=expiry,
    )
    budget_record = SimpleNamespace(
        task_id=task_id,
        incarnation_generation=3,
    )
    inference_key = "inf-r12-f3b-replay"
    usage_key = "usage-r12-f3b-replay"
    usage_payload = {
        "tokens": 7,
        "cost_usd": str(normalize_task_budget_cost("0")),
    }
    reservations = {
        (
            TaskBudgetReservationKind.INFERENCE.value,
            inference_key,
        ): SimpleNamespace(
            payload_fingerprint=task_budget_module._reservation_fingerprint(
                TaskBudgetReservationKind.INFERENCE,
                inference_key,
                {},
            )
        ),
        (
            TaskBudgetReservationKind.USAGE.value,
            usage_key,
        ): SimpleNamespace(
            payload_fingerprint=task_budget_module._reservation_fingerprint(
                TaskBudgetReservationKind.USAGE,
                usage_key,
                usage_payload,
            )
        ),
    }
    agents = _ReplayAgents(
        execution,
        budget_record,
        reservations,
    )
    service = TaskBudgetService(
        lambda: _ReplayUow(agents),
        user_inference_quota_enabled=True,
    )
    replay_budget = SimpleNamespace(
        task_id=task_id,
        incarnation_generation=3,
    )
    monkeypatch.setattr(
        task_budget_module,
        "_budget_from_record",
        lambda _record: replay_budget,
    )
    fence = {
        "owner_instance_id": "worker-r12-f3b",
        "lease_generation": 9,
        "lease_expires_at": expiry,
    }

    first = await service.reserve_inference(
        task_id,
        request_id=inference_key,
        recovery_execution_id=execution_id,
        recovery_fence=fence,
        expected_incarnation_generation=3,
    )
    second = await service.account_usage(
        task_id,
        usage_key=usage_key,
        tokens=7,
        cost_usd="0",
        recovery_execution_id=execution_id,
        recovery_fence=fence,
        expected_incarnation_generation=3,
    )
    assert first is replay_budget
    assert second is replay_budget
    assert agents.reservation_lookups == 2

    execution.owner_instance_id = "stale-worker"
    with pytest.raises(
        TaskBudgetConflictError,
        match="RECOVERY_ACTIVE_LEASE_FENCE_LOST",
    ):
        await service.reserve_inference(
            task_id,
            request_id=inference_key,
            recovery_execution_id=execution_id,
            recovery_fence=fence,
            expected_incarnation_generation=3,
        )
    with pytest.raises(
        TaskBudgetConflictError,
        match="RECOVERY_ACTIVE_LEASE_FENCE_LOST",
    ):
        await service.account_usage(
            task_id,
            usage_key=usage_key,
            tokens=7,
            cost_usd="0",
            recovery_execution_id=execution_id,
            recovery_fence=fence,
            expected_incarnation_generation=3,
        )
    assert agents.reservation_lookups == 2

    execution.owner_instance_id = "worker-r12-f3b"
    budget_record.incarnation_generation = 4
    with pytest.raises(TaskBudgetIncarnationChangedError):
        await service.reserve_inference(
            task_id,
            request_id=inference_key,
            recovery_execution_id=execution_id,
            recovery_fence=fence,
            expected_incarnation_generation=3,
        )
    assert agents.reservation_lookups == 2

class _TerminalAgents:
    def __init__(
        self,
        execution,
        budget_record,
        *,
        reservation=None,
        lose_after_budget_read=False,
    ):
        self.execution = execution
        self.budget_record = budget_record
        self.reservation = reservation
        self.lose_after_budget_read = lose_after_budget_read
        self.reservation_lookups = 0
        self.budget_cas = 0
        self.execution_cas = 0
        self.saved_reservations = 0

    async def get_execution_for_update(self, execution_id):
        assert execution_id == self.execution.id
        return self.execution

    async def get_task_budget(self, task_id):
        assert task_id == self.budget_record.task_id
        if self.lose_after_budget_read:
            self.execution.owner_instance_id = "lost-worker"
        return self.budget_record

    async def has_execution_for_task(self, task_id):
        return True

    async def get_task_budget_reservation(
        self,
        task_id,
        kind,
        reservation_key,
        *,
        expected_incarnation_generation,
    ):
        self.reservation_lookups += 1
        return self.reservation

    async def compare_and_set_task_budget(
        self,
        task_id,
        revision,
        values,
        *,
        expected_incarnation_generation,
    ):
        self.budget_cas += 1
        return SimpleNamespace(**self.budget_record.__dict__, **values)

    async def get_execution(self, execution_id):
        return self.execution

    async def compare_and_set_execution(
        self,
        execution_id,
        source_revision,
        values,
    ):
        self.execution_cas += 1
        for key, value in values.items():
            setattr(self.execution, key, value)
        self.execution.revision = source_revision + 1
        return self.execution

    async def save_task_budget_reservation(self, values):
        self.saved_reservations += 1
        self.reservation = SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_f3b_terminal_transition_is_one_exact_fenced_taskbudget_uow(
    monkeypatch,
):
    task_id = "task-r12-f3b-terminal"
    execution_id = "exec-r12-f3b-terminal"
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    execution = SimpleNamespace(
        id=execution_id,
        task_id=task_id,
        state="RUNNING",
        revision=5,
        owner_instance_id="worker-r12-f3b",
        lease_generation=9,
        lease_expires_at=expiry,
    )
    budget_record = SimpleNamespace(
        task_id=task_id,
        revision=7,
        incarnation_generation=3,
    )
    agents = _TerminalAgents(execution, budget_record)
    service = TaskBudgetService(lambda: _ReplayUow(agents))
    budget = SimpleNamespace(
        task_id=task_id,
        revision=7,
        active_executions=1,
        active_parallel_agents=0,
    )
    monkeypatch.setattr(
        task_budget_module,
        "_budget_from_record",
        lambda _record: budget,
    )
    fence = {
        "owner_instance_id": "worker-r12-f3b",
        "lease_generation": 9,
        "lease_expires_at": expiry,
    }

    target_revision = await service.finish_task_scoped_execution(
        task_id,
        execution_id=execution_id,
        source_revision=5,
        transition_values={
            "state": "COMPLETED",
            "wait_reason": None,
            "wait_expires_at": None,
            "result": {"output": "done"},
            "error": None,
            "completed_at": datetime.now(timezone.utc),
        },
        delegated=False,
        recovery_fence=fence,
        expected_incarnation_generation=3,
    )

    assert target_revision == 6
    assert agents.reservation_lookups == 1
    assert agents.budget_cas == 1
    assert agents.execution_cas == 1
    assert agents.saved_reservations == 1
    assert execution.state == "COMPLETED"
    assert execution.revision == 6


@pytest.mark.asyncio
async def test_f3b_terminal_existing_reservation_with_stale_owner_cannot_replay_or_remint(
    monkeypatch,
):
    task_id = "task-r12-f3b-terminal-stale"
    execution_id = "exec-r12-f3b-terminal-stale"
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    execution = SimpleNamespace(
        id=execution_id,
        task_id=task_id,
        state="RUNNING",
        revision=5,
        owner_instance_id="stale-worker",
        lease_generation=9,
        lease_expires_at=expiry,
    )
    budget_record = SimpleNamespace(
        task_id=task_id,
        revision=7,
        incarnation_generation=3,
    )
    reservation = SimpleNamespace(payload_fingerprint="already-terminal")
    agents = _TerminalAgents(
        execution,
        budget_record,
        reservation=reservation,
    )
    service = TaskBudgetService(lambda: _ReplayUow(agents))
    monkeypatch.setattr(
        task_budget_module,
        "_budget_from_record",
        lambda _record: SimpleNamespace(
            task_id=task_id,
            revision=7,
            active_executions=1,
            active_parallel_agents=0,
        ),
    )
    fence = {
        "owner_instance_id": "worker-r12-f3b",
        "lease_generation": 9,
        "lease_expires_at": expiry,
    }

    with pytest.raises(
        TaskBudgetConflictError,
        match="RECOVERY_ACTIVE_LEASE_FENCE_LOST",
    ):
        await service.finish_task_scoped_execution(
            task_id,
            execution_id=execution_id,
            source_revision=5,
            transition_values={
                "state": "COMPLETED",
                "wait_reason": None,
                "wait_expires_at": None,
                "result": {"output": "done"},
                "error": None,
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=False,
            recovery_fence=fence,
            expected_incarnation_generation=3,
        )

    assert agents.reservation_lookups == 0
    assert agents.budget_cas == 0
    assert agents.execution_cas == 0
    assert agents.saved_reservations == 0
    assert execution.state == "RUNNING"
    assert execution.revision == 5


@pytest.mark.asyncio
async def test_f3b_lease_loss_inside_taskbudget_uow_stops_before_any_terminal_write(
    monkeypatch,
):
    task_id = "task-r12-f3b-terminal-race"
    execution_id = "exec-r12-f3b-terminal-race"
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    execution = SimpleNamespace(
        id=execution_id,
        task_id=task_id,
        state="RUNNING",
        revision=5,
        owner_instance_id="worker-r12-f3b",
        lease_generation=9,
        lease_expires_at=expiry,
    )
    budget_record = SimpleNamespace(
        task_id=task_id,
        revision=7,
        incarnation_generation=3,
    )
    agents = _TerminalAgents(
        execution,
        budget_record,
        lose_after_budget_read=True,
    )
    service = TaskBudgetService(lambda: _ReplayUow(agents))
    monkeypatch.setattr(
        task_budget_module,
        "_budget_from_record",
        lambda _record: SimpleNamespace(
            task_id=task_id,
            revision=7,
            active_executions=1,
            active_parallel_agents=0,
        ),
    )
    fence = {
        "owner_instance_id": "worker-r12-f3b",
        "lease_generation": 9,
        "lease_expires_at": expiry,
    }

    with pytest.raises(
        TaskBudgetConflictError,
        match="RECOVERY_ACTIVE_LEASE_FENCE_LOST",
    ):
        await service.finish_task_scoped_execution(
            task_id,
            execution_id=execution_id,
            source_revision=5,
            transition_values={
                "state": "COMPLETED",
                "wait_reason": None,
                "wait_expires_at": None,
                "result": {"output": "done"},
                "error": None,
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=False,
            recovery_fence=fence,
            expected_incarnation_generation=3,
        )

    assert agents.reservation_lookups == 1
    assert agents.budget_cas == 0
    assert agents.execution_cas == 0
    assert agents.saved_reservations == 0
    assert execution.state == "RUNNING"
    assert execution.revision == 5

@pytest.mark.asyncio
async def test_f3b_half_open_probe_released_neutrally_on_authority_loss_before_send():
    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )

    async def guard():
        raise ProviderRecoveryAuthorityLostError(
            reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST"
        )

    with pytest.raises(ProviderRecoveryAuthorityLostError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            recovery_pre_attempt_guard=guard,
        )

    assert provider.calls == 0
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False

    # The abandoned recovery call must not strand the exclusive HALF_OPEN
    # probe; a later independently-authorized request can acquire it.
    await breaker.before_request()
    assert breaker._half_open_lock.locked() is True
    breaker._half_open_lock.release()


@pytest.mark.asyncio
async def test_f3b_half_open_probe_released_neutrally_on_deadline_before_send(
    monkeypatch,
):
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )

    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=101.0,
        max_retries=0,
    )

    async def guard():
        clock["now"] = 102.0
        raise ProviderRecoveryAuthorityLostError(
            reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST"
        )

    with pytest.raises(ProviderDeadlineExceededError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert provider.calls == 0
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


@pytest.mark.asyncio
async def test_f3b_half_open_probe_released_neutrally_on_guard_cancellation_before_send():
    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )

    async def guard():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            recovery_pre_attempt_guard=guard,
        )

    assert provider.calls == 0
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


@pytest.mark.asyncio
async def test_f3b_half_open_probe_released_neutrally_on_guard_error_before_send():
    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )

    async def guard():
        raise RuntimeError("lease store unavailable")

    with pytest.raises(RuntimeError, match="lease store unavailable"):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            recovery_pre_attempt_guard=guard,
        )

    assert provider.calls == 0
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False



@pytest.mark.asyncio
async def test_f3b_retry_guard_cancellation_releases_half_open_probe_neutrally(
    monkeypatch,
):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        no_sleep,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider(
        outcomes=[httpx.ConnectError("transient")],
    )
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=time.monotonic() + 30.0,
        max_retries=2,
    )
    guard_calls = 0

    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert guard_calls == 2
    assert provider.calls == 1
    assert budget.retries_used == 1
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


@pytest.mark.asyncio
async def test_f3b_retry_guard_error_preserves_control_plane_identity_without_fallback(
    monkeypatch,
):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        no_sleep,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider(
        outcomes=[httpx.ConnectError("transient")],
    )
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=time.monotonic() + 30.0,
        max_retries=2,
    )
    guard_calls = 0
    control_plane_error = RuntimeError("lease store unavailable")

    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise control_plane_error

    with pytest.raises(RuntimeError) as raised:
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert raised.value is control_plane_error
    assert guard_calls == 2
    assert provider.calls == 1
    assert budget.retries_used == 1
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False

@pytest.mark.asyncio
async def test_f3b_provider_like_guard_error_bypasses_retry_policy(
    monkeypatch,
):
    async def no_sleep(_delay):
        raise AssertionError("guard failure must not enter provider retry backoff")

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        no_sleep,
    )

    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=time.monotonic() + 30.0,
        max_retries=2,
    )
    control_plane_error = httpx.ConnectError("lease store unavailable")

    async def guard():
        raise control_plane_error

    with pytest.raises(httpx.ConnectError) as raised:
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
            recovery_pre_attempt_guard=guard,
        )

    assert raised.value is control_plane_error
    assert isinstance(raised.value.__cause__, ProviderRecoveryGuardError)
    assert raised.value.__cause__.original_error is raised.value
    assert provider.calls == 0
    assert budget.retries_used == 0
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


class _ProviderLikeGuardChatProvider:
    def __init__(self, name):
        self.name = name
        self.chat = SimpleNamespace(chat=self._chat)
        self.calls = 0
        self.capability_calls = 0

    async def has_capability(self, *args, **kwargs):
        self.capability_calls += 1
        return True

    async def _chat(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            model="logical-model",
            usage=SimpleNamespace(total_tokens=1),
            metadata=SimpleNamespace(provider=self.name),
        )


class _PerProviderBreakerManager:
    def __init__(self, providers):
        self.breakers = {
            provider.name: CircuitBreaker(
                provider_name=provider.name,
                failure_threshold=1,
                reset_timeout=60,
                success_threshold=1,
            )
            for provider in providers
        }

    async def get_breaker(self, provider_name):
        return self.breakers[provider_name]


@pytest.mark.asyncio
async def test_f3b_provider_like_guard_error_bypasses_handler_fallback():
    providers = [
        _ProviderLikeGuardChatProvider("p1"),
        _ProviderLikeGuardChatProvider("p2"),
    ]
    executor = ProviderExecutor(
        _PerProviderBreakerManager(providers),
        retry_policy=RetryPolicy(max_retries=2),
    )
    handler = ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_HandlerRouting(providers),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        generated_asset_canonicalizer=_PassthroughCanonicalizer(),
    )
    control_plane_error = httpx.ConnectError("lease store unavailable")

    async def guard():
        raise control_plane_error

    with pytest.raises(httpx.ConnectError) as raised:
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            recovery_pre_attempt_guard=guard,
        )

    assert raised.value is control_plane_error
    assert [provider.calls for provider in providers] == [0, 0]



@pytest.mark.asyncio
async def test_f3b_non_exception_guard_exit_releases_half_open_probe_neutrally():
    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )
    control_plane_exit = GeneratorExit("guard stopped")

    async def guard():
        raise control_plane_exit

    with pytest.raises(GeneratorExit) as raised:
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            recovery_pre_attempt_guard=guard,
        )

    assert raised.value is control_plane_exit
    assert provider.calls == 0
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


@pytest.mark.asyncio
async def test_f3b_provider_cancellation_records_half_open_failure():
    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider(outcomes=[asyncio.CancelledError()])
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
        )

    assert provider.calls == 1
    assert breaker.current_state is CircuitBreakerState.OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


@pytest.mark.asyncio
async def test_f3b_retry_backoff_cancellation_records_half_open_failure(
    monkeypatch,
):
    async def cancel_backoff(_delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        cancel_backoff,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN
    provider = _ExecutorProvider(outcomes=[httpx.ConnectError("transient")])
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
        )

    assert provider.calls == 1
    assert breaker.current_state is CircuitBreakerState.OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False


class _ImmutableGuardConnectError(httpx.ConnectError):
    def __init__(self, message):
        super().__init__(message)
        super().__setattr__("_immutable", True)

    def __setattr__(self, name, value):
        if (
            getattr(self, "_immutable", False)
            and not name.startswith("__")
        ):
            raise TypeError("immutable guard exception")
        super().__setattr__(name, value)


@pytest.mark.asyncio
async def test_f3b_immutable_provider_like_guard_error_bypasses_handler_fallback():
    providers = [
        _ProviderLikeGuardChatProvider("p1"),
        _ProviderLikeGuardChatProvider("p2"),
    ]
    executor = ProviderExecutor(
        _PerProviderBreakerManager(providers),
        retry_policy=RetryPolicy(max_retries=2),
    )
    handler = ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_HandlerRouting(providers),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        generated_asset_canonicalizer=_PassthroughCanonicalizer(),
    )
    control_plane_error = _ImmutableGuardConnectError(
        "lease store unavailable"
    )

    async def guard():
        raise control_plane_error

    with pytest.raises(_ImmutableGuardConnectError) as raised:
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            recovery_pre_attempt_guard=guard,
        )

    assert raised.value is control_plane_error
    assert isinstance(raised.value.__cause__, ProviderRecoveryGuardError)
    assert raised.value.__cause__.original_error is raised.value
    assert [provider.calls for provider in providers] == [0, 0]

@pytest.mark.asyncio
async def test_f3b_external_caller_cancellation_preserves_half_open_no_failure():
    breaker = CircuitBreaker(
        provider_name="p1",
        failure_threshold=1,
        reset_timeout=60,
        success_threshold=1,
    )
    breaker._state = CircuitBreakerState.HALF_OPEN

    class _BlockingProvider:
        name = "p1"

        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.chat = SimpleNamespace(chat=self._chat)

        async def _chat(self, **kwargs):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    provider = _BlockingProvider()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=0),
    )
    budget = ProviderCallBudget(
        deadline_monotonic=time.monotonic() + 30.0,
        max_retries=0,
    )

    task = asyncio.create_task(
        executor.execute(
            provider=provider,
            body={"model": "logical-model"},
            timeout=5.0,
            call_budget=budget,
        )
    )
    await provider.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.cancelled.is_set()
    assert breaker.current_state is CircuitBreakerState.HALF_OPEN
    assert breaker.failure_count == 0
    assert breaker.success_count == 0
    assert breaker._half_open_lock.locked() is False

