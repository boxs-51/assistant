from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.contracts.tool import ToolExecutionRequest
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.sandbox import SandboxProfile
from se.src.runtimes.capability.contracts.target import (
    CapabilityInvocationTarget,
    ResourceScope,
)
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.runtimes.capability.drivers.sandbox_python_driver import (
    SANDBOX_FILE_CAPABILITY_IDS,
    SandboxPythonCapabilityDriver,
)
from se.src.runtimes.capability.local_tool_loader import _build_canonical_v2_plans
from se.src.runtimes.capability.sandbox import (
    SandboxError,
    SandboxManager,
    SandboxPathError,
)
from tools.v1.file_tool import TOOL_METADATA as FILE_METADATA
from tools.v1.file_tool import run as file_run
from tools.v1.find_by_glob import TOOL_METADATA as GLOB_METADATA
from tools.v1.find_by_glob import run as glob_run


def _definition(capability_id: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description=capability_id,
        input_schema={"type": "object"},
        source="LOCAL",
        execution_kind="PYTHON",
        require_auth=False,
    )


def _target(execution_id: str) -> CapabilityInvocationTarget:
    return CapabilityInvocationTarget(
        resource_scope=ResourceScope.SANDBOX,
        resource_ref=execution_id,
    )


def _context(
    execution_id: str,
    *,
    user_id: str = "user-sbx2",
    target: CapabilityInvocationTarget | None = None,
) -> CapabilityExecutionContext:
    return CapabilityExecutionContext.create(
        identity=Identity(user_id=user_id, auth_type="jwt"),
        execution_id=execution_id,
        invocation_id=f"inv-{execution_id}",
        target=target or _target(execution_id),
    )


def _file_handler(action: str, *, mode: str | None = None):
    def handler(**arguments):
        if mode is not None:
            arguments = {"mode": mode, **arguments}
        return file_run(action=action, **arguments)

    return handler


def _driver(
    capability_id: str,
    manager: SandboxManager,
    profile: SandboxProfile,
) -> SandboxPythonCapabilityDriver:
    if capability_id == "glob.find":
        handler = glob_run
    else:
        bindings = {
            "file.read": ("read", None),
            "file.search": ("search", None),
            "file.write": ("write", "w"),
            "file.append": ("write", "a"),
            "file.replace": ("replace", None),
        }
        action, mode = bindings[capability_id]
        handler = _file_handler(action, mode=mode)
    return SandboxPythonCapabilityDriver(
        _definition(capability_id),
        handler,
        manager,
        profile,
    )


@pytest.mark.asyncio
async def test_sbx2_file_glob_share_one_execution_root_and_block_host_escape(
    tmp_path: Path,
):
    manager = SandboxManager(tmp_path / "sandboxes")
    profile = SandboxProfile(profile_id="sbx2-file-glob")
    context = _context("exec-a")

    write = _driver("file.write", manager, profile)
    read = _driver("file.read", manager, profile)
    append = _driver("file.append", manager, profile)
    replace = _driver("file.replace", manager, profile)
    search = _driver("file.search", manager, profile)
    glob = _driver("glob.find", manager, profile)

    await write.execute(context, {"file_paths": "note.txt", "content": "alpha"})
    lease = manager.current_for_execution("exec-a")
    root = lease.root
    assert (root / "note.txt").read_text() == "alpha"

    await append.execute(context, {"file_paths": "note.txt", "content": " beta"})
    await replace.execute(
        context,
        {
            "file_paths": "note.txt",
            "queries": "alpha",
            "replacements": "omega",
        },
    )
    await search.execute(
        context,
        {"file_paths": "note.txt", "queries": "omega"},
    )
    await read.execute(context, {"file_paths": "note.txt"})
    await glob.execute(context, {"pattern": "*.txt"})

    reused = manager.current_for_execution("exec-a")
    assert reused.sandbox_id == lease.sandbox_id
    assert (root / "note.txt").read_text() == "omega beta"

    host_secret = tmp_path / "host-secret.txt"
    host_secret.write_text("host-only")
    with pytest.raises(SandboxPathError):
        await read.execute(
            context,
            {"file_paths": str(host_secret.resolve())},
        )
    with pytest.raises(SandboxPathError):
        await read.execute(
            context,
            {"file_paths": "../host-secret.txt"},
        )
    assert host_secret.read_text() == "host-only"

    link = root / "escape-link"
    try:
        link.symlink_to(host_secret)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(SandboxPathError):
            await read.execute(context, {"file_paths": "escape-link"})


@pytest.mark.asyncio
async def test_sbx2_execution_identity_owner_profile_and_restart_are_isolated(
    tmp_path: Path,
):
    manager = SandboxManager(tmp_path / "sandboxes")
    profile = SandboxProfile(profile_id="sbx2-file-glob")
    write = _driver("file.write", manager, profile)

    await write.execute(
        _context("exec-a"),
        {"file_paths": "a.txt", "content": "a"},
    )
    await write.execute(
        _context("exec-b"),
        {"file_paths": "b.txt", "content": "b"},
    )
    lease_a = manager.current_for_execution("exec-a")
    lease_b = manager.current_for_execution("exec-b")
    assert lease_a.root != lease_b.root
    assert lease_a.sandbox_id != lease_b.sandbox_id

    with pytest.raises(SandboxError):
        manager.acquire_for_execution(
            execution_id="exec-a",
            owner_user_id="other-user",
            profile=profile,
        )
    with pytest.raises(SandboxError):
        manager.acquire_for_execution(
            execution_id="exec-a",
            owner_user_id="user-sbx2",
            profile=SandboxProfile(profile_id="other-profile"),
        )

    old_root = lease_a.root
    old_id = lease_a.sandbox_id
    manager.release_execution("exec-a", owner_user_id="user-sbx2")
    assert not old_root.exists()
    assert manager.release_execution("exec-a") is None

    replacement = manager.acquire_for_execution(
        execution_id="exec-a",
        owner_user_id="user-sbx2",
        profile=profile,
    )
    assert replacement.sandbox_id != old_id
    assert replacement.root != old_root


@pytest.mark.asyncio
async def test_sbx2_driver_fails_closed_for_client_local_target(tmp_path: Path):
    manager = SandboxManager(tmp_path / "sandboxes")
    profile = SandboxProfile(profile_id="sbx2-file-glob")
    read = _driver("file.read", manager, profile)
    context = _context(
        "exec-client",
        target=CapabilityInvocationTarget(
            resource_scope=ResourceScope.CLIENT_LOCAL,
            stable_client_id="client-a",
        ),
    )
    with pytest.raises(SandboxError):
        await read.execute(context, {"file_paths": "note.txt"})


def test_sbx2_loader_binds_exact_file_glob_ids_to_sandbox_driver(
    tmp_path: Path,
):
    manager = SandboxManager(tmp_path / "sandboxes")
    profile = SandboxProfile(profile_id="sbx2-file-glob")

    file_plans = _build_canonical_v2_plans(
        deepcopy(FILE_METADATA),
        file_run,
        sandbox_manager=manager,
        sandbox_profile=profile,
    )
    glob_plans = _build_canonical_v2_plans(
        deepcopy(GLOB_METADATA),
        glob_run,
        sandbox_manager=manager,
        sandbox_profile=profile,
    )

    plans = [*file_plans, *glob_plans]
    assert {plan.definition.capability_id for plan in plans} == set(
        SANDBOX_FILE_CAPABILITY_IDS
    )
    assert all(
        isinstance(plan.driver, SandboxPythonCapabilityDriver)
        for plan in plans
    )

    ordinary = _build_canonical_v2_plans(
        deepcopy(FILE_METADATA),
        file_run,
    )
    assert all(
        isinstance(plan.driver, PythonCapabilityDriver)
        for plan in ordinary
    )


class _AllowToolPolicy:
    def is_visible(self, **kwargs):
        return True

    def authorize(self, **kwargs):
        return PolicyDecision.ALLOW


class _AllowExecutionPolicy:
    def check_tool_call(self, context, request):
        return PolicyDecision.ALLOW


class _CaptureCapabilityRuntime:
    def __init__(self):
        self.kwargs = None
        self.catalog = None
        self.registry = SimpleNamespace(get=self._get)

    def _get(self, capability_id):
        return SimpleNamespace(
            definition=_definition(capability_id),
            executable=True,
        )

    async def execute_capability(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            invocation_id=kwargs["invocation_id"],
            output={"ok": True},
            metadata={},
        )


class _AgentContext:
    def __init__(self):
        self.execution_id = "agent-exec"
        self.connection_id = None
        self.identity = Identity(user_id="user-sbx2", auth_type="jwt")
        self.remaining_seconds = 30.0
        self.remaining_iteration_seconds = 30.0
        self.limits = SimpleNamespace(
            tool_timeout_seconds=10.0,
            max_tool_calls=10,
        )
        self.tool_calls_used = 0
        self.metadata = {}
        self.request_id = "req"
        self.session_id = "session"
        self.task_id = "task"
        self.branch_id = "branch"
        self.correlation_id = "corr"
        self.trace_id = "trace"
        self.workflow_id = None
        self.cancellation_event = asyncio.Event()
        self.recorded_usage = 0

    def ensure_active(self):
        return None

    async def reserve_tool_call(self):
        self.tool_calls_used += 1
        return True

    def remaining_for_operation(self, requested):
        return min(self.remaining_seconds, requested or self.remaining_seconds)

    def record_usage(self, *, tool_invocations=0, **kwargs):
        self.recorded_usage += tool_invocations


@pytest.mark.asyncio
async def test_sbx2_agent_adapter_constructs_explicit_sandbox_target():
    runtime = _CaptureCapabilityRuntime()
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        _AllowToolPolicy(),
        _AllowExecutionPolicy(),
    )
    context = _AgentContext()
    request = ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id="inv-agent",
        tool_call_id="tool-1",
        capability_id="file.read",
        arguments={"file_paths": "note.txt"},
    )

    result = await adapter.execute(context, request)
    assert result.success is True
    target = runtime.kwargs["target"]
    assert target.resource_scope is ResourceScope.SANDBOX
    assert target.resource_ref == context.execution_id
    assert target.stable_client_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "waiting", "failure", "cancel"])
async def test_sbx2_agent_runtime_releases_execution_sandbox_on_every_exit(
    tmp_path: Path,
    monkeypatch,
    mode: str,
):
    manager = SandboxManager(tmp_path / "sandboxes")
    profile = SandboxProfile(profile_id="sbx2-file-glob")
    lease = manager.acquire_for_execution(
        execution_id="exec-runtime",
        owner_user_id="user-sbx2",
        profile=profile,
    )
    root = lease.root

    runtime = AgentRuntime(
        context_builder=object(),
        inference=object(),
        tool_execution=object(),
        execution_policy=object(),
        sandbox_manager=manager,
    )
    context = SimpleNamespace(
        execution_id="exec-runtime",
        identity=Identity(user_id="user-sbx2", auth_type="jwt"),
    )

    async def begin(_context):
        return None

    async def loop(_context, *, initial_tool_results=()):
        if mode == "failure":
            raise RuntimeError("boom")
        if mode == "cancel":
            raise asyncio.CancelledError()
        return SimpleNamespace(state=mode)

    async def finish(_context, result, revision):
        return result

    async def cancel(_context, revision, *, error_message):
        return None

    monkeypatch.setattr(runtime, "_begin_durable_execution_owned", begin)
    monkeypatch.setattr(runtime, "_execute_loop", loop)
    monkeypatch.setattr(runtime, "_finish_durable_execution", finish)
    monkeypatch.setattr(runtime, "_cancel_durable_revision", cancel)

    if mode == "failure":
        with pytest.raises(RuntimeError):
            await runtime.execute(context)
    elif mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await runtime.execute(context)
    else:
        await runtime.execute(context)

    assert not root.exists()
    assert manager.release_execution("exec-runtime") is None
