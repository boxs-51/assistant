import tempfile
import unittest
from pathlib import Path
from tools.v1.file_reader import read_file


class TestFileReader(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_existing_file(self):
        file = self.dir_path / "sample.txt"
        file.write_text("Dữ liệu thử nghiệm", encoding="utf-8")
        result = read_file(str(file))
        self.assertEqual(result, "Dữ liệu thử nghiệm")

    def test_read_non_existent_file(self):
        result = read_file(str(self.dir_path / "not_found.txt"))
        self.assertTrue(result.startswith("Lỗi: File"))

    def test_read_directory(self):
        result = read_file(str(self.dir_path))
        self.assertTrue(result.startswith("Lỗi: Đường dẫn"))


if __name__ == "__main__":
    unittest.main()