import math
import unittest

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from tools.v1._shared.contracts import failure_result, success_result, tool_result_schema
from tools.v1._shared.errors import ToolContractError, ToolJsonSafetyError


class TestSharedContracts(unittest.TestCase):
    def test_success_envelope(self):
        res = success_result(tool="file_tool", action="read", version="2.0.0", data={"x": 1})
        self.assertEqual(set(res), {"ok", "tool", "action", "data", "error", "meta"})
        self.assertTrue(res["ok"])
        self.assertIsNone(res["error"])
        self.assertEqual(res["data"], {"x": 1})
        self.assertEqual(res["meta"]["version"], "2.0.0")
        self.assertFalse(res["meta"]["truncated"])
        self.assertEqual(res["meta"]["warnings"], [])

    def test_failure_envelope(self):
        res = failure_result(
            tool="file_tool", action="read", version="2.0.0",
            code="FILE_NOT_FOUND", message="missing", details={"path": "x"},
        )
        self.assertFalse(res["ok"])
        self.assertIsNone(res["data"])
        self.assertEqual(res["error"]["code"], "FILE_NOT_FOUND")
        self.assertEqual(res["error"]["details"], {"path": "x"})

    def test_builders_return_fresh_containers_and_do_not_mutate_inputs(self):
        data = {"items": [1]}
        warnings = ["w"]
        meta = {"source": {"x": 1}}
        first = success_result(
            tool="x", action="y", version="1", data=data,
            warnings=warnings, extra_meta=meta,
        )
        second = success_result(tool="x", action="y", version="1", data=data)
        first["data"]["items"].append(2)
        first["meta"]["warnings"].append("z")
        first["meta"]["source"]["x"] = 2
        self.assertEqual(data, {"items": [1]})
        self.assertEqual(warnings, ["w"])
        self.assertEqual(meta, {"source": {"x": 1}})
        self.assertEqual(second["data"], {"items": [1]})
        self.assertIsNot(first["meta"], second["meta"])

    def test_invalid_identity_and_error_code_are_rejected(self):
        with self.assertRaises(ToolContractError):
            success_result(tool="", action="read", version="1")
        with self.assertRaises(ToolContractError):
            failure_result(tool="x", action="y", version="1", code="bad-code", message="x")

    def test_invalid_json_data_is_rejected(self):
        with self.assertRaises(ToolJsonSafetyError):
            success_result(tool="x", action="y", version="1", data={"bad": b"x"})
        with self.assertRaises(ToolJsonSafetyError):
            success_result(tool="x", action="y", version="1", data={"bad": math.inf})

    def test_reserved_meta_fields_cannot_be_overridden(self):
        with self.assertRaises(ToolContractError):
            success_result(
                tool="x", action="y", version="1",
                extra_meta={"version": "evil"},
            )

    def test_result_schema_accepts_success_and_failure(self):
        schema = tool_result_schema({
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        })
        good = success_result(tool="x", action="y", version="1", data={"value": 1})
        bad = failure_result(tool="x", action="y", version="1", code="NOT_FOUND", message="x")
        validate(good, schema)
        validate(bad, schema)

    def test_result_schema_rejects_malformed_envelope(self):
        schema = tool_result_schema({"type": "object"})
        malformed = {
            "ok": True,
            "tool": "x",
            "action": "y",
            "data": {},
            "error": {"code": "X"},
            "meta": {"version": "1", "truncated": False, "warnings": []},
        }
        with self.assertRaises(ValidationError):
            validate(malformed, schema)


if __name__ == "__main__":
    unittest.main()
