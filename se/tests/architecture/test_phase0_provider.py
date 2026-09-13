import pytest

from src.domain.schemas import ModelCapability
from src.provider.mock.provider import MockProvider, MOCK_MODEL
from src.provider.registry import ProviderRegistry
from src.provider.policies.routing_policy import RoutingPolicy
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.infrastructure.config.schemas import ProviderConfig


def test_mock_provider_registers_like_a_normal_provider(monkeypatch):
    provider = MockProvider(config=ProviderConfig(
                    enabled=True,
                    base_url="http://testserver"
                ))
    registry = ProviderRegistry()
    registry.register(provider)

    assert registry.get_provider("mock") is provider
    assert registry.list_all_providers()["mock"].name == "mock"


@pytest.mark.asyncio
async def test_mock_provider_capability_does_not_touch_http_client():
    provider = MockProvider(config=ProviderConfig(
                    enabled=True,
                    base_url="http://testserver"
                ))
    sentinel = object()
    assert await provider.has_capability(MOCK_MODEL, ModelCapability.CHAT, sentinel, 1.0)

@pytest.mark.asyncio
async def test_mock_is_not_discovered_when_disabled(monkeypatch):
    from src.infrastructure.config import ConfigurationRegistry

    config = ConfigSchema(
        provider=ProviderSettings(
            priority=["mock"],
            configs={
                "mock": ProviderConfig(
                    enabled=False,
                    base_url="http://testserver"
                )
            }
        )
    )

    ConfigurationRegistry.set_config(config)
    registry = ProviderRegistry()
    from src.provider.discovery import ProviderDiscovery
    ProviderDiscovery(registry=registry, config=config.provider).run()
    assert "mock" not in registry.list_all_providers()


def test_mock_does_not_enter_default_fallback_chain_implicitly(monkeypatch):
    from src.infrastructure.config import ConfigurationRegistry
    config = ConfigSchema(
        provider=ProviderSettings(
            priority=["mock", "openai"],
            configs={
                "mock": ProviderConfig(
                    enabled=True,
                    base_url="http://testserver"
                ),
                "openai": ProviderConfig(
                    enabled=True,
                    base_url="http://testserver1"
                )
            }
        )
    )
    ConfigurationRegistry.set_config(config)
    from src.provider.mock.provider import MockProvider
    providers = {"openai": MockProvider(config=ProviderConfig(
                    enabled=True,
                    base_url="http://testserver"
                )), "mock": MockProvider(config=ProviderConfig(
                    enabled=True,
                    base_url="http://testserver1"
                ))}
    providers["openai"].name = "openai"
    policy = RoutingPolicy(providers=providers,config=config.provider)
    assert [provider.name for provider in policy.get_fallback_chain("unknown-model")] == ["mock"]