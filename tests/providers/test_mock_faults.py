import pytest
from src.provider.mock import MockProvider, MockScenario
from src.provider.exceptions import ProviderRateLimitError, ProviderUnavailableError

@pytest.mark.asyncio
async def test_rate_limit_fault():
    p=MockProvider(scenario=MockScenario(name="rate",error_type="rate_limit",fail_operations={"chat"}))
    with pytest.raises(ProviderRateLimitError):
        await p.chat.chat(body={"model":"mock-chat","messages":[{"role":"user","content":"x"}]})

@pytest.mark.asyncio
async def test_fail_next_is_consumed():
    p=MockProvider(scenario=MockScenario(error_type="rate_limit",fail_operations={"chat"},fail_next=1))
    ok=await p.chat.chat(body={"model":"mock-chat","messages":[{"role":"user","content":"x"}]})
    assert ok.provider == "mock"
    assert p.snapshot()["calls"]["chat"] == 1

@pytest.mark.asyncio
async def test_stream_fault_after_first_chunk():
    p=MockProvider(scenario=MockScenario(name="stream-fail",error_type="service_unavailable",fail_after_chunks=1))
    chunks=[]
    with pytest.raises(ProviderUnavailableError):
        async for c in p.chat.chat_stream(body={"model":"mock-chat","messages":[{"role":"user","content":"one two three"}]}):
            chunks.append(c)
    assert chunks
