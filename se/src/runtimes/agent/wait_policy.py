from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Protocol

from ...domain.schemas.agent_execution import AgentExecutionWaitReason
from .contracts.context import AgentExecutionContext


class ExecutionWaitPolicy(Protocol):
    """Select wall-clock TTL for one durable WAITING execution."""

    def wait_ttl_seconds(
        self,
        *,
        reason: AgentExecutionWaitReason,
        context: AgentExecutionContext,
    ) -> float | None:
        ...


class ConfiguredExecutionWaitPolicy:
    """Configurable wait TTL mapping.

    R4 intentionally does not invent product TTL defaults. Missing reasons and
    explicit ``None`` values mean no automatic expiry under the current
    policy. A future config/bootstrap patch may inject concrete durations.
    """

    def __init__(
        self,
        ttl_by_reason: Mapping[
            AgentExecutionWaitReason | str,
            float | None,
        ] | None = None,
    ) -> None:
        normalized: dict[AgentExecutionWaitReason, float | None] = {}
        for raw_reason, raw_ttl in dict(ttl_by_reason or {}).items():
            reason = (
                raw_reason
                if isinstance(raw_reason, AgentExecutionWaitReason)
                else AgentExecutionWaitReason(str(raw_reason))
            )
            if raw_ttl is None:
                normalized[reason] = None
                continue
            ttl = float(raw_ttl)
            if not math.isfinite(ttl) or ttl <= 0:
                raise ValueError(
                    "wait TTL must be a finite positive number of seconds"
                )
            normalized[reason] = ttl
        self._ttl_by_reason = normalized

    def wait_ttl_seconds(
        self,
        *,
        reason: AgentExecutionWaitReason,
        context: AgentExecutionContext,
    ) -> float | None:
        del context
        return self._ttl_by_reason.get(reason)


__all__ = [
    "ConfiguredExecutionWaitPolicy",
    "ExecutionWaitPolicy",
]
