from types import SimpleNamespace

import httpx
import pytest

from src.circuit_breaker import CircuitBreakerManager
from src.infrastructure.config import ConfigurationRegistry
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.discovery import ProviderDiscovery
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.registry import ProviderRegistry
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler


@pytest.fixture(autouse=True)
def mock_config():
    ConfigurationRegistry.set_config(ConfigSchema(
        provider=ProviderSettings(
            priority=["mock"],
            mock_enabled=True,
            timeout=5,
        )
    ))


@pytest.mark.asyncio
async def test_mock_provider_exercises_provider_runtime_handlers_end_to_end():
    registry = ProviderRegistry()
    ProviderDiscovery(registry).run()
    providers = registry.list_all_providers()
    assert "mock" in providers

    routing = RoutingPolicy(providers)
    executor = ProviderExecutor(CircuitBreakerManager())
    http_client = httpx.AsyncClient()
    kwargs = dict(
        providers=providers,
        routing_policy=routing,
        executor=executor,
        circuit_breaker_manager=executor.breaker_manager,
    )
    chat_handler = ChatExecutionHandler(**kwargs)
    embedding_handler = EmbeddingExecutionHandler(**kwargs)
    model_handler = ModelOperationHandler(**kwargs)
    file_handler = FileOperationHandler(**kwargs)

    try:
        chat = await chat_handler.execute_with_fallback(http_client, {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "phase0"}],
        })
        assert chat.provider == "mock"
        assert chat.choices[0].message.content == "mock:phase0"

        embedding = await embedding_handler.execute(http_client, {
            "model": "mock-embedding",
            "input": ["phase0"],
        })
        assert embedding["data"][0]["embedding"]

        models = await model_handler.execute("mock", None, http_client)
        assert {item.id for item in models.data} == {"mock-chat", "mock-embedding"}

        uploaded = await file_handler.execute({
            "action": "upload",
            "provider_name": "mock",
            "file_bytes": b"e2e",
            "file_size": 3,
            "mime_type": "text/plain",
            "display_name": "e2e.txt",
        }, http_client)
        assert uploaded["display_name"] == "e2e.txt"
    finally:
        await http_client.aclose()
