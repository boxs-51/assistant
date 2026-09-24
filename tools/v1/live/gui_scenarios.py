from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psutil

from tools.v1 import desktop_tool, terminal_tool, window_tool
from tools.v1.live.harness import (
    ARTIFACT_ROOT_ENV,
    GUI_GATE_ENV,
    MASTER_GATE_ENV,
    NETWORK_GATE_ENV,
    LiveCategory,
    LiveHarnessConfigError,
    Scenario,
    ScenarioContext,
    ScenarioRunner,
    ScenarioStep,
    ProcessIdentity,
    create_live_run_config,
    write_evidence,
)
from tools.v1.live.local_scenarios import PsutilProcessController


GUI_DISCOVERY_TIMEOUT_SECONDS = 5.0
GUI_DISCOVERY_POLL_SECONDS = 0.1
GUI_TITLE_PREFIX = "TOOLS_V1_T10E"


class GuiProcessController(PsutilProcessController):
    """LOCAL process cleanup plus exact owned-descendant verification for GUI windows."""

    def verify_owned_window_pid(self, root_pid: int, candidate_pid: int) -> bool:
        if type(root_pid) is not int or root_pid <= 0:
            return False
        if type(candidate_pid) is not int or candidate_pid <= 0:
            return False
        root_keys = [key for key in self._descendants if key[0] == root_pid]
        if len(root_keys) != 1:
            return False
        key = root_keys[0]

        if candidate_pid == root_pid:
            try:
                candidate = psutil.Process(candidate_pid)
                return (
                    abs(candidate.create_time() - key[1]) < 0.001
                    and candidate.status() != psutil.STATUS_ZOMBIE
                )
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                return False
        try:
            candidate = psutil.Process(candidate_pid)
            candidate_identity = ProcessIdentity(
                pid=candidate_pid,
                token=candidate.create_time(),
            )
            parents = candidate.parents()
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            return False

        root_token = key[1]
        owned = False
        for parent in parents:
            if parent.pid != root_pid:
                continue
            try:
                if abs(parent.create_time() - root_token) < 0.001:
                    owned = True
                    break
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                return False

        if not owned:
            return False

        self._descendants[key][candidate_pid] = candidate_identity
        return True


def _python_command(code: str) -> str:
    args = [sys.executable, "-c", code]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def _target_script(title: str, typed_title: str, marker: str) -> str:
    return (
        "import tkinter as tk\n"
        "root=tk.Tk()\n"
        f"root.title({title!r})\n"
        "entry=tk.Entry(root)\n"
        "entry.pack(fill='both', expand=True)\n"
        "entry.focus_force()\n"
        f"marker={marker!r}\n"
        f"typed_title={typed_title!r}\n"
        "def changed(event=None):\n"
        "    if entry.get() == marker:\n"
        "        root.title(typed_title)\n"
        "entry.bind('<KeyRelease>', changed)\n"
        "root.geometry('480x160+120+120')\n"
        "root.mainloop()\n"
    )


def _window_identity(data: Any) -> tuple[int, int] | None:
    if not isinstance(data, Mapping):
        return None
    window = data.get("window")
    if not isinstance(window, Mapping):
        return None
    selector = window.get("selector")
    if not isinstance(selector, Mapping):
        return None
    handle = selector.get("window_handle")
    pid = selector.get("pid")
    if type(handle) is not int or handle <= 0 or type(pid) is not int or pid <= 0:
        return None
    return handle, pid


def _validate_launch(result: Mapping[str, Any], *, expected_cwd: str) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and type(data.get("pid")) is int
        and data["pid"] > 0
        and data.get("started") is True
        and data.get("cwd") == expected_cwd
    )


def _validate_find(result: Mapping[str, Any], *, expected_title: str, expected_pid: int) -> bool:
    data = result.get("data")
    if not isinstance(data, Mapping):
        return False
    windows = data.get("windows")
    if data.get("returned_count") != 1 or not isinstance(windows, list) or len(windows) != 1:
        return False
    item = windows[0]
    if not isinstance(item, Mapping) or item.get("title") != expected_title:
        return False
    selector = item.get("selector")
    return (
        isinstance(selector, Mapping)
        and type(selector.get("window_handle")) is int
        and selector["window_handle"] > 0
        and selector.get("pid") == expected_pid
    )


def _validate_identity_result(
    result: Mapping[str, Any],
    *,
    expected_handle: int,
    expected_pid: int,
    flag: str,
) -> bool:
    data = result.get("data")
    ident = _window_identity(data)
    return (
        ident == (expected_handle, expected_pid)
        and isinstance(data, Mapping)
        and data.get(flag) is True
    )


def _validate_geometry(
    result: Mapping[str, Any],
    *,
    expected_handle: int,
    expected_pid: int,
) -> bool:
    data = result.get("data")
    if _window_identity(data) != (expected_handle, expected_pid):
        return False
    if not isinstance(data, Mapping):
        return False
    area = data.get("client_area") or data.get("overall")
    return (
        isinstance(area, Mapping)
        and all(type(area.get(key)) is int for key in ("left", "top", "width", "height"))
        and area["width"] > 20
        and area["height"] > 20
    )


def _validate_click(result: Mapping[str, Any], *, x: int, y: int) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and data.get("x") == x
        and data.get("y") == y
        and data.get("position_mode") == "explicit"
        and data.get("button") == "left"
        and data.get("clicks") == 1
    )


def _validate_type(result: Mapping[str, Any], *, marker: str) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and data.get("character_count") == len(marker)
        and data.get("method") in {"pyautogui", "pynput", "clipboard"}
    )


def build_gui_scenario(
    *,
    process_controller,
    terminal_run=terminal_tool.run,
    window_run=window_tool.run,
    desktop_run=desktop_tool.run,
) -> Scenario:
    expected: dict[str, Any] = {}

    def launch(context: ScenarioContext) -> Mapping[str, Any]:
        run_id = context.config.run_id
        title = f"{GUI_TITLE_PREFIX}_{run_id}"
        marker = f"T10E_{run_id}"
        typed_title = f"{title}_TYPED"
        expected.update(title=title, marker=marker, typed_title=typed_title)
        cwd = str(context.config.artifact_directory.resolve())
        expected["cwd"] = cwd
        result = terminal_run(
            action="launch",
            command=_python_command(_target_script(title, typed_title, marker)),
            cwd=cwd,
        )
        if result.get("ok") is True:
            expected["launch_pid"] = context.processes.register_launch_result(result)
        return result

    def discover(context: ScenarioContext) -> Mapping[str, Any]:
        deadline = time.monotonic() + GUI_DISCOVERY_TIMEOUT_SECONDS
        last: Mapping[str, Any] | None = None
        while time.monotonic() < deadline:
            last = window_run(
                action="find",
                title_query=expected["title"],
                max_results=10,
            )
            if last.get("ok") is not True:
                return last
            data = last.get("data")
            if isinstance(data, Mapping) and data.get("returned_count") == 1:
                windows = data.get("windows")
                if isinstance(windows, list) and len(windows) == 1:
                    item = windows[0]
                    if isinstance(item, Mapping):
                        selector = item.get("selector")
                        if isinstance(selector, Mapping):
                            candidate_pid = selector.get("pid")
                            candidate_handle = selector.get("window_handle")
                            if (
                                type(candidate_pid) is int
                                and type(candidate_handle) is int
                                and candidate_handle > 0
                                and process_controller.verify_owned_window_pid(
                                    expected["launch_pid"],
                                    candidate_pid,
                                )
                            ):
                                expected["pid"] = candidate_pid
                                expected["handle"] = candidate_handle
                                return last
            time.sleep(GUI_DISCOVERY_POLL_SECONDS)
        return last or window_run(action="find", title_query=expected["title"], max_results=10)

    def focus(context: ScenarioContext) -> Mapping[str, Any]:
        return window_run(
            action="focus",
            window_handle=expected["handle"],
            pid=expected["pid"],
        )

    def geometry(context: ScenarioContext) -> Mapping[str, Any]:
        result = window_run(
            action="get_geometry",
            window_handle=expected["handle"],
            pid=expected["pid"],
        )
        if result.get("ok") is True:
            data = result.get("data")
            if isinstance(data, Mapping):
                area = data.get("client_area") or data.get("overall")
                if isinstance(area, Mapping):
                    expected["click_x"] = area["left"] + max(1, area["width"] // 2)
                    expected["click_y"] = area["top"] + max(1, area["height"] // 2)
        return result

    def click(context: ScenarioContext) -> Mapping[str, Any]:
        return desktop_run(
            action="mouse_click",
            x=expected["click_x"],
            y=expected["click_y"],
            button="left",
            clicks=1,
        )

    def type_marker(context: ScenarioContext) -> Mapping[str, Any]:
        return desktop_run(
            action="type_text",
            text=expected["marker"],
            interval=0.02,
        )

    def verify_typed(context: ScenarioContext) -> Mapping[str, Any]:
        deadline = time.monotonic() + GUI_DISCOVERY_TIMEOUT_SECONDS
        last: Mapping[str, Any] | None = None
        while time.monotonic() < deadline:
            last = window_run(
                action="find",
                title_query=expected["typed_title"],
                max_results=10,
            )
            if last.get("ok") is not True:
                return last
            if _validate_find(
                last,
                expected_title=expected["typed_title"],
                expected_pid=expected["pid"],
            ):
                windows = last["data"]["windows"]
                selector = windows[0]["selector"]
                if selector.get("window_handle") == expected["handle"]:
                    return last
            time.sleep(GUI_DISCOVERY_POLL_SECONDS)
        return last or window_run(action="find", title_query=expected["typed_title"], max_results=10)

    def close(context: ScenarioContext) -> Mapping[str, Any]:
        return window_run(
            action="close",
            window_handle=expected["handle"],
            pid=expected["pid"],
        )

    return Scenario(
        id="gui-window-desktop-owned-target",
        category=LiveCategory.GUI,
        steps=(
            ScenarioStep(
                id="gui-target-launch",
                tool="terminal_tool",
                action="launch",
                summary="Launch one unique scenario-owned Tkinter target.",
                execute=launch,
                validate=lambda result: _validate_launch(result, expected_cwd=expected["cwd"]),
            ),
            ScenarioStep(
                id="window-discover-owned",
                tool="window_tool",
                action="find",
                summary="Discover the unique GUI target and bind it to the owned PID.",
                execute=discover,
                validate=lambda result: _validate_find(
                    result,
                    expected_title=expected["title"],
                    expected_pid=expected["pid"],
                ),
            ),
            ScenarioStep(
                id="window-focus-owned",
                tool="window_tool",
                action="focus",
                summary="Focus only the exact owned window handle and PID.",
                execute=focus,
                validate=lambda result: _validate_identity_result(
                    result,
                    expected_handle=expected["handle"],
                    expected_pid=expected["pid"],
                    flag="confirmed",
                ),
            ),
            ScenarioStep(
                id="window-geometry-owned",
                tool="window_tool",
                action="get_geometry",
                summary="Read geometry from the exact owned window before desktop input.",
                execute=geometry,
                validate=lambda result: _validate_geometry(
                    result,
                    expected_handle=expected["handle"],
                    expected_pid=expected["pid"],
                ),
            ),
            ScenarioStep(
                id="desktop-click-owned",
                tool="desktop_automation",
                action="mouse_click",
                summary="Click inside the verified owned target before keyboard input.",
                execute=click,
                validate=lambda result: _validate_click(
                    result,
                    x=expected["click_x"],
                    y=expected["click_y"],
                ),
            ),
            ScenarioStep(
                id="desktop-type-owned",
                tool="desktop_automation",
                action="type_text",
                summary="Type a bounded marker into the verified foreground target.",
                execute=type_marker,
                validate=lambda result: _validate_type(result, marker=expected["marker"]),
            ),
            ScenarioStep(
                id="window-verify-typed-owned",
                tool="window_tool",
                action="find",
                summary="Verify the marker reached the same owned handle and PID.",
                execute=verify_typed,
                validate=lambda result: (
                    _validate_find(
                        result,
                        expected_title=expected["typed_title"],
                        expected_pid=expected["pid"],
                    )
                    and result["data"]["windows"][0]["selector"]["window_handle"] == expected["handle"]
                ),
            ),
            ScenarioStep(
                id="window-close-owned",
                tool="window_tool",
                action="close",
                summary="Close only the exact owned handle and PID.",
                execute=close,
                validate=lambda result: _validate_identity_result(
                    result,
                    expected_handle=expected["handle"],
                    expected_pid=expected["pid"],
                    flag="closed",
                ),
            ),
        ),
    )


def run_gui_live(
    *,
    env: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
    terminal_run=terminal_tool.run,
    window_run=window_tool.run,
    desktop_run=desktop_tool.run,
) -> dict[str, Any]:
    effective_env = os.environ if env is None else env
    if effective_env.get(NETWORK_GATE_ENV) == "1":
        raise LiveHarnessConfigError("TV1-T10-E GUI execution requires NETWORK gate OFF")

    root = (
        Path(__file__).resolve().parents[3]
        if repo_root is None
        else Path(repo_root)
    )
    config = create_live_run_config(
        category=LiveCategory.GUI,
        repo_root=root,
        env=effective_env,
    )
    process_controller = GuiProcessController()
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
    write_evidence(evidence, config.artifact_directory)
    return evidence


if __name__ == "__main__":
    evidence = run_gui_live()
    raise SystemExit(0 if evidence["status"] == "PASS" else 1)
