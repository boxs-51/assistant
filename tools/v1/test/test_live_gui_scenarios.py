from __future__ import annotations

from collections.abc import Mapping

import tools.v1.live.gui_scenarios as gui_module
from tools.v1.live.gui_scenarios import (
    GuiProcessController,
    _target_command,
    build_gui_scenario,
    run_gui_live,
)
from tools.v1.live.harness import (
    ARTIFACT_ROOT_ENV,
    GUI_GATE_ENV,
    MASTER_GATE_ENV,
    NETWORK_GATE_ENV,
    LiveCategory,
    LiveHarnessConfigError,
    LiveHarnessDisabled,
    ProcessIdentity,
    ScenarioRunner,
    create_live_run_config,
)


def _success(tool, action, data):
    return {
        "ok": True,
        "tool": tool,
        "action": action,
        "data": data,
        "error": None,
        "meta": {"version": "2.0.0", "truncated": False, "warnings": []},
    }


class FakeProcessController:
    def __init__(self):
        self.alive = {}
        self.allowed_descendants = {}

    def capture(self, pid):
        ident = ProcessIdentity(pid=pid, token=f"token-{pid}")
        self.alive[pid] = True
        return ident

    def is_alive(self, identity):
        return self.alive.get(identity.pid, False)

    def terminate(self, identity):
        self.alive[identity.pid] = False

    def wait(self, identity, timeout_seconds):
        return not self.is_alive(identity)

    def kill(self, identity):
        self.alive[identity.pid] = False

    def verify_owned_window_pid(self, root_pid, candidate_pid):
        return candidate_pid == root_pid or self.allowed_descendants.get(root_pid) == candidate_pid


def _config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return create_live_run_config(
        category=LiveCategory.GUI,
        repo_root=repo,
        env={
            MASTER_GATE_ENV: "1",
            GUI_GATE_ENV: "1",
            ARTIFACT_ROOT_ENV: str(tmp_path / "artifacts"),
        },
        run_id="gui-unit",
    )


def test_gui_owned_target_flow_uses_pid_and_handle_authority(tmp_path):
    config = _config(tmp_path)
    process_controller = FakeProcessController()
    calls = []
    state = {"title": None, "typed_title": None, "launch_pid": 4321, "pid": 4322, "handle": 8765}
    process_controller.allowed_descendants[state["launch_pid"]] = state["pid"]

    def terminal_run(*, action, command, cwd):
        calls.append(("terminal", action, {"cwd": cwd}))
        assert action == "launch"
        return _success(
            "terminal_tool",
            "launch",
            {"pid": state["launch_pid"], "started": True, "cwd": cwd, "command": command},
        )

    def window_run(*, action, **kwargs):
        calls.append(("window", action, dict(kwargs)))
        if action == "find":
            query = kwargs["title_query"]
            if state["title"] is None:
                state["title"] = query
                title = query
            elif query != state["title"]:
                state["typed_title"] = query
                title = query
            else:
                title = query
            return _success(
                "window_tool",
                "find",
                {
                    "returned_count": 1,
                    "total_count": 1,
                    "windows": [
                        {
                            "selector": {
                                "window_handle": state["handle"],
                                "pid": state["pid"],
                            },
                            "title": title,
                            "title_truncated": False,
                            "app_name": "python",
                            "app_name_truncated": False,
                        }
                    ],
                },
            )
        if action == "focus":
            assert kwargs == {"window_handle": state["handle"], "pid": state["pid"]}
            return _success(
                "window_tool",
                "focus",
                {
                    "window": {
                        "selector": {"window_handle": state["handle"], "pid": state["pid"]},
                        "title": state["title"],
                        "title_truncated": False,
                        "app_name": "python",
                        "app_name_truncated": False,
                    },
                    "confirmed": True,
                },
            )
        if action == "get_geometry":
            assert kwargs == {"window_handle": state["handle"], "pid": state["pid"]}
            return _success(
                "window_tool",
                "get_geometry",
                {
                    "window": {
                        "selector": {"window_handle": state["handle"], "pid": state["pid"]},
                        "title": state["title"],
                        "title_truncated": False,
                        "app_name": "python",
                        "app_name_truncated": False,
                    },
                    "overall": {"left": 100, "top": 100, "width": 480, "height": 160, "right": 580, "bottom": 260},
                    "client_area": {"left": 110, "top": 130, "width": 460, "height": 120, "right": 570, "bottom": 250},
                    "frame_elements": {},
                },
            )
        if action == "close":
            assert kwargs == {"window_handle": state["handle"], "pid": state["pid"]}
            process_controller.alive[state["pid"]] = False
            return _success(
                "window_tool",
                "close",
                {
                    "window": {
                        "selector": {"window_handle": state["handle"], "pid": state["pid"]},
                        "title": state["typed_title"],
                        "title_truncated": False,
                        "app_name": "python",
                        "app_name_truncated": False,
                    },
                    "closed": True,
                },
            )
        raise AssertionError(action)

    def desktop_run(*, action, **kwargs):
        calls.append(("desktop", action, dict(kwargs)))
        if action == "mouse_click":
            assert kwargs["x"] == 340
            assert kwargs["y"] == 190
            return _success(
                "desktop_automation",
                "mouse_click",
                {
                    "x": kwargs["x"],
                    "y": kwargs["y"],
                    "position_mode": "explicit",
                    "button": "left",
                    "clicks": 1,
                },
            )
        if action == "type_text":
            return _success(
                "desktop_automation",
                "type_text",
                {
                    "character_count": len(kwargs["text"]),
                    "method": "pyautogui",
                    "clipboard_restored": None,
                },
            )
        raise AssertionError(action)

    evidence = ScenarioRunner(
        config,
        process_controller=process_controller,
    ).run(
        (
            build_gui_scenario(
                process_controller=process_controller,
                terminal_run=terminal_run,
                window_run=window_run,
                desktop_run=desktop_run,
            ),
        )
    )

    assert evidence["status"] == "PASS"
    scenario = evidence["scenarios"][0]
    assert scenario["id"] == "gui-window-desktop-owned-target"
    assert [step["status"] for step in scenario["steps"]] == ["PASS"] * 8
    assert evidence["cleanup"]["owned_pids"] == [state["launch_pid"]]
    assert evidence["cleanup"]["terminated_pids"] == [state["launch_pid"]]
    assert evidence["cleanup"]["still_alive_pids"] == []
    assert evidence["cleanup"]["errors"] == []

    side_effect_window_calls = [
        item for item in calls
        if item[0] == "window" and item[1] in {"focus", "get_geometry", "close"}
    ]
    assert all(
        call[2] == {"window_handle": state["handle"], "pid": state["pid"]}
        for call in side_effect_window_calls
    )


def test_gui_mismatched_pid_fails_before_desktop_actions(tmp_path):
    config = _config(tmp_path)
    controller = FakeProcessController()
    desktop_calls = []

    def terminal_run(**kwargs):
        return _success(
            "terminal_tool",
            "launch",
            {"pid": 111, "started": True, "cwd": kwargs["cwd"]},
        )

    def window_run(*, action, **kwargs):
        assert action == "find"
        return _success(
            "window_tool",
            "find",
            {
                "returned_count": 1,
                "total_count": 1,
                "windows": [
                    {
                        "selector": {"window_handle": 222, "pid": 999},
                        "title": kwargs["title_query"],
                        "title_truncated": False,
                        "app_name": "python",
                        "app_name_truncated": False,
                    }
                ],
            },
        )

    evidence = ScenarioRunner(config, process_controller=controller).run(
        (
            build_gui_scenario(
                process_controller=controller,
                terminal_run=terminal_run,
                window_run=window_run,
                desktop_run=lambda **kwargs: desktop_calls.append(kwargs),
            ),
        )
    )

    assert evidence["status"] == "FAIL"
    assert evidence["scenarios"][0]["steps"][-1]["id"] == "window-discover-owned"
    assert desktop_calls == []
    assert evidence["cleanup"]["owned_pids"] == [111]


def test_gui_entry_requires_master_and_gui_before_artifacts(tmp_path):
    repo = tmp_path / "repo"

    for env in ({}, {MASTER_GATE_ENV: "1"}, {GUI_GATE_ENV: "1"}):
        try:
            run_gui_live(env=env, repo_root=repo)
        except LiveHarnessDisabled:
            pass
        else:
            raise AssertionError("missing literal GUI gates must fail closed")
        assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_gui_entry_rejects_network_opt_in_before_artifacts(tmp_path):
    calls = []

    try:
        run_gui_live(
            env={
                MASTER_GATE_ENV: "1",
                GUI_GATE_ENV: "1",
                NETWORK_GATE_ENV: "1",
            },
            repo_root=tmp_path / "repo",
            terminal_run=lambda **kwargs: calls.append(("terminal", kwargs)),
            window_run=lambda **kwargs: calls.append(("window", kwargs)),
            desktop_run=lambda **kwargs: calls.append(("desktop", kwargs)),
        )
    except LiveHarnessConfigError:
        pass
    else:
        raise AssertionError("T10-E must reject simultaneous NETWORK opt-in")

    assert calls == []
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_gui_default_repo_root_ignores_nested_cwd_for_artifact_containment(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    module_path = repo / "tools" / "v1" / "live" / "gui_scenarios.py"
    module_path.parent.mkdir(parents=True)
    nested_cwd = repo / "tools"
    nested_cwd.mkdir(exist_ok=True)
    inside_repo_artifacts = repo / ".live-artifacts"

    monkeypatch.setattr(gui_module, "__file__", str(module_path))
    monkeypatch.chdir(nested_cwd)

    calls = []
    try:
        run_gui_live(
            env={
                MASTER_GATE_ENV: "1",
                GUI_GATE_ENV: "1",
                ARTIFACT_ROOT_ENV: str(inside_repo_artifacts),
            },
            terminal_run=lambda **kwargs: calls.append(("terminal", kwargs)),
            window_run=lambda **kwargs: calls.append(("window", kwargs)),
            desktop_run=lambda **kwargs: calls.append(("desktop", kwargs)),
        )
    except LiveHarnessConfigError:
        pass
    else:
        raise AssertionError(
            "artifact root inside full repository must fail despite nested cwd"
        )

    assert calls == []
    assert not inside_repo_artifacts.exists()


def test_gui_root_pid_reuse_fails_closed(monkeypatch):
    controller = GuiProcessController()
    controller._descendants[(777, 10.0)] = {}

    class ReusedProcess:
        def __init__(self, pid):
            assert pid == 777
            self.pid = pid

        def create_time(self):
            return 20.0

        def status(self):
            return "running"

    monkeypatch.setattr(gui_module.psutil, "Process", ReusedProcess)

    assert controller.verify_owned_window_pid(777, 777) is False


def test_gui_zero_window_timeout_preserves_empty_classification(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    controller = FakeProcessController()
    desktop_calls = []
    monkeypatch.setattr(gui_module, "GUI_DISCOVERY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(gui_module, "GUI_DISCOVERY_POLL_SECONDS", 0.0)

    def terminal_run(**kwargs):
        return _success(
            "terminal_tool",
            "launch",
            {"pid": 201, "started": True, "cwd": kwargs["cwd"]},
        )

    def window_run(*, action, **kwargs):
        assert action == "find"
        return _success(
            "window_tool",
            "find",
            {
                "returned_count": 0,
                "total_count": 0,
                "windows": [],
            },
        )

    evidence = ScenarioRunner(config, process_controller=controller).run(
        (
            build_gui_scenario(
                process_controller=controller,
                terminal_run=terminal_run,
                window_run=window_run,
                desktop_run=lambda **kwargs: desktop_calls.append(kwargs),
            ),
        )
    )

    step = evidence["scenarios"][0]["steps"][-1]
    assert step["id"] == "window-discover-owned"
    assert step["status"] == "FAIL"
    assert step["error"]["code"] == "LIVE_GUI_DISCOVERY_TIMEOUT"
    assert step["error"]["details"]["live_classification"] == "EMPTY_VALID_RESULT"
    assert step["error"]["details"]["final_returned_count"] == 0
    assert desktop_calls == []
    assert evidence["cleanup"]["owned_pids"] == [201]
    assert evidence["cleanup"]["still_alive_pids"] == []
    assert evidence["cleanup"]["errors"] == []


def test_gui_ownership_mismatch_timeout_preserves_classification(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    controller = FakeProcessController()
    desktop_calls = []
    monkeypatch.setattr(gui_module, "GUI_DISCOVERY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(gui_module, "GUI_DISCOVERY_POLL_SECONDS", 0.0)

    def terminal_run(**kwargs):
        return _success(
            "terminal_tool",
            "launch",
            {"pid": 301, "started": True, "cwd": kwargs["cwd"]},
        )

    def window_run(*, action, **kwargs):
        assert action == "find"
        return _success(
            "window_tool",
            "find",
            {
                "returned_count": 1,
                "total_count": 1,
                "windows": [
                    {
                        "selector": {
                            "window_handle": 444,
                            "pid": 999,
                        },
                        "title": kwargs["title_query"],
                        "title_truncated": False,
                        "app_name": "python",
                        "app_name_truncated": False,
                    }
                ],
            },
        )

    evidence = ScenarioRunner(config, process_controller=controller).run(
        (
            build_gui_scenario(
                process_controller=controller,
                terminal_run=terminal_run,
                window_run=window_run,
                desktop_run=lambda **kwargs: desktop_calls.append(kwargs),
            ),
        )
    )

    step = evidence["scenarios"][0]["steps"][-1]
    assert step["id"] == "window-discover-owned"
    assert step["status"] == "FAIL"
    assert step["error"]["code"] == "LIVE_GUI_DISCOVERY_TIMEOUT"
    assert step["error"]["details"]["live_classification"] == "OWNERSHIP_MISMATCH"
    assert step["error"]["details"]["final_returned_count"] == 1
    assert desktop_calls == []
    assert evidence["cleanup"]["owned_pids"] == [301]
    assert evidence["cleanup"]["still_alive_pids"] == []
    assert evidence["cleanup"]["errors"] == []


def test_gui_target_command_is_deterministic_and_single_line():
    args = ("TOOLS_V1_T10E_test", "TOOLS_V1_T10E_test_TYPED", "T10E_test")
    first = _target_command(*args)
    second = _target_command(*args)

    assert first == second
    assert "\n" not in first
    assert "\r" not in first
    assert "base64" in first.lower()


def test_gui_launch_ack_does_not_imply_target_readiness(tmp_path, monkeypatch):
    config = _config(tmp_path)
    controller = FakeProcessController()
    desktop_calls = []
    commands = []
    monkeypatch.setattr(gui_module, "GUI_DISCOVERY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(gui_module, "GUI_DISCOVERY_POLL_SECONDS", 0.0)

    def terminal_run(**kwargs):
        commands.append(kwargs["command"])
        return _success(
            "terminal_tool",
            "launch",
            {"pid": 401, "started": True, "cwd": kwargs["cwd"]},
        )

    def window_run(*, action, **kwargs):
        assert action == "find"
        return _success(
            "window_tool",
            "find",
            {
                "returned_count": 0,
                "total_count": 0,
                "windows": [],
            },
        )

    evidence = ScenarioRunner(config, process_controller=controller).run(
        (
            build_gui_scenario(
                process_controller=controller,
                terminal_run=terminal_run,
                window_run=window_run,
                desktop_run=lambda **kwargs: desktop_calls.append(kwargs),
            ),
        )
    )

    assert len(commands) == 1
    assert "\n" not in commands[0]
    assert "\r" not in commands[0]
    steps = evidence["scenarios"][0]["steps"]
    assert steps[0]["id"] == "gui-target-launch"
    assert steps[0]["status"] == "PASS"
    assert steps[1]["id"] == "window-discover-owned"
    assert steps[1]["status"] == "FAIL"
    assert steps[1]["error"]["code"] == "LIVE_GUI_DISCOVERY_TIMEOUT"
    assert desktop_calls == []
    assert evidence["cleanup"]["owned_pids"] == [401]
    assert evidence["cleanup"]["still_alive_pids"] == []
    assert evidence["cleanup"]["errors"] == []
