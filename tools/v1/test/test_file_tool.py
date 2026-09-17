import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Import class và hàm run từ module chứa FileTool
from tools.v1.file_tool import FileTool, run, TOOL_METADATA


class TestFileTool(unittest.TestCase):

    def setUp(self):
        """Khởi tạo thư mục tạm và FileTool trước mỗi test case."""
        self.test_dir = tempfile.mkdtemp()
        self.tool = FileTool()

        # Tạo sẵn một số file thử nghiệm
        self.sample_file = os.path.join(self.test_dir, "sample.txt")
        self.sample_content = "Line 1: Hello World\nLine 2: Python Code\nLine 3: File Tool Test\nLine 4: Hello Again"
        with open(self.sample_file, "w", encoding="utf-8") as f:
            f.write(self.sample_content)

    def tearDown(self):
        """Dọn dẹp thư mục tạm sau khi test xong."""
        shutil.rmtree(self.test_dir)

    # ==================== 1. TEST READ ACTION ====================

    def test_read_full_file(self):
        """Kiểm tra đọc toàn bộ nội dung file."""
        result = self.tool.read(self.sample_file)
        self.assertEqual(result, self.sample_content)

    def test_read_specific_lines(self):
        """Kiểm tra đọc file theo dòng (start_line, num_lines)."""
        result = self.tool.read(self.sample_file, start_line=2, num_lines=2)
        expected = "Line 2: Python Code\nLine 3: File Tool Test\n"
        self.assertEqual(result, expected)

    def test_read_non_existent_file(self):
        """Kiểm tra đọc file không tồn tại."""
        fake_path = os.path.join(self.test_dir, "not_exist.txt")
        result = self.tool.read(fake_path)
        self.assertTrue(result.startswith("Lỗi: File"))

    def test_read_directory_error(self):
        """Kiểm tra đọc đường dẫn là thư mục."""
        result = self.tool.read(self.test_dir)
        self.assertTrue(result.startswith("Lỗi: Đường dẫn"))

    def test_read_invalid_line_params(self):
        """Kiểm tra tham số dòng không hợp lệ."""
        res1 = self.tool.read(self.sample_file, start_line=0)
        res2 = self.tool.read(self.sample_file, num_lines=-1)
        self.assertIn("start_line", res1)
        self.assertIn("num_lines", res2)

    # ==================== 2. TEST WRITE ACTION ====================

    def test_write_overwrite_mode(self):
        """Kiểm tra ghi đè file (mode='w')."""
        target_file = os.path.join(self.test_dir, "new_file.txt")
        result = self.tool.write(target_file, "New Content", mode="w")
        self.assertIn("Thành công", result)
        
        with open(target_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "New Content")

    def test_write_append_mode(self):
        """Kiểm tra ghi nối thêm (mode='a')."""
        result = self.tool.write(self.sample_file, "\nLine 5: Appended", mode="a")
        self.assertIn("Thành công", result)
        
        content = Path(self.sample_file).read_text(encoding="utf-8")
        self.assertTrue(content.endswith("Line 5: Appended"))

    def test_write_no_changes_skip(self):
        """Kiểm tra bỏ qua khi nội dung mới giống hệt nội dung cũ."""
        result = self.tool.write(self.sample_file, self.sample_content, mode="w")
        self.assertTrue(result.startswith("Bỏ qua"))

    # ==================== 3. TEST SEARCH ACTION ====================

    def test_search_literal_text(self):
        """Kiểm tra tìm kiếm chuỗi văn bản thông thường."""
        result = self.tool.search(self.sample_file, queries="Hello")
        self.assertIn("[Dòng 1]: Line 1: Hello World", result)
        self.assertIn("[Dòng 4]: Line 4: Hello Again", result)

    def test_search_regex_and_case_sensitivity(self):
        """Kiểm tra tìm kiếm bằng Regex và phân biệt hoa/thường."""
        # Case insensitive regex
        res_regex = self.tool.search(self.sample_file, queries=r"line \d", use_regex=True, case_sensitive=False)
        self.assertIn("Dòng 1", res_regex)

        # Case sensitive search
        res_case = self.tool.search(self.sample_file, queries="hello", case_sensitive=True)
        self.assertIn("Không tìm thấy kết quả", res_case)

    def test_search_max_results_limit(self):
        """Kiểm tra giới hạn số lượng kết quả tìm kiếm (max_results_per_file)."""
        result = self.tool.search(self.sample_file, queries="Line", max_results_per_file=2)
        matches = [line for line in result.splitlines() if "[Dòng" in line]
        self.assertEqual(len(matches), 2)

    # ==================== 4. TEST REPLACE ACTION ====================

    def test_replace_literal_text(self):
        """Kiểm tra thay thế văn bản tĩnh."""
        result = self.tool.replace(
            file_paths=self.sample_file,
            queries="Hello",
            replacements="Hi"
        )
        self.assertIn("[ĐÃ CẬP NHẬT]", result)
        
        updated_content = Path(self.sample_file).read_text(encoding="utf-8")
        self.assertIn("Line 1: Hi World", updated_content)
        self.assertIn("Line 4: Hi Again", updated_content)

    def test_replace_multiple_queries_and_replacements(self):
        """Kiểm tra thay thế nhiều chuỗi cùng lúc."""
        result = self.tool.replace(
            file_paths=self.sample_file,
            queries=["World", "Code"],
            replacements=["Earth", "Program"]
        )
        self.assertIn("[ĐÃ CẬP NHẬT]", result)
        
        updated_content = Path(self.sample_file).read_text(encoding="utf-8")
        self.assertIn("Line 1: Hello Earth", updated_content)
        self.assertIn("Line 2: Python Program", updated_content)

    def test_replace_mismatched_replacements_count(self):
        """Kiểm tra lỗi khi số lượng 'replacements' không khớp với 'queries'."""
        result = self.tool.replace(
            file_paths=self.sample_file,
            queries=["A", "B"],
            replacements=["X"]
        )
        self.assertTrue(result.startswith("Lỗi: Số lượng 'replacements'"))

    # ==================== 5. TEST SAFETY & CONFIRMATION ====================

    def test_dangerous_path_detection(self):
        """Kiểm tra khả năng nhận diện tệp nhạy cảm/nguy hiểm."""
        env_file = os.path.join(self.test_dir, ".env")
        ssh_key = os.path.join(self.test_dir, "id_rsa")
        
        self.assertTrue(self.tool._is_dangerous_path(env_file))
        self.assertTrue(self.tool._is_dangerous_path(ssh_key))
        self.assertFalse(self.tool._is_dangerous_path(self.sample_file))

    def test_confirmation_callback_rejection(self):
        """Kiểm tra việc hủy thao tác ghi khi callback từ chối (trả về False)."""
        def reject_callback(info):
            return False

        custom_tool = FileTool(confirm_callback=reject_callback)
        result = custom_tool.write(self.sample_file, "Unauthorized Edit", mode="w")
        self.assertIn("Đã hủy", result)
        
        # Đảm bảo nội dung file không bị thay đổi
        current_content = Path(self.sample_file).read_text(encoding="utf-8")
        self.assertEqual(current_content, self.sample_content)

    def test_confirmation_callback_payload(self):
        """Kiểm tra cấu trúc dữ liệu gửi tới confirm_callback."""
        received_info = {}

        def capture_callback(info):
            nonlocal received_info
            received_info = info
            return True

        custom_tool = FileTool(confirm_callback=capture_callback)
        custom_tool.write(self.sample_file, "New Test Content", mode="w")

        self.assertEqual(received_info["action"], "write")
        self.assertEqual(received_info["file_path"], self.sample_file)
        self.assertTrue(received_info["has_changes"])
        self.assertIn("diff", received_info)

    # ==================== 6. TEST EXECUTE & RUN ENTRYPOINT ====================

    def test_execute_alias_file_path(self):
        """Kiểm tra tham số alias 'file_path' truyền qua kwargs."""
        result = self.tool.execute(action="read", file_path=self.sample_file)
        self.assertEqual(result, self.sample_content)

    def test_global_run_function(self):
        """Kiểm tra hàm entrypoint run() chuẩn."""
        result = run(action="read", file_paths=self.sample_file)
        self.assertEqual(result, self.sample_content)


if __name__ == "__main__":
    unittest.main(verbosity=2)