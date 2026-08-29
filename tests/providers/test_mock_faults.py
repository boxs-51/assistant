import pytest

from src.provider.mock import MockProvider, MockScenario
from src.provider.exceptions import ProviderRateLimitError, ProviderUnavailableError


@pytest.mark.asyncio
async def test_persistent_rate_limit_fault():
    p = MockProvider(
        scenario=MockScenario(
            name="rate-limit",
            error_type="rate_limit",
            fail_operations={"chat"},
        )
    )
    with pytest.raises(ProviderRateLimitError):
        await p.chat.chat(body={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}]})


@pytest.mark.asyncio
async def test_fail_next_means_exactly_next_n_calls():
    p = MockProvider(
        scenario=MockScenario(
            name="fail-once",
            error_type="rate_limit",
            fail_operations={"chat"},
            fail_next=1,
        )
    )
    with pytest.raises(ProviderRateLimitError):
        await p.chat.chat(body={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}]})

    response = await p.chat.chat(body={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}]})
    assert response.provider == "mock"


@pytest.mark.asyncio
async def test_stream_fault_after_first_chunk():
    p = MockProvider(
        scenario=MockScenario(
            name="stream-fail",
            error_type="service_unavailable",
            fail_after_chunks=1,
        )
    )
    chunks = []
    with pytest.raises(ProviderUnavailableError):
        async for chunk in p.chat.chat_stream(
            body={"model": "mock-chat", "messages": [{"role": "user", "content": "one two three"}]}
        ):
            chunks.append(chunk)
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_stream_fault_before_first_chunk_when_zero():
    p = MockProvider(
        scenario=MockScenario(
            name="stream-fail-zero",
            error_type="service_unavailable",
            fail_after_chunks=0,
        )
    )
    with pytest.raises(ProviderUnavailableError):
        async for _ in p.chat.chat_stream(
            body={"model": "mock-chat", "messages": [{"role": "user", "content": "hello"}]}
        ):
            pass
