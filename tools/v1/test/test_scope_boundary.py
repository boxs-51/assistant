import ast
import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "_shared"
FORBIDDEN_ROOTS = {"se", "cl"}
SHARED_FORBIDDEN_IMPORTS = {
    "subprocess",
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "playwright",
    "pyautogui",
    "pywinctl",
    "pynput",
    "curl_cffi",
}
PHYSICAL_MODULES = {
    "tools.v1.desktop_tool",
    "tools.v1.file_tool",
    "tools.v1.find_by_glob",
    "tools.v1.terminal_tool",
    "tools.v1.window_tool",
    "tools.v1.web_tool",
}


def imports_in(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".")[0], node.module


class TestToolsV1ScopeBoundary(unittest.TestCase):
    def test_no_production_source_imports_se_or_cl(self):
        violations = []
        for path in ROOT.rglob("*.py"):
            if "test" in path.parts or "live" in path.parts or "__pycache__" in path.parts:
                continue
            for root, full in imports_in(path):
                if root in FORBIDDEN_ROOTS:
                    violations.append(f"{path.relative_to(ROOT)} -> {full}")
        self.assertEqual(violations, [])

    def test_shared_has_no_execution_dependencies(self):
        violations = []
        for path in SHARED.glob("*.py"):
            for root, full in imports_in(path):
                if root in SHARED_FORBIDDEN_IMPORTS:
                    violations.append(f"{path.name} -> {full}")
        self.assertEqual(violations, [])

    def test_importing_shared_does_not_import_physical_tools(self):
        before = set(sys.modules)
        module = importlib.import_module("tools.v1._shared")
        self.assertIsNotNone(module)
        newly_loaded = set(sys.modules) - before
        self.assertFalse(PHYSICAL_MODULES.intersection(newly_loaded))


if __name__ == "__main__":
    unittest.main()
