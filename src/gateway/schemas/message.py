from .base import GatewayBaseModel
from typing import Literal, Optional, Dict, Union, List, Any
from pydantic import Field
from .attachment import GatewayAttachment, ImageContent, AudioContent, UrlContent, VideoContent
from .tool import GatewayToolCall, GatewayToolResult
from .enums import MessageContentType

# =================================================================
# 5. GATEWAY MESSAGE (Hỗ trợ Multimodal & Tool)
# =================================================================
class MessageContentPart(GatewayBaseModel):
    """
    Một phần của nội dung message, hỗ trợ đa phương tiện.
    Thiết kế lại để sử dụng GatewayAttachment.
    """
    type: MessageContentType
    text: Optional[str] = None
    image: Optional[ImageContent] = None
    audio: Optional[AudioContent] = None
    video: Optional[VideoContent] = None
    file: Optional[GatewayAttachment] = None
    url: Optional[UrlContent] = None

class GatewayMessage(GatewayBaseModel):
    """
    Cấu trúc message được thiết kế lại:
    - `content` là một list các `MessageContentPart` để hỗ trợ multimodal.
    - `tool_calls` chứa các yêu cầu gọi tool từ assistant.
    - `tool_results` chứa kết quả thực thi tool từ client.
    """
    role: Literal["system", "user", "assistant", "tool"]
    content: Union[List[MessageContentPart], str]

    # Dành cho assistant và tool
    tool_calls: Optional[List[GatewayToolCall]] = None
    tool_results: Optional[List[GatewayToolResult]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

