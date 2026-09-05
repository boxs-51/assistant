import unittest
from unittest.mock import patch
from tools.v1.terminal_tool import launch_app, run_terminal_command


class TestTerminalTool(unittest.TestCase):

    # --- Test cho run_terminal_command ---
    def test_run_simple_command(self):
        res = run_terminal_command("echo Hello AI")
        self.assertIn("Exit Code: 0", res)
        self.assertIn("Hello AI", res)

    def test_run_empty_command(self):
        res = run_terminal_command("  ")
        self.assertIn("Lỗi: Lệnh terminal không được để trống.", res)

    def test_run_command_timeout(self):
        res = run_terminal_command(
            'python -c "import time; time.sleep(2)"', timeout=1
        )
        self.assertIn("vượt quá thời gian chờ", res)

    # --- Test cho launch_app ---
    def test_launch_app_empty(self):
        res = launch_app("")
        self.assertIn("Lỗi: Lệnh không được để trống.", res)

    @patch("tools.v1.terminal_tool.subprocess.Popen")
    def test_launch_app_success(self, mock_popen):
        res = launch_app("notepad")
        mock_popen.assert_called_once()
        self.assertIn("Thành công: Đã khởi chạy 'notepad' ngầm.", res)


if __name__ == "__main__":
    unittest.main()