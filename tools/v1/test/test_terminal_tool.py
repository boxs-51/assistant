import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools.terminal_tool import TerminalTool, run, TOOL_METADATA


class TestTerminalTool(unittest.TestCase):

    def setUp(self):
        """Khởi tạo môi trường tạm và đối tượng TerminalTool trước mỗi test case."""
        self.test_dir = tempfile.mkdtemp()
        self.tool = TerminalTool(default_timeout=10)

    def tearDown(self):
        """Dọn dẹp thư mục tạm sau khi test xong."""
        shutil.rmtree(self.test_dir)

    # ==================== 1. TEST KIỂM TRA CWD ====================

    def test_validate_cwd_valid(self):
        """Kiểm tra đường dẫn cwd hợp lệ."""
        valid_path = self.tool._validate_cwd(self.test_dir)
        self.assertEqual(valid_path, os.path.realpath(self.test_dir))

    def test_validate_cwd_not_exist(self):
        """Kiểm tra báo lỗi khi cwd không tồn tại."""
        fake_path = os.path.join(self.test_dir, "non_existent_folder")
        result = self.tool._validate_cwd(fake_path)
        self.assertTrue(isinstance(result, str) and result.startswith("Lỗi:"))

    def test_validate_cwd_is_file(self):
        """Kiểm tra báo lỗi khi cwd là một file chứ không phải thư mục."""
        file_path = os.path.join(self.test_dir, "test.txt")
        with open(file_path, "w") as f:
            f.write("data")
        result = self.tool._validate_cwd(file_path)
        self.assertTrue(isinstance(result, str) and "không phải là thư mục" in result)

    # ==================== 2. TEST THỰC THI ĐỒNG BỘ (RUN) ====================

    @patch("subprocess.run")
    def test_run_success(self, mock_subproc_run):
        """Kiểm tra lệnh run thực thi thành công trả về STDOUT và Exit Code."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Hello World\n"
        mock_process.stderr = ""
        mock_subproc_run.return_value = mock_process

        res = self.tool.run(command="echo Hello World", cwd=self.test_dir)

        self.assertIn("Exit Code: 0", res)
        self.assertIn("--- STDOUT ---\nHello World", res)
        mock_subproc_run.assert_called_once_with(
            "echo Hello World",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.path.realpath(self.test_dir),
            errors="replace"
        )

    @patch("subprocess.run")
    def test_run_stderr_output(self, mock_subproc_run):
        """Kiểm tra lệnh run khi có STDERR và Exit Code khác 0."""
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.stdout = ""
        mock_process.stderr = "Command not found"
        mock_subproc_run.return_value = mock_process

        res = self.tool.run(command="invalid_cmd")
        self.assertIn("Exit Code: 1", res)
        self.assertIn("--- STDERR ---\nCommand not found", res)

    def test_run_empty_command(self):
        """Kiểm tra báo lỗi khi lệnh trống hoặc chỉ chứa khoảng trắng."""
        res_empty = self.tool.run(command="")
        res_spaces = self.tool.run(command="   ")
        self.assertTrue(res_empty.startswith("Lỗi:"))
        self.assertTrue(res_spaces.startswith("Lỗi:"))

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sleep 100", timeout=5))
    def test_run_timeout_expired(self, mock_subproc_run):
        """Kiểm tra bắt lỗi TimeoutExpired."""
        res = self.tool.run(command="sleep 100", timeout=5)
        self.assertIn("vượt quá thời gian chờ (5s)", res)

    @patch("subprocess.run", side_effect=Exception("Unexpected System Error"))
    def test_run_general_exception(self, mock_subproc_run):
        """Kiểm tra bắt các Exception không xác định khác."""
        res = self.tool.run(command="dir")
        self.assertIn("Lỗi khi thực thi lệnh: Unexpected System Error", res)

    # ==================== 3. TEST KHỞI CHẠY NGẦM (LAUNCH) ====================

    @patch("subprocess.Popen")
    def test_launch_success(self, mock_popen):
        """Kiểm tra chạy ngầm bằng Popen thành công."""
        res = self.tool.launch(command="notepad", cwd=self.test_dir)
        
        self.assertIn("Thành công: Đã khởi chạy 'notepad' ngầm.", res)
        mock_popen.assert_called_once_with(
            "notepad",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.realpath(self.test_dir)
        )

    def test_launch_empty_command(self):
        """Kiểm tra báo lỗi khi lệnh launch trống."""
        res = self.tool.launch(command="")
        self.assertTrue(res.startswith("Lỗi:"))

    @patch("subprocess.Popen", side_effect=Exception("Cannot spawn process"))
    def test_launch_exception(self, mock_popen):
        """Kiểm tra bắt ngoại lệ khi launch thất bại."""
        res = self.tool.launch(command="bad_app")
        self.assertIn("Lỗi khi khởi chạy ứng dụng: Cannot spawn process", res)

    # ==================== 4. TEST DISPATCHER & ENTRYPOINT ====================

    @patch.object(TerminalTool, "run", return_value="run_executed")
    @patch.object(TerminalTool, "launch", return_value="launch_executed")
    def test_execute_dispatcher(self, mock_launch, mock_run):
        """Kiểm tra hàm execute điều hướng chính xác giữa run và launch."""
        res_run = self.tool.execute(action="run", command="dir")
        self.assertEqual(res_run, "run_executed")

        res_launch = self.tool.execute(action="launch", command="calc")
        self.assertEqual(res_launch, "launch_executed")

        res_invalid = self.tool.execute(action="invalid_action", command="dir")
        self.assertTrue(res_invalid.startswith("Lỗi: Action 'invalid_action' không hợp lệ."))

    @patch("subprocess.run")
    def test_standalone_run_function(self, mock_subproc_run):
        """Kiểm tra hàm standalone entrypoint run()."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "OK"
        mock_process.stderr = ""
        mock_subproc_run.return_value = mock_process

        res = run(action="run", command="echo OK")
        self.assertIn("Exit Code: 0", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)