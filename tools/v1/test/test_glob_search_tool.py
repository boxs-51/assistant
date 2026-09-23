import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.v1._shared.errors import ToolLimitConfigError
from tools.v1.find_by_glob import (
    GLOB_MAX_RESULTS_HARD,
    MAX_GLOB_PATTERN_CHARS,
    GlobSearchTool,
    run,
)


class TestGlobSearchToolV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sub_a").mkdir()
        (self.root / "sub_b" / "deep").mkdir(parents=True)
        (self.root / "file1.py").write_text("x", encoding="utf-8")
        (self.root / "file2.json").write_text("x", encoding="utf-8")
        (self.root / "README.md").write_text("x", encoding="utf-8")
        (self.root / "sub_a" / "sub_file1.py").write_text("x", encoding="utf-8")
        (self.root / "sub_a" / "sub_file2.txt").write_text("x", encoding="utf-8")
        (self.root / "sub_b" / "deep" / "deep_file.py").write_text("x", encoding="utf-8")
        self.tool = GlobSearchTool(default_max_results=10)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_ok(self, result):
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "find_by_glob")
        self.assertEqual(result["action"], "find")
        self.assertEqual(result["meta"]["version"], "2.0.0")
        json.dumps(result)
        return result["data"]

    def assert_error(self, result, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsNone(result["data"])
        json.dumps(result)

    def test_recursive_and_non_recursive(self):
        data = self.assert_ok(self.tool.find("*.py", str(self.root), recursive=True))
        self.assertEqual(data["returned_count"], 3)
        names = [Path(item["path"]).name for item in data["matches"]]
        self.assertEqual(set(names), {"file1.py", "sub_file1.py", "deep_file.py"})

        data = self.assert_ok(self.tool.find("*.py", str(self.root), recursive=False))
        self.assertEqual(data["returned_count"], 1)
        self.assertEqual(Path(data["matches"][0]["path"]).name, "file1.py")

    def test_explicit_recursive_pattern_and_empty_success(self):
        data = self.assert_ok(self.tool.find("**/*.txt", str(self.root), recursive=True))
        self.assertEqual(data["returned_count"], 1)
        self.assertEqual(Path(data["matches"][0]["path"]).name, "sub_file2.txt")

        result = self.tool.find("*.cpp", str(self.root), recursive=True)
        data = self.assert_ok(result)
        self.assertEqual(data["matches"], [])
        self.assertEqual(data["returned_count"], 0)
        self.assertFalse(result["meta"]["truncated"])

    def test_root_errors_and_empty_pattern(self):
        self.assert_error(self.tool.find("", str(self.root)), "INVALID_ARGUMENT")
        self.assert_error(
            self.tool.find("*.py", str(self.root / "missing")),
            "GLOB_ROOT_NOT_FOUND",
        )
        self.assert_error(
            self.tool.find("*.py", str(self.root / "file1.py")),
            "GLOB_ROOT_NOT_DIRECTORY",
        )

    def test_absolute_and_parent_escape_patterns_rejected(self):
        outside = self.root.parent / "outside-tools-v1-test.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            self.assert_error(
                self.tool.find("../*.txt", str(self.root)),
                "GLOB_PATTERN_OUTSIDE_ROOT",
            )
            self.assert_error(
                self.tool.find("a/../../*.txt", str(self.root)),
                "GLOB_PATTERN_OUTSIDE_ROOT",
            )
            self.assert_error(
                self.tool.find("/tmp/*.txt", str(self.root)),
                "GLOB_PATTERN_OUTSIDE_ROOT",
            )
            self.assert_error(
                self.tool.find(r"C:\temp\*.txt", str(self.root)),
                "GLOB_PATTERN_OUTSIDE_ROOT",
            )
        finally:
            outside.unlink(missing_ok=True)

    def test_recursive_and_max_results_are_strict_types(self):
        self.assert_error(
            self.tool.find("*.py", str(self.root), recursive=1),
            "INVALID_ARGUMENT",
        )
        for value in (0, -1, True, GLOB_MAX_RESULTS_HARD + 1):
            with self.subTest(value=value):
                self.assert_error(
                    self.tool.find("*", str(self.root), max_results=value),
                    "INVALID_ARGUMENT",
                )

    def test_constructor_default_is_hard_bounded(self):
        with self.assertRaises(ToolLimitConfigError):
            GlobSearchTool(default_max_results=0)
        with self.assertRaises(ToolLimitConfigError):
            GlobSearchTool(default_max_results=GLOB_MAX_RESULTS_HARD + 1)

    def test_exact_truncation_detection(self):
        root = self.root / "exact"
        root.mkdir()
        for name in ("a.txt", "b.txt", "c.txt"):
            (root / name).write_text("x", encoding="utf-8")

        result = self.tool.find("*.txt", str(root), recursive=False, max_results=3)
        data = self.assert_ok(result)
        self.assertEqual(data["returned_count"], 3)
        self.assertFalse(result["meta"]["truncated"])

        (root / "d.txt").write_text("x", encoding="utf-8")
        result = self.tool.find("*.txt", str(root), recursive=False, max_results=3)
        data = self.assert_ok(result)
        self.assertEqual(data["returned_count"], 3)
        self.assertTrue(result["meta"]["truncated"])

    def test_deterministic_lexical_first_n_even_when_generator_shuffled(self):
        fake_root = self.root / "shuffle"
        fake_root.mkdir()
        paths = []
        for name in ("c.txt", "a.txt", "b.txt"):
            path = fake_root / name
            path.write_text("x", encoding="utf-8")
            paths.append(path)

        original_glob = Path.glob

        def shuffled(path_obj, pattern):
            if path_obj == fake_root:
                return iter(paths)
            return original_glob(path_obj, pattern)

        with patch("tools.v1.find_by_glob.Path.glob", new=shuffled):
            result = self.tool.find(
                "*.txt",
                str(fake_root),
                recursive=False,
                max_results=2,
            )

        data = self.assert_ok(result)
        names = [Path(item["path"]).name for item in data["matches"]]
        self.assertEqual(names, ["a.txt", "b.txt"])
        self.assertTrue(result["meta"]["truncated"])

    def test_paths_are_absolute_posix_and_classified(self):
        data = self.assert_ok(self.tool.find("*", str(self.root), recursive=False))
        self.assertGreater(data["returned_count"], 0)
        for item in data["matches"]:
            self.assertTrue(Path(item["path"]).is_absolute())
            self.assertNotIn("\\", item["path"])
            self.assertIn(item["kind"], {"file", "directory", "other"})
            self.assertIs(type(item["is_symlink"]), bool)

    def test_symlink_descriptor_and_recursive_behavior_when_supported(self):
        target_dir = self.root / "real_dir"
        target_dir.mkdir()
        (target_dir / "inside.txt").write_text("x", encoding="utf-8")
        link_dir = self.root / "link_dir"
        try:
            link_dir.symlink_to(target_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")

        data = self.assert_ok(
            self.tool.find("link_dir", str(self.root), recursive=False)
        )
        self.assertEqual(data["returned_count"], 1)
        self.assertTrue(data["matches"][0]["is_symlink"])

        data = self.assert_ok(self.tool.find("*.txt", str(self.root), recursive=True))
        returned = [item["path"] for item in data["matches"]]
        self.assertFalse(any("/link_dir/" in path for path in returned))

    def test_global_run_returns_toolresult(self):
        result = run("*.json", root_dir=str(self.root))
        data = self.assert_ok(result)
        self.assertEqual(data["returned_count"], 1)


    def test_pattern_length_hard_cap(self):
        self.assert_error(
            self.tool.find("a" * (MAX_GLOB_PATTERN_CHARS + 1), str(self.root)),
            "INVALID_ARGUMENT",
        )

    def test_match_payload_never_contains_warning_strings(self):
        result = self.tool.find("*", str(self.root), recursive=True, max_results=2)
        data = self.assert_ok(result)
        self.assertTrue(result["meta"]["truncated"])
        self.assertTrue(all(isinstance(item, dict) for item in data["matches"]))
        self.assertTrue(all("path" in item for item in data["matches"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
