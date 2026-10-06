"""Agent-side Skill descriptor and activation contracts for SKV2-P0."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...capability.contracts.definition import CapabilityDefinition
from ...capability.contracts.skill_manifest import (
    SkillActivationDescriptor,
    SkillCapabilityHint,
    SkillReferenceDescriptor,
)


class SkillActivationSource(str, Enum):
    ASSIGNED = "ASSIGNED"
    ON_DEMAND = "ON_DEMAND"
    AUTO_MATCH = "AUTO_MATCH"
    ALWAYS_ON = "ALWAYS_ON"


class SkillDescriptor(BaseModel):
    """Trusted metadata-only descriptor for one exact Skill/version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    activation: SkillActivationDescriptor = Field(
        default_factory=SkillActivationDescriptor
    )
    capability_hints: tuple[SkillCapabilityHint, ...] = ()
    references: tuple[SkillReferenceDescriptor, ...] = ()
    provenance: str = "SERVER_SKILL_MANIFEST"

    @classmethod
    def from_definition(cls, definition: CapabilityDefinition) -> "SkillDescriptor":
        metadata = dict(definition.metadata or {})
        if str(metadata.get("schema_version", "")) != "2":
            raise ValueError("Skill descriptor requires trusted schema_version=2 metadata.")
        if metadata.get("server_managed") is not True:
            raise ValueError("Skill descriptor requires server-managed metadata.")
        if metadata.get("runtime_owned") is not True:
            raise ValueError("Skill descriptor requires runtime-owned metadata.")
        if str(metadata.get("skill_id") or definition.capability_id) != definition.capability_id:
            raise ValueError("Skill descriptor identity does not match capability_id.")
        return cls(
            skill_id=definition.capability_id,
            version=definition.version,
            name=definition.name,
            description=definition.description,
            activation=SkillActivationDescriptor.model_validate(
                metadata.get("activation") or {}
            ),
            capability_hints=tuple(
                SkillCapabilityHint.model_validate(item)
                for item in metadata.get("capability_hints", ())
            ),
            references=tuple(
                SkillReferenceDescriptor.model_validate(item)
                for item in metadata.get("references", ())
            ),
            provenance=str(metadata.get("provenance") or "SERVER_SKILL_MANIFEST"),
        )


class SkillActivationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    active: bool
    source: SkillActivationSource
    reason: str = ""


class ActiveSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    descriptor: SkillDescriptor
    source: SkillActivationSource


class ActiveSkillSet(BaseModel):
    """Ephemeral Skill activation set; persistence/integration is deferred to DCS."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: tuple[ActiveSkill, ...] = ()

    @model_validator(mode="after")
    def unique_skill_versions(self) -> "ActiveSkillSet":
        identities = [
            (item.descriptor.skill_id, item.descriptor.version)
            for item in self.skills
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("ActiveSkillSet contains a duplicate Skill/version identity.")
        return self


__all__ = [
    "SkillActivationSource",
    "SkillDescriptor",
    "SkillActivationDecision",
    "ActiveSkill",
    "ActiveSkillSet",
]
