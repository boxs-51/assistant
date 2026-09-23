import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import tools.v1.window_tool as window_module
from tools.v1.window_tool import (
    MAX_AMBIGUITY_CANDIDATES,
    MAX_APP_NAME_CHARS,
    MAX_TITLE_QUERY_CHARS,
    MAX_WINDOW_TITLE_CHARS,
    MAX_WINDOWS_ENUMERATED,
    WINDOW_RESULTS,
    WINDOW_TOOL_VERSION,
    WindowTool,
    close_window,
    find_windows,
    focus_window,
    list_windows,
    restore_window,
    run,
)


class FakeRe:
    CONTAINS = 1
    IGNORECASE = 2


class Frame:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeWindow:
    def __init__(
        self,
        *,
        title,
        handle,
        pid,
        app_name="App",
        left=10,
        top=20,
        width=300,
        height=200,
        right=None,
        bottom=None,
        client_frame=None,
        minimized=False,
        alive_sequence=None,
    ):
        self.title = title
        self._handle = handle
        self._pid = pid
        self._app_name = app_name
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.right = left + width if right is None else right
        self.bottom = top + height if bottom is None else bottom
        self._client_frame = client_frame
        self.isMinimized = minimized
        self._alive_sequence = list(alive_sequence or [False])
        self._alive_last = self._alive_sequence[-1] if self._alive_sequence else False

        self.activate = MagicMock(return_value=True)
        self.minimize = MagicMock(return_value=True)
        self.maximize = MagicMock(return_value=True)
        self.restore = MagicMock(return_value=True)
        self.close = MagicMock(return_value=True)

    def getHandle(self):
        return self._handle

    def getPID(self):
        if isinstance(self._pid, BaseException):
            raise self._pid
        return self._pid

    def getAppName(self):
        if isinstance(self._app_name, BaseException):
            raise self._app_name
        return self._app_name

    def getClientFrame(self):
        if isinstance(self._client_frame, BaseException):
            raise self._client_frame
        return self._client_frame

    @property
    def isAlive(self):
        if self._alive_sequence:
            value = self._alive_sequence.pop(0)
            self._alive_last = value
        else:
            value = self._alive_last
        if isinstance(value, BaseException):
            raise value
        return value


class FakeBackend:
    Re = FakeRe

    def __init__(self, windows=None):
        self.windows = list(windows or [])
        self.get_all_calls = 0
        self.get_title_calls = []

    def getAllWindows(self):
        self.get_all_calls += 1
        return list(self.windows)

    def getWindowsWithTitle(self, title_query, *, condition, flags):
        self.get_title_calls.append((title_query, condition, flags))
        needle = title_query.casefold()
        return [
            window
            for window in self.windows
            if needle in str(window.title).casefold()
        ]


class TestWindowToolV2(unittest.TestCase):
    def make_tool(self, windows=None):
        tool = WindowTool()
        tool._backend = FakeBackend(windows)
        return tool

    def assert_ok(self, result, action):
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "window_tool")
        self.assertEqual(result["action"], action)
        self.assertEqual(result["meta"]["version"], WINDOW_TOOL_VERSION)
        self.assertIsNone(result["error"])
        json.dumps(result)
        return result["data"]

    def assert_error(self, result, action, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["tool"], "window_tool")
        self.assertEqual(result["action"], action)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsNone(result["data"])
        json.dumps(result)

    def test_metadata_matches_frozen_contract(self):
        props = window_module.TOOL_METADATA["parameters"]["properties"]
        actions = props["action"]["enum"]
        self.assertIn("restore", actions)
        self.assertEqual(props["title_query"]["maxLength"], MAX_TITLE_QUERY_CHARS)
        self.assertEqual(props["max_results"]["maximum"], WINDOW_RESULTS.maximum)
        self.assertEqual(props["window_handle"]["minimum"], 1)
        self.assertEqual(props["pid"]["minimum"], 1)

    def test_module_import_does_not_import_pywinctl(self):
        script = (
            "import builtins, sys\n"
            "real = builtins.__import__\n"
            "def guard(name, *a, **k):\n"
            "    if name == 'pywinctl' or name.startswith('pywinctl.'):\n"
            "        raise AssertionError('eager pywinctl import')\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = guard\n"
            "import tools.v1.window_tool\n"
            "assert 'pywinctl' not in sys.modules\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_lazy_dependency_failure_and_control_flow(self):
        tool = WindowTool()
        with patch(
            "tools.v1.window_tool.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            result = tool.list_windows()
        self.assert_error(result, "list", "DEPENDENCY_UNAVAILABLE")
        self.assertEqual(
            result["error"]["details"]["exception_type"],
            "ImportError",
        )

        tool = WindowTool()
        with patch(
            "tools.v1.window_tool.importlib.import_module",
            side_effect=RuntimeError("platform init"),
        ):
            result = tool.list_windows()
        self.assert_error(result, "list", "DEPENDENCY_UNAVAILABLE")

        tool = WindowTool()
        with patch(
            "tools.v1.window_tool.importlib.import_module",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                tool.list_windows()

    def test_lazy_dependency_success_is_cached_per_instance(self):
        backend = FakeBackend([])
        tool = WindowTool()
        with patch(
            "tools.v1.window_tool.importlib.import_module",
            return_value=backend,
        ) as loader:
            self.assert_ok(tool.list_windows(), "list")
            self.assert_ok(tool.list_windows(), "list")
        loader.assert_called_once_with("pywinctl")

    def test_selector_and_limit_validation(self):
        tool = self.make_tool([])
        for value in (0, -1, True, (1 << 64)):
            with self.subTest(window_handle=value):
                result = tool.focus(window_handle=value)
                self.assert_error(result, "focus", "INVALID_ARGUMENT")

        for value in (0, True, (1 << 31)):
            with self.subTest(pid=value):
                result = tool.focus(pid=value)
                self.assert_error(result, "focus", "INVALID_ARGUMENT")

        for value in (0, -1, True, WINDOW_RESULTS.maximum + 1):
            with self.subTest(max_results=value):
                result = tool.list_windows(max_results=value)
                self.assert_error(result, "list", "INVALID_ARGUMENT")

        result = tool.find_windows("")
        self.assert_error(result, "find", "INVALID_ARGUMENT")

        result = tool.find_windows("x" * (MAX_TITLE_QUERY_CHARS + 1))
        self.assert_error(result, "find", "INVALID_ARGUMENT")

        result = tool.focus()
        self.assert_error(result, "focus", "INVALID_ARGUMENT")

    def test_list_structured_deterministic_and_exact_truncation(self):
        windows = [
            FakeWindow(title="Zulu", handle=30, pid=3),
            FakeWindow(title="alpha", handle=20, pid=2),
            FakeWindow(title="Alpha", handle=10, pid=1),
        ]
        tool = self.make_tool(windows)

        result = tool.list_windows(max_results=3)
        data = self.assert_ok(result, "list")
        self.assertEqual(data["total_count"], 3)
        self.assertEqual(data["returned_count"], 3)
        self.assertFalse(result["meta"]["truncated"])
        self.assertEqual(
            [item["selector"]["window_handle"] for item in data["windows"]],
            [10, 20, 30],
        )

        result = tool.list_windows(max_results=2)
        data = self.assert_ok(result, "list")
        self.assertEqual(data["returned_count"], 2)
        self.assertTrue(result["meta"]["truncated"])

    def test_empty_list_and_find_are_success(self):
        tool = self.make_tool([])
        data = self.assert_ok(tool.list_windows(), "list")
        self.assertEqual(data["windows"], [])

        data = self.assert_ok(tool.find_windows("missing"), "find")
        self.assertEqual(data["windows"], [])
        self.assertEqual(data["total_count"], 0)

    def test_find_uses_case_insensitive_contains_backend_contract(self):
        first = FakeWindow(title="Terminal - Bash", handle=1, pid=11)
        second = FakeWindow(title="Editor", handle=2, pid=22)
        tool = self.make_tool([first, second])

        data = self.assert_ok(tool.find_windows("terminal"), "find")
        self.assertEqual(data["returned_count"], 1)
        self.assertEqual(data["windows"][0]["selector"]["window_handle"], 1)
        self.assertEqual(
            tool._backend.get_title_calls,
            [("terminal", FakeRe.CONTAINS, FakeRe.IGNORECASE)],
        )

    def test_descriptor_pid_optional_and_display_fields_bounded(self):
        long_title = "T" * (MAX_WINDOW_TITLE_CHARS + 50)
        long_app = "A" * (MAX_APP_NAME_CHARS + 50)
        window = FakeWindow(
            title=long_title,
            handle=99,
            pid=RuntimeError("pid unavailable"),
            app_name=long_app,
        )
        tool = self.make_tool([window])
        data = self.assert_ok(tool.list_windows(), "list")
        descriptor = data["windows"][0]
        self.assertIsNone(descriptor["selector"]["pid"])
        self.assertEqual(len(descriptor["title"]), MAX_WINDOW_TITLE_CHARS)
        self.assertTrue(descriptor["title_truncated"])
        self.assertEqual(len(descriptor["app_name"]), MAX_APP_NAME_CHARS)
        self.assertTrue(descriptor["app_name_truncated"])

        window = FakeWindow(
            title="App",
            handle=100,
            pid=10,
            app_name=RuntimeError("optional"),
        )
        data = self.assert_ok(self.make_tool([window]).list_windows(), "list")
        self.assertIsNone(data["windows"][0]["app_name"])

    def test_enumeration_limit_and_backend_failure(self):
        tool = self.make_tool(
            [
                FakeWindow(title=f"W{i}", handle=i + 1, pid=i + 1)
                for i in range(MAX_WINDOWS_ENUMERATED + 1)
            ]
        )
        result = tool.list_windows()
        self.assert_error(result, "list", "WINDOW_ENUMERATION_LIMIT")

        backend = FakeBackend([])
        backend.getAllWindows = MagicMock(side_effect=RuntimeError("backend"))
        tool = WindowTool()
        tool._backend = backend
        result = tool.list_windows()
        self.assert_error(result, "list", "WINDOW_ENUMERATION_FAILED")

    def test_duplicate_title_mutation_is_rejected_without_side_effect(self):
        first = FakeWindow(title="Untitled", handle=1, pid=10)
        second = FakeWindow(title="Untitled", handle=2, pid=20)
        tool = self.make_tool([first, second])

        result = tool.close(title_query="Untitled")
        self.assert_error(result, "close", "AMBIGUOUS_TARGET")
        first.close.assert_not_called()
        second.close.assert_not_called()
        self.assertEqual(result["error"]["details"]["candidate_count"], 2)

    def test_ambiguity_details_are_bounded(self):
        windows = [
            FakeWindow(title="Same", handle=i + 1, pid=i + 1)
            for i in range(MAX_AMBIGUITY_CANDIDATES + 5)
        ]
        result = self.make_tool(windows).focus(title_query="Same")
        self.assert_error(result, "focus", "AMBIGUOUS_TARGET")
        self.assertEqual(
            len(result["error"]["details"]["candidates"]),
            MAX_AMBIGUITY_CANDIDATES,
        )

    def test_handle_and_pid_targeting(self):
        first = FakeWindow(title="Editor", handle=101, pid=10)
        second = FakeWindow(title="Editor", handle=202, pid=20)
        tool = self.make_tool([first, second])

        data = self.assert_ok(tool.minimize(window_handle=202), "minimize")
        self.assertEqual(data["window"]["selector"]["window_handle"], 202)
        first.minimize.assert_not_called()
        second.minimize.assert_called_once_with(wait=True)

        result = tool.maximize(window_handle=202, pid=10)
        self.assert_error(result, "maximize", "NOT_FOUND")
        second.maximize.assert_not_called()

        data = self.assert_ok(tool.restore(pid=10), "restore")
        self.assertEqual(data["window"]["selector"]["window_handle"], 101)

    def test_pid_only_multiple_windows_is_ambiguous(self):
        first = FakeWindow(title="A", handle=1, pid=99)
        second = FakeWindow(title="B", handle=2, pid=99)
        tool = self.make_tool([first, second])
        result = tool.focus(pid=99)
        self.assert_error(result, "focus", "AMBIGUOUS_TARGET")
        first.activate.assert_not_called()
        second.activate.assert_not_called()

    def test_handle_and_title_narrowing(self):
        first = FakeWindow(title="Alpha Editor", handle=1, pid=10)
        second = FakeWindow(title="Beta Editor", handle=2, pid=20)
        tool = self.make_tool([first, second])

        data = self.assert_ok(
            tool.restore(title_query="beta", window_handle=2),
            "restore",
        )
        self.assertEqual(data["window"]["selector"]["window_handle"], 2)

        result = tool.restore(title_query="alpha", window_handle=2)
        self.assert_error(result, "restore", "NOT_FOUND")

    def test_geometry_normalization_and_client_frame(self):
        window = FakeWindow(
            title="Calc",
            handle=1,
            pid=10,
            left=100,
            top=100,
            width=400,
            height=500,
            right=999,
            bottom=999,
            client_frame=Frame(105, 130, 495, 590),
        )
        data = self.assert_ok(
            self.make_tool([window]).get_geometry(window_handle=1),
            "get_geometry",
        )
        self.assertEqual(
            data["overall"],
            {
                "left": 100,
                "top": 100,
                "width": 400,
                "height": 500,
                "right": 500,
                "bottom": 600,
            },
        )
        self.assertEqual(data["client_area"]["width"], 390)
        self.assertEqual(data["client_area"]["height"], 460)
        self.assertEqual(data["frame_elements"]["titlebar_height"], 30)
        self.assertEqual(data["frame_elements"]["border_left"], 5)

    def test_geometry_client_failure_is_warning_success(self):
        window = FakeWindow(
            title="Calc",
            handle=1,
            pid=10,
            client_frame=RuntimeError("optional client frame"),
        )
        result = self.make_tool([window]).get_geometry(window_handle=1)
        data = self.assert_ok(result, "get_geometry")
        self.assertIsNone(data["client_area"])
        self.assertIsNone(data["frame_elements"])
        self.assertEqual(
            result["meta"]["warnings"],
            ["client geometry unavailable"],
        )

    def test_geometry_required_property_failure_is_structured(self):
        window = FakeWindow(title="Calc", handle=1, pid=10)
        type(window).left = PropertyMock(side_effect=RuntimeError("left"))
        try:
            result = self.make_tool([window]).get_geometry(window_handle=1)
            self.assert_error(result, "get_geometry", "WINDOW_PROPERTY_FAILED")
        finally:
            delattr(type(window), "left")

    def test_focus_restores_minimized_before_activate(self):
        window = FakeWindow(
            title="Editor",
            handle=1,
            pid=10,
            minimized=True,
        )
        events = []
        window.restore.side_effect = lambda **kwargs: (
            events.append(("restore", kwargs)),
            True,
        )[1]
        window.activate.side_effect = lambda **kwargs: (
            events.append(("activate", kwargs)),
            True,
        )[1]

        data = self.assert_ok(
            self.make_tool([window]).focus(window_handle=1),
            "focus",
        )
        self.assertTrue(data["confirmed"])
        self.assertEqual(
            events,
            [
                ("restore", {"wait": True}),
                ("activate", {"wait": True}),
            ],
        )

    def test_focus_aborts_when_restore_not_confirmed(self):
        window = FakeWindow(
            title="Editor",
            handle=1,
            pid=10,
            minimized=True,
        )
        window.restore.return_value = False
        result = self.make_tool([window]).focus(window_handle=1)
        self.assert_error(
            result,
            "focus",
            "WINDOW_OPERATION_NOT_CONFIRMED",
        )
        window.activate.assert_not_called()

    def test_focus_property_and_backend_failures(self):
        window = FakeWindow(title="Editor", handle=1, pid=10)
        type(window).isMinimized = PropertyMock(side_effect=RuntimeError("state"))
        try:
            result = self.make_tool([window]).focus(window_handle=1)
            self.assert_error(result, "focus", "WINDOW_PROPERTY_FAILED")
        finally:
            delattr(type(window), "isMinimized")

        window = FakeWindow(title="Editor", handle=1, pid=10)
        window.activate.side_effect = RuntimeError("activate")
        result = self.make_tool([window]).focus(window_handle=1)
        self.assert_error(result, "focus", "WINDOW_OPERATION_FAILED")

    def test_confirmed_operations_and_false_returns(self):
        for action in ("minimize", "maximize", "restore"):
            with self.subTest(action=action):
                window = FakeWindow(title="App", handle=1, pid=10)
                tool = self.make_tool([window])
                data = self.assert_ok(
                    getattr(tool, action)(window_handle=1),
                    action,
                )
                self.assertTrue(data["confirmed"])
                getattr(window, action).assert_called_once_with(wait=True)

                window = FakeWindow(title="App", handle=1, pid=10)
                getattr(window, action).return_value = False
                result = getattr(self.make_tool([window]), action)(
                    window_handle=1
                )
                self.assert_error(
                    result,
                    action,
                    "WINDOW_OPERATION_NOT_CONFIRMED",
                )

    def test_confirmed_operation_backend_exception(self):
        window = FakeWindow(title="App", handle=1, pid=10)
        window.maximize.side_effect = RuntimeError("backend")
        result = self.make_tool([window]).maximize(window_handle=1)
        self.assert_error(result, "maximize", "WINDOW_OPERATION_FAILED")

    def test_close_success_is_verified_by_liveness_not_return_value(self):
        window = FakeWindow(
            title="App",
            handle=1,
            pid=10,
            alive_sequence=[True, False],
        )
        window.close.return_value = False
        with patch.object(window_module, "CLOSE_VERIFY_POLL_SECONDS", 0):
            data = self.assert_ok(
                self.make_tool([window]).close(window_handle=1),
                "close",
            )
        self.assertTrue(data["closed"])
        window.close.assert_called_once_with()

    def test_close_unconfirmed_is_failure(self):
        window = FakeWindow(
            title="App",
            handle=1,
            pid=10,
            alive_sequence=[True],
        )
        with patch.object(
            window_module,
            "CLOSE_VERIFY_TIMEOUT_SECONDS",
            0.0,
        ):
            result = self.make_tool([window]).close(window_handle=1)
        self.assert_error(result, "close", "WINDOW_CLOSE_NOT_CONFIRMED")

    def test_close_backend_and_liveness_errors(self):
        window = FakeWindow(title="App", handle=1, pid=10)
        window.close.side_effect = RuntimeError("close")
        result = self.make_tool([window]).close(window_handle=1)
        self.assert_error(result, "close", "WINDOW_OPERATION_FAILED")

        window = FakeWindow(
            title="App",
            handle=1,
            pid=10,
            alive_sequence=[RuntimeError("alive")],
        )
        result = self.make_tool([window]).close(window_handle=1)
        self.assert_error(result, "close", "WINDOW_PROPERTY_FAILED")

    def test_execute_aliases_and_selector_aliases(self):
        window = FakeWindow(title="Sublime Text", handle=55, pid=77)
        tool = self.make_tool([window])

        data = self.assert_ok(
            tool.execute(action="activate", query="Sublime"),
            "focus",
        )
        self.assertEqual(data["window"]["selector"]["window_handle"], 55)

        data = self.assert_ok(
            tool.execute(action="restore_window", handle=55),
            "restore",
        )
        self.assertTrue(data["confirmed"])

        data = self.assert_ok(
            tool.execute(action="geometry", handle=55),
            "get_geometry",
        )
        self.assertEqual(data["window"]["selector"]["window_handle"], 55)

    def test_execute_invalid_action_is_structured(self):
        result = self.make_tool([]).execute(action="invalid_act")
        self.assert_error(result, "invalid_act", "INVALID_ARGUMENT")

    def test_module_wrappers_return_toolresult(self):
        fake_tool = self.make_tool(
            [FakeWindow(title="App", handle=1, pid=10)]
        )
        with patch.object(window_module, "_default_window_tool", fake_tool):
            self.assert_ok(list_windows(), "list")
            self.assert_ok(find_windows("App"), "find")
            self.assert_ok(focus_window(window_handle=1), "focus")
            self.assert_ok(restore_window(window_handle=1), "restore")
            self.assert_ok(
                close_window(window_handle=1),
                "close",
            )
            self.assert_ok(run("list"), "list")


if __name__ == "__main__":
    unittest.main(verbosity=2)
