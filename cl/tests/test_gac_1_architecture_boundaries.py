import inspect
from pathlib import Path

import pytest

from cl.src.game_automation.capture.window_capture import WindowsGdiCaptureBackend
from cl.src.game_automation.session.game_session import GameWindowIdentity
from cl.src.game_automation.session.window_manager import (
    CaptureGeometry,
    UnsupportedPlatformError,
    WindowsWindowBackend,
)


ROOT = Path(__file__).resolve().parents[2]
GAME_AUTOMATION_ROOT = ROOT / "cl/src/game_automation"


def test_gac1_exact_production_surface_is_four_new_modules_only():
    production_files = {
        path.relative_to(ROOT).as_posix()
        for path in GAME_AUTOMATION_ROOT.rglob("*.py")
    }
    assert production_files == {
        "cl/src/game_automation/session/game_session.py",
        "cl/src/game_automation/session/window_manager.py",
        "cl/src/game_automation/capture/frame_source.py",
        "cl/src/game_automation/capture/window_capture.py",
    }


def test_gac1_production_has_no_server_network_ui_capability_or_persistence_path():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in GAME_AUTOMATION_ROOT.rglob("*.py")
    )

    forbidden = (
        "ClientRuntime",
        "CapabilityRuntime",
        "CapabilityDispatcher",
        "UIBridge",
        "requests.",
        "httpx.",
        "websocket",
        "AssetService",
        "FileAsset",
        "FileBlob",
        "sqlalchemy",
        "redis",
        "socket.",
        "subprocess",
        "PyAutoGUI",
        "pyautogui",
        "key_down",
        "key_up",
        "mouse_move",
    )
    for token in forbidden:
        assert token not in source

    # GAC-1 is synchronous capture-one-frame only.
    assert "threading.Thread" not in source
    assert "asyncio" not in source


def test_win32_dependencies_are_lazy_not_module_level():
    manager_source = inspect.getsource(
        __import__(
            "cl.src.game_automation.session.window_manager",
            fromlist=["WindowsWindowBackend"],
        )
    )
    capture_source = inspect.getsource(
        __import__(
            "cl.src.game_automation.capture.window_capture",
            fromlist=["WindowsGdiCaptureBackend"],
        )
    )

    assert "import win32gui" in manager_source
    assert "def _modules" in manager_source
    assert "import win32gui" in capture_source
    assert "def _modules" in capture_source


def test_concrete_windows_backends_fail_explicitly_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")

    with pytest.raises(UnsupportedPlatformError, match="only on win32"):
        WindowsWindowBackend().enumerate_windows()

    with pytest.raises(UnsupportedPlatformError, match="only on win32"):
        WindowsGdiCaptureBackend().capture(
            identity=GameWindowIdentity(
                hwnd=100,
                process_id=200,
                process_start_time=300.0,
            ),
            geometry=CaptureGeometry(left=0, top=0, width=2, height=2),
        )
