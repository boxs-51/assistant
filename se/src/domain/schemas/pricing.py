from .base import GatewayBaseModel
from .model import ModelInfo
from .provider import ProviderInfo
from pydantic import Field
from typing import List, Optional

# =================================================================
# 3. PRICING, LIMITS 
# =================================================================

class TokenPricing(GatewayBaseModel): # Giữ nguyên
    """Chi tiết giá cho từng loại token trên 1 triệu tokens."""
    input_tokens: float = Field(default=0.0, description="Giá trên 1M input tokens (USD)")
    output_tokens: float = Field(default=0.0, description="Giá trên 1M output tokens (USD)")
    cached_input_tokens: Optional[float] = Field(default=None, description="Giá trên 1M cached input tokens")
    reasoning_tokens: Optional[float] = Field(default=None, description="Giá riêng cho reasoning tokens nếu có")

class PricingInfo(GatewayBaseModel):
    """Thông tin pricing tổng thể của model.""" # Giữ nguyên
    currency: str = "USD"
    prompt_pricing: TokenPricing = Field(default_factory=TokenPricing)
    completion_pricing: TokenPricing = Field(default_factory=TokenPricing)

class PricingTable(GatewayBaseModel):
    """Bảng giá tổng hợp cho nhiều model của một provider."""
    provider: ProviderInfo
    model_id: List[ModelInfo] = Field(default_factory=list)
    pricing: PricingInfo
    currency: str = "USD"