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


class OTPBackendRuntimeContract(unittest.TestCase):
    @staticmethod
    def selector():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "s04_otp_selector", ROOT / "se" / "src" / "transport"
            / "gateway" / "authentication" / "services" / "otp_backend_admission.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def storage(registered=True, available=True, fails=False, empty=False):
        class Registry:
            def get_entry(self, name):
                return object() if name == "redis" and registered else None
            def is_available(self, name):
                return name == "redis" and available
        class Storage:
            drivers = Registry()
            def __init__(self):
                self.calls = []
            def get_cache_driver(self, name="in-memory"):
                self.calls.append(name)
                if fails:
                    raise RuntimeError("secret://do-not-log")
                return None if empty else name
        return Storage()

    def test_enabled_selects_named_redis_not_memory(self):
        mod = self.selector()
        engine = self.storage()
        self.assertEqual(mod.select_otp_cache_driver(engine, auth_enabled=True), "redis")
        self.assertEqual(engine.calls, ["redis"])

    def test_enabled_missing_redis_rejects_without_fallback(self):
        mod = self.selector()
        engine = self.storage(registered=False)
        with self.assertRaises(mod.OTPBackendUnavailable):
            mod.select_otp_cache_driver(engine, auth_enabled=True)
        self.assertEqual(engine.calls, [])

    def test_enabled_disconnected_redis_rejects_without_fallback(self):
        mod = self.selector()
        engine = self.storage(available=False)
        with self.assertRaises(mod.OTPBackendUnavailable):
            mod.select_otp_cache_driver(engine, auth_enabled=True)
        self.assertEqual(engine.calls, [])

    def test_enabled_driver_exception_masked_and_fails_closed(self):
        mod = self.selector()
        engine = self.storage(fails=True)
        with self.assertRaises(mod.OTPBackendUnavailable) as caught:
            mod.select_otp_cache_driver(engine, auth_enabled=True)
        self.assertNotIn("secret://", str(caught.exception))
        self.assertEqual(engine.calls, ["redis"])

    def test_enabled_null_backend_never_grants_sql_fallback(self):
        mod = self.selector()
        engine = self.storage(empty=True)
        with self.assertRaises(mod.OTPBackendUnavailable):
            mod.select_otp_cache_driver(engine, auth_enabled=True)
        self.assertEqual(engine.calls, ["redis"])

    def test_auth_disabled_retains_development_default(self):
        mod = self.selector()
        engine = self.storage(registered=False)
        self.assertEqual(mod.select_otp_cache_driver(engine, auth_enabled=False), "in-memory")
        self.assertEqual(engine.calls, ["in-memory"])


if __name__ == "__main__":
    unittest.main()
