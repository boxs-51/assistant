from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.v1.file_writer import write_file


class TestFileWriter(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_new_file(self):
        """Tạo file mới hoàn toàn (không trigger confirm/diff)."""
        target = self.dir_path / "output.txt"
        res = write_file(str(target), "Nội dung mới")
        self.assertIn("Thành công", res)
        self.assertEqual(target.read_text(encoding="utf-8"), "Nội dung mới")

    def test_auto_create_nested_dirs(self):
        """Tự động tạo các thư mục con lồng nhau."""
        target = self.dir_path / "deep" / "path" / "file.txt"
        res = write_file(str(target), "Thư mục tự động tạo")
        self.assertIn("Thành công", res)
        self.assertTrue(target.exists())

    def test_append_mode(self):
        """Ghi nối tiếp ở chế độ mode='a'."""
        target = self.dir_path / "append.txt"
        write_file(str(target), "Dòng 1\n", mode="w")
        write_file(str(target), "Dòng 2", mode="a")
        self.assertEqual(target.read_text(encoding="utf-8"), "Dòng 1\nDòng 2")

    @patch("builtins.input", return_value="y")
    def test_overwrite_confirm_yes(self, mock_input):
        """Ghi đè file cũ với xác nhận 'y' từ người dùng."""
        target = self.dir_path / "confirm.txt"
        write_file(str(target), "Nội dung cũ", mode="w")

        res = write_file(
            str(target), "Nội dung mới đã sửa", mode="w", confirm_overwrite=True
        )
        self.assertIn("Thành công", res)
        self.assertEqual(
            target.read_text(encoding="utf-8"), "Nội dung mới đã sửa"
        )
        mock_input.assert_called_once()

    @patch("builtins.input", return_value="n")
    def test_overwrite_confirm_no(self, mock_input):
        """Từ chối ghi đè file ('n') -> Giữ nguyên nội dung cũ."""
        target = self.dir_path / "confirm.txt"
        write_file(str(target), "Nội dung nguyên bản", mode="w")

        res = write_file(
            str(target), "Nội dung bị từ chối", mode="w", confirm_overwrite=True
        )
        self.assertIn("Đã hủy", res)
        self.assertEqual(
            target.read_text(encoding="utf-8"), "Nội dung nguyên bản"
        )

    def test_overwrite_identical_content(self):
        """Nội dung ghi mới giống hệt nội dung cũ -> Bỏ qua không xin confirm."""
        target = self.dir_path / "same.txt"
        write_file(str(target), "Giống nhau", mode="w")

        res = write_file(
            str(target), "Giống nhau", mode="w", confirm_overwrite=True
        )
        self.assertIn("Bỏ qua", res)

    def test_overwrite_confirm_disabled(self):
        """Tắt confirm_overwrite=False -> Ghi đè trực tiếp không prompt."""
        target = self.dir_path / "no_prompt.txt"
        write_file(str(target), "Văn bản A", mode="w")

        res = write_file(
            str(target), "Văn bản B", mode="w", confirm_overwrite=False
        )
        self.assertIn("Thành công", res)
        self.assertEqual(target.read_text(encoding="utf-8"), "Văn bản B")


if __name__ == "__main__":
    unittest.main()