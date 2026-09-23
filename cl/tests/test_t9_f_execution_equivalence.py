from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from cl.src.loader.local_tools import LocalToolManager
from tools.v1 import (
    desktop_tool,
    file_tool,
    find_by_glob,
    terminal_tool,
    window_tool,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tools_config() -> dict:
    path = _repo_root() / "cl" / "config" / "setting.json"
    return json.loads(path.read_text(encoding="utf-8"))["tools_config"]


def _python_echo_command(value: str) -> str:
    args = [sys.executable, "-c", f"print({value!r})"]
    return (
        subprocess.list2cmdline(args)
        if os.name == "nt"
        else shlex.join(args)
    )


def _load_logical_tools():
    return LocalToolManager(
        _repo_root() / "tools" / "v1",
        set(),
    ).load_tools(_tools_config())


def test_logical_representatives_bind_to_unchanged_physical_dispatchers(
    monkeypatch,
    tmp_path,
):
    loaded = _load_logical_tools()

    file_module = sys.modules["local_tools.file_tool"]
    glob_module = sys.modules["local_tools.find_by_glob"]
    terminal_module = sys.modules["local_tools.terminal_tool"]
    window_module = sys.modules["local_tools.window_tool"]
    desktop_module = sys.modules["local_tools.desktop_tool"]

    calls: dict[str, tuple] = {}

    class FakeFileTool:
        def execute(self, action, file_paths=None, **kwargs):
            calls["file"] = (action, file_paths, kwargs)
            return file_module.success_result(
                tool="file_tool",
                action=action,
                version=file_module.FILE_TOOL_VERSION,
                data={"file_paths": file_paths, "kwargs": kwargs},
            )

    class FakeGlobTool:
        def execute(
            self,
            pattern,
            root_dir=".",
            recursive=True,
            max_results=None,
            **kwargs,
        ):
            calls["glob"] = (
                pattern,
                root_dir,
                recursive,
                max_results,
                kwargs,
            )
            return glob_module.success_result(
                tool="find_by_glob",
                action="find",
                version=glob_module.GLOB_TOOL_VERSION,
                data={"pattern": pattern},
            )

    class FakeTerminalTool:
        def execute(
            self,
            action,
            command,
            timeout=None,
            cwd=None,
            encoding=None,
            **kwargs,
        ):
            calls["terminal"] = (
                action,
                command,
                timeout,
                cwd,
                encoding,
                kwargs,
            )
            return terminal_module.success_result(
                tool="terminal_tool",
                action=action,
                version=terminal_module.TERMINAL_TOOL_VERSION,
                data={"command": command},
            )

    class FakeWindowTool:
        def execute(self, action, **kwargs):
            calls["window"] = (action, kwargs)
            return window_module.success_result(
                tool="window_tool",
                action=action,
                version=window_module.WINDOW_TOOL_VERSION,
                data={"kwargs": kwargs},
            )

    class FakeDesktopTool:
        def execute(self, action, **kwargs):
            calls.setdefault("desktop", []).append((action, kwargs))
            return desktop_module.success_result(
                tool=desktop_module.DESKTOP_TOOL_NAME,
                action=action,
                version=desktop_module.DESKTOP_TOOL_VERSION,
                data={"kwargs": kwargs},
            )

    monkeypatch.setattr(file_module, "_default_file_tool", FakeFileTool())
    monkeypatch.setattr(glob_module, "_default_glob_tool", FakeGlobTool())
    monkeypatch.setattr(terminal_module, "TerminalTool", FakeTerminalTool)
    monkeypatch.setattr(window_module, "_default_window_tool", FakeWindowTool())
    monkeypatch.setattr(
        desktop_module,
        "_default_desktop_automation",
        FakeDesktopTool(),
    )

    append_entry = loaded["file.append"]
    assert append_entry["metadata"]["version"] == "1.0"
    assert append_entry["metadata"]["physical_version"] == "2.0.0"
    assert append_entry["metadata"]["bind"] == {
        "action": "write",
        "mode": "a",
    }
    appended = append_entry["func"](
        file_paths=str(tmp_path / "append.txt"),
        content="B",
        encoding="utf-8",
    )
    assert calls["file"] == (
        "write",
        str(tmp_path / "append.txt"),
        {"mode": "a", "content": "B", "encoding": "utf-8"},
    )
    assert appended["tool"] == "file_tool"
    assert appended["action"] == "write"
    assert appended["meta"]["version"] == "2.0.0"

    glob_entry = loaded["glob.find"]
    assert glob_entry["metadata"]["bind"] == {}
    found = glob_entry["func"](
        pattern="*.txt",
        root_dir=str(tmp_path),
        recursive=False,
        max_results=7,
    )
    assert calls["glob"] == (
        "*.txt",
        str(tmp_path),
        False,
        7,
        {},
    )
    assert found["tool"] == "find_by_glob"
    assert found["action"] == "find"
    assert found["meta"]["version"] == "2.0.0"

    terminal_entry = loaded["terminal.run"]
    assert terminal_entry["metadata"]["bind"] == {"action": "run"}
    terminal = terminal_entry["func"](
        command="echo t9-f",
        timeout=3,
        cwd=str(tmp_path),
        encoding="utf-8",
    )
    assert calls["terminal"] == (
        "run",
        "echo t9-f",
        3,
        str(tmp_path),
        "utf-8",
        {},
    )
    assert terminal["tool"] == "terminal_tool"
    assert terminal["action"] == "run"
    assert terminal["meta"]["version"] == "2.0.0"

    geometry_entry = loaded["window.geometry"]
    assert geometry_entry["metadata"]["bind"] == {
        "action": "get_geometry"
    }
    geometry = geometry_entry["func"](window_handle=42)
    assert calls["window"] == (
        "get_geometry",
        {"window_handle": 42},
    )
    assert geometry["tool"] == "window_tool"
    assert geometry["action"] == "get_geometry"
    assert geometry["meta"]["version"] == "2.0.0"

    screen_entry = loaded["desktop.screen_info"]
    assert screen_entry["metadata"]["bind"] == {
        "action": "get_screen_info"
    }
    screen = screen_entry["func"]()
    assert calls["desktop"][0] == ("get_screen_info", {})
    assert screen["tool"] == "desktop_automation"
    assert screen["action"] == "get_screen_info"
    assert screen["meta"]["version"] == "2.0.0"

    # Non-idempotent desktop side effect is proven only through a fake.
    click = loaded["desktop.mouse_click"]["func"](
        x=10,
        y=20,
        button="left",
        clicks=2,
    )
    assert calls["desktop"][1] == (
        "mouse_click",
        {
            "x": 10,
            "y": 20,
            "button": "left",
            "clicks": 2,
        },
    )
    assert click["tool"] == "desktop_automation"
    assert click["action"] == "mouse_click"
    assert click["meta"]["version"] == "2.0.0"

    for capability_id, forbidden in (
        ("file.append", {"action": "read"}),
        ("window.geometry", {"action": "focus"}),
        ("desktop.screen_info", {"action": "mouse_click"}),
    ):
        with pytest.raises(TypeError, match="immutable bound fields"):
            loaded[capability_id]["func"](**forbidden)


def test_direct_physical_run_entrypoints_remain_compatible(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "physical.txt"
    target.write_text("A", encoding="utf-8")

    file_result = file_tool.run(
        action="write",
        file_paths=str(target),
        mode="a",
        content="B",
        encoding="utf-8",
    )
    assert file_result["ok"] is True
    assert file_result["tool"] == "file_tool"
    assert file_result["action"] == "write"
    assert file_result["meta"]["version"] == file_tool.FILE_TOOL_VERSION
    assert target.read_text(encoding="utf-8") == "AB"

    glob_result = find_by_glob.run(
        pattern="*.txt",
        root_dir=str(tmp_path),
        recursive=False,
        max_results=10,
    )
    assert glob_result["ok"] is True
    assert glob_result["tool"] == "find_by_glob"
    assert glob_result["action"] == "find"
    assert glob_result["meta"]["version"] == find_by_glob.GLOB_TOOL_VERSION

    terminal_result = terminal_tool.run(
        action="run",
        command=_python_echo_command("t9-f-direct"),
        timeout=10,
        cwd=str(tmp_path),
        encoding="utf-8",
    )
    assert terminal_result["ok"] is True
    assert terminal_result["tool"] == "terminal_tool"
    assert terminal_result["action"] == "run"
    assert terminal_result["meta"]["version"] == terminal_tool.TERMINAL_TOOL_VERSION
    assert "t9-f-direct" in terminal_result["data"]["stdout"]

    window_calls = []
    desktop_calls = []

    class FakePhysicalWindowTool:
        def execute(self, action, **kwargs):
            window_calls.append((action, kwargs))
            return window_tool.success_result(
                tool="window_tool",
                action=action,
                version=window_tool.WINDOW_TOOL_VERSION,
                data={"kwargs": kwargs},
            )

    class FakePhysicalDesktopTool:
        def execute(self, action, **kwargs):
            desktop_calls.append((action, kwargs))
            return desktop_tool.success_result(
                tool=desktop_tool.DESKTOP_TOOL_NAME,
                action=action,
                version=desktop_tool.DESKTOP_TOOL_VERSION,
                data={"kwargs": kwargs},
            )

    monkeypatch.setattr(
        window_tool,
        "_default_window_tool",
        FakePhysicalWindowTool(),
    )
    monkeypatch.setattr(
        desktop_tool,
        "_default_desktop_automation",
        FakePhysicalDesktopTool(),
    )

    window_result = window_tool.run(
        action="get_geometry",
        window_handle=17,
    )
    assert window_calls == [("get_geometry", {"window_handle": 17})]
    assert window_result["tool"] == "window_tool"
    assert window_result["action"] == "get_geometry"
    assert window_result["meta"]["version"] == window_tool.WINDOW_TOOL_VERSION

    desktop_result = desktop_tool.run(action="get_screen_info")
    assert desktop_calls == [("get_screen_info", {})]
    assert desktop_result["tool"] == "desktop_automation"
    assert desktop_result["action"] == "get_screen_info"
    assert desktop_result["meta"]["version"] == desktop_tool.DESKTOP_TOOL_VERSION
