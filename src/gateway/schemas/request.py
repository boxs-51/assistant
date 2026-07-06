from typing import Any, Dict, List, Optional
from pydantic import Field
from .base import GatewayBaseModel
from .attachment import GatewayAttachment
from .message import GatewayMessage
from .tool import GatewayToolDefinition

# =================================================================
# 7. GATEWAY REQUEST DTO
# =================================================================

class RequestMetadata(GatewayBaseModel):
    """Metadata được phân tầng rõ ràng trong request."""
    user: Dict[str, Any] = Field(default_factory=dict)
    trace: Dict[str, Any] = Field(default_factory=dict)
    routing: Dict[str, Any] = Field(default_factory=dict)
    cache: Dict[str, Any] = Field(default_factory=dict)

class GatewayChatRequest(GatewayBaseModel):
    """DTO chuẩn hóa cho mọi request chat, để Adapter chỉ nhận một object duy nhất."""
    model: str
    messages: List[GatewayMessage]
    tools: Optional[List[GatewayToolDefinition]] = None
    #tools: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[GatewayAttachment]] = None
    
    # Generation parameters
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    
    metadata: RequestMetadata = Field(default_factory=RequestMetadata)
