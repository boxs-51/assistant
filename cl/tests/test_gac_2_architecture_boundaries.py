import inspect
import sys
from pathlib import Path

import pytest

from cl.src.game_automation.actions.cancellation import EmergencyStopLatch
from cl.src.game_automation.actions.focus_guard import WindowsForegroundBackend
from cl.src.game_automation.actions.keyboard import WindowsKeyboardBackend
from cl.src.game_automation.actions.mouse import WindowsMouseBackend
from cl.src.game_automation.session.window_manager import UnsupportedPlatformError


ROOT = Path(__file__).resolve().parents[2]
ACTION_ROOT = ROOT / "cl/src/game_automation/actions"


def test_gac2_exact_production_surface_is_six_new_action_modules():
    action_files = {
        path.relative_to(ROOT).as_posix()
        for path in ACTION_ROOT.glob("*.py")
    }
    assert action_files == {
        "cl/src/game_automation/actions/action.py",
        "cl/src/game_automation/actions/cancellation.py",
        "cl/src/game_automation/actions/scheduler.py",
        "cl/src/game_automation/actions/keyboard.py",
        "cl/src/game_automation/actions/mouse.py",
        "cl/src/game_automation/actions/focus_guard.py",
    }


def test_gac2_has_no_server_network_capability_ubq_cas_or_behavior_integration():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in ACTION_ROOT.glob("*.py")
    )

    forbidden = (
        "from se.",
        "import se.",
        "cl.src.core",
        "ClientRuntime",
        "CapabilityRuntime",
        "CapabilityDispatcher",
        "requests.",
        "httpx.",
        "websocket",
        "sqlalchemy",
        "redis",
        "AssetService",
        "FileAsset",
        "FileBlob",
        "behavior_engine",
        "yolo",
        "bytetrack",
    )
    for token in forbidden:
        assert token not in source.lower() if token.islower() else token not in source

    assert "time.sleep(" not in source
    assert not hasattr(EmergencyStopLatch(), "reset")


def test_generic_gac2_modules_import_without_eager_win32_dependencies():
    for module in (
        "cl.src.game_automation.actions.action",
        "cl.src.game_automation.actions.cancellation",
        "cl.src.game_automation.actions.scheduler",
        "cl.src.game_automation.actions.keyboard",
        "cl.src.game_automation.actions.mouse",
        "cl.src.game_automation.actions.focus_guard",
    ):
        assert module in sys.modules or __import__(module, fromlist=["*"])


def test_windows_action_backends_keep_native_imports_lazy():
    keyboard_source = inspect.getsource(WindowsKeyboardBackend)
    mouse_source = inspect.getsource(WindowsMouseBackend)
    focus_source = inspect.getsource(WindowsForegroundBackend)

    assert "import pyautogui" in keyboard_source
    assert "def _module" in keyboard_source
    assert "import pyautogui" in mouse_source
    assert "def _module" in mouse_source
    assert "import win32gui" in focus_source
    assert "def _module" in focus_source


def test_concrete_windows_action_backends_fail_explicitly_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")

    with pytest.raises(UnsupportedPlatformError, match="only on win32"):
        WindowsKeyboardBackend().key_down("w")
    with pytest.raises(UnsupportedPlatformError, match="only on win32"):
        WindowsMouseBackend().move_to(1, 1)
    with pytest.raises(UnsupportedPlatformError, match="only on win32"):
        WindowsForegroundBackend().foreground_hwnd()
