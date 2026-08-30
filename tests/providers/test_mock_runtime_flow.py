import httpx
import pytest

from src.circuit_breaker import CircuitBreakerManager
from src.infrastructure.config.schemas import (ConfigSchema, ProviderSettings,ProviderConfig,
                                               CircuitBreakerSettings, CircuitBreakerProviderSettings)
from src.provider.discovery import ProviderDiscovery
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.registry import ProviderRegistry
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler


@pytest.fixture
def offline_config():
    return ConfigSchema(
        provider=ProviderSettings(
            priority=["mock"],
            timeout=5,
            retry=0,
            configs={
                "mock" : ProviderConfig(enabled=True)
            }
        ),
        circuit_breaker=CircuitBreakerSettings(
            default=CircuitBreakerProviderSettings(
                failure_threshold=3,
                reset_timeout=5,
                success_threshold=1,
            ),
            providers={
                "mock": CircuitBreakerProviderSettings(
                    failure_threshold=1,
                    reset_timeout=2,
                    success_threshold=1,
                )
            }
        )
    )



@pytest.mark.asyncio
async def test_full_provider_handler_path_is_fully_offline(offline_config):
    registry = ProviderRegistry()
    ProviderDiscovery(registry, config=offline_config.provider).run()
    providers = registry.list_all_providers()
    assert list(providers) == ["mock"]

    breaker = CircuitBreakerManager(config=offline_config.circuit_breaker)
    executor = ProviderExecutor(breaker, max_retries=0)
    routing = RoutingPolicy(
        providers,
        config=offline_config.provider,
    )
    client = httpx.AsyncClient()
    kwargs = dict(
        providers=providers,
        routing_policy=routing,
        executor=executor,
        circuit_breaker_manager=breaker,
        timeout=offline_config.provider.timeout,
    )
    try:
        chat = await ChatExecutionHandler(**kwargs).execute_with_fallback(
            client,
            {"model": "mock-chat", "messages": [{"role": "user", "content": "phase0"}]},
        )
        assert chat.provider == "mock"

        embeddings = await EmbeddingExecutionHandler(**kwargs).execute(
            client,
            {"model": "mock-embedding", "input": ["phase0"]},
        )
        assert embeddings["data"][0]["embedding"]

        models = await ModelOperationHandler(**kwargs).execute("mock", None, client)
        assert "mock-chat" in {item.id for item in models.data}

        uploaded = await FileOperationHandler(**kwargs).execute(
            {
                "action": "upload",
                "provider_name": "mock",
                "file_bytes": b"e2e",
                "file_size": 3,
                "mime_type": "text/plain",
                "display_name": "e2e.txt",
            },
            client,
        )
        assert uploaded["display_name"] == "e2e.txt"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_offline_config_does_not_instantiate_ollama(offline_config):
    registry = ProviderRegistry()
    ProviderDiscovery(registry, config=offline_config.provider).run()
    assert list(registry.list_all_providers()) == ["mock"]
