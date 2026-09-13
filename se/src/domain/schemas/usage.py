from typing import Dict
from .base import GatewayBaseModel
from pydantic import Field

class GatewayUsage(GatewayBaseModel):
    """Mở rộng GatewayUsage để chứa cả provider-specific usage."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    
    # Provider-specific usage details
    provider_usage: Dict[str, int] = Field(default_factory=dict, description="Chi tiết token usage từ provider (cached, reasoning, tool_use...)")
