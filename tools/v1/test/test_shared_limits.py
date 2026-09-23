import math
import unittest

from tools.v1._shared.errors import ToolLimitConfigError
from tools.v1._shared.limits import FloatLimitSpec, IntLimitSpec, resolve_float_limit, resolve_int_limit


class TestSharedLimits(unittest.TestCase):
    def test_int_resolution_boundaries(self):
        spec = IntLimitSpec("count", default=5, minimum=1, maximum=10)
        self.assertEqual(resolve_int_limit(None, spec), 5)
        self.assertEqual(resolve_int_limit(1, spec), 1)
        self.assertEqual(resolve_int_limit(10, spec), 10)
        for value in (0, -1, 11, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ToolLimitConfigError):
                    resolve_int_limit(value, spec)

    def test_invalid_int_specs_fail(self):
        with self.assertRaises(ToolLimitConfigError):
            IntLimitSpec("x", default=0, minimum=1, maximum=10)
        with self.assertRaises(ToolLimitConfigError):
            IntLimitSpec("x", default=5, minimum=10, maximum=1)
        with self.assertRaises(ToolLimitConfigError):
            IntLimitSpec("x", default=True, minimum=0, maximum=1)

    def test_float_resolution_and_finite_requirements(self):
        spec = FloatLimitSpec("duration", default=1.0, minimum=0.1, maximum=5.0)
        self.assertEqual(resolve_float_limit(None, spec), 1.0)
        self.assertEqual(resolve_float_limit(0.1, spec), 0.1)
        self.assertEqual(resolve_float_limit(5, spec), 5.0)
        for value in (0.0, 5.1, True, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ToolLimitConfigError):
                    resolve_float_limit(value, spec)

    def test_invalid_float_spec_rejects_non_finite(self):
        with self.assertRaises(ToolLimitConfigError):
            FloatLimitSpec("x", default=1.0, minimum=0.0, maximum=math.inf)


if __name__ == "__main__":
    unittest.main()
