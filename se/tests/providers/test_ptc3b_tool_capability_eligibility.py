from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.domain.schemas import ModelCapability
from se.src.provider.exceptions import (
    NoAvailableProviderError,
    ProviderDeadlineExceededError,
    ProviderUnavailableError,
)
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.policies.routing_policy import RoutingPolicy
from se.src.provider.retry_contracts import ProviderCallBudget


class _Provider:
    def __init__(self, name: str, capabilities=None):
        self.name = name
        self.capabilities = dict(capabilities or {})
        self.calls = []

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.calls.append((model, capability, timeout))
        outcome = self.capabilities.get(capability, True)
        if isinstance(outcome, BaseException):
            raise outcome
        return bool(outcome)


class _BlockingToolProvider(_Provider):
    def __init__(self, name: str):
        super().__init__(name)
        self.tool_probe_started = asyncio.Event()
        self.tool_probe_cancelled = asyncio.Event()

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.calls.append((model, capability, timeout))
        if capability == ModelCapability.TOOL_CALLING:
            self.tool_probe_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.tool_probe_cancelled.set()
                raise
        return True


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


class _Executor:
    def __init__(self, *, max_retries=2):
        self.retry_policy = SimpleNamespace(max_retries=max_retries)
        self.provider_calls = []
        self.stream_calls = []
        self.budgets = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, call_budget, **kwargs):
        self.provider_calls.append(provider.name)
        self.budgets.append(call_budget)
        return f"{provider.name}-ok"

    async def execute_stream(self, *, provider, call_budget, **kwargs):
        self.stream_calls.append(provider.name)
        self.budgets.append(call_budget)
        yield f"{provider.name}-chunk"


def _handler(providers, executor=None, *, timeout=30.0, routing=None):
    executor = executor or _Executor()
    return (
        ChatExecutionHandler(
            providers={provider.name: provider for provider in providers},
            routing_policy=routing or _Routing(providers),
            executor=executor,
            circuit_breaker_manager=SimpleNamespace(),
            timeout=timeout,
        ),
        executor,
    )


def _tool_body(model="logical-model", *, tools=True, metadata=None):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if tools is not None:
        body["tools"] = (
            [
                {
                    "name": "web.search",
                    "description": "Search",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                }
            ]
            if tools
            else []
        )
    if metadata is not None:
        body["metadata"] = metadata
    return body


def _caps(provider):
    return [capability for _, capability, _ in provider.calls]


@pytest.mark.asyncio
async def test_ptc3b_no_tools_nonstream_probes_chat_only():
    provider = _Provider("p1")
    handler, executor = _handler([provider])

    result = await handler.execute_with_fallback(
        object(),
        _tool_body(tools=None),
    )

    assert result == "p1-ok"
    assert _caps(provider) == [ModelCapability.CHAT]
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_empty_tools_nonstream_behaves_like_no_tools():
    provider = _Provider("p1")
    handler, executor = _handler([provider])

    result = await handler.execute_with_fallback(
        object(),
        _tool_body(tools=False),
    )

    assert result == "p1-ok"
    assert _caps(provider) == [ModelCapability.CHAT]
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_null_tools_nonstream_behaves_like_no_tools():
    provider = _Provider("p1")
    handler, executor = _handler([provider])
    body = _tool_body(tools=None)
    body["tools"] = None

    result = await handler.execute_with_fallback(object(), body)

    assert result == "p1-ok"
    assert _caps(provider) == [ModelCapability.CHAT]
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_tools_nonstream_requires_chat_then_tool_calling():
    provider = _Provider("p1")
    handler, executor = _handler([provider])

    result = await handler.execute_with_fallback(
        object(),
        _tool_body(),
    )

    assert result == "p1-ok"
    assert _caps(provider) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_tool_ineligible_provider_skips_to_eligible_provider_without_retry_charge():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    result = await handler.execute_with_fallback(object(), _tool_body())

    assert result == "p2-ok"
    assert _caps(p1) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert _caps(p2) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert executor.provider_calls == ["p2"]
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_ptc3b_consumed_retry_budget_survives_later_tool_ineligible_skip():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])
    budget = ProviderCallBudget(
        deadline_monotonic=10**12,
        max_retries=2,
    )
    assert budget.try_consume_retry() is True
    handler._new_call_budget = lambda _deadline=None: budget

    result = await handler.execute_with_fallback(object(), _tool_body())

    assert result == "p2-ok"
    assert executor.provider_calls == ["p2"]
    assert executor.budgets[0] is budget
    assert budget.retries_used == 1


@pytest.mark.asyncio
async def test_ptc3b_all_tool_ineligible_has_no_network_attempt_and_no_fake_provider_cause():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider(
        "p2",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    handler, executor = _handler([p1, p2])

    with pytest.raises(NoAvailableProviderError) as raised:
        await handler.execute_with_fallback(object(), _tool_body())

    assert executor.provider_calls == []
    assert raised.value.provider_name is None
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_ptc3b_chat_false_short_circuits_tool_probe_before_fallback():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: False,
            ModelCapability.TOOL_CALLING: True,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    result = await handler.execute_with_fallback(object(), _tool_body())

    assert result == "p2-ok"
    assert _caps(p1) == [ModelCapability.CHAT]
    assert executor.provider_calls == ["p2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("routing_type", ["strict", "direct"])
async def test_ptc3b_strict_direct_tool_ineligible_fails_closed_without_second_provider(
    routing_type,
):
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider("p2")
    config = SimpleNamespace(
        priority=["p1", "p2"],
        routing_rules_path="__ptc3b_missing_rules__.yaml",
        enable_fallback=True,
    )
    routing = RoutingPolicy(
        providers={"p1": p1, "p2": p2},
        config=config,
    )
    handler, executor = _handler(
        [p1, p2],
        routing=routing,
    )

    with pytest.raises(NoAvailableProviderError):
        await handler.execute_with_fallback(
            object(),
            _tool_body(
                metadata={
                    "routing": {
                        "type": routing_type,
                        "prefer_provider": "p1",
                    }
                }
            ),
        )

    assert _caps(p1) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert p2.calls == []
    assert executor.provider_calls == []


@pytest.mark.asyncio
async def test_ptc3b_enable_fallback_false_tool_ineligible_stays_single_provider():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider("p2")
    config = SimpleNamespace(
        priority=["p1", "p2"],
        routing_rules_path="__ptc3b_missing_rules__.yaml",
        enable_fallback=False,
    )
    routing = RoutingPolicy(
        providers={"p1": p1, "p2": p2},
        config=config,
    )
    handler, executor = _handler(
        [p1, p2],
        routing=routing,
    )

    with pytest.raises(NoAvailableProviderError):
        await handler.execute_with_fallback(object(), _tool_body())

    assert _caps(p1) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert p2.calls == []
    assert executor.provider_calls == []


@pytest.mark.asyncio
async def test_ptc3b_tool_probe_error_is_provider_failure_and_fallback_may_continue():
    first_error = ProviderUnavailableError(
        "tool probe failed",
        provider_name="p1",
    )
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: first_error,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    result = await handler.execute_with_fallback(object(), _tool_body())

    assert result == "p2-ok"
    assert executor.provider_calls == ["p2"]
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_ptc3b_final_tool_probe_error_preserves_last_provider_and_cause():
    p1_error = ProviderUnavailableError(
        "p1 tool probe failed",
        provider_name="p1",
    )
    p2_error = ProviderUnavailableError(
        "p2 tool probe failed",
        provider_name="p2",
    )
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: p1_error,
        },
    )
    p2 = _Provider(
        "p2",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: p2_error,
        },
    )
    handler, executor = _handler([p1, p2])

    with pytest.raises(NoAvailableProviderError) as raised:
        await handler.execute_with_fallback(object(), _tool_body())

    assert executor.provider_calls == []
    assert raised.value.provider_name == "p2"
    assert raised.value.__cause__ is p2_error


@pytest.mark.asyncio
async def test_ptc3b_cancellation_during_tool_probe_starts_no_executor_or_fallback():
    p1 = _BlockingToolProvider("p1")
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    task = asyncio.create_task(
        handler.execute_with_fallback(object(), _tool_body())
    )
    await asyncio.wait_for(
        p1.tool_probe_started.wait(),
        timeout=0.20,
    )
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert executor.provider_calls == []
    assert p2.calls == []


@pytest.mark.asyncio
async def test_ptc3b_second_probe_recomputes_remaining_from_same_budget():
    class _SequencedBudget:
        def __init__(self):
            self.remaining_values = [9.0, 8.0, 7.0, 6.0]
            self.retries_used = 0

        def remaining_seconds(self, *, now_monotonic):
            return self.remaining_values.pop(0)

    provider = _Provider("p1")
    handler, executor = _handler([provider], timeout=10.0)
    budget = _SequencedBudget()
    handler._new_call_budget = lambda _deadline=None: budget

    result = await handler.execute_with_fallback(object(), _tool_body())

    assert result == "p1-ok"
    assert [timeout for _, _, timeout in provider.calls] == [
        pytest.approx(9.0),
        pytest.approx(7.0),
    ]
    assert executor.budgets[0] is budget


@pytest.mark.asyncio
async def test_ptc3b_tool_probe_is_hard_bounded_by_r10_budget_and_drained():
    import time

    p1 = _BlockingToolProvider("p1")
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2], timeout=0.03)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )
    handler._new_call_budget = lambda _deadline=None: budget

    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(
            handler.execute_with_fallback(object(), _tool_body()),
            timeout=0.20,
        )

    assert p1.tool_probe_started.is_set()
    assert p1.tool_probe_cancelled.is_set()
    assert p2.calls == []
    assert executor.provider_calls == []
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_ptc3b_stream_tool_probe_is_hard_bounded_before_stream_and_drained():
    import time

    p1 = _BlockingToolProvider("p1")
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2], timeout=0.03)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )
    handler._new_call_budget = lambda _deadline=None: budget
    stream = handler.stream_with_fallback(object(), _tool_body())

    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(stream.__anext__(), timeout=0.20)

    assert p1.tool_probe_started.is_set()
    assert p1.tool_probe_cancelled.is_set()
    assert p2.calls == []
    assert executor.stream_calls == []
    assert budget.retries_used == 0
    await stream.aclose()


@pytest.mark.asyncio
async def test_ptc3b_history_tool_messages_without_tools_do_not_require_tool_calling():
    provider = _Provider("p1")
    handler, executor = _handler([provider])
    body = {
        "model": "logical-model",
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "done"},
        ],
    }

    result = await handler.execute_with_fallback(object(), body)

    assert result == "p1-ok"
    assert _caps(provider) == [ModelCapability.CHAT]
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_concurrent_tool_and_no_tool_requests_do_not_leak_capability_state():
    provider = _Provider("p1")
    handler, executor = _handler([provider])

    tool_result, plain_result = await asyncio.gather(
        handler.execute_with_fallback(
            object(),
            _tool_body(model="with-tools"),
        ),
        handler.execute_with_fallback(
            object(),
            _tool_body(model="without-tools", tools=None),
        ),
    )

    assert {tool_result, plain_result} == {"p1-ok"}
    by_model = {}
    for model, capability, _ in provider.calls:
        by_model.setdefault(model, []).append(capability)

    assert by_model["with-tools"] == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert by_model["without-tools"] == [ModelCapability.CHAT]
    assert executor.provider_calls.count("p1") == 2


@pytest.mark.asyncio
async def test_ptc3b_stream_no_tools_probes_chat_stream_only():
    provider = _Provider("p1")
    handler, executor = _handler([provider])

    chunks = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(tools=None),
        )
    ]

    assert chunks == ["p1-chunk"]
    assert _caps(provider) == [ModelCapability.CHAT_STREAM]
    assert executor.stream_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_stream_tools_requires_chat_stream_then_tool_calling():
    provider = _Provider("p1")
    handler, executor = _handler([provider])

    chunks = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(),
        )
    ]

    assert chunks == ["p1-chunk"]
    assert _caps(provider) == [
        ModelCapability.CHAT_STREAM,
        ModelCapability.TOOL_CALLING,
    ]
    assert executor.stream_calls == ["p1"]


@pytest.mark.asyncio
async def test_ptc3b_stream_tool_ineligible_skips_before_stream_creation():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT_STREAM: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    chunks = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(),
        )
    ]

    assert chunks == ["p2-chunk"]
    assert _caps(p1) == [
        ModelCapability.CHAT_STREAM,
        ModelCapability.TOOL_CALLING,
    ]
    assert executor.stream_calls == ["p2"]
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_ptc3b_stream_chat_false_short_circuits_tool_probe():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT_STREAM: False,
            ModelCapability.TOOL_CALLING: True,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    chunks = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(),
        )
    ]

    assert chunks == ["p2-chunk"]
    assert _caps(p1) == [ModelCapability.CHAT_STREAM]
    assert executor.stream_calls == ["p2"]


@pytest.mark.asyncio
async def test_ptc3b_stream_tool_probes_use_exact_logical_model():
    provider = _Provider("p1")
    handler, _ = _handler([provider])

    _ = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(model="logical/tool-model"),
        )
    ]

    assert [model for model, _, _ in provider.calls] == [
        "logical/tool-model",
        "logical/tool-model",
    ]



class _NativeAdapterExecutor(_Executor):
    def __init__(self):
        super().__init__()
        self.adapter_calls = []

    async def execute(self, *, provider, body, call_budget, **kwargs):
        from se.src.provider.gemini.converters.chats.request import (
            RequestChats as GeminiRequestChats,
        )
        from se.src.provider.ollama.converters.chat.request import (
            RequestChats as OllamaRequestChats,
        )
        from se.src.provider.openai.converters.chats.request import (
            RequestChats as OpenAIRequestChats,
        )

        self.provider_calls.append(provider.name)
        self.budgets.append(call_budget)
        self.adapter_calls.append(provider.name)

        if provider.name == "openai":
            return OpenAIRequestChats().adapt_chat_request(body)
        if provider.name == "gemini":
            return GeminiRequestChats().adapt_chat(body)
        if provider.name == "ollama":
            return OllamaRequestChats().adapt_chat_request(body)
        raise AssertionError(f"unexpected provider {provider.name}")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["openai", "gemini", "ollama"])
async def test_ptc3b_cross_provider_native_adapter_runs_only_after_tool_eligibility(
    provider_name,
):
    provider = _Provider(provider_name)
    executor = _NativeAdapterExecutor()
    handler, _ = _handler([provider], executor=executor)

    prepared = await handler.execute_with_fallback(
        object(),
        _tool_body(model="provider-model"),
    )

    assert _caps(provider) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert executor.adapter_calls == [provider_name]

    if provider_name == "openai":
        assert prepared["tools"][0]["type"] == "function"
        assert prepared["tools"][0]["function"]["name"]
    elif provider_name == "gemini":
        assert prepared["tools"][0]["function_declarations"][0]["name"]
    else:
        assert prepared["tools"][0]["type"] == "function"
        assert prepared["tools"][0]["function"]["name"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["openai", "gemini", "ollama"])
async def test_ptc3b_cross_provider_tool_ineligible_never_enters_native_adapter(
    provider_name,
):
    provider = _Provider(
        provider_name,
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    executor = _NativeAdapterExecutor()
    handler, _ = _handler([provider], executor=executor)

    with pytest.raises(NoAvailableProviderError):
        await handler.execute_with_fallback(
            object(),
            _tool_body(model="provider-model"),
        )

    assert _caps(provider) == [
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
    ]
    assert executor.adapter_calls == []



@pytest.mark.asyncio
async def test_ptc3b_deadline_expiry_between_chat_and_tool_probe_starts_no_executor(
    monkeypatch,
):
    times = iter([100.0, 101.0, 111.0])
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: next(times),
    )
    provider = _Provider("p1")
    handler, executor = _handler([provider], timeout=10.0)

    with pytest.raises(ProviderDeadlineExceededError):
        await handler.execute_with_fallback(object(), _tool_body())

    assert _caps(provider) == [ModelCapability.CHAT]
    assert executor.provider_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("routing_type", ["strict", "direct"])
async def test_ptc3b_strict_direct_tool_probe_error_preserves_error_and_never_falls_back(
    routing_type,
):
    tool_error = ProviderUnavailableError(
        "tool capability probe failed",
        provider_name="p1",
    )
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT: True,
            ModelCapability.TOOL_CALLING: tool_error,
        },
    )
    p2 = _Provider("p2")
    config = SimpleNamespace(
        priority=["p1", "p2"],
        routing_rules_path="__ptc3b_missing_rules__.yaml",
        enable_fallback=True,
    )
    routing = RoutingPolicy(
        providers={"p1": p1, "p2": p2},
        config=config,
    )
    handler, executor = _handler([p1, p2], routing=routing)

    with pytest.raises(NoAvailableProviderError) as raised:
        await handler.execute_with_fallback(
            object(),
            _tool_body(
                metadata={
                    "routing": {
                        "type": routing_type,
                        "prefer_provider": "p1",
                    }
                }
            ),
        )

    assert p2.calls == []
    assert executor.provider_calls == []
    assert raised.value.provider_name == "p1"
    assert raised.value.__cause__ is tool_error


@pytest.mark.asyncio
async def test_ptc3b_stream_enable_fallback_false_tool_ineligible_stays_single_provider():
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT_STREAM: True,
            ModelCapability.TOOL_CALLING: False,
        },
    )
    p2 = _Provider("p2")
    config = SimpleNamespace(
        priority=["p1", "p2"],
        routing_rules_path="__ptc3b_missing_rules__.yaml",
        enable_fallback=False,
    )
    routing = RoutingPolicy(
        providers={"p1": p1, "p2": p2},
        config=config,
    )
    handler, executor = _handler([p1, p2], routing=routing)

    with pytest.raises(NoAvailableProviderError):
        async for _ in handler.stream_with_fallback(
            object(),
            _tool_body(),
        ):
            pass

    assert _caps(p1) == [
        ModelCapability.CHAT_STREAM,
        ModelCapability.TOOL_CALLING,
    ]
    assert p2.calls == []
    assert executor.stream_calls == []


@pytest.mark.asyncio
async def test_ptc3b_stream_tool_probe_error_falls_back_before_stream_creation():
    tool_error = ProviderUnavailableError(
        "stream tool probe failed",
        provider_name="p1",
    )
    p1 = _Provider(
        "p1",
        {
            ModelCapability.CHAT_STREAM: True,
            ModelCapability.TOOL_CALLING: tool_error,
        },
    )
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])

    chunks = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(),
        )
    ]

    assert chunks == ["p2-chunk"]
    assert executor.stream_calls == ["p2"]
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_ptc3b_stream_cancellation_during_tool_probe_never_starts_stream_or_fallback():
    p1 = _BlockingToolProvider("p1")
    p2 = _Provider("p2")
    handler, executor = _handler([p1, p2])
    stream = handler.stream_with_fallback(object(), _tool_body())

    read_task = asyncio.create_task(stream.__anext__())
    await asyncio.wait_for(
        p1.tool_probe_started.wait(),
        timeout=0.20,
    )
    read_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await read_task

    assert executor.stream_calls == []
    assert p2.calls == []
    await stream.aclose()


@pytest.mark.asyncio
async def test_ptc3b_stream_second_probe_recomputes_remaining_from_same_budget():
    class _SequencedBudget:
        def __init__(self):
            self.remaining_values = [9.0, 8.0, 7.0, 6.0]
            self.retries_used = 0

        def remaining_seconds(self, *, now_monotonic):
            return self.remaining_values.pop(0)

    provider = _Provider("p1")
    handler, executor = _handler([provider], timeout=10.0)
    budget = _SequencedBudget()
    handler._new_call_budget = lambda _deadline=None: budget

    chunks = [
        chunk
        async for chunk in handler.stream_with_fallback(
            object(),
            _tool_body(),
        )
    ]

    assert chunks == ["p1-chunk"]
    assert [timeout for _, _, timeout in provider.calls] == [
        pytest.approx(9.0),
        pytest.approx(7.0),
    ]
    assert executor.budgets[0] is budget
