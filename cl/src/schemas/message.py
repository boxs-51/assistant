from .base import GatewayBaseModel
from typing import Literal, Optional, Dict, Union, List, Any
from pydantic import Field, model_validator
from .attachment import GatewayAttachment, ImageContent, AudioContent, UrlContent, VideoContent, DocumentContent
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

    data: Optional[Union[
        ImageContent,
        AudioContent,
        VideoContent,
        DocumentContent,
        GatewayAttachment,
        UrlContent,
    ]] = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_text_content(cls, value):
        """Read old TextContent-shaped payloads as flat text.

        This keeps rolling-upgrade and persisted-session compatibility while
        removing TextContent from the public schema.
        """
        if not isinstance(value, dict):
            return value

        raw_type = value.get("type")
        part_type = raw_type.value if hasattr(raw_type, "value") else raw_type
        if part_type not in {"text", "thinking"} or value.get("text") is not None:
            return value

        legacy = value.get("data")
        text = None
        if isinstance(legacy, str):
            text = legacy
        elif isinstance(legacy, dict) and isinstance(legacy.get("data"), str):
            text = legacy["data"]
            if legacy.get("format") == "code":
                language = legacy.get("language") or "text"
                text = f"```{language}\n{text}\n```"

        if text is None:
            return value

        migrated = dict(value)
        migrated["text"] = text
        migrated["data"] = None
        return migrated

    metadata: Dict[str, Any] = Field(default_factory=dict)


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
