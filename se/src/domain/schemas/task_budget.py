from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .base import GatewayBaseModel


TASK_BUDGET_COST_QUANTUM = Decimal("0.00000001")


def normalize_task_budget_cost(value) -> Decimal:
    normalized = Decimal(str(value))
    if not normalized.is_finite():
        raise ValueError("task budget cost must be finite")
    return normalized.quantize(
        TASK_BUDGET_COST_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )


class TaskBudgetState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TaskBudgetReservationKind(str, Enum):
    NEW_EXECUTION = "NEW_EXECUTION"
    RESUME_EXECUTION = "RESUME_EXECUTION"
    RELEASE_EXECUTION = "RELEASE_EXECUTION"
    TOOL_CALL = "TOOL_CALL"
    INFERENCE = "INFERENCE"
    USAGE = "USAGE"
    BRANCH = "BRANCH"
    RELEASE_BRANCH = "RELEASE_BRANCH"


class TaskBudgetLimits(GatewayBaseModel):
    max_total_executions: int = Field(gt=0)
    max_active_executions: int = Field(gt=0)
    max_active_branches: int = Field(gt=0)
    max_parallel_agents: int = Field(gt=0)
    max_total_tool_calls: int = Field(gt=0)
    max_total_inference_calls: int = Field(gt=0)
    max_delegation_depth: int = Field(gt=0)
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_total_cost_usd: Decimal | None = Field(default=None, gt=Decimal("0"))

    @field_validator("max_total_cost_usd", mode="before")
    @classmethod
    def _normalize_max_cost(cls, value):
        if value is None:
            return None
        return normalize_task_budget_cost(value)


class TaskBudgetPolicy(GatewayBaseModel):
    version: str = Field(default="r5-v1", min_length=1, max_length=64)
    deny_recursive_agent_cycle: bool = True


def task_budget_policy_fingerprint(
    limits: TaskBudgetLimits,
    policy: TaskBudgetPolicy,
) -> str:
    payload = {
        "limits": limits.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TaskBudget(GatewayBaseModel):
    task_id: str
    revision: int = Field(default=0, ge=0)
    state: TaskBudgetState = TaskBudgetState.OPEN
    limits: TaskBudgetLimits

    policy_version: str = Field(min_length=1, max_length=64)
    policy_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    deny_recursive_agent_cycle: bool = True

    used_executions: int = Field(default=0, ge=0)
    active_executions: int = Field(default=0, ge=0)
    active_branches: int = Field(default=0, ge=0)
    active_parallel_agents: int = Field(default=0, ge=0)
    used_tool_calls: int = Field(default=0, ge=0)
    used_inference_calls: int = Field(default=0, ge=0)
    used_tokens: int = Field(default=0, ge=0)
    used_cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None

    @field_validator("used_cost_usd", mode="before")
    @classmethod
    def _normalize_used_cost(cls, value):
        return normalize_task_budget_cost(value)

    @model_validator(mode="after")
    def _validate_budget_invariants(self):
        limits = self.limits
        if self.active_executions > self.used_executions:
            raise ValueError(
                "active_executions cannot exceed used_executions"
            )
        if self.used_executions > limits.max_total_executions:
            raise ValueError(
                "used_executions cannot exceed max_total_executions"
            )
        if self.active_executions > limits.max_active_executions:
            raise ValueError(
                "active_executions cannot exceed max_active_executions"
            )
        if self.active_parallel_agents > self.active_executions:
            raise ValueError(
                "active_parallel_agents cannot exceed active_executions"
            )
        if self.active_parallel_agents > limits.max_parallel_agents:
            raise ValueError(
                "active_parallel_agents cannot exceed max_parallel_agents"
            )
        if self.active_branches > limits.max_active_branches:
            raise ValueError(
                "active_branches cannot exceed max_active_branches"
            )
        if self.used_tool_calls > limits.max_total_tool_calls:
            raise ValueError(
                "used_tool_calls cannot exceed max_total_tool_calls"
            )
        if self.used_inference_calls > limits.max_total_inference_calls:
            raise ValueError(
                "used_inference_calls cannot exceed max_total_inference_calls"
            )
        if self.state is TaskBudgetState.OPEN and self.closed_at is not None:
            raise ValueError("OPEN TaskBudget must not have closed_at")
        if self.state is TaskBudgetState.CLOSED and self.closed_at is None:
            raise ValueError("CLOSED TaskBudget requires closed_at")
        return self


__all__ = [
    "TASK_BUDGET_COST_QUANTUM",
    "TaskBudget",
    "TaskBudgetLimits",
    "TaskBudgetPolicy",
    "TaskBudgetReservationKind",
    "TaskBudgetState",
    "normalize_task_budget_cost",
    "task_budget_policy_fingerprint",
]
