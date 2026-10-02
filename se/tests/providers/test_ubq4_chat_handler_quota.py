from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.application.user_inference_quota import (
    NormalizedInferenceUsage,
    UserInferenceQuotaContextError,
)
from se.src.provider.exceptions import ProviderError
from se.src.provider.handlers.chat_handler import ChatExecutionHandler


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, **kwargs):
        return list(self.providers)


class _Provider:
    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.probes = 0

    async def has_capability(self, *args, **kwargs):
        self.probes += 1
        self.events.append(f"probe:{self.name}")
        return True


class _Executor:
    def __init__(self, outcomes, events, *, max_retries=1):
        self.outcomes = {
            name: list(values)
            for name, values in outcomes.items()
        }
        self.events = events
        self.calls = []
        self.retry_policy = SimpleNamespace(max_retries=max_retries)

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, call_budget, **kwargs):
        self.calls.append(provider.name)
        self.events.append(f"execute:{provider.name}")
        outcome = self.outcomes[provider.name].pop(0)
        if callable(outcome):
            outcome = outcome(call_budget)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def execute_stream(self, *, provider, call_budget, **kwargs):
        self.calls.append(provider.name)
        self.events.append(f"execute_stream:{provider.name}")
        for outcome in self.outcomes[provider.name]:
            if isinstance(outcome, BaseException):
                raise outcome
            yield outcome


class _Quota:
    enabled = True

    def __init__(
        self,
        events,
        *,
        settle_error: Exception | None = None,
    ):
        self.events = events
        self.settle_error = settle_error
        self.reserve_calls = 0
        self.settlements = []

    async def reserve(self, *, context, body, streaming_mode):
        self.reserve_calls += 1
        self.events.append("reserve")
        if context != "trusted-context":
            raise UserInferenceQuotaContextError(
                "trusted quota context required"
            )
        return SimpleNamespace(id="admission")

    @staticmethod
    def _known_usage(provider="p1", model="logical-model"):
        return NormalizedInferenceUsage(
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            compute_units=None,
            cost_usd=None,
            normalization_identity="test-normalizer",
            provider=provider,
            model=model,
        )

    def normalize_gateway_usage(self, response):
        self.events.append("normalize_gateway")
        return self._known_usage(
            provider=getattr(
                getattr(response, "metadata", None),
                "provider",
                "p1",
            ),
            model=getattr(response, "model", "logical-model"),
        )

    def normalize_stream_usage(self, *, usage, provider, model):
        self.events.append("normalize_stream")
        if usage is None:
            return NormalizedInferenceUsage(
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                compute_units=None,
                cost_usd=None,
                normalization_identity="test-normalizer",
                provider=provider,
                model=model,
            )
        return self._known_usage(provider=provider, model=model)

    async def settle_success(self, admission, usage):
        self.events.append("settle")
        self.settlements.append(usage)
        if self.settle_error is not None:
            raise self.settle_error


class _DisabledQuota(_Quota):
    enabled = False

    async def reserve(self, **kwargs):
        raise AssertionError("feature-OFF must not call UBQ reserve")


class _Canonicalizer:
    def __init__(
        self,
        events,
        *,
        canonicalize_error: Exception | None = None,
        finalize_error: Exception | None = None,
    ):
        self.events = events
        self.canonicalize_error = canonicalize_error
        self.finalize_error = finalize_error

    async def canonicalize(self, response, *, owner_user_id):
        self.events.append("canonicalize")
        if self.canonicalize_error is not None:
            raise self.canonicalize_error
        return response


class _ObservedHandler(ChatExecutionHandler):
    def __init__(self, *args, events, **kwargs):
        self._events = events
        super().__init__(*args, **kwargs)

    def _new_call_budget(self, caller_deadline_monotonic=None):
        self._events.append("budget")
        return super()._new_call_budget(caller_deadline_monotonic)


class _ObservedAssembler:
    def __init__(self, canonicalizer, *, owner_user_id=None):
        self._canonicalizer = canonicalizer
        self.media_seen = False

    def observe(self, chunk):
        self._canonicalizer.events.append("observe")
        return chunk

    async def finalize(self):
        self._canonicalizer.events.append("finalize")
        if self._canonicalizer.finalize_error is not None:
            raise self._canonicalizer.finalize_error
        return None


class _SettlementError(RuntimeError):
    pass


def _response(provider="p1"):
    return SimpleNamespace(
        model="logical-model",
        usage=SimpleNamespace(),
        metadata=SimpleNamespace(provider=provider),
    )


def _stream_chunk(
    provider="p1",
    *,
    with_usage=True,
    finish_reason=None,
    content=None,
    reasoning_content=None,
    tool_calls=None,
    content_parts=None,
    extra_choices=None,
):
    choices = [
        SimpleNamespace(
            finish_reason=finish_reason,
            delta=SimpleNamespace(
                content=content,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
            ),
        )
    ]
    choices.extend(extra_choices or [])
    return SimpleNamespace(
        model="logical-model",
        usage=(SimpleNamespace(total_tokens=5) if with_usage else None),
        metadata=SimpleNamespace(
            provider=provider,
            content_parts=content_parts,
        ),
        choices=choices,
    )


def _handler(
    providers,
    executor,
    quota,
    events,
    *,
    canonicalizer=None,
):
    return _ObservedHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        inference_quota=quota,
        generated_asset_canonicalizer=(
            canonicalizer or _Canonicalizer(events)
        ),
        events=events,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_context", [None, "malformed"])
async def test_ubq4_handler_fails_closed_before_budget_and_provider_probe(
    bad_context,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor({"p1": [_response()]}, events)
    handler = _handler([provider], executor, quota, events)

    with pytest.raises(UserInferenceQuotaContextError):
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            quota_context=bad_context,
        )

    assert events == ["reserve"]
    assert provider.probes == 0
    assert executor.calls == []


@pytest.mark.asyncio
async def test_ubq4_reserves_once_across_fallback_and_keeps_tokens_unknown():
    events = []
    first = _Provider("p1", events)
    second = _Provider("p2", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [ProviderError("first failed", provider_name="p1")],
            "p2": [_response("p2")],
        },
        events,
    )
    handler = _handler([first, second], executor, quota, events)

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    )

    assert result.metadata.provider == "p2"
    assert quota.reserve_calls == 1
    assert executor.calls == ["p1", "p2"]
    assert events.index("reserve") < events.index("budget")
    assert len(quota.settlements) == 1
    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_retry_success_keeps_attempt_local_tokens_unknown():
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)

    def consume_retry_then_succeed(call_budget):
        assert call_budget.try_consume_retry() is True
        return _response("p1")

    executor = _Executor(
        {"p1": [consume_retry_then_succeed]},
        events,
        max_retries=1,
    )
    handler = _handler([provider], executor, quota, events)

    await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    )

    assert len(quota.settlements) == 1
    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_clean_nonstream_settles_before_cas_even_when_cas_fails():
    events = []
    first = _Provider("p1", events)
    second = _Provider("p2", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [_response("p1")],
            "p2": [_response("p2")],
        },
        events,
    )
    canonicalizer = _Canonicalizer(
        events,
        canonicalize_error=RuntimeError("CAS terminal failure"),
    )
    handler = _handler(
        [first, second],
        executor,
        quota,
        events,
        canonicalizer=canonicalizer,
    )

    with pytest.raises(RuntimeError, match="CAS terminal failure"):
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            quota_context="trusted-context",
        )

    assert executor.calls == ["p1"]
    assert second.probes == 0
    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)
    assert events.index("settle") < events.index("canonicalize")


@pytest.mark.asyncio
async def test_ubq4_settlement_failure_is_terminal_and_never_falls_back():
    events = []
    first = _Provider("p1", events)
    second = _Provider("p2", events)
    quota = _Quota(
        events,
        settle_error=_SettlementError("settlement failed"),
    )
    executor = _Executor(
        {
            "p1": [_response("p1")],
            "p2": [_response("p2")],
        },
        events,
    )
    handler = _handler([first, second], executor, quota, events)

    with pytest.raises(_SettlementError, match="settlement failed"):
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            quota_context="trusted-context",
        )

    assert executor.calls == ["p1"]
    assert second.probes == 0
    assert "canonicalize" not in events


@pytest.mark.asyncio
async def test_ubq4_stream_captures_usage_before_observe_and_settles_before_finalize(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                )
            ]
        },
        events,
        max_retries=0,
    )
    canonicalizer = _Canonicalizer(
        events,
        finalize_error=RuntimeError("CAS finalize failure"),
    )
    handler = _handler(
        [provider],
        executor,
        quota,
        events,
        canonicalizer=canonicalizer,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    with pytest.raises(RuntimeError, match="CAS finalize failure"):
        async for _ in handler.stream_with_fallback(
            object(),
            {"model": "logical-model"},
            quota_context="trusted-context",
        ):
            pass

    assert events.index("normalize_stream") < events.index("observe")
    assert events.index("settle") < events.index("finalize")
    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)


@pytest.mark.asyncio
async def test_ubq4_feature_off_preserves_legacy_handler_path():
    events = []
    provider = _Provider("p1", events)
    quota = _DisabledQuota(events)
    response = _response("p1")
    executor = _Executor({"p1": [response]}, events)
    handler = _handler([provider], executor, quota, events)

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result is response
    assert quota.reserve_calls == 0
    assert quota.settlements == []
    assert "settle" not in events
    assert events == [
        "budget",
        "probe:p1",
        "execute:p1",
        "canonicalize",
    ]


@pytest.mark.asyncio
async def test_ubq4_stream_early_usage_without_terminal_evidence_stays_unknown(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk("p1", with_usage=True),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
            ]
        },
        events,
        max_retries=0,
    )
    canonicalizer = _Canonicalizer(events)
    handler = _handler(
        [provider],
        executor,
        quota,
        events,
        canonicalizer=canonicalizer,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None
    assert events.index("settle") < events.index("finalize")


@pytest.mark.asyncio
async def test_ubq4_stream_terminal_usage_is_trusted(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                )
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)


@pytest.mark.asyncio
async def test_ubq4_stream_trailing_usage_after_terminal_marker_is_trusted(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
                _stream_chunk("p1", with_usage=True),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)


@pytest.mark.asyncio
async def test_ubq4_post_terminal_usage_with_content_stays_unknown(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    content="late semantic output",
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_post_terminal_usage_with_tool_call_stays_unknown(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    tool_calls=[SimpleNamespace(id="call-late")],
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_post_terminal_usage_with_cas_content_parts_stays_unknown(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    content_parts=[
                        {"type": "image", "data": {"source": "provider"}}
                    ],
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_later_terminal_usage_replaces_prior_terminal_marker(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)

@pytest.mark.asyncio
async def test_ubq4_terminal_usage_then_later_content_without_usage_revokes_candidate(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    content="continued semantic output",
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_terminal_usage_then_later_tool_without_usage_revokes_candidate(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    tool_calls=[SimpleNamespace(id="continued-call")],
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_terminal_usage_then_later_cas_content_without_usage_revokes_candidate(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    content_parts=[
                        {"type": "image", "data": {"source": "provider"}}
                    ],
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_terminal_usage_then_multichoice_progression_revokes_candidate(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    extra_choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                content="other choice continued",
                                reasoning_content=None,
                                tool_calls=None,
                            ),
                        )
                    ],
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_invalidated_candidate_can_be_replaced_by_later_terminal_usage(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    content="continued semantic output",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)

@pytest.mark.asyncio
async def test_ubq4_terminal_usage_then_later_finish_without_usage_revokes_candidate(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                ),
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                ),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None

@pytest.mark.asyncio
async def test_ubq4_same_chunk_partial_multichoice_terminal_usage_stays_unknown(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                    extra_choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                content="unfinished choice output",
                                reasoning_content=None,
                                tool_calls=None,
                            ),
                        )
                    ],
                )
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_partial_multichoice_terminal_does_not_authorize_trailing_usage_only(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=False,
                    finish_reason="stop",
                    extra_choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                content="unfinished choice output",
                                reasoning_content=None,
                                tool_calls=None,
                            ),
                        )
                    ],
                ),
                _stream_chunk("p1", with_usage=True),
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert settled.input_tokens is None
    assert settled.output_tokens is None
    assert settled.total_tokens is None


@pytest.mark.asyncio
async def test_ubq4_same_chunk_all_choices_terminal_usage_is_known(
    monkeypatch,
):
    events = []
    provider = _Provider("p1", events)
    quota = _Quota(events)
    executor = _Executor(
        {
            "p1": [
                _stream_chunk(
                    "p1",
                    with_usage=True,
                    finish_reason="stop",
                    extra_choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content=None,
                                tool_calls=None,
                            ),
                        )
                    ],
                )
            ]
        },
        events,
        max_retries=0,
    )
    handler = _handler([provider], executor, quota, events)
    monkeypatch.setattr(
        "se.src.provider.handlers.chat_handler.GeneratedAssetStreamAssembler",
        _ObservedAssembler,
    )

    async for _ in handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
        quota_context="trusted-context",
    ):
        pass

    settled = quota.settlements[0]
    assert (
        settled.input_tokens,
        settled.output_tokens,
        settled.total_tokens,
    ) == (2, 3, 5)

