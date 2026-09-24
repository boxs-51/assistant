from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.v1.live.harness import (
    ARTIFACT_ROOT_ENV,
    GUI_GATE_ENV,
    MASTER_GATE_ENV,
    NETWORK_GATE_ENV,
    EVIDENCE_SCHEMA,
    LiveCategory,
    LiveHarnessConfigError,
    LiveHarnessDisabled,
    OwnedProcessRegistry,
    Scenario,
    ScenarioRunner,
    ScenarioStep,
    ToolResultContractError,
    create_live_run_config,
    project_tool_error,
    require_live_enabled,
    write_evidence,
)


def _success(
    *,
    tool: str = "fake_tool",
    action: str = "read",
    data=None,
):
    return {
        "ok": True,
        "tool": tool,
        "action": action,
        "data": {} if data is None else data,
        "error": None,
        "meta": {
            "version": "2.0.0",
            "truncated": False,
            "warnings": [],
        },
    }


def _failure():
    return {
        "ok": False,
        "tool": "fake_tool",
        "action": "read",
        "data": None,
        "error": {
            "code": "FAKE_FAILED",
            "message": "synthetic failure",
            "retryable": False,
            "details": {
                "api_token": "secret-token",
                "nested": {"password": "secret-password"},
                "safe": "visible",
            },
        },
        "meta": {
            "version": "2.0.0",
            "truncated": False,
            "warnings": [],
        },
    }


def _clock():
    values = iter(
        [
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc),
        ]
    )
    return lambda: next(values)


def _configured_run(tmp_path: Path, category=LiveCategory.LOCAL):
    repo = tmp_path / "repo"
    repo.mkdir()
    artifacts = tmp_path / "artifacts"
    env = {
        MASTER_GATE_ENV: "1",
        ARTIFACT_ROOT_ENV: str(artifacts),
    }
    if category is LiveCategory.NETWORK:
        env[NETWORK_GATE_ENV] = "1"
    if category is LiveCategory.GUI:
        env[GUI_GATE_ENV] = "1"
    return create_live_run_config(
        category=category,
        repo_root=repo,
        env=env,
        run_id="unit-run",
    )


def test_live_gates_require_literal_one():
    assert require_live_enabled(
        LiveCategory.LOCAL,
        {MASTER_GATE_ENV: "1"},
    ) == (MASTER_GATE_ENV,)

    for value in ("true", "yes", "on", "TRUE", "", "0"):
        with pytest.raises(LiveHarnessDisabled):
            require_live_enabled(
                LiveCategory.LOCAL,
                {MASTER_GATE_ENV: value},
            )

    with pytest.raises(LiveHarnessDisabled):
        require_live_enabled(
            LiveCategory.NETWORK,
            {MASTER_GATE_ENV: "1"},
        )
    assert require_live_enabled(
        LiveCategory.NETWORK,
        {
            MASTER_GATE_ENV: "1",
            NETWORK_GATE_ENV: "1",
        },
    ) == (MASTER_GATE_ENV, NETWORK_GATE_ENV)

    with pytest.raises(LiveHarnessDisabled):
        require_live_enabled(
            LiveCategory.GUI,
            {MASTER_GATE_ENV: "1"},
        )
    assert require_live_enabled(
        LiveCategory.GUI,
        {
            MASTER_GATE_ENV: "1",
            GUI_GATE_ENV: "1",
        },
    ) == (MASTER_GATE_ENV, GUI_GATE_ENV)


def test_disabled_gate_allocates_no_artifact_directory(tmp_path):
    calls = []

    def forbidden_factory(prefix: str) -> str:
        calls.append(prefix)
        raise AssertionError("factory must not be called")

    with pytest.raises(LiveHarnessDisabled):
        create_live_run_config(
            category=LiveCategory.LOCAL,
            repo_root=tmp_path / "repo",
            env={},
            run_id="disabled",
            temp_directory_factory=forbidden_factory,
        )

    assert calls == []


def test_artifact_root_rejects_relative_and_repository_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(LiveHarnessConfigError):
        create_live_run_config(
            category=LiveCategory.LOCAL,
            repo_root=repo,
            env={
                MASTER_GATE_ENV: "1",
                ARTIFACT_ROOT_ENV: "relative/path",
            },
            run_id="relative",
        )

    inside = repo / "live-artifacts"
    with pytest.raises(LiveHarnessConfigError):
        create_live_run_config(
            category=LiveCategory.LOCAL,
            repo_root=repo,
            env={
                MASTER_GATE_ENV: "1",
                ARTIFACT_ROOT_ENV: str(inside),
            },
            run_id="inside",
        )
    assert not inside.exists()


def test_configured_artifact_root_creates_unique_per_run_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "artifacts"

    config = create_live_run_config(
        category=LiveCategory.LOCAL,
        repo_root=repo,
        env={
            MASTER_GATE_ENV: "1",
            ARTIFACT_ROOT_ENV: str(root),
        },
        run_id="abc-123",
    )

    assert config.artifact_directory == (
        root / "tools-v1-live-abc-123"
    ).resolve()
    assert config.artifact_directory.is_dir()
    assert config.enabled_gates == (MASTER_GATE_ENV,)


def test_default_temp_factory_runs_only_after_gate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def factory(prefix: str) -> str:
        calls.append(prefix)
        target = tmp_path / f"{prefix}factory"
        target.mkdir()
        return str(target)

    config = create_live_run_config(
        category=LiveCategory.LOCAL,
        repo_root=repo,
        env={MASTER_GATE_ENV: "1"},
        run_id="temp-run",
        temp_directory_factory=factory,
    )

    assert calls == ["tools-v1-live-temp-run-"]
    assert config.artifact_directory.is_dir()


def test_structured_error_projection_redacts_secret_details():
    error = project_tool_error(_failure())

    assert error == {
        "code": "FAKE_FAILED",
        "message": "synthetic failure",
        "retryable": False,
        "details": {
            "api_token": "[REDACTED]",
            "nested": {"password": "[REDACTED]"},
            "safe": "visible",
        },
    }

    with pytest.raises(ToolResultContractError):
        project_tool_error({"ok": False})


class FakeProcessController:
    def __init__(self, alive, *, stubborn=()):
        self.alive = set(alive)
        self.stubborn = set(stubborn)
        self.calls = []

    def is_alive(self, pid: int) -> bool:
        self.calls.append(("is_alive", pid))
        return pid in self.alive

    def terminate(self, pid: int) -> None:
        self.calls.append(("terminate", pid))
        if pid not in self.stubborn:
            self.alive.discard(pid)

    def wait(self, pid: int, timeout_seconds: float) -> bool:
        self.calls.append(("wait", pid, timeout_seconds))
        return pid not in self.alive

    def kill(self, pid: int) -> None:
        self.calls.append(("kill", pid))
        if pid not in self.stubborn:
            self.alive.discard(pid)


def test_owned_process_cleanup_never_targets_unowned_pid():
    controller = FakeProcessController({123, 999})
    registry = OwnedProcessRegistry(controller)
    registry.register_launch_result(
        _success(
            tool="terminal_tool",
            action="launch",
            data={"pid": 123, "started": True},
        )
    )

    report = registry.cleanup()

    assert report == {
        "owned_pids": [123],
        "terminated_pids": [123],
        "still_alive_pids": [],
        "errors": [],
    }
    assert all(
        call[1] != 999
        for call in controller.calls
        if len(call) >= 2
    )
    assert 999 in controller.alive


def test_missing_or_stubborn_process_controller_fails_cleanup():
    missing = OwnedProcessRegistry(None)
    missing.register_pid(7)
    report = missing.cleanup()
    assert report["owned_pids"] == [7]
    assert report["still_alive_pids"] == [7]
    assert report["errors"][0]["code"] == "LIVE_PROCESS_CONTROLLER_MISSING"

    stubborn_controller = FakeProcessController({9}, stubborn={9})
    stubborn = OwnedProcessRegistry(stubborn_controller)
    stubborn.register_pid(9)
    report = stubborn.cleanup()
    assert report["still_alive_pids"] == [9]
    assert ("terminate", 9) in stubborn_controller.calls
    assert ("kill", 9) in stubborn_controller.calls


def test_scenario_runner_is_explicit_fail_fast_and_always_tears_down(tmp_path):
    config = _configured_run(tmp_path)
    events = []

    def setup(context):
        events.append("setup")
        context.state["ready"] = True

    def first(context):
        events.append("step-1")
        assert context.state["ready"] is True
        return _success(data={"value": 1})

    def second(context):
        events.append("step-2")
        return _failure()

    def never(context):
        events.append("step-3")
        return _success()

    def teardown(context):
        events.append("teardown")

    scenario = Scenario(
        id="explicit-order",
        category=LiveCategory.LOCAL,
        setup=setup,
        teardown=teardown,
        steps=(
            ScenarioStep(
                id="one",
                tool="fake_tool",
                action="read",
                summary="first structured step",
                execute=first,
            ),
            ScenarioStep(
                id="two",
                tool="fake_tool",
                action="read",
                summary="failing structured step",
                execute=second,
            ),
            ScenarioStep(
                id="three",
                tool="fake_tool",
                action="read",
                summary="must not run",
                execute=never,
            ),
        ),
    )

    evidence = ScenarioRunner(config, clock=_clock()).run([scenario])

    assert events == ["setup", "step-1", "step-2", "teardown"]
    assert evidence["schema"] == EVIDENCE_SCHEMA
    assert evidence["status"] == "FAIL"
    assert evidence["scenarios"][0]["status"] == "FAIL"
    assert [step["id"] for step in evidence["scenarios"][0]["steps"]] == [
        "one",
        "two",
    ]
    assert evidence["scenarios"][0]["steps"][1]["error"]["code"] == "FAKE_FAILED"


def test_scenario_runner_cleanup_failure_forces_run_failure(tmp_path):
    config = _configured_run(tmp_path)
    controller = FakeProcessController({321}, stubborn={321})

    def launch(context):
        result = _success(
            tool="terminal_tool",
            action="launch",
            data={"pid": 321, "started": True},
        )
        context.processes.register_launch_result(result)
        return result

    scenario = Scenario(
        id="cleanup-failure",
        category=LiveCategory.LOCAL,
        steps=(
            ScenarioStep(
                id="launch",
                tool="terminal_tool",
                action="launch",
                summary="synthetic launch ownership",
                execute=launch,
            ),
        ),
    )

    evidence = ScenarioRunner(
        config,
        process_controller=controller,
        clock=_clock(),
    ).run([scenario])

    assert evidence["status"] == "FAIL"
    assert evidence["cleanup"]["owned_pids"] == [321]
    assert evidence["cleanup"]["still_alive_pids"] == [321]
    assert evidence["scenarios"][0]["error"]["code"] == (
        "LIVE_SCENARIO_CLEANUP_FAILED"
    )


def test_scenario_category_mismatch_rejects_before_setup(tmp_path):
    config = _configured_run(tmp_path, category=LiveCategory.LOCAL)
    called = []

    scenario = Scenario(
        id="network-wrong-gate",
        category=LiveCategory.NETWORK,
        setup=lambda context: called.append("setup"),
        steps=(),
    )

    with pytest.raises(LiveHarnessConfigError):
        ScenarioRunner(config).run([scenario])

    assert called == []


def test_write_evidence_is_deterministic_json(tmp_path):
    config = _configured_run(tmp_path)
    scenario = Scenario(
        id="pass",
        category=LiveCategory.LOCAL,
        steps=(
            ScenarioStep(
                id="read",
                tool="fake_tool",
                action="read",
                summary="structured pass",
                execute=lambda context: _success(data={"value": 1}),
                validate=lambda result: result["data"]["value"] == 1,
            ),
        ),
    )
    evidence = ScenarioRunner(config, clock=_clock()).run([scenario])

    target = write_evidence(evidence, config.artifact_directory)
    loaded = json.loads(target.read_text(encoding="utf-8"))

    assert loaded == evidence
    assert loaded["status"] == "PASS"
    assert loaded["cleanup"] == {
        "owned_pids": [],
        "terminated_pids": [],
        "still_alive_pids": [],
        "errors": [],
    }
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_evidence_filename_cannot_escape_artifact_directory(tmp_path):
    config = _configured_run(tmp_path)

    with pytest.raises(LiveHarnessConfigError):
        write_evidence(
            {"schema": EVIDENCE_SCHEMA},
            config.artifact_directory,
            filename="../escape.json",
        )
