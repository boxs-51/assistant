from datetime import datetime, timezone
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field


class CapabilityResult(BaseModel):
    """Normalized successful result of a single capability invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    invocation_id: str
    capability_id: str
    success: bool = True
    output: Any = None
    output_type: str = "unknown"
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)