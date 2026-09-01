from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ....domain.schemas.identity import Identity


@dataclass(slots=True)
class CapabilityExecutionContext:
    """Request-scoped context for exactly one capability invocation."""

    identity: Identity | None
    execution_id: str
    invocation_id: str
    request_id: str | None = None
    session_id: str | None = None
    workflow_id: str | None = None
    deadline: float | None = None
    attempt: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    cancellation_event: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def create(
        cls,
        *,
        identity: Identity | None,
        execution_id: str | None = None,
        invocation_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        workflow_id: str | None = None,
        timeout_seconds: float | None = None,
        attempt: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
        cancellation_event: asyncio.Event | None = None,
    ) -> "CapabilityExecutionContext":
        now = time.monotonic()
        deadline = now + timeout_seconds if timeout_seconds is not None else None
        return cls(
            identity=identity,
            execution_id=execution_id or f"exec_{uuid.uuid4().hex}",
            invocation_id=invocation_id or f"capinv_{uuid.uuid4().hex}",
            request_id=request_id or getattr(identity, "request_id", None),
            session_id=session_id or getattr(identity, "session_id", None),
            workflow_id=workflow_id,
            deadline=deadline,
            attempt=attempt,
            metadata=dict(metadata or {}),
            cancellation_event=cancellation_event or asyncio.Event(),
        )

    @property
    def remaining_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    @property
    def cancelled(self) -> bool:
        return self.cancellation_event.is_set()

    def cancel(self) -> None:
        self.cancellation_event.set()