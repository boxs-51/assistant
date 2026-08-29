from typing import Annotated
from fastapi import Depends
from .schemas import (
    ConfigSchema, GatewaySettings, AuthenticationSettings, 
    ProviderSettings, OpenAISettings, RedisSettings
)
from .manager import ConfigManager

def get_config_manager() -> ConfigManager:
    return ConfigManager.get_instance()

def get_settings(
    manager: ConfigManager = Depends(get_config_manager)
) -> ConfigSchema:
    return manager.config

def get_auth_settings(
    settings: ConfigSchema = Depends(get_settings)
) -> AuthenticationSettings:
    return settings.auth

def get_gateway_settings(
    settings: ConfigSchema = Depends(get_settings)
) -> GatewaySettings:
    return settings.gateway

def get_provider_settings(
    settings: ConfigSchema = Depends(get_settings)
) -> ProviderSettings:
    return settings.provider

def get_openai_settings(
    settings: ConfigSchema = Depends(get_settings)
) -> OpenAISettings:
    return settings.openai

# --- Type Aliases hỗ trợ Clean Code trong Router (FastAPI 0.95+) ---
SettingsDep = Annotated[ConfigSchema, Depends(get_settings)]
AuthSettingsDep = Annotated[AuthenticationSettings, Depends(get_auth_settings)]
GatewaySettingsDep = Annotated[GatewaySettings, Depends(get_gateway_settings)]
ProviderSettingsDep = Annotated[ProviderSettings, Depends(get_provider_settings)]