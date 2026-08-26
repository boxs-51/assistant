import pytest

from src.domain.schemas import ModelCapability
from src.provider.mock.provider import MockProvider, MOCK_MODEL
from src.provider.registry import ProviderRegistry
from src.provider.policies.routing_policy import RoutingPolicy


def test_mock_provider_registers_like_a_normal_provider(monkeypatch):
    monkeypatch.setenv("GATEWAY_PROVIDER__MOCK_ENABLED", "true")
    provider = MockProvider()
    registry = ProviderRegistry()
    registry.register(provider)

    assert registry.get_provider("mock") is provider
    assert registry.list_all_providers()["mock"].name == "mock"


@pytest.mark.asyncio
async def test_mock_provider_capability_does_not_touch_http_client():
    provider = MockProvider()
    sentinel = object()
    assert await provider.has_capability(MOCK_MODEL, ModelCapability.CHAT, sentinel, 1.0)

@pytest.mark.asyncio
async def test_mock_is_not_discovered_when_disabled(monkeypatch):
    monkeypatch.delenv("GATEWAY_PROVIDER__MOCK_ENABLED", raising=False)
    from src.infrastructure.config import ConfigurationRegistry
    from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
    ConfigurationRegistry.set_config(ConfigSchema(provider=ProviderSettings(priority=["mock"], mock_enabled=False)))
    registry = ProviderRegistry()
    from src.provider.discovery import ProviderDiscovery
    ProviderDiscovery(registry).run()
    assert "mock" not in registry.list_all_providers()


def test_mock_does_not_enter_default_fallback_chain_implicitly(monkeypatch):
    monkeypatch.setenv("GATEWAY_PROVIDER__MOCK_ENABLED", "true")
    from src.infrastructure.config import ConfigurationRegistry
    from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
    ConfigurationRegistry.set_config(ConfigSchema(provider=ProviderSettings(priority=["openai"], mock_enabled=True)))
    from src.provider.mock.provider import MockProvider
    providers = {"openai": MockProvider(), "mock": MockProvider()}
    providers["openai"].name = "openai"
    policy = RoutingPolicy(providers)
    assert [provider.name for provider in policy.get_fallback_chain("unknown-model")] == ["openai"]