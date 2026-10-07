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
    SandboxProfile,
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

    assert profile.network.mode is SandboxNetworkMode.NONE
    assert profile.network.allow_hosts == ()
    assert profile.secrets.deny_by_default is True
    assert profile.secrets.allowed_names == ()
    assert profile.environment.allowed_names == ()

    with pytest.raises(ValidationError):
        profile.profile_id = "mutated"


def test_sandbox_limits_are_representation_not_ubq_or_timeout_authority():
    profile = SandboxProfile.model_validate(
        {
            "profile_id": "bounded",
            "filesystem": {
                "max_bytes": 1024,
                "max_files": 8,
                "max_path_length": 256,
            },
            "process": {
                "max_processes": 2,
                "max_cpu_seconds": 3.5,
                "max_memory_bytes": 4096,
            },
            "io": {
                "max_stdout_bytes": 100,
                "max_stderr_bytes": 100,
            },
        }
    )

    assert profile.filesystem.max_bytes == 1024
    assert profile.process.max_cpu_seconds == 3.5
    assert profile.io.max_stdout_bytes == 100
    assert "budget" not in SandboxProfile.model_fields
    assert "timeout" not in SandboxProfile.model_fields


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
