from pydantic import BaseModel, ConfigDict, Field
from ...domain.schemas.enums import ChatExecutionMode


class DirectChatPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_tool_rounds: int = Field(default=2, ge=0, le=8)
    max_tool_calls: int = Field(default=4, ge=0, le=32)
    allow_parallel_tools: bool = True


__all__ = ["ChatExecutionMode", "DirectChatPolicy"]
