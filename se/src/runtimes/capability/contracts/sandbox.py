from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CapabilityExecutionBoundary(str, Enum):
    """Execution isolation boundary, distinct from physical implementation location."""

    TRUSTED_PROCESS = "TRUSTED_PROCESS"
    EPHEMERAL_SANDBOX = "EPHEMERAL_SANDBOX"
    REMOTE_ENDPOINT = "REMOTE_ENDPOINT"
    DECLARATIVE = "DECLARATIVE"


class SandboxLeaseState(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    QUIESCING = "QUIESCING"
    CLEANUP = "CLEANUP"
    DESTROYED = "DESTROYED"


class SandboxNetworkMode(str, Enum):
    NONE = "NONE"
    ALLOWLIST = "ALLOWLIST"


class SandboxFilesystemLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_bytes: int | None = Field(default=None, ge=0)
    max_files: int | None = Field(default=None, ge=0)
    max_path_length: int | None = Field(default=None, ge=1)


class SandboxProcessLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_processes: int | None = Field(default=None, ge=0)
    max_cpu_seconds: float | None = Field(default=None, ge=0)
    max_memory_bytes: int | None = Field(default=None, ge=0)


class SandboxIOLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_stdout_bytes: int | None = Field(default=None, ge=0)
    max_stderr_bytes: int | None = Field(default=None, ge=0)


class SandboxNetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: SandboxNetworkMode = SandboxNetworkMode.NONE
    allow_hosts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_allowlist(self) -> "SandboxNetworkPolicy":
        if self.mode is SandboxNetworkMode.NONE and self.allow_hosts:
            raise ValueError("network NONE cannot carry allowed hosts")
        return self


class SandboxEnvironmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_names: tuple[str, ...] = ()


class SandboxSecretsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deny_by_default: bool = True
    allowed_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_default_deny(self) -> "SandboxSecretsPolicy":
        if not self.deny_by_default:
            raise ValueError("SBX-1 secrets policy must remain deny-by-default")
        return self


class SandboxProfile(BaseModel):
    """Immutable representation of sandbox isolation policy.

    SBX-1 represents limits only. Process/network enforcement belongs to later
    execution-boundary stages.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    filesystem: SandboxFilesystemLimits = Field(
        default_factory=SandboxFilesystemLimits
    )
    process: SandboxProcessLimits = Field(default_factory=SandboxProcessLimits)
    io: SandboxIOLimits = Field(default_factory=SandboxIOLimits)
    network: SandboxNetworkPolicy = Field(default_factory=SandboxNetworkPolicy)
    environment: SandboxEnvironmentPolicy = Field(
        default_factory=SandboxEnvironmentPolicy
    )
    secrets: SandboxSecretsPolicy = Field(default_factory=SandboxSecretsPolicy)

    @field_validator("profile_id")
    @classmethod
    def _canonical_profile_id(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("profile_id must be non-empty and canonical")
        return value


class SandboxLease(BaseModel):
    """Immutable lease snapshot; manager transitions replace snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox_id: str
    execution_id: str
    owner_user_id: str
    profile_id: str
    root: Path
    state: SandboxLeaseState = SandboxLeaseState.CREATED

    @field_validator(
        "sandbox_id",
        "execution_id",
        "owner_user_id",
        "profile_id",
    )
    @classmethod
    def _canonical_identity(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("sandbox lease identity must be non-empty and canonical")
        return value

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.sandbox_id,
            self.execution_id,
            self.owner_user_id,
            self.profile_id,
        )


__all__ = [
    "CapabilityExecutionBoundary",
    "SandboxEnvironmentPolicy",
    "SandboxFilesystemLimits",
    "SandboxIOLimits",
    "SandboxLease",
    "SandboxLeaseState",
    "SandboxNetworkMode",
    "SandboxNetworkPolicy",
    "SandboxProcessLimits",
    "SandboxProfile",
    "SandboxSecretsPolicy",
]
