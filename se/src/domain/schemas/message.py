from .base import GatewayBaseModel
from typing import Literal, Optional, Dict, Union, List, Any
from datetime import datetime
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
    def _normalize_content_part(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        raw_type = migrated.get("type")
        part_type = raw_type.value if hasattr(raw_type, "value") else raw_type
        if part_type in {"text", "thinking"}:
            if migrated.get("text") is not None:
                return migrated
            legacy = migrated.get("data")
            text = None
            if isinstance(legacy, str):
                text = legacy
            elif isinstance(legacy, dict) and isinstance(legacy.get("data"), str):
                text = legacy["data"]
                if legacy.get("format") == "code":
                    language = legacy.get("language") or "text"
                    fence = chr(96) * 3
                    text = f"{fence}{language}\n{text}\n{fence}"
            if text is not None:
                migrated["text"] = text
                migrated["data"] = None
            return migrated
        data = migrated.get("data")
        if data is None:
            return migrated
        if part_type == "image":
            migrated["data"] = ImageContent.model_validate(data)
        elif part_type == "audio":
            migrated["data"] = AudioContent.model_validate(data)
        elif part_type == "video":
            migrated["data"] = VideoContent.model_validate(data)
        elif part_type == "file":
            if isinstance(data, dict) and "attachment" in data:
                migrated["data"] = DocumentContent.model_validate(data)
            elif isinstance(data, DocumentContent):
                migrated["data"] = data
            else:
                migrated["data"] = GatewayAttachment.model_validate(data)
        elif part_type == "url":
            migrated["data"] = UrlContent.model_validate(data)
        return migrated

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
    turn_id: Optional[str] = None
    sequence: Optional[int] = Field(default=None, ge=1)
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

def decode_persisted_message_content(value: Any) -> Any:
    """Read the legacy text envelope without flattening canonical JSON."""
    if (
        isinstance(value, dict)
        and value.get("type") == "text"
        and "data" in value
    ):
        return value.get("data", "")
    return value

def contains_canonical_asset_content(value: Any) -> bool:
    """Detect canonical CAS content without interpreting provider identities."""
    value = decode_persisted_message_content(value)
    if isinstance(value, dict):
        asset_id = value.get("asset_id")
        source = value.get("source")
        uri = value.get("uri")
        if asset_id or source == "asset":
            return True
        if isinstance(uri, str) and uri.startswith("asset://"):
            return True
        return any(
            contains_canonical_asset_content(item)
            for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(
            contains_canonical_asset_content(item)
            for item in value
        )
    return False
