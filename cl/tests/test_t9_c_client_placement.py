from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cl.src.loader.local_tools import LocalToolManager


T9_C_ALL_IDS = (
    "file.read",
    "file.search",
    "file.write",
    "file.append",
    "file.replace",
    "glob.find",
    "terminal.run",
    "terminal.launch",
    "window.list",
    "window.find",
    "window.geometry",
    "window.focus",
    "window.close",
    "window.minimize",
    "window.maximize",
    "window.restore",
    "desktop.screen_info",
    "desktop.mouse_move",
    "desktop.mouse_click",
    "desktop.mouse_drag",
    "desktop.mouse_scroll",
    "desktop.type_text",
    "desktop.press_key",
    "desktop.hotkey",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_tools_config() -> dict:
    path = _repo_root() / "cl" / "config" / "setting.json"
    return json.loads(path.read_text(encoding="utf-8"))["tools_config"]


def test_default_client_placement_is_exact_24_non_web_ids():
    enabled = _default_tools_config()["enabled_v2_capabilities"]

    assert enabled == list(T9_C_ALL_IDS)
    assert len(enabled) == len(set(enabled)) == 24
    assert "*" not in enabled
    assert not any(item.startswith("web.") for item in enabled)
    assert not {
        "window_tool",
        "desktop_automation",
    }.intersection(enabled)


def test_real_window_desktop_top_level_v2_modules_load_without_physical_roots():
    root = _repo_root() / "tools" / "v1"
    loaded = LocalToolManager(root, set()).load_tools(_default_tools_config())

    assert set(T9_C_ALL_IDS).issubset(loaded)
    assert "window_tool" not in loaded
    assert "desktop_automation" not in loaded
    assert not {"web.search", "web.read", "web.read_many"}.intersection(loaded)


def test_window_desktop_bound_execution_preserves_physical_provenance(monkeypatch):
    root = _repo_root() / "tools" / "v1"
    loaded = LocalToolManager(root, set()).load_tools(_default_tools_config())

    window_module = sys.modules["local_tools.window_tool"]
    desktop_module = sys.modules["local_tools.desktop_tool"]

    window_calls = []
    desktop_calls = []

    class FakeWindowTool:
        def execute(self, action, **kwargs):
            window_calls.append((action, kwargs))
            return window_module.success_result(
                tool="window_tool",
                action=action,
                version=window_module.WINDOW_TOOL_VERSION,
                data={"kwargs": kwargs},
            )

    class FakeDesktopTool:
        def execute(self, action, **kwargs):
            desktop_calls.append((action, kwargs))
            return desktop_module.success_result(
                tool=desktop_module.DESKTOP_TOOL_NAME,
                action=action,
                version=desktop_module.DESKTOP_TOOL_VERSION,
                data={"kwargs": kwargs},
            )

    monkeypatch.setattr(window_module, "_default_window_tool", FakeWindowTool())
    monkeypatch.setattr(
        desktop_module,
        "_default_desktop_automation",
        FakeDesktopTool(),
    )

    geometry = loaded["window.geometry"]["func"](window_handle=42)
    assert geometry["tool"] == "window_tool"
    assert geometry["action"] == "get_geometry"
    assert window_calls == [("get_geometry", {"window_handle": 42})]

    screen = loaded["desktop.screen_info"]["func"]()
    assert screen["tool"] == "desktop_automation"
    assert screen["action"] == "get_screen_info"
    assert desktop_calls[0] == ("get_screen_info", {})

    click = loaded["desktop.mouse_click"]["func"](x=10, y=20, clicks=1)
    assert click["tool"] == "desktop_automation"
    assert click["action"] == "mouse_click"
    assert desktop_calls[1] == (
        "mouse_click",
        {"x": 10, "y": 20, "clicks": 1},
    )

    with pytest.raises(TypeError, match="immutable bound fields"):
        loaded["window.geometry"]["func"](
            action="focus",
            window_handle=42,
        )
