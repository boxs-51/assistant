from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.provider.exceptions import (
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderRateLimitError,
)
from se.src.provider.executor import ProviderExecutor
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.policies.retry import RetryPolicy
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.adapters.policy import DefaultAgentExecutionPolicy
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentExecutionContext,
    AgentEventName,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceUsage,
)
from se.src.runtimes.agent.runtime import AgentRuntime


class _Clock:
    def __init__(self, current: float):
        self.current = current

    def monotonic(self) -> float:
        return self.current


class _ContextBuilder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(InferenceMessage(role="user", content="hello"),),
        )


class _Tools:
    async def execute_many(self, context, requests, *, max_parallel):
        return []


class _AdvanceOnInferenceRequested:
    def __init__(self, clock: _Clock, delay: float):
        self.clock = clock
        self.delay = delay

    async def publish(self, event):
        if event.event_name == AgentEventName.INFERENCE_REQUESTED:
            self.clock.current += self.delay


class _CapturingInference:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(role="assistant", content="done"),
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="fake",
            model="fake-model",
        )


def _agent_context(clock: _Clock) -> AgentExecutionContext:
    agent = AgentDefinition(
        name="r10-f-agent",
        goal="deadline handoff",
        instruction="test",
        tools=[],
    )
    return AgentExecutionContext.create(
        execution_id="exec-r10-f",
        agent_id=agent.name,
        session_id="session-r10-f",
        correlation_id="corr-r10-f",
        identity=Identity(
            user_id="r10-f-user",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            max_iterations=1,
            timeout_seconds=5.0,
            iteration_timeout_seconds=5.0,
            inference_timeout_seconds=2.0,
            tool_timeout_seconds=1.0,
        ),
        agent=agent,
        input={"prompt": "hello"},
        clock=clock,
        now_monotonic=clock.monotonic(),
    )


@pytest.mark.asyncio
async def test_r10_f_agent_runtime_preserves_absolute_deadline_across_pre_handoff_await(
    monkeypatch,
):
    clock = _Clock(100.0)
    monkeypatch.setattr(
        "se.src.runtimes.agent.runtime.monotonic",
        clock.monotonic,
    )
    inference = _CapturingInference()
    runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=inference,
        tool_execution=_Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=_AdvanceOnInferenceRequested(clock, 0.75),
    )

    result = await runtime.execute(_agent_context(clock))

    assert result.output == "done"
    assert len(inference.requests) == 1
    request = inference.requests[0]
    assert request.timeout_seconds == pytest.approx(2.0)
    assert request.deadline_monotonic == pytest.approx(102.0)
    assert clock.monotonic() == pytest.approx(100.75)
    assert request.deadline_monotonic - clock.monotonic() == pytest.approx(1.25)


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


class _Provider:
    def __init__(self, name: str):
        self.name = name
        self.probe_timeouts = []
        self.probe_calls = 0

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_calls += 1
        self.probe_timeouts.append(timeout)
        return True


class _RecordingExecutor:
    def __init__(self, outcomes):
        self.retry_policy = SimpleNamespace(max_retries=2)
        self.outcomes = list(outcomes)
        self.budgets = []
        self.configured_timeouts = []
        self.provider_calls = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, call_budget, timeout, **kwargs):
        self.provider_calls.append(provider.name)
        self.budgets.append(call_budget)
        self.configured_timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _handler(providers, executor, *, timeout=60.0):
    return ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_r10_f_caller_deadline_shorter_than_provider_timeout_wins(monkeypatch):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    provider = _Provider("p1")
    executor = _RecordingExecutor(["ok"])

    result = await _handler(
        [provider],
        executor,
        timeout=60.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
        deadline_monotonic=105.0,
    )

    assert result == "ok"
    assert provider.probe_timeouts == [pytest.approx(5.0)]
    assert executor.budgets[0].deadline_monotonic == pytest.approx(105.0)
    assert executor.configured_timeouts == [60.0]


@pytest.mark.asyncio
async def test_r10_f_provider_timeout_shorter_than_caller_deadline_wins(monkeypatch):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    provider = _Provider("p1")
    executor = _RecordingExecutor(["ok"])

    result = await _handler(
        [provider],
        executor,
        timeout=3.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
        deadline_monotonic=110.0,
    )

    assert result == "ok"
    assert provider.probe_timeouts == [pytest.approx(3.0)]
    assert executor.budgets[0].deadline_monotonic == pytest.approx(103.0)


@pytest.mark.asyncio
async def test_r10_f_expired_caller_deadline_starts_no_probe_or_attempt(monkeypatch):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    provider = _Provider("p1")
    executor = _RecordingExecutor(["never"])

    with pytest.raises(ProviderDeadlineExceededError):
        await _handler(
            [provider],
            executor,
            timeout=60.0,
        ).execute_with_fallback(
            object(),
            {"model": "logical-model"},
            deadline_monotonic=99.0,
        )

    assert provider.probe_calls == 0
    assert executor.provider_calls == []


@pytest.mark.asyncio
async def test_r10_f_direct_handler_without_caller_deadline_uses_provider_timeout(monkeypatch):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 50.0,
    )
    provider = _Provider("p1")
    executor = _RecordingExecutor(["ok"])

    result = await _handler(
        [provider],
        executor,
        timeout=4.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "ok"
    assert provider.probe_timeouts == [pytest.approx(4.0)]
    assert executor.budgets[0].deadline_monotonic == pytest.approx(54.0)


@pytest.mark.asyncio
async def test_r10_f_fallback_reuses_same_absolute_budget(monkeypatch):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    p1 = _Provider("p1")
    p2 = _Provider("p2")
    executor = _RecordingExecutor(
        [ProviderError("p1 failed", provider_name="p1"), "ok"]
    )

    result = await _handler(
        [p1, p2],
        executor,
        timeout=60.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
        deadline_monotonic=106.0,
    )

    assert result == "ok"
    assert executor.provider_calls == ["p1", "p2"]
    assert executor.budgets[0] is executor.budgets[1]
    assert executor.budgets[1].deadline_monotonic == pytest.approx(106.0)


def test_r10_f_deadline_is_process_local_and_not_serialized():
    request = InferenceRequest(
        request_id="req-r10-f-process-local",
        execution_id="exec-r10-f",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=5.0,
        deadline_monotonic=123.0,
    )

    dumped = request.model_dump()

    assert request.deadline_monotonic == 123.0
    assert "deadline_monotonic" not in dumped


@pytest.mark.asyncio
async def test_r10_f_adapter_forwards_absolute_deadline_unchanged(monkeypatch):
    received = []

    class Handler:
        async def execute_with_fallback(
            self,
            http_client,
            body,
            *,
            deadline_monotonic=None,
        ):
            received.append(deadline_monotonic)
            raise RuntimeError("stop after capture")

    class Runtime:
        chat_handler = Handler()

    monkeypatch.setattr(
        "se.src.runtimes.agent.adapters.inference.monotonic",
        lambda: 100.0,
    )
    adapter = ProviderInferenceAdapter(Runtime(), object())
    request = InferenceRequest(
        request_id="req-r10-f-forward",
        execution_id="exec-r10-f",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=30.0,
        deadline_monotonic=105.0,
    )

    with pytest.raises(RuntimeError, match="stop after capture"):
        await adapter.complete(request)

    assert received == [105.0]


@pytest.mark.asyncio
async def test_r10_f_adapter_expired_absolute_deadline_starts_no_provider_task(monkeypatch):
    calls = 0

    class Handler:
        async def execute_with_fallback(
            self,
            http_client,
            body,
            *,
            deadline_monotonic=None,
        ):
            nonlocal calls
            calls += 1
            raise AssertionError("provider task must not start")

    class Runtime:
        chat_handler = Handler()

    monkeypatch.setattr(
        "se.src.runtimes.agent.adapters.inference.monotonic",
        lambda: 110.0,
    )
    adapter = ProviderInferenceAdapter(Runtime(), object())
    request = InferenceRequest(
        request_id="req-r10-f-expired",
        execution_id="exec-r10-f",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=30.0,
        deadline_monotonic=105.0,
    )

    with pytest.raises(asyncio.TimeoutError):
        await adapter.complete(request)

    assert calls == 0


class _Breaker:
    async def is_open(self):
        return False

    async def before_request(self):
        return None

    async def on_success(self):
        return None

    async def on_failure(self):
        return None


class _BreakerManager:
    def __init__(self):
        self.breakers = {}

    async def get_breaker(self, provider_name):
        return self.breakers.setdefault(provider_name, _Breaker())


class _RetryProvider(_Provider):
    def __init__(self, name: str, outcomes):
        super().__init__(name)
        self.outcomes = list(outcomes)
        self.chat_attempts = 0
        self.provider_tasks = []
        self.chat = SimpleNamespace(chat=self._chat)

    async def _chat(self, **kwargs):
        self.chat_attempts += 1
        self.provider_tasks.append(asyncio.current_task())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_r10_f_cancellation_during_retry_backoff_starts_no_retry_or_fallback(
    monkeypatch,
):
    backoff_started = asyncio.Event()
    backoff_cancelled = asyncio.Event()

    async def blocking_sleep(delay):
        backoff_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            backoff_cancelled.set()
            raise

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        blocking_sleep,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    p1 = _RetryProvider(
        "p1",
        [ProviderRateLimitError("retry later", provider_name="p1")],
    )
    p2 = _RetryProvider("p2", ["should-not-run"])
    breaker_manager = _BreakerManager()
    executor = ProviderExecutor(
        breaker_manager,
        retry_policy=RetryPolicy(max_retries=1),
    )
    handler = ChatExecutionHandler(
        providers={"p1": p1, "p2": p2},
        routing_policy=_Routing([p1, p2]),
        executor=executor,
        circuit_breaker_manager=breaker_manager,
        timeout=30.0,
    )

    class Runtime:
        chat_handler = handler

    cancellation = asyncio.Event()
    adapter = ProviderInferenceAdapter(Runtime(), object())
    request = InferenceRequest(
        request_id="req-r10-f-cancel",
        execution_id="exec-r10-f",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=20.0,
        cancellation_event=cancellation,
    )

    task = asyncio.create_task(adapter.complete(request))
    await backoff_started.wait()
    cancellation.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert backoff_cancelled.is_set()
    assert p1.chat_attempts == 1
    assert p2.probe_calls == 0
    assert p2.chat_attempts == 0
    assert len(p1.provider_tasks) == 1
    assert p1.provider_tasks[0] is not None
    assert p1.provider_tasks[0].done()
