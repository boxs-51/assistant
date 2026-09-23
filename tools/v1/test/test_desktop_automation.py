import json
import math
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import tools.v1.desktop_tool as desktop_module
from tools.v1.desktop_tool import (
    ACTIONS_METADATA,
    CLICK_COUNT,
    DESKTOP_TOOL_NAME,
    DESKTOP_TOOL_VERSION,
    DRAG_DURATION,
    MAX_COORD_ABS,
    MAX_HOTKEY_KEYS,
    MAX_KEY_CHARS,
    MAX_SCROLL_ABS,
    MAX_TEXT_CHARS,
    MOVE_DURATION,
    PRESS_COUNT,
    PYAUTOGUI_PAUSE,
    TOOL_METADATA,
    TYPE_INTERVAL,
    DesktopAutomation,
    get_screen_info,
    hotkey,
    mouse_click,
    mouse_drag,
    mouse_move,
    mouse_scroll,
    press_key,
    run,
    type_text,
)


class FakeFailSafeException(Exception):
    pass


class FakePyAutoGUI:
    FailSafeException = FakeFailSafeException

    def __init__(self):
        self.FAILSAFE = False
        self.PAUSE = 0.77
        self.size = MagicMock(return_value=(1920, 1080))
        self.position = MagicMock(return_value=(500, 300))
        self.click = MagicMock()
        self.moveTo = MagicMock()
        self.dragTo = MagicMock()
        self.scroll = MagicMock()
        self.write = MagicMock()
        self.press = MagicMock()
        self.hotkey = MagicMock()


class FakeClipboard:
    def __init__(self, old="old clipboard"):
        self.old = old
        self.paste = MagicMock(return_value=old)
        self.copy = MagicMock()


class RestoreFailBackend(FakePyAutoGUI):
    def __init__(self):
        self._failsafe = False
        self._pause = 0.77
        self._pause_assignments = 0
        self.size = MagicMock(return_value=(1920, 1080))
        self.position = MagicMock(return_value=(500, 300))
        self.click = MagicMock()
        self.moveTo = MagicMock()
        self.dragTo = MagicMock()
        self.scroll = MagicMock()
        self.write = MagicMock()
        self.press = MagicMock()
        self.hotkey = MagicMock()

    @property
    def FAILSAFE(self):
        return self._failsafe

    @FAILSAFE.setter
    def FAILSAFE(self, value):
        self._failsafe = value

    @property
    def PAUSE(self):
        return self._pause

    @PAUSE.setter
    def PAUSE(self, value):
        self._pause_assignments += 1
        if self._pause_assignments >= 2:
            raise RuntimeError("restore failed")
        self._pause = value


class TestDesktopAutomationV2(unittest.TestCase):
    def setUp(self):
        self.backend = FakePyAutoGUI()
        self.tool = DesktopAutomation(failsafe=True, pause=0.1)
        self.tool._pyautogui = self.backend

    def assert_ok(self, result, action):
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], DESKTOP_TOOL_NAME)
        self.assertEqual(result["action"], action)
        self.assertEqual(result["meta"]["version"], DESKTOP_TOOL_VERSION)
        self.assertIsNone(result["error"])
        json.dumps(result)
        return result["data"]

    def assert_error(self, result, action, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["tool"], DESKTOP_TOOL_NAME)
        self.assertEqual(result["action"], action)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsNone(result["data"])
        json.dumps(result)

    def test_metadata_root_identity_and_actions_metadata_consistency(self):
        self.assertEqual(TOOL_METADATA["name"], "desktop_automation")
        canonical = TOOL_METADATA["parameters"]["properties"]["action"]["enum"]
        self.assertEqual(canonical, list(ACTIONS_METADATA))
        self.assertEqual(
            ACTIONS_METADATA["type_text"]["parameters"]["properties"]["text"]["maxLength"],
            MAX_TEXT_CHARS,
        )
        self.assertEqual(
            ACTIONS_METADATA["mouse_click"]["parameters"]["properties"]["clicks"]["maximum"],
            CLICK_COUNT.maximum,
        )
        self.assertEqual(
            TOOL_METADATA["parameters"]["properties"]["clicks"]["minimum"],
            -MAX_SCROLL_ABS,
        )
        self.assertEqual(
            TOOL_METADATA["parameters"]["properties"]["clicks"]["maximum"],
            MAX_SCROLL_ABS,
        )

    def test_module_import_does_not_import_desktop_dependencies(self):
        script = (
            "import builtins, sys\n"
            "real = builtins.__import__\n"
            "def guard(name, *a, **k):\n"
            "    if name in {'pyautogui','pyperclip','pynput','pynput.keyboard'} or "
            "name.startswith('pynput.'):\n"
            "        raise AssertionError('eager desktop dependency import: ' + name)\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = guard\n"
            "import tools.v1.desktop_tool\n"
            "assert 'pyautogui' not in sys.modules\n"
            "assert 'pyperclip' not in sys.modules\n"
            "assert 'pynput.keyboard' not in sys.modules\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_constructor_is_side_effect_free_and_validates_config(self):
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=AssertionError("constructor must not import"),
        ):
            tool = DesktopAutomation(failsafe=False, pause=0.0)
        self.assertFalse(tool.failsafe)
        self.assertEqual(tool.pause, 0.0)

        for failsafe in (0, 1, "yes"):
            with self.subTest(failsafe=failsafe):
                with self.assertRaises(Exception):
                    DesktopAutomation(failsafe=failsafe)

        for pause in (-1, True, float("inf"), PYAUTOGUI_PAUSE.maximum + 1):
            with self.subTest(pause=pause):
                with self.assertRaises(Exception):
                    DesktopAutomation(pause=pause)

    def test_lazy_dependency_failures_and_control_flow(self):
        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            result = tool.get_screen_info()
        self.assert_error(result, "get_screen_info", "DEPENDENCY_UNAVAILABLE")
        self.assertEqual(result["error"]["details"]["dependency"], "pyautogui")

        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=RuntimeError("headless"),
        ):
            result = tool.get_screen_info()
        self.assert_error(result, "get_screen_info", "DEPENDENCY_UNAVAILABLE")

        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                tool.get_screen_info()

    def test_scoped_pyautogui_state_restores_after_success_and_failure(self):
        original = (self.backend.FAILSAFE, self.backend.PAUSE)
        data = self.assert_ok(self.tool.mouse_move(10, 20), "mouse_move")
        self.assertEqual(data["x"], 10)
        self.assertEqual((self.backend.FAILSAFE, self.backend.PAUSE), original)

        self.backend.click.side_effect = RuntimeError("backend error")
        result = self.tool.mouse_click()
        self.assert_error(result, "mouse_click", "DESKTOP_OPERATION_FAILED")
        self.assertEqual((self.backend.FAILSAFE, self.backend.PAUSE), original)

    def test_backend_state_restore_failure_has_precedence(self):
        backend = RestoreFailBackend()
        tool = DesktopAutomation()
        tool._pyautogui = backend
        result = tool.mouse_move(1, 2)
        self.assert_error(
            result,
            "mouse_move",
            "DESKTOP_BACKEND_STATE_RESTORE_FAILED",
        )

    def test_failsafe_is_structured(self):
        self.backend.click.side_effect = FakeFailSafeException()
        result = self.tool.mouse_click()
        self.assert_error(result, "mouse_click", "DESKTOP_FAILSAFE_TRIGGERED")

    def test_screen_info_valid_and_malformed(self):
        data = self.assert_ok(self.tool.get_screen_info(), "get_screen_info")
        self.assertEqual(
            data,
            {
                "screen_width": 1920,
                "screen_height": 1080,
                "mouse_x": 500,
                "mouse_y": 300,
            },
        )

        self.backend.size.return_value = ("1920", 1080)
        result = self.tool.get_screen_info()
        self.assert_error(result, "get_screen_info", "DESKTOP_PROPERTY_FAILED")

        self.backend.size.return_value = (1920, 1080)
        self.backend.position.return_value = (True, 0)
        result = self.tool.get_screen_info()
        self.assert_error(result, "get_screen_info", "DESKTOP_PROPERTY_FAILED")

    def test_mouse_click_current_and_explicit_coordinates(self):
        data = self.assert_ok(self.tool.mouse_click(button=" RIGHT "), "mouse_click")
        self.assertEqual(data["position_mode"], "current")
        self.backend.click.assert_called_with(
            x=None, y=None, clicks=1, button="right"
        )

        data = self.assert_ok(
            self.tool.mouse_click(x=-100, y=200, clicks=2),
            "mouse_click",
        )
        self.assertEqual(data["position_mode"], "explicit")
        self.assertEqual(data["x"], -100)
        self.backend.click.assert_called_with(
            x=-100, y=200, clicks=2, button="left"
        )

    def test_mouse_click_validation(self):
        for kwargs in (
            {"x": 1, "y": None},
            {"x": None, "y": 1},
            {"x": True, "y": 1},
            {"x": MAX_COORD_ABS + 1, "y": 0},
            {"button": "side"},
            {"clicks": 0},
            {"clicks": True},
            {"clicks": CLICK_COUNT.maximum + 1},
        ):
            with self.subTest(kwargs=kwargs):
                result = self.tool.mouse_click(**kwargs)
                self.assert_error(result, "mouse_click", "INVALID_ARGUMENT")

    def test_mouse_move_and_duration_bounds(self):
        data = self.assert_ok(
            self.tool.mouse_move(-10, 20, duration=0),
            "mouse_move",
        )
        self.assertEqual(data["duration_seconds"], 0.0)
        self.backend.moveTo.assert_called_with(-10, 20, duration=0.0)

        for duration in (True, -1, float("nan"), MOVE_DURATION.maximum + 1):
            with self.subTest(duration=duration):
                result = self.tool.mouse_move(1, 2, duration=duration)
                self.assert_error(result, "mouse_move", "INVALID_ARGUMENT")

    def test_mouse_drag_without_and_with_explicit_start(self):
        data = self.assert_ok(
            self.tool.mouse_drag(50, 60, duration=0.25),
            "mouse_drag",
        )
        self.assertFalse(data["explicit_start"])
        self.backend.dragTo.assert_called_with(
            50, 60, duration=0.25, button="left"
        )

        self.backend.moveTo.reset_mock()
        self.backend.dragTo.reset_mock()
        data = self.assert_ok(
            self.tool.mouse_drag(
                100,
                200,
                start_x=-10,
                start_y=-20,
                button="middle",
                duration=0.5,
            ),
            "mouse_drag",
        )
        self.assertTrue(data["explicit_start"])
        self.backend.moveTo.assert_called_once_with(-10, -20)
        self.backend.dragTo.assert_called_once_with(
            100, 200, duration=0.5, button="middle"
        )

    def test_mouse_drag_rejects_partial_start_and_invalid_duration(self):
        result = self.tool.mouse_drag(1, 2, start_x=0)
        self.assert_error(result, "mouse_drag", "INVALID_ARGUMENT")
        result = self.tool.mouse_drag(1, 2, start_y=0)
        self.assert_error(result, "mouse_drag", "INVALID_ARGUMENT")
        result = self.tool.mouse_drag(1, 2, duration=DRAG_DURATION.maximum + 1)
        self.assert_error(result, "mouse_drag", "INVALID_ARGUMENT")

    def test_mouse_scroll_contract(self):
        data = self.assert_ok(self.tool.mouse_scroll(-300), "mouse_scroll")
        self.assertEqual(data["clicks"], -300)
        self.backend.scroll.assert_called_with(-300)

        for value in (0, True, MAX_SCROLL_ABS + 1, -MAX_SCROLL_ABS - 1):
            with self.subTest(value=value):
                result = self.tool.mouse_scroll(value)
                self.assert_error(result, "mouse_scroll", "INVALID_ARGUMENT")

    def test_press_key_contract(self):
        data = self.assert_ok(
            self.tool.press_key(" ENTER ", presses=2),
            "press_key",
        )
        self.assertEqual(data, {"key": "enter", "presses": 2})
        self.backend.press.assert_called_with("enter", presses=2)

        for key, presses in (
            ("", 1),
            ("x" * (MAX_KEY_CHARS + 1), 1),
            ("enter", 0),
            ("enter", True),
            ("enter", PRESS_COUNT.maximum + 1),
        ):
            with self.subTest(key=key[:10], presses=presses):
                result = self.tool.press_key(key, presses)
                self.assert_error(result, "press_key", "INVALID_ARGUMENT")

    def test_hotkey_contract_and_bounds(self):
        data = self.assert_ok(
            self.tool.hotkey([" CTRL ", "C"]),
            "hotkey",
        )
        self.assertEqual(data["keys"], ["ctrl", "c"])
        self.assertEqual(data["key_count"], 2)
        self.backend.hotkey.assert_called_with("ctrl", "c")

        invalid = (
            [],
            "ctrl+c",
            ["ctrl", ""],
            ["x" * (MAX_KEY_CHARS + 1)],
            ["x"] * (MAX_HOTKEY_KEYS + 1),
        )
        for keys in invalid:
            with self.subTest(keys=repr(keys)[:40]):
                result = self.tool.hotkey(keys)
                self.assert_error(result, "hotkey", "INVALID_ARGUMENT")

    def test_type_text_preflight_rejects_invalid_inputs_before_dependencies(self):
        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=AssertionError("invalid input must fail before imports"),
        ):
            for kwargs in (
                {"text": ""},
                {"text": "a\x00b"},
                {"text": "x" * (MAX_TEXT_CHARS + 1)},
                {"text": "x", "force_direct": 1},
                {"text": "x", "restore_clipboard": 1},
                {"text": "x", "interval": True},
                {"text": "x", "interval": float("inf")},
                {"text": "x", "interval": TYPE_INTERVAL.maximum + 1},
            ):
                with self.subTest(kwargs=list(kwargs)):
                    result = tool.type_text(**kwargs)
                    self.assert_error(result, "type_text", "INVALID_ARGUMENT")

    def test_whitespace_only_text_is_valid(self):
        data = self.assert_ok(
            self.tool.type_text("   ", force_direct=True),
            "type_text",
        )
        self.assertEqual(data["character_count"], 3)
        self.backend.write.assert_called_with("   ", interval=TYPE_INTERVAL.default)

    def test_ascii_short_and_long_are_direct_and_never_touch_clipboard(self):
        clipboard = FakeClipboard()
        self.tool._pyperclip = clipboard

        for text in ("Hello", "x" * 100):
            with self.subTest(length=len(text)):
                self.backend.write.reset_mock()
                data = self.assert_ok(self.tool.type_text(text), "type_text")
                self.assertEqual(data["method"], "pyautogui")
                self.assertIsNone(data["clipboard_restored"])
                self.backend.write.assert_called_once_with(
                    text,
                    interval=TYPE_INTERVAL.default,
                )
        clipboard.paste.assert_not_called()
        clipboard.copy.assert_not_called()

    def test_type_text_result_never_echoes_secret(self):
        secret = "SECRET_TOKEN_123"
        result = self.tool.type_text(secret)
        self.assert_ok(result, "type_text")
        self.assertNotIn(secret, json.dumps(result))

        self.backend.write.side_effect = RuntimeError(secret)
        result = self.tool.type_text(secret)
        self.assert_error(result, "type_text", "DESKTOP_OPERATION_FAILED")
        self.assertNotIn(secret, json.dumps(result))

    def test_unicode_force_direct_uses_lazy_pynput(self):
        keyboard = MagicMock()
        module = SimpleNamespace(Controller=MagicMock(return_value=keyboard))
        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            return_value=module,
        ) as loader:
            result = tool.type_text("Xin chào", force_direct=True)
        data = self.assert_ok(result, "type_text")
        self.assertEqual(data["method"], "pynput")
        keyboard.type.assert_called_once_with("Xin chào")
        loader.assert_called_once_with("pynput.keyboard")

    def test_unicode_force_direct_missing_or_broken_pynput_is_structured(self):
        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            result = tool.type_text("Tiếng Việt", force_direct=True)
        self.assert_error(result, "type_text", "DEPENDENCY_UNAVAILABLE")
        self.assertEqual(
            result["error"]["details"]["dependency"],
            "pynput.keyboard",
        )

        module = SimpleNamespace(Controller=MagicMock(side_effect=RuntimeError("init")))
        tool = DesktopAutomation()
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            return_value=module,
        ):
            result = tool.type_text("Tiếng Việt", force_direct=True)
        self.assert_error(result, "type_text", "DEPENDENCY_UNAVAILABLE")

    def test_unicode_force_direct_operation_error_is_secret_safe(self):
        secret = "Mật khẩu bí mật"
        keyboard = MagicMock()
        keyboard.type.side_effect = RuntimeError(secret)
        self.tool._unicode_keyboard = keyboard
        result = self.tool.type_text(secret, force_direct=True)
        self.assert_error(result, "type_text", "DESKTOP_OPERATION_FAILED")
        self.assertNotIn(secret, json.dumps(result))

    def test_unicode_clipboard_missing_dependency_is_lazy_and_structured(self):
        tool = DesktopAutomation()
        tool._pyautogui = self.backend
        with patch(
            "tools.v1.desktop_tool.importlib.import_module",
            side_effect=ImportError("missing clipboard"),
        ):
            result = tool.type_text("Tiếng Việt")
        self.assert_error(result, "type_text", "DEPENDENCY_UNAVAILABLE")
        self.assertEqual(result["error"]["details"]["dependency"], "pyperclip")

    def test_clipboard_restore_does_not_mask_keyboard_interrupt(self):
        clipboard = FakeClipboard("before")
        clipboard.copy.side_effect = [None, RuntimeError("restore failed")]
        self.tool._pyperclip = clipboard

        with patch.object(
            desktop_module,
            "CLIPBOARD_COPY_SETTLE_SECONDS",
            0,
        ), patch.object(
            desktop_module,
            "CLIPBOARD_PASTE_SETTLE_SECONDS",
            0,
        ), patch.object(
            self.tool,
            "_run_with_scoped_pyautogui",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.tool.type_text("Tiếng Việt")

        self.assertEqual(
            clipboard.copy.call_args_list,
            [unittest.mock.call("Tiếng Việt"), unittest.mock.call("before")],
        )

    def test_unicode_clipboard_success_restores_in_finally(self):
        clipboard = FakeClipboard("before")
        self.tool._pyperclip = clipboard
        with patch.object(desktop_module, "CLIPBOARD_COPY_SETTLE_SECONDS", 0), patch.object(
            desktop_module,
            "CLIPBOARD_PASTE_SETTLE_SECONDS",
            0,
        ):
            result = self.tool.type_text("Xin chào", restore_clipboard=True)
        data = self.assert_ok(result, "type_text")
        self.assertEqual(data["method"], "clipboard")
        self.assertTrue(data["clipboard_restored"])
        clipboard.paste.assert_called_once_with()
        self.assertEqual(
            clipboard.copy.call_args_list,
            [unittest.mock.call("Xin chào"), unittest.mock.call("before")],
        )
        self.backend.hotkey.assert_called_with(
            *desktop_module.DesktopAutomation._paste_keys()
        )
        self.assertNotIn("Xin chào", json.dumps(result))
        self.assertNotIn("before", json.dumps(result))

    def test_clipboard_snapshot_failure_causes_zero_mutation(self):
        clipboard = FakeClipboard()
        clipboard.paste.side_effect = RuntimeError("snapshot")
        self.tool._pyperclip = clipboard
        result = self.tool.type_text("Tiếng Việt")
        self.assert_error(result, "type_text", "DESKTOP_CLIPBOARD_ERROR")
        clipboard.copy.assert_not_called()
        self.backend.hotkey.assert_not_called()

        clipboard = FakeClipboard()
        clipboard.paste.return_value = None
        self.tool._pyperclip = clipboard
        result = self.tool.type_text("Tiếng Việt")
        self.assert_error(result, "type_text", "DESKTOP_CLIPBOARD_ERROR")
        clipboard.copy.assert_not_called()

    def test_clipboard_paste_failure_still_restores(self):
        clipboard = FakeClipboard("before")
        self.tool._pyperclip = clipboard
        self.backend.hotkey.side_effect = RuntimeError("paste failed")
        with patch.object(desktop_module, "CLIPBOARD_COPY_SETTLE_SECONDS", 0), patch.object(
            desktop_module,
            "CLIPBOARD_PASTE_SETTLE_SECONDS",
            0,
        ):
            result = self.tool.type_text("Tiếng Việt")
        self.assert_error(result, "type_text", "DESKTOP_OPERATION_FAILED")
        self.assertEqual(clipboard.copy.call_args_list[-1], unittest.mock.call("before"))

    def test_clipboard_restore_failure_has_precedence(self):
        clipboard = FakeClipboard("before")
        clipboard.copy.side_effect = [None, RuntimeError("restore failed")]
        self.tool._pyperclip = clipboard
        with patch.object(desktop_module, "CLIPBOARD_COPY_SETTLE_SECONDS", 0), patch.object(
            desktop_module,
            "CLIPBOARD_PASTE_SETTLE_SECONDS",
            0,
        ):
            result = self.tool.type_text("Tiếng Việt")
        self.assert_error(
            result,
            "type_text",
            "DESKTOP_CLIPBOARD_RESTORE_FAILED",
        )

        clipboard = FakeClipboard("before")
        clipboard.copy.side_effect = [None, RuntimeError("restore failed")]
        self.tool._pyperclip = clipboard
        self.backend.hotkey.side_effect = RuntimeError("paste")
        with patch.object(desktop_module, "CLIPBOARD_COPY_SETTLE_SECONDS", 0), patch.object(
            desktop_module,
            "CLIPBOARD_PASTE_SETTLE_SECONDS",
            0,
        ):
            result = self.tool.type_text("Tiếng Việt")
        self.assert_error(
            result,
            "type_text",
            "DESKTOP_CLIPBOARD_RESTORE_FAILED",
        )
        self.assertEqual(
            result["error"]["details"]["trigger"],
            "DESKTOP_OPERATION_FAILED",
        )

    def test_restore_clipboard_false_skips_snapshot(self):
        clipboard = FakeClipboard("before")
        self.tool._pyperclip = clipboard
        with patch.object(desktop_module, "CLIPBOARD_COPY_SETTLE_SECONDS", 0), patch.object(
            desktop_module,
            "CLIPBOARD_PASTE_SETTLE_SECONDS",
            0,
        ):
            data = self.assert_ok(
                self.tool.type_text("Tiếng Việt", restore_clipboard=False),
                "type_text",
            )
        self.assertFalse(data["clipboard_restored"])
        clipboard.paste.assert_not_called()
        clipboard.copy.assert_called_once_with("Tiếng Việt")

    def test_platform_paste_shortcut(self):
        with patch.object(desktop_module.sys, "platform", "darwin"):
            self.assertEqual(self.tool._paste_keys(), ("command", "v"))
        with patch.object(desktop_module.sys, "platform", "win32"):
            self.assertEqual(self.tool._paste_keys(), ("ctrl", "v"))

    def test_execute_canonical_and_aliases(self):
        routes = [
            ("info", {}, "get_screen_info"),
            ("click", {}, "mouse_click"),
            ("move", {"x": 1, "y": 2}, "mouse_move"),
            ("drag", {"x": 1, "y": 2}, "mouse_drag"),
            ("scroll", {"clicks": -1}, "mouse_scroll"),
            ("write", {"text": "abc"}, "type_text"),
            ("press", {"key": "enter"}, "press_key"),
            ("shortcut", {"keys": ["ctrl", "c"]}, "hotkey"),
        ]
        for alias, kwargs, canonical in routes:
            with self.subTest(alias=alias):
                result = self.tool.execute(alias, **kwargs)
                self.assert_ok(result, canonical)

    def test_execute_missing_params_and_invalid_action(self):
        for action, kwargs, canonical in (
            ("mouse_move", {}, "mouse_move"),
            ("mouse_drag", {}, "mouse_drag"),
            ("mouse_scroll", {}, "mouse_scroll"),
            ("type_text", {}, "type_text"),
            ("press_key", {}, "press_key"),
            ("hotkey", {}, "hotkey"),
        ):
            with self.subTest(action=action):
                result = self.tool.execute(action, **kwargs)
                self.assert_error(result, canonical, "INVALID_ARGUMENT")

        result = self.tool.execute("invalid_action")
        self.assert_error(result, "invalid_action", "INVALID_ARGUMENT")

    def test_module_wrappers_return_canonical_toolresult(self):
        fake = self.tool
        with patch.object(desktop_module, "_default_desktop_automation", fake):
            self.assert_ok(get_screen_info(), "get_screen_info")
            self.assert_ok(mouse_click(), "mouse_click")
            self.assert_ok(mouse_move(1, 2), "mouse_move")
            self.assert_ok(mouse_drag(1, 2), "mouse_drag")
            self.assert_ok(mouse_scroll(-1), "mouse_scroll")
            self.assert_ok(type_text("abc"), "type_text")
            self.assert_ok(press_key("esc"), "press_key")
            self.assert_ok(hotkey(["alt", "tab"]), "hotkey")
            self.assert_ok(run("info"), "get_screen_info")


if __name__ == "__main__":
    unittest.main(verbosity=2)
