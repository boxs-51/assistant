"""Client DTO for Agent execution budgets used by chat requests."""

from typing import Optional
from pydantic import AliasChoices, Field, model_validator

from .base import GatewayBaseModel


class AgentExecutionLimits(GatewayBaseModel):
    max_iterations: int = 8
    max_tool_calls: int = 16
    max_parallel_agents: int = 4
    max_parallel_tools: int = 4
    timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "execution_timeout_seconds",
            "timeout_seconds",
        ),
        serialization_alias="timeout_seconds",
    )
    iteration_timeout_seconds: float = 20.0
    inference_timeout_seconds: float = Field(
        default=15.0,
        validation_alias=AliasChoices(
            "provider_call_timeout_seconds",
            "inference_timeout_seconds",
        ),
        serialization_alias="inference_timeout_seconds",
    )
    tool_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "tool_call_timeout_seconds",
            "tool_timeout_seconds",
        ),
        serialization_alias="tool_timeout_seconds",
    )
    task_timeout_seconds: Optional[float] = Field(default=None, gt=0)
    max_retry_attempts: int = 1
    max_cost: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _map_canonical_timeout_fields(cls, values):
        """Dual-read canonical timeout names without changing the legacy wire."""

        if not isinstance(values, dict):
            return values
        values = dict(values)
        for canonical, legacy in (
            ("execution_timeout_seconds", "timeout_seconds"),
            ("provider_call_timeout_seconds", "inference_timeout_seconds"),
            ("tool_call_timeout_seconds", "tool_timeout_seconds"),
        ):
            if canonical not in values:
                continue
            canonical_value = values.pop(canonical)
            if legacy in values and values[legacy] != canonical_value:
                raise ValueError(
                    f"Conflicting timeout fields: {canonical} and {legacy}"
                )
            values[legacy] = canonical_value
        return values

    @property
    def execution_timeout_seconds(self) -> float:
        return self.timeout_seconds

    @property
    def provider_call_timeout_seconds(self) -> float:
        return self.inference_timeout_seconds

    @property
    def tool_call_timeout_seconds(self) -> float:
        return self.tool_timeout_seconds

    @property
    def legacy_max_cost(self) -> Optional[float]:
        """Read-only semantic label for legacy max_cost compatibility."""
        return self.max_cost
