import math
import unittest
from pathlib import Path

from tools.v1._shared.errors import ToolJsonSafetyError
from tools.v1._shared.validation import REDACTED, assert_json_safe, redact_sensitive, redact_url_credentials


class TestSharedValidation(unittest.TestCase):
    def test_nested_json_value_is_accepted(self):
        assert_json_safe({"a": [1, 2.5, True, None, {"b": "x"}]})

    def test_non_json_values_report_path(self):
        cases = [
            ({"data": [{"payload": b"x"}]}, "$.data[0].payload"),
            ({"path": Path("x")}, "$.path"),
            ({"set": {1}}, "$.set"),
            ({1: "x"}, "$"),
            ({"n": math.nan}, "$.n"),
            ({"n": math.inf}, "$.n"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                with self.assertRaises(ToolJsonSafetyError) as ctx:
                    assert_json_safe(value)
                self.assertIn(expected, str(ctx.exception))

    def test_recursive_container_is_rejected(self):
        value = []
        value.append(value)
        with self.assertRaises(ToolJsonSafetyError) as ctx:
            assert_json_safe(value)
        self.assertIn("recursive container", str(ctx.exception))

    def test_sensitive_redaction_is_recursive_and_non_mutating(self):
        source = {
            "Authorization": "Bearer abc",
            "nested": [{"api-key": "secret", "ordinary": "keep"}],
        }
        result = redact_sensitive(source)
        self.assertEqual(result["Authorization"], REDACTED)
        self.assertEqual(result["nested"][0]["api-key"], REDACTED)
        self.assertEqual(result["nested"][0]["ordinary"], "keep")
        self.assertEqual(source["Authorization"], "Bearer abc")
        self.assertEqual(source["nested"][0]["api-key"], "secret")

    def test_url_credentials_are_redacted(self):
        url = "http://user:pass@proxy.example:8080/path?q=1"
        redacted = redact_url_credentials(url)
        self.assertNotIn("user", redacted)
        self.assertNotIn("pass", redacted)
        self.assertIn("proxy.example:8080", redacted)
        self.assertEqual(
            redact_url_credentials("https://example.com/a"),
            "https://example.com/a",
        )


if __name__ == "__main__":
    unittest.main()
