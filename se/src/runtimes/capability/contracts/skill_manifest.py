"""Trusted Skill V2 manifest contracts and legacy normalization."""
from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SKILL_ACTIVATION_TERMS = 32
MAX_SKILL_CAPABILITY_HINTS = 64
MAX_SKILL_REFERENCES = 64

SKILL_RUNTIME_RESERVED_METADATA_KEYS = frozenset(
    {
        "kind",
        "instruction",
        "instruction_descriptor",
        "server_managed",
        "lazy",
        "loaded",
        "schema_version",
        "skill_id",
        "activation",
        "capability_hints",
        "references",
        "security",
        "provenance",
        "ownership",
        "owner_id",
        "owner_type",
        "runtime_owned",
    }
)


def _contained_relative_path(value: str, *, reference: bool = False) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        raise ValueError("Skill paths must be non-empty package-relative paths.")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("Skill paths must not contain empty, '.' or '..' segments.")
    path = PurePosixPath(*raw_parts)
    if reference and (not path.parts or path.parts[0] != "references"):
        raise ValueError("Skill references must remain under the references/ directory.")
    return path.as_posix()


def _normalized_terms(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


class SkillActivationMode(str, Enum):
    ON_DEMAND = "ON_DEMAND"
    AUTO_ELIGIBLE = "AUTO_ELIGIBLE"
    ALWAYS_ON = "ALWAYS_ON"


class SkillInstructionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = "instruction.md"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _contained_relative_path(value)


class SkillActivationDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: SkillActivationMode = SkillActivationMode.ON_DEMAND
    intents: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_SKILL_ACTIVATION_TERMS)
    keywords: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_SKILL_ACTIVATION_TERMS)

    @field_validator("intents", "keywords")
    @classmethod
    def normalize_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_terms(values)


class SkillCapabilityHint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=1000)
    stage: str = Field(default="", max_length=200)
    required: bool = False

    @field_validator("capability_id")
    @classmethod
    def normalize_capability_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("capability_id must not be blank.")
        return normalized


class SkillReferenceDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_id: str = Field(min_length=1, max_length=200)
    path: str
    description: str = Field(default="", max_length=1000)
    activation_tags: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=MAX_SKILL_ACTIVATION_TERMS,
    )

    @field_validator("reference_id")
    @classmethod
    def normalize_reference_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reference_id must not be blank.")
        return normalized

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _contained_relative_path(value, reference=True)

    @field_validator("activation_tags")
    @classmethod
    def normalize_activation_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_terms(values)


class SkillSecurityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    require_auth: bool = False
    required_scopes: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()

    @field_validator("required_scopes", "required_permissions")
    @classmethod
    def normalize_security_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_terms(values)


class SkillManifestV2(BaseModel):
    """Canonical machine-readable Skill V2 manifest.

    Instruction/reference bodies are intentionally not part of this model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="2", pattern=r"^2$")
    skill_id: str = Field(min_length=1, max_length=200)
    version: str = Field(default="1.0", min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    instruction: SkillInstructionDescriptor = Field(
        default_factory=SkillInstructionDescriptor
    )
    activation: SkillActivationDescriptor = Field(
        default_factory=SkillActivationDescriptor
    )
    capability_hints: tuple[SkillCapabilityHint, ...] = Field(
        default_factory=tuple,
        max_length=MAX_SKILL_CAPABILITY_HINTS,
    )
    references: tuple[SkillReferenceDescriptor, ...] = Field(
        default_factory=tuple,
        max_length=MAX_SKILL_REFERENCES,
    )
    security: SkillSecurityMetadata = Field(default_factory=SkillSecurityMetadata)

    @field_validator("skill_id", "version", "name")
    @classmethod
    def normalize_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Skill identity fields must not be blank.")
        return normalized


def normalize_skill_manifest(data: Mapping[str, Any]) -> SkillManifestV2:
    """Normalize one legacy or V2 manifest without reading any body files."""
    raw = dict(data)
    schema_version = raw.get("schema_version")
    if schema_version is not None and str(schema_version).strip() not in {"", "1", "2"}:
        raise ValueError(f"Unsupported Skill manifest schema_version: {schema_version!r}")
    if str(schema_version or "").strip() == "2":
        return SkillManifestV2.model_validate(raw)

    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Legacy Skill manifest requires a non-empty name.")
    instruction_file = str(raw.get("instruction_file") or "instruction.md")
    return SkillManifestV2(
        skill_id=name,
        version=str(raw.get("version") or "1.0"),
        name=name,
        description=str(raw.get("description") or name),
        instruction=SkillInstructionDescriptor(path=instruction_file),
        security=SkillSecurityMetadata(
            require_auth=bool(raw.get("require_auth", False)),
            required_scopes=tuple(raw.get("required_scopes", ()) or ()),
            required_permissions=tuple(raw.get("required_permissions", ()) or ()),
        ),
    )


def trusted_skill_runtime_metadata(manifest: SkillManifestV2) -> dict[str, Any]:
    """Project a validated manifest into server-owned catalog metadata."""
    return {
        "kind": "SKILL",
        "server_managed": True,
        "runtime_owned": True,
        "lazy": True,
        "loaded": False,
        "schema_version": "2",
        "skill_id": manifest.skill_id,
        "instruction_descriptor": manifest.instruction.model_dump(mode="json"),
        "activation": manifest.activation.model_dump(mode="json"),
        "capability_hints": [
            item.model_dump(mode="json") for item in manifest.capability_hints
        ],
        "references": [
            item.model_dump(mode="json") for item in manifest.references
        ],
        "security": manifest.security.model_dump(mode="json"),
        "required_permissions": list(manifest.security.required_permissions),
        "provenance": "SERVER_SKILL_MANIFEST",
        "ownership": "SERVER",
    }


__all__ = [
    "MAX_SKILL_ACTIVATION_TERMS",
    "MAX_SKILL_CAPABILITY_HINTS",
    "MAX_SKILL_REFERENCES",
    "SKILL_RUNTIME_RESERVED_METADATA_KEYS",
    "SkillActivationMode",
    "SkillInstructionDescriptor",
    "SkillActivationDescriptor",
    "SkillCapabilityHint",
    "SkillReferenceDescriptor",
    "SkillSecurityMetadata",
    "SkillManifestV2",
    "normalize_skill_manifest",
    "trusted_skill_runtime_metadata",
]
