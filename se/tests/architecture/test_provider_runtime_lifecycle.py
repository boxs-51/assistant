import pytest

from src.runtimes.provider.runtime import ProviderRuntime


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_provider_runtime_does_not_close_container_owned_http_client_on_stop():
    runtime = ProviderRuntime(circuit_breaker_manager=object())
    client = FakeHttpClient()
    runtime._http_client = client

    await runtime.stop()

    assert client.closed is False