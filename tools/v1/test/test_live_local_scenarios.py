from __future__ import annotations

from pathlib import Path

import pytest

from tools.v1.live.harness import (
    ARTIFACT_ROOT_ENV,
    MASTER_GATE_ENV,
    LiveCategory,
    LiveHarnessDisabled,
    ProcessIdentity,
    ScenarioRunner,
    create_live_run_config,
)
from tools.v1.live.local_scenarios import (
    LOCAL_FILE_NAME,
    LOCAL_MARKER,
    LiveProcessIdentityError,
    PsutilProcessController,
    _python_command,
    build_local_scenario,
    run_local_live,
)


def _success(tool, action, data):
    return {
        "ok": True,
        "tool": tool,
        "action": action,
        "data": data,
        "error": None,
        "meta": {
            "version": "2.0.0",
            "truncated": False,
            "warnings": [],
        },
    }


class FakeController:
    def __init__(self):
        self.alive = {4321}
        self.calls = []

    def capture(self, pid):
        self.calls.append(("capture", pid))
        if pid not in self.alive:
            return None
        return ProcessIdentity(pid=pid, token="stable-4321")

    def is_alive(self, identity):
        self.calls.append(("is_alive", identity.pid, identity.token))
        return identity.pid in self.alive

    def terminate(self, identity):
        self.calls.append(("terminate", identity.pid, identity.token))
        self.alive.discard(identity.pid)

    def wait(self, identity, timeout_seconds):
        self.calls.append(
            ("wait", identity.pid, identity.token, timeout_seconds)
        )
        return identity.pid not in self.alive

    def kill(self, identity):
        self.calls.append(("kill", identity.pid, identity.token))
        self.alive.discard(identity.pid)


def _config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return create_live_run_config(
        category=LiveCategory.LOCAL,
        repo_root=repo,
        env={
            MASTER_GATE_ENV: "1",
            ARTIFACT_ROOT_ENV: str(tmp_path / "artifacts"),
        },
        run_id="local-unit",
    )


def test_local_scenario_uses_structured_file_glob_terminal_contracts(tmp_path):
    config = _config(tmp_path)
    calls = []
    controller = FakeController()

    def terminal_run(*, action, command, cwd, **kwargs):
        calls.append(("terminal", action, cwd, kwargs))
        if action == "run":
            return _success(
                "terminal_tool",
                "run",
                {
                    "exit_code": 0,
                    "stdout": f"{LOCAL_MARKER}\n",
                    "stderr": "",
                    "cwd": str(Path(cwd).resolve()),
                    "encoding": "utf-8",
                    "duration_ms": 1,
                    "stdout_bytes": len(LOCAL_MARKER) + 1,
                    "stderr_bytes": 0,
                },
            )
        assert action == "launch"
        return _success(
            "terminal_tool",
            "launch",
            {
                "pid": 4321,
                "cwd": str(Path(cwd).resolve()),
                "started": True,
            },
        )

    def file_run(*, action, file_paths, **kwargs):
        calls.append(("file", action, file_paths, kwargs))
        if action == "write":
            return _success(
                "file_tool",
                "write",
                {
                    "path": Path(file_paths).absolute().as_posix(),
                    "created": True,
                    "changed": True,
                },
            )
        assert action == "read"
        return _success(
            "file_tool",
            "read",
            {
                "path": Path(file_paths).absolute().as_posix(),
                "content": f"{LOCAL_MARKER}\n",
                "eof": True,
                "returned_line_count": 1,
                "next_start_line": None,
            },
        )

    def glob_run(*, pattern, root_dir, recursive, max_results):
        calls.append(
            ("glob", pattern, root_dir, recursive, max_results)
        )
        return _success(
            "find_by_glob",
            "find",
            {
                "root_dir": Path(root_dir).absolute().as_posix(),
                "pattern": pattern,
                "returned_count": 1,
                "matches": [
                    {
                        "path": (
                            Path(root_dir)
                            / LOCAL_FILE_NAME
                        ).absolute().as_posix(),
                        "kind": "file",
                    }
                ],
            },
        )

    scenario = build_local_scenario(
        file_run=file_run,
        glob_run=glob_run,
        terminal_run=terminal_run,
    )
    evidence = ScenarioRunner(
        config,
        process_controller=controller,
    ).run((scenario,))

    assert scenario.category is LiveCategory.LOCAL
    assert [step.action for step in scenario.steps] == [
        "run",
        "write",
        "read",
        "find",
        "launch",
    ]
    assert evidence["status"] == "PASS"
    assert [
        step["status"]
        for step in evidence["scenarios"][0]["steps"]
    ] == ["PASS"] * 5
    assert evidence["cleanup"]["owned_pids"] == [4321]
    assert evidence["cleanup"]["terminated_pids"] == [4321]
    assert evidence["cleanup"]["still_alive_pids"] == []
    assert any(call[0] == "terminate" for call in controller.calls)

    artifact = str(config.artifact_directory)
    assert calls[0][0:2] == ("terminal", "run")
    assert calls[0][2] == artifact
    assert calls[1][0:2] == ("file", "write")
    assert Path(calls[1][2]).parent == config.artifact_directory
    assert calls[2][0:2] == ("file", "read")
    assert calls[3] == (
        "glob",
        "*.txt",
        artifact,
        False,
        20,
    )
    assert calls[4][0:3] == ("terminal", "launch", artifact)


@pytest.mark.parametrize(
    "wrong_field",
    [
        "terminal_run_cwd",
        "file_write_path",
        "file_read_path",
        "glob_root",
        "glob_match_path",
        "terminal_launch_cwd",
    ],
)
def test_local_scenario_rejects_wrong_requested_location(
    tmp_path,
    wrong_field,
):
    config = _config(tmp_path)
    controller = FakeController()
    artifact_terminal = str(config.artifact_directory.resolve())
    artifact_canonical = config.artifact_directory.absolute().as_posix()
    file_canonical = (
        config.artifact_directory / LOCAL_FILE_NAME
    ).absolute().as_posix()
    wrong_terminal = str((tmp_path / "wrong-cwd").resolve())
    wrong_canonical = (tmp_path / "wrong-path").absolute().as_posix()

    def terminal_run(*, action, command, cwd, **kwargs):
        if action == "run":
            return _success(
                "terminal_tool",
                "run",
                {
                    "exit_code": 0,
                    "stdout": f"{LOCAL_MARKER}\n",
                    "stderr": "",
                    "cwd": (
                        wrong_terminal
                        if wrong_field == "terminal_run_cwd"
                        else artifact_terminal
                    ),
                    "encoding": "utf-8",
                    "duration_ms": 1,
                    "stdout_bytes": len(LOCAL_MARKER) + 1,
                    "stderr_bytes": 0,
                },
            )
        return _success(
            "terminal_tool",
            "launch",
            {
                "pid": 4321,
                "cwd": (
                    wrong_terminal
                    if wrong_field == "terminal_launch_cwd"
                    else artifact_terminal
                ),
                "started": True,
            },
        )

    def file_run(*, action, file_paths, **kwargs):
        if action == "write":
            return _success(
                "file_tool",
                "write",
                {
                    "path": (
                        wrong_canonical
                        if wrong_field == "file_write_path"
                        else file_canonical
                    ),
                    "created": True,
                    "changed": True,
                },
            )
        return _success(
            "file_tool",
            "read",
            {
                "path": (
                    wrong_canonical
                    if wrong_field == "file_read_path"
                    else file_canonical
                ),
                "content": f"{LOCAL_MARKER}\n",
                "eof": True,
                "returned_line_count": 1,
                "next_start_line": None,
            },
        )

    def glob_run(*, pattern, root_dir, recursive, max_results):
        return _success(
            "find_by_glob",
            "find",
            {
                "root_dir": (
                    wrong_canonical
                    if wrong_field == "glob_root"
                    else artifact_canonical
                ),
                "pattern": pattern,
                "recursive": recursive,
                "returned_count": 1,
                "matches": [
                    {
                        "path": (
                            wrong_canonical
                            if wrong_field == "glob_match_path"
                            else file_canonical
                        ),
                        "kind": "file",
                        "is_symlink": False,
                    }
                ],
            },
        )

    scenario = build_local_scenario(
        file_run=file_run,
        glob_run=glob_run,
        terminal_run=terminal_run,
    )
    evidence = ScenarioRunner(
        config,
        process_controller=controller,
    ).run((scenario,))

    assert evidence["status"] == "FAIL"
    failed_steps = [
        step
        for step in evidence["scenarios"][0]["steps"]
        if step["status"] == "FAIL"
    ]
    assert len(failed_steps) == 1
    assert failed_steps[0]["error"]["code"] == (
        "LIVE_SCENARIO_ASSERTION_FAILED"
    )

    if wrong_field != "terminal_launch_cwd":
        assert controller.calls == []
    else:
        # launch ownership is captured before semantic validation so cleanup
        # still drains the exact owned process even when cwd evidence is wrong.
        assert any(call[0] == "capture" for call in controller.calls)
        assert any(call[0] == "terminate" for call in controller.calls)


def test_local_entry_gate_blocks_before_controller_or_artifacts(
    monkeypatch,
    tmp_path,
):
    def forbidden_controller():
        raise AssertionError("controller must not be constructed")

    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.PsutilProcessController",
        forbidden_controller,
    )

    with pytest.raises(LiveHarnessDisabled):
        run_local_live(
            env={},
            repo_root=tmp_path / "repo",
        )

    assert list(tmp_path.iterdir()) == []


def test_python_command_uses_current_interpreter():
    command = _python_command("print('x')")
    assert str(Path(__import__("sys").executable)) in command
    assert "D:\\assistant" not in command


class FakePsutilProcess:
    def __init__(self, pid, create_time, status="running"):
        self.pid = pid
        self._create_time = create_time
        self._status = status
        self._children = []
        self.terminate_calls = 0
        self.kill_calls = 0

    def create_time(self):
        return self._create_time

    def status(self):
        return self._status

    def children(self, recursive=True):
        assert recursive is True
        return list(self._children)

    def terminate(self):
        self.terminate_calls += 1
        self._status = "zombie"

    def kill(self):
        self.kill_calls += 1
        self._status = "zombie"


def test_psutil_controller_refuses_reused_pid(monkeypatch):
    original = FakePsutilProcess(88, 100.0)
    reused = FakePsutilProcess(88, 200.0)
    current = {"process": original}

    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.psutil.Process",
        lambda pid: current["process"],
    )
    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.psutil.STATUS_ZOMBIE",
        "zombie",
    )

    controller = PsutilProcessController()
    identity = controller.capture(88)
    assert identity == ProcessIdentity(pid=88, token=100.0)

    current["process"] = reused

    assert controller.is_alive(identity) is False
    controller.terminate(identity)
    controller.kill(identity)

    assert reused.terminate_calls == 0
    assert reused.kill_calls == 0


def test_psutil_controller_access_denied_fails_closed(monkeypatch):
    class AccessDeniedProcess(FakePsutilProcess):
        def create_time(self):
            raise __import__("psutil").AccessDenied(pid=self.pid)

    denied = AccessDeniedProcess(77, 1.0)

    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.psutil.Process",
        lambda pid: denied,
    )
    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.psutil.STATUS_ZOMBIE",
        "zombie",
    )

    controller = PsutilProcessController()
    with pytest.raises(LiveProcessIdentityError):
        controller.capture(77)

    # An identity captured earlier must also fail closed if later inspection
    # becomes access-denied; it must never be treated as already gone.
    identity = ProcessIdentity(pid=77, token=1.0)
    with pytest.raises(LiveProcessIdentityError):
        controller.is_alive(identity)


def test_psutil_controller_cleans_exact_owned_tree(monkeypatch):
    root = FakePsutilProcess(100, 10.0)
    child = FakePsutilProcess(101, 11.0)
    root._children = [child]
    processes = {100: root, 101: child}

    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.psutil.Process",
        lambda pid: processes[pid],
    )
    monkeypatch.setattr(
        "tools.v1.live.local_scenarios.psutil.STATUS_ZOMBIE",
        "zombie",
    )

    controller = PsutilProcessController()
    identity = controller.capture(100)
    assert identity == ProcessIdentity(pid=100, token=10.0)

    controller.terminate(identity)

    assert root.terminate_calls == 1
    assert child.terminate_calls == 1
    assert controller.wait(identity, 0.1) is True


def test_local_scenario_validators_fail_closed_on_wrong_structured_data(
    tmp_path,
):
    config = _config(tmp_path)
    controller = FakeController()

    def bad_terminal(**kwargs):
        action = kwargs["action"]
        if action == "run":
            return _success(
                "terminal_tool",
                "run",
                {"exit_code": 7, "stdout": "", "stderr": "bad"},
            )
        return _success(
            "terminal_tool",
            "launch",
            {"pid": 4321, "started": True, "cwd": kwargs["cwd"]},
        )

    scenario = build_local_scenario(
        file_run=lambda **kwargs: _success(
            "file_tool",
            kwargs["action"],
            {},
        ),
        glob_run=lambda **kwargs: _success(
            "find_by_glob",
            "find",
            {"matches": []},
        ),
        terminal_run=bad_terminal,
    )

    evidence = ScenarioRunner(
        config,
        process_controller=controller,
    ).run((scenario,))

    assert evidence["status"] == "FAIL"
    assert len(evidence["scenarios"][0]["steps"]) == 1
    assert evidence["scenarios"][0]["steps"][0]["error"]["code"] == (
        "LIVE_SCENARIO_ASSERTION_FAILED"
    )
    assert controller.calls == []
