"""S04 RED-first: OTP bootstrap must not silently choose a process-local issuer."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MAIN = ROOT / "se" / "src" / "main.py"


class OTPBackendSelectionContract(unittest.TestCase):
    def test_auth_bootstrap_uses_explicit_separate_otp_selector(self):
        tree = ast.parse(MAIN.read_text(encoding="utf-8"))
        bootstrap = next(node for node in tree.body
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == "bootstrap_security")
        otp_assignments = [
            node.value for node in ast.walk(bootstrap)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "redis_driver"
                    for target in node.targets)
        ]
        self.assertEqual(len(otp_assignments), 1)
        expr = otp_assignments[0]
        self.assertTrue(
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "select_otp_cache_driver",
            "S04_BOOTSTRAP_SEMANTIC_RED: OTP issuer shares default InMemory "
            "selector instead of an explicit fail-closed auth backend",
        )
        keyword_names = {item.arg for item in expr.keywords}
        self.assertIn("auth_enabled", keyword_names)


if __name__ == "__main__":
    unittest.main()
