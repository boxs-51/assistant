# src/kernel/events/provider.py
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class ExecuteProviderPayload(BaseModel):
    request_body: Dict[str, Any]
    is_stream: bool = False

class ProviderRespondedPayload(BaseModel):
    gateway_response: Dict[str, Any]
    provider: str
    model: str
    latency: float

class ProviderErrorPayload(BaseModel):
    error_message: str
    status_code: int = 500