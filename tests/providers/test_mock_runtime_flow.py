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

@pytest.mark.asyncio
async def test_full_provider_handler_path():
    ConfigurationRegistry.set_config(ConfigSchema(provider=ProviderSettings(priority=["mock"],mock_enabled=True,timeout=5)))
    registry=ProviderRegistry(); ProviderDiscovery(registry).run(); providers=registry.list_all_providers()
    assert list(providers) == ["mock"]
    breaker=CircuitBreakerManager(); executor=ProviderExecutor(breaker); routing=RoutingPolicy(providers)
    client=httpx.AsyncClient()
    kwargs=dict(providers=providers,routing_policy=routing,executor=executor,circuit_breaker_manager=breaker)
    try:
        chat=await ChatExecutionHandler(**kwargs).execute_with_fallback(client,{"model":"mock-chat","messages":[{"role":"user","content":"phase0"}]})
        emb=await EmbeddingExecutionHandler(**kwargs).execute(client,{"model":"mock-embedding","input":["phase0"]})
        models=await ModelOperationHandler(**kwargs).execute("mock",None,client)
        f=await FileOperationHandler(**kwargs).execute({"action":"upload","provider_name":"mock","file_bytes":b"x","file_size":1,"display_name":"x"},client)
        assert chat.provider=="mock" and emb["data"] and models.data and f["name"]
    finally:
        await client.aclose()
