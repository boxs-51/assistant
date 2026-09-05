import tempfile
import unittest
from pathlib import Path
from tools.v1.glob_search import glob_search


class TestGlobSearch(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        (self.root / "main.py").touch()
        (self.root / "config.json").touch()

        sub = self.root / "src"
        sub.mkdir()
        (sub / "helper.py").touch()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_python_files_recursive(self):
        results = glob_search("*.py", root_dir=str(self.root), recursive=True)
        self.assertEqual(len(results), 2)

    def test_find_json_files(self):
        results = glob_search("*.json", root_dir=str(self.root), recursive=False)
        self.assertEqual(len(results), 1)

    def test_invalid_directory(self):
        res = glob_search("*.py", root_dir=str(self.root / "non_existing"))
        self.assertTrue(isinstance(res, str) and res.startswith("Lỗi:"))


if __name__ == "__main__":
    unittest.main()