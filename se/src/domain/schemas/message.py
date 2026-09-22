from datetime import datetime
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
    """One canonical multimodal content part."""

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
    def _migrate_legacy_text_content(cls, value):
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
                text = f"~~~{language}\n{text}\n~~~"

        if text is None:
            return value

        migrated = dict(value)
        migrated["text"] = text
        migrated["data"] = None
        return migrated


class GatewayMessage(GatewayBaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Union[List[MessageContentPart], str]
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
