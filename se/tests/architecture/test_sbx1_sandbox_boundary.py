from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
)
from se.src.runtimes.capability.contracts.sandbox import (
    CapabilityExecutionBoundary,
    SandboxNetworkMode,
    SandboxNetworkPolicy,
    SandboxProfile,
    SandboxSecretsMode,
    SandboxSymlinkPolicy,
)
from se.src.runtimes.capability.contracts.target import ResourceScope
from se.src.runtimes.capability.sandbox import SandboxManager


def test_execution_boundary_is_distinct_from_physical_server_topology():
    assert CapabilityExecutionLocation.SERVER.value == "SERVER"
    assert (
        CapabilityExecutionBoundary.EPHEMERAL_SANDBOX.value
        != CapabilityExecutionLocation.SERVER.value
    )
    assert CapabilityExecutionBoundary.EPHEMERAL_SANDBOX.value == (
        "EPHEMERAL_SANDBOX"
    )


def test_sandbox_profile_defaults_deny_network_secrets_and_ambient_environment():
    profile = SandboxProfile(profile_id="sbx-default")

    assert profile.filesystem.root == "."
    assert profile.filesystem.symlink_policy is SandboxSymlinkPolicy.DENY_ESCAPE
    assert profile.network.mode is SandboxNetworkMode.NONE
    assert profile.network.allowed_hosts == ()
    assert profile.secrets.mode is SandboxSecretsMode.NONE
    assert profile.secrets.allowed_names == ()
    assert profile.process.environment_allowlist == ()

    with pytest.raises(ValidationError):
        profile.profile_id = "mutated"


def test_sandbox_profile_represents_canonical_minimum_dimensions():
    profile = SandboxProfile.model_validate(
        {
            "profile_id": "bounded",
            "filesystem": {
                "max_bytes": 1024,
                "max_files": 8,
                "root": ".",
                "symlink_policy": "DENY_ESCAPE",
            },
            "process": {
                "max_processes": 2,
                "max_memory_bytes": 4096,
                "cpu_quota": 3.5,
                "allowed_interpreters": ("python",),
                "environment_allowlist": ("LANG",),
            },
            "io": {
                "max_stdout_bytes": 100,
                "max_stderr_bytes": 100,
                "max_total_output_bytes": 150,
            },
            "network": {
                "mode": "RESTRICTED_EGRESS",
                "allowed_hosts": ("example.com",),
            },
            "secrets": {"mode": "NONE"},
        }
    )

    assert profile.filesystem.max_bytes == 1024
    assert profile.filesystem.root == "."
    assert profile.filesystem.symlink_policy.value == "DENY_ESCAPE"
    assert profile.process.max_memory_bytes == 4096
    assert profile.process.cpu_quota == 3.5
    assert profile.process.allowed_interpreters == ("python",)
    assert profile.process.environment_allowlist == ("LANG",)
    assert profile.io.max_total_output_bytes == 150
    assert profile.network.mode is SandboxNetworkMode.RESTRICTED_EGRESS
    assert profile.network.allowed_hosts == ("example.com",)
    assert {mode.value for mode in SandboxNetworkMode} == {
        "NONE",
        "RESTRICTED_EGRESS",
        "EGRESS",
    }
    assert "budget" not in SandboxProfile.model_fields
    assert "timeout" not in SandboxProfile.model_fields


def test_network_none_is_default_deny_and_restricted_egress_is_explicit():
    with pytest.raises(ValidationError):
        SandboxNetworkPolicy(mode="NONE", allowed_hosts=("example.com",))
    with pytest.raises(ValidationError):
        SandboxNetworkPolicy(mode="RESTRICTED_EGRESS")

    restricted = SandboxNetworkPolicy(
        mode="RESTRICTED_EGRESS",
        allowed_hosts=("example.com",),
    )
    assert restricted.allowed_hosts == ("example.com",)


def test_manager_requires_explicit_base_root_and_does_not_use_ambient_paths(
    tmp_path,
):
    parameter = inspect.signature(SandboxManager).parameters["base_root"]
    assert parameter.default is inspect.Parameter.empty

    manager = SandboxManager((tmp_path / "sandboxes").resolve())
    assert manager.base_root == (tmp_path / "sandboxes").resolve()
    assert manager.base_root != Path.cwd().resolve()
    assert manager.base_root != Path.home().resolve()


def test_sbx1_does_not_widen_crt_resource_scope():
    assert {scope.value for scope in ResourceScope} == {
        "SANDBOX",
        "CLIENT_LOCAL",
        "ASSET",
        "EXTERNAL",
        "INTERNAL_TRUSTED",
    }
