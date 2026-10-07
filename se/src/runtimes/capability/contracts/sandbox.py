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


class SandboxSymlinkPolicy(str, Enum):
    DENY_ESCAPE = "DENY_ESCAPE"
    DENY_ALL = "DENY_ALL"


class SandboxNetworkMode(str, Enum):
    NONE = "NONE"
    RESTRICTED_EGRESS = "RESTRICTED_EGRESS"
    EGRESS = "EGRESS"


class SandboxSecretsMode(str, Enum):
    NONE = "NONE"
    ALLOWLIST = "ALLOWLIST"


class SandboxFilesystemLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_bytes: int | None = Field(default=None, ge=0)
    max_files: int | None = Field(default=None, ge=0)
    root: str = "."
    symlink_policy: SandboxSymlinkPolicy = SandboxSymlinkPolicy.DENY_ESCAPE

    @field_validator("root")
    @classmethod
    def _sandbox_relative_root(cls, value: str) -> str:
        if value != ".":
            raise ValueError("SBX-1 filesystem.root must denote the lease root")
        return value


class SandboxProcessLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_processes: int | None = Field(default=None, ge=0)
    max_memory_bytes: int | None = Field(default=None, ge=0)
    cpu_quota: float | None = Field(default=None, ge=0)
    allowed_interpreters: tuple[str, ...] = ()
    environment_allowlist: tuple[str, ...] = ()


class SandboxIOLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_stdout_bytes: int | None = Field(default=None, ge=0)
    max_stderr_bytes: int | None = Field(default=None, ge=0)
    max_total_output_bytes: int | None = Field(default=None, ge=0)


class SandboxNetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: SandboxNetworkMode = SandboxNetworkMode.NONE
    allowed_hosts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_mode(self) -> "SandboxNetworkPolicy":
        if self.mode is SandboxNetworkMode.NONE and self.allowed_hosts:
            raise ValueError("network NONE cannot carry allowed hosts")
        if (
            self.mode is SandboxNetworkMode.RESTRICTED_EGRESS
            and not self.allowed_hosts
        ):
            raise ValueError("RESTRICTED_EGRESS requires allowed_hosts")
        if self.mode is SandboxNetworkMode.EGRESS and self.allowed_hosts:
            raise ValueError("EGRESS does not use an allowed-host restriction")
        return self


class SandboxSecretsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: SandboxSecretsMode = SandboxSecretsMode.NONE
    allowed_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_mode(self) -> "SandboxSecretsPolicy":
        if self.mode is SandboxSecretsMode.NONE and self.allowed_names:
            raise ValueError("secrets NONE cannot carry allowed names")
        if self.mode is SandboxSecretsMode.ALLOWLIST and not self.allowed_names:
            raise ValueError("secrets ALLOWLIST requires allowed_names")
        return self


class SandboxProfile(BaseModel):
    """Immutable representation of the canonical sandbox policy dimensions.

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
    "SandboxFilesystemLimits",
    "SandboxIOLimits",
    "SandboxLease",
    "SandboxLeaseState",
    "SandboxNetworkMode",
    "SandboxNetworkPolicy",
    "SandboxProcessLimits",
    "SandboxProfile",
    "SandboxSecretsMode",
    "SandboxSecretsPolicy",
    "SandboxSymlinkPolicy",
]
