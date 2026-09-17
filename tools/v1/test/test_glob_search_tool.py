import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Import class và entrypoint chuẩn
from tools.v1.find_by_glob import GlobSearchTool, run, TOOL_METADATA


class TestGlobSearchTool(unittest.TestCase):

    def setUp(self):
        """Khởi tạo cây thư mục giả lập trước mỗi test case."""
        self.test_dir = tempfile.mkdtemp()
        self.tool = GlobSearchTool(default_max_results=10)

        # Cấu trúc thư mục thử nghiệm:
        # test_dir/
        # ├── file1.py
        # ├── file2.json
        # ├── README.md
        # ├── sub_a/
        # │   ├── sub_file1.py
        # │   └── sub_file2.txt
        # └── sub_b/
        #     └── deep/
        #         └── deep_file.py

        self.file1 = os.path.join(self.test_dir, "file1.py")
        self.file2 = os.path.join(self.test_dir, "file2.json")
        self.readme = os.path.join(self.test_dir, "README.md")
        
        self.sub_a = os.path.join(self.test_dir, "sub_a")
        self.sub_a_file1 = os.path.join(self.sub_a, "sub_file1.py")
        self.sub_a_file2 = os.path.join(self.sub_a, "sub_file2.txt")

        self.sub_b_deep = os.path.join(self.test_dir, "sub_b", "deep")
        self.deep_file = os.path.join(self.sub_b_deep, "deep_file.py")

        os.makedirs(self.sub_a, exist_ok=True)
        os.makedirs(self.sub_b_deep, exist_ok=True)

        for filepath in [self.file1, self.file2, self.readme, self.sub_a_file1, self.sub_a_file2, self.deep_file]:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("test content")

    def tearDown(self):
        """Dọn dẹp thư mục tạm."""
        shutil.rmtree(self.test_dir)

    # ==================== 1. TEST TÌM KIẾM CƠ BẢN & ĐỆ QUY ====================

    def test_find_recursive_default(self):
        """Kiểm tra tìm kiếm đệ quy tất cả file .py."""
        results = self.tool.find(pattern="*.py", root_dir=self.test_dir, recursive=True)
        self.assertIsInstance(results, list)
        
        # Loại bỏ cảnh báo nếu có để so sánh
        clean_results = [r for r in results if not r.startswith("...")]
        self.assertEqual(len(clean_results), 3)
        self.assertTrue(any("file1.py" in p for p in clean_results))
        self.assertTrue(any("sub_file1.py" in p for p in clean_results))
        self.assertTrue(any("deep_file.py" in p for p in clean_results))

    def test_find_non_recursive(self):
        """Kiểm tra tìm kiếm không đệ quy (chỉ tìm tại thư mục gốc)."""
        results = self.tool.find(pattern="*.py", root_dir=self.test_dir, recursive=False)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].endswith("file1.py"))

    def test_find_specific_subfolder_pattern(self):
        """Kiểm tra truyền pattern có tiền tố thư mục sẵn."""
        results = self.tool.find(pattern="**/*.txt", root_dir=self.test_dir, recursive=True)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].endswith("sub_file2.txt"))

    # ==================== 2. TEST GIỚI HẠN KẾT QUẢ (MAX RESULTS) ====================

    def test_max_results_limit_reached(self):
        """Kiểm tra khi số lượng file vượt quá giới hạn max_results."""
        results = self.tool.find(pattern="*", root_dir=self.test_dir, recursive=True, max_results=3)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 4)  # 3 kết quả + 1 chuỗi cảnh báo
        self.assertTrue(results[-1].startswith("... [CẢNH BÁO:"))

    def test_alphabetical_sorting(self):
        """Kiểm tra danh sách kết quả trả về được sắp xếp theo bảng chữ cái."""
        results = self.tool.find(pattern="*.*", root_dir=self.test_dir, recursive=False)
        paths_only = [p for p in results if not p.startswith("...")]
        self.assertEqual(paths_only, sorted(paths_only))

    # ==================== 3. TEST XỬ LÝ LỖI VÀ EDGE CASES ====================

    def test_empty_pattern_error(self):
        """Kiểm tra báo lỗi khi pattern trống hoặc chỉ chứa khoảng trắng."""
        res1 = self.tool.find(pattern="", root_dir=self.test_dir)
        res2 = self.tool.find(pattern="   ", root_dir=self.test_dir)
        self.assertTrue(res1.startswith("Lỗi:"))
        self.assertTrue(res2.startswith("Lỗi:"))

    def test_non_existent_root_dir(self):
        """Kiểm tra báo lỗi khi root_dir không tồn tại."""
        fake_dir = os.path.join(self.test_dir, "invalid_dir")
        result = self.tool.find(pattern="*.py", root_dir=fake_dir)
        self.assertTrue(result.startswith("Lỗi: Thư mục gốc"))

    def test_root_dir_is_file(self):
        """Kiểm tra báo lỗi khi root_dir truyền vào là một file thay vì thư mục."""
        result = self.tool.find(pattern="*.py", root_dir=self.file1)
        self.assertTrue(result.startswith("Lỗi:"))
        self.assertIn("không phải là thư mục", result)

    def test_no_matches_found(self):
        """Kiểm tra thông báo khi không tìm thấy kết quả phù hợp."""
        result = self.tool.find(pattern="*.cpp", root_dir=self.test_dir)
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Thông báo: Không tìm thấy"))

    # ==================== 4. TEST EXECUTE & RUN ENTRYPOINT ====================

    def test_execute_method(self):
        """Kiểm tra phương thức execute điều hướng tham số chuẩn."""
        results = self.tool.execute(pattern="README.md", root_dir=self.test_dir, unused_param="ignore")
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)

    def test_global_run_function(self):
        """Kiểm tra hàm run() tương thích với ToolExecutor."""
        results = run(pattern="*.json", root_dir=self.test_dir)
        self.assertIsInstance(results, list)
        self.assertTrue(results[0].endswith("file2.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)