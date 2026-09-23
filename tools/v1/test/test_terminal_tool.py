import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import tools.v1.terminal_tool as terminal_module
from tools.v1._shared.errors import ToolLimitConfigError
from tools.v1.terminal_tool import (
    DEFAULT_DANGER_PATTERNS,
    MAX_COMMAND_CHARS,
    MAX_CWD_CHARS,
    MAX_ENCODING_CHARS,
    RUN_TIMEOUT,
    TERMINAL_TOOL_VERSION,
    TerminalTool,
    _CleanupReport,
    _OutputCaptureState,
    _record_output_chunk,
    run,
)


def python_shell_command(code: str) -> str:
    args = [sys.executable, "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


class TestTerminalToolV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tool = TerminalTool(default_timeout=10)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_ok(self, result, action):
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "terminal_tool")
        self.assertEqual(result["action"], action)
        self.assertEqual(result["meta"]["version"], TERMINAL_TOOL_VERSION)
        self.assertFalse(result["meta"]["truncated"])
        self.assertIsNone(result["error"])
        json.dumps(result)
        return result["data"]

    def assert_error(self, result, action, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["tool"], "terminal_tool")
        self.assertEqual(result["action"], action)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsNone(result["data"])
        json.dumps(result)

    def test_metadata_matches_frozen_bounds(self):
        params = terminal_module.TOOL_METADATA["parameters"]["properties"]
        self.assertEqual(params["command"]["maxLength"], MAX_COMMAND_CHARS)
        self.assertEqual(params["cwd"]["maxLength"], MAX_CWD_CHARS)
        self.assertEqual(params["encoding"]["maxLength"], MAX_ENCODING_CHARS)
        self.assertEqual(params["timeout"]["minimum"], RUN_TIMEOUT.minimum)
        self.assertEqual(params["timeout"]["maximum"], RUN_TIMEOUT.maximum)
        self.assertEqual(
            terminal_module.TOOL_METADATA["danger_patterns"],
            list(DEFAULT_DANGER_PATTERNS),
        )

    def test_constructor_default_timeout_is_hard_bounded(self):
        self.assertEqual(TerminalTool(default_timeout=1).default_timeout, 1)
        self.assertEqual(
            TerminalTool(default_timeout=RUN_TIMEOUT.maximum).default_timeout,
            RUN_TIMEOUT.maximum,
        )
        for value in (0, -1, True, RUN_TIMEOUT.maximum + 1):
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    TerminalTool(default_timeout=value)

    def test_confirm_callback_is_deprecated_and_never_invoked(self):
        callback = MagicMock()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tool = TerminalTool(confirm_callback=callback)
        self.assertTrue(any(item.category is DeprecationWarning for item in caught))

        command = python_shell_command("print('ok')")
        data = self.assert_ok(tool.run(command, cwd=str(self.root)), "run")
        self.assertEqual(data["exit_code"], 0)
        callback.assert_not_called()

    def test_validate_cwd(self):
        self.assertEqual(
            self.tool._validate_cwd(str(self.root)),
            str(self.root.resolve()),
        )
        self.assertEqual(
            self.tool._validate_cwd(None),
            str(Path.cwd().resolve()),
        )

        missing = self.root / "missing"
        result = self.tool.run(
            python_shell_command("print('x')"),
            cwd=str(missing),
        )
        self.assert_error(result, "run", "TERMINAL_CWD_NOT_FOUND")

        file_path = self.root / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        result = self.tool.run(
            python_shell_command("print('x')"),
            cwd=str(file_path),
        )
        self.assert_error(result, "run", "TERMINAL_CWD_NOT_DIRECTORY")

        with patch.object(Path, "exists", side_effect=OSError("cwd I/O failure")):
            result = self.tool.run(
                python_shell_command("print('x')"),
                cwd=str(self.root),
            )
        self.assert_error(result, "run", "TERMINAL_IO_ERROR")

    def test_command_validation(self):
        for command in ("", "   ", "abc\x00def", "x" * (MAX_COMMAND_CHARS + 1)):
            with self.subTest(command=repr(command[:20])):
                result = self.tool.run(command)
                self.assert_error(result, "run", "INVALID_ARGUMENT")

    def test_timeout_validation_never_silently_falls_back(self):
        command = python_shell_command("print('never')")
        with patch("tools.v1.terminal_tool.subprocess.Popen") as popen:
            for timeout in (0, -1, True, RUN_TIMEOUT.maximum + 1):
                with self.subTest(timeout=timeout):
                    result = self.tool.run(command, timeout=timeout)
                    self.assert_error(result, "run", "INVALID_ARGUMENT")
            popen.assert_not_called()

    def test_encoding_validation(self):
        command = python_shell_command("print('never')")
        with patch("tools.v1.terminal_tool.subprocess.Popen") as popen:
            result = self.tool.run(command, encoding="not-a-real-codec")
            self.assert_error(result, "run", "TERMINAL_ENCODING_INVALID")
            result = self.tool.run(command, encoding="x" * (MAX_ENCODING_CHARS + 1))
            self.assert_error(result, "run", "TERMINAL_ENCODING_INVALID")
            popen.assert_not_called()

    def test_run_exit_zero_and_exact_whitespace(self):
        command = python_shell_command(
            "import sys; sys.stdout.write('  out\\n\\n'); "
            "sys.stderr.write(' err \\n')"
        )
        data = self.assert_ok(
            self.tool.run(command, cwd=str(self.root), encoding="utf-8"),
            "run",
        )
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["stdout"], "  out\n\n")
        self.assertEqual(data["stderr"], " err \n")
        self.assertEqual(data["stdout_bytes"], len(b"  out\n\n"))
        self.assertEqual(data["stderr_bytes"], len(b" err \n"))
        self.assertEqual(data["encoding"], "utf-8")
        self.assertEqual(data["cwd"], str(self.root.resolve()))
        self.assertIsInstance(data["duration_ms"], int)
        self.assertGreaterEqual(data["duration_ms"], 0)

    def test_nonzero_exit_is_successful_execution_data(self):
        command = python_shell_command(
            "import sys; sys.stderr.write('bad\\n'); sys.exit(7)"
        )
        result = self.tool.run(command, encoding="utf-8")
        data = self.assert_ok(result, "run")
        self.assertEqual(data["exit_code"], 7)
        self.assertEqual(data["stderr"], "bad\n")

    def test_explicit_output_encoding(self):
        command = python_shell_command(
            "import sys; sys.stdout.buffer.write(bytes([0xe9]))"
        )
        data = self.assert_ok(
            self.tool.run(command, encoding="latin-1"),
            "run",
        )
        self.assertEqual(data["stdout"], "é")

    def test_result_and_errors_never_echo_command(self):
        secret_command = python_shell_command(
            "import time; SECRET_TOKEN_123 = 'x'; time.sleep(5)"
        )
        result = self.tool.run(secret_command, timeout=1)
        self.assert_error(result, "run", "TERMINAL_TIMEOUT")
        self.assertNotIn("SECRET_TOKEN_123", json.dumps(result))

        fake = MagicMock()
        fake.pid = 1234
        with patch("tools.v1.terminal_tool.subprocess.Popen", return_value=fake):
            result = self.tool.launch("echo SECRET_TOKEN_456", cwd=str(self.root))
        self.assert_ok(result, "launch")
        self.assertNotIn("SECRET_TOKEN_456", json.dumps(result))

    def test_output_state_exact_and_over_limit(self):
        state = _OutputCaptureState()
        with patch.object(terminal_module, "MAX_STDOUT_BYTES", 4), patch.object(
            terminal_module, "MAX_STDERR_BYTES", 4
        ), patch.object(terminal_module, "MAX_TOTAL_OUTPUT_BYTES", 8):
            _record_output_chunk(state, "stdout", b"1234")
            self.assertIsNone(state.overflow_stream)
            self.assertEqual(bytes(state.stdout_buffer), b"1234")

            _record_output_chunk(state, "stdout", b"5")
            self.assertEqual(state.overflow_stream, "stdout")
            self.assertEqual(bytes(state.stdout_buffer), b"1234")
            self.assertEqual(state.stdout_bytes_observed, 5)

        state = _OutputCaptureState()
        with patch.object(terminal_module, "MAX_STDOUT_BYTES", 10), patch.object(
            terminal_module, "MAX_STDERR_BYTES", 10
        ), patch.object(terminal_module, "MAX_TOTAL_OUTPUT_BYTES", 5):
            _record_output_chunk(state, "stdout", b"123")
            _record_output_chunk(state, "stderr", b"456")
            self.assertEqual(state.overflow_stream, "aggregate")
            self.assertLessEqual(
                len(state.stdout_buffer) + len(state.stderr_buffer),
                5,
            )

    def test_output_limit_returns_failure_without_partial_output(self):
        command = python_shell_command(
            "import sys, time; "
            "sys.stdout.buffer.write(b'x' * 4096); "
            "sys.stdout.buffer.flush(); time.sleep(5)"
        )
        with patch.object(terminal_module, "MAX_STDOUT_BYTES", 1024), patch.object(
            terminal_module, "MAX_TOTAL_OUTPUT_BYTES", 2048
        ):
            result = self.tool.run(command, timeout=5, encoding="utf-8")
        self.assert_error(result, "run", "TERMINAL_OUTPUT_LIMIT")
        self.assertNotIn("stdout", result["error"]["details"])
        self.assertEqual(result["error"]["details"]["max_stdout_bytes"], 1024)

    def test_timeout_is_structured(self):
        command = python_shell_command("import time; time.sleep(5)")
        result = self.tool.run(command, timeout=1)
        self.assert_error(result, "run", "TERMINAL_TIMEOUT")
        self.assertEqual(result["error"]["details"]["timeout_seconds"], 1)

    def test_launch_success_returns_pid_and_detached_stdio(self):
        fake_process = MagicMock()
        fake_process.pid = 4321

        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            return_value=fake_process,
        ) as popen:
            result = self.tool.launch("echo hello", cwd=str(self.root))

        data = self.assert_ok(result, "launch")
        self.assertEqual(data["pid"], 4321)
        self.assertTrue(data["started"])
        self.assertEqual(data["cwd"], str(self.root.resolve()))

        _, kwargs = popen.call_args
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["close_fds"])
        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_launch_start_failure_is_structured_and_redacted(self):
        error = OSError("echo VERY_SECRET failed")
        error.errno = 5
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            side_effect=error,
        ):
            result = self.tool.launch("echo VERY_SECRET", cwd=str(self.root))
        self.assert_error(result, "launch", "TERMINAL_START_FAILED")
        self.assertNotIn("VERY_SECRET", json.dumps(result))
        self.assertEqual(result["error"]["details"]["errno"], 5)

    def test_invalid_launch_pid_is_failure(self):
        fake_process = MagicMock()
        fake_process.pid = 0
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            return_value=fake_process,
        ):
            result = self.tool.launch("echo x", cwd=str(self.root))
        self.assert_error(result, "launch", "TERMINAL_START_FAILED")

    def test_execute_dispatch_and_standalone_entrypoint(self):
        command = python_shell_command("print('ok')")
        data = self.assert_ok(
            self.tool.execute("run", command, encoding="utf-8"),
            "run",
        )
        self.assertEqual(data["stdout"], "ok\n")

        result = self.tool.execute("invalid", command)
        self.assert_error(result, "invalid", "INVALID_ARGUMENT")

        result = run("run", command, encoding="utf-8")
        self.assert_ok(result, "run")

    def test_baseexception_triggers_cleanup_and_is_reraised(self):
        fake_process = MagicMock()
        fake_process.pid = 999999
        fake_process.stdout = io.BytesIO(b"")
        fake_process.stderr = io.BytesIO(b"")
        fake_process.poll.return_value = None

        cleanup = _CleanupReport(attempted=True, verified=True)
        with patch(
            "tools.v1.terminal_tool.subprocess.Popen",
            return_value=fake_process,
        ), patch(
            "tools.v1.terminal_tool._capture_identity",
            return_value=None,
        ), patch(
            "tools.v1.terminal_tool._observe_descendants",
            side_effect=KeyboardInterrupt(),
        ), patch(
            "tools.v1.terminal_tool._terminate_process_tree",
            return_value=cleanup,
        ) as terminate:
            with self.assertRaises(KeyboardInterrupt):
                self.tool._run_managed_process(
                    command="echo x",
                    timeout=1,
                    cwd=str(self.root),
                    encoding="utf-8",
                )
        terminate.assert_called_once()

    def test_cleanup_failure_payload_has_precedence_shape(self):
        failed_cleanup = _CleanupReport(
            attempted=True,
            survivor_pids=[999],
            verified=False,
        )
        error = terminal_module._cleanup_failure(
            trigger="timeout",
            report=failed_cleanup,
        )
        self.assertEqual(error.code, "TERMINAL_CLEANUP_FAILED")
        self.assertEqual(error.details["trigger"], "timeout")
        self.assertEqual(error.details["survivor_pids"], [999])


if __name__ == "__main__":
    unittest.main(verbosity=2)
