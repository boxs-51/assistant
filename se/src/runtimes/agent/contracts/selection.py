"""DCS-1 per-iteration capability selection contracts."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .inference import InferenceMessage
from .skills import ActiveSkillSet


class CapabilitySelectionCandidate(BaseModel):
    """One already-eligible logical Tool candidate.

    Eligibility (Agent envelope, authorization, availability/routability) is
    established before DCS.  This contract carries no grant authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""


class CapabilityWorkingSet(BaseModel):
    """Exact model-visible logical Tool set for one Agent iteration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible_capability_ids: tuple[str, ...] = ()
    active_groups: tuple[str, ...] = ()
    reason: str = "DCS1"
    provenance: tuple[str, ...] = ()
    revision: int = Field(ge=1)

    @field_validator("visible_capability_ids")
    @classmethod
    def unique_visible_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("CapabilityWorkingSet contains duplicate capability IDs.")
        return value


class CapabilitySelectionContext(BaseModel):
    """Immutable input to one DCS decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_user_id: str | None = None
    agent_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    iteration: int = Field(ge=1)
    messages: tuple[InferenceMessage, ...] = ()
    eligible_capabilities: tuple[CapabilitySelectionCandidate, ...] = ()
    explicit_requested_capability_ids: tuple[str, ...] = ()
    active_skill_set: ActiveSkillSet = Field(default_factory=ActiveSkillSet)


class CapabilitySelectionResult(BaseModel):
    """DCS-1 result consumed by context assembly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    working_set: CapabilityWorkingSet
    active_skill_set: ActiveSkillSet = Field(default_factory=ActiveSkillSet)


class CapabilitySelector(Protocol):
    def select(
        self,
        context: CapabilitySelectionContext,
    ) -> CapabilitySelectionResult:
        ...


__all__ = [
    "CapabilitySelectionCandidate",
    "CapabilitySelectionContext",
    "CapabilitySelectionResult",
    "CapabilitySelector",
    "CapabilityWorkingSet",
]
