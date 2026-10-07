from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from .base import GatewayBaseModel
from pydantic import AliasChoices, Field, field_validator, model_validator

MAX_AGENT_PROPOSED_TASK_SECONDS = 86400.0


class AgentExecutionState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class AgentExecutionWaitReason(str, Enum):
    NONE = "NONE"
    CONNECTION = "CONNECTION"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    DEPENDENCY = "DEPENDENCY"
    RESOURCE = "RESOURCE"
    EXPLICIT_PAUSE = "EXPLICIT_PAUSE"
    RECOVERY = "RECOVERY"
    RETRY_BACKOFF = "RETRY_BACKOFF"
    AGENT = "AGENT"


def normalize_execution_waiting(
    state: AgentExecutionState | str,
    wait_reason: AgentExecutionWaitReason | str | None = None,
) -> tuple[AgentExecutionState, AgentExecutionWaitReason | None]:
    """Validate canonical execution waiting state at the server boundary."""
    raw_state = state.value if isinstance(state, AgentExecutionState) else str(state)
    normalized_state = AgentExecutionState(raw_state)
    normalized_reason = (
        AgentExecutionWaitReason(wait_reason)
        if wait_reason not in (None, AgentExecutionWaitReason.NONE, "NONE")
        else None
    )
    if normalized_state is AgentExecutionState.WAITING and normalized_reason is None:
        raise ValueError("WAITING execution requires wait_reason")
    if normalized_state is not AgentExecutionState.WAITING and normalized_reason is not None:
        raise ValueError("wait_reason is only valid for WAITING execution")
    return normalized_state, normalized_reason


class AgentExecution(GatewayBaseModel):
    execution_id: str
    session_id: str
    agent_id: str
    task_id: Optional[str] = None
    branch_id: Optional[str] = None
    parent_execution_id: Optional[str] = None
    retry_of_execution_id: Optional[str] = None
    base_execution_id: Optional[str] = None
    base_checkpoint_id: Optional[str] = None
    correlation_id: str
    state: AgentExecutionState = AgentExecutionState.CREATED
    wait_reason: Optional[AgentExecutionWaitReason] = None
    revision: int = 0
    owner_instance_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    lease_generation: int = Field(default=0, ge=0)
    current_checkpoint_id: Optional[str] = None
    bound_client_id: Optional[str] = None
    bound_connection_id: Optional[str] = None
    remaining_active_budget_seconds: Optional[float] = None
    wait_expires_at: Optional[datetime] = None
    request: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float
    updated_at: float

    @model_validator(mode="before")
    @classmethod
    def _normalize_waiting_contract(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        state, reason = normalize_execution_waiting(
            values.get("state", AgentExecutionState.CREATED),
            values.get("wait_reason"),
        )
        values["state"] = state
        values["wait_reason"] = reason
        return values

    @field_validator("lease_expires_at")
    @classmethod
    def _normalize_lease_expiry_utc(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lease_expires_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_lease_representation(self):
        owner_present = self.owner_instance_id is not None
        expiry_present = self.lease_expires_at is not None
        if owner_present != expiry_present:
            raise ValueError(
                "owner_instance_id and lease_expires_at must be both set or both null"
            )
        if owner_present and self.lease_generation <= 0:
            raise ValueError(
                "active durable lease requires lease_generation > 0"
            )
        return self


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
        description=(
            "Total active execution budget, excluding durable WAITING time."
        ),
    )
    iteration_timeout_seconds: float = Field(default=20.0, description="Budget for one Agent loop iteration, bounded by total execution time.")
    inference_timeout_seconds: float = Field(
        default=15.0,
        validation_alias=AliasChoices(
            "provider_call_timeout_seconds",
            "inference_timeout_seconds",
        ),
        serialization_alias="inference_timeout_seconds",
        description=(
            "Budget for one model call, bounded by iteration and execution time."
        ),
    )
    tool_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "tool_call_timeout_seconds",
            "tool_timeout_seconds",
        ),
        serialization_alias="tool_timeout_seconds",
        description=(
            "Budget for one ordinary tool call, bounded by iteration and "
            "execution time."
        ),
    )
    task_timeout_seconds: Optional[float] = Field(default=None, gt=0, description="Optional hard wall-clock limit for the task; the Agent may propose it when omitted.")
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


class AgentExecutionRequest(GatewayBaseModel):
    session_id: str
    agent_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    parent_execution_id: Optional[str] = None
    limits: AgentExecutionLimits = Field(default_factory=AgentExecutionLimits)
