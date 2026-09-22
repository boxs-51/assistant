from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import Field, model_validator

from .attachment import (
    AudioContent,
    DocumentContent,
    GatewayAttachment,
    ImageContent,
    UrlContent,
    VideoContent,
)
from .base import GatewayBaseModel
from .enums import MessageContentType
from .tool import GatewayToolCall, GatewayToolResult


class MessageContentPart(GatewayBaseModel):
    type: MessageContentType
    text: Optional[str] = None
    data: Optional[
        Union[
            ImageContent,
            AudioContent,
            VideoContent,
            DocumentContent,
            GatewayAttachment,
            UrlContent,
        ]
    ] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

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
    role: Literal["system", "user", "assistant", "tool"]
    content: Union[List[MessageContentPart], str]
    tool_calls: Optional[List[GatewayToolCall]] = None
    tool_results: Optional[List[GatewayToolResult]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def decode_persisted_message_content(value: Any) -> Any:
    if (
        isinstance(value, dict)
        and value.get("type") == "text"
        and "data" in value
    ):
        return value.get("data", "")
    return value
