import copy
import unittest
from unittest.mock import patch

from tools.v1._shared.contracts import tool_result_schema
from tools.v1._shared.errors import ToolMetadataError
from tools.v1._shared.metadata import validate_tool_manifest_v2


def valid_manifest():
    return {
        "manifest_version": "2.0",
        "name": "file_tool",
        "version": "2.0.0",
        "description": "Physical file tool",
        "expose_root": False,
        "effects": ["READ", "WRITE"],  # legacy root compatibility; no inheritance
        "exports": [
            {
                "id": "file.read",
                "version": "2.0",
                "name": "file.read",
                "description": "Read one file",
                "bind": {"action": "read"},
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": tool_result_schema({"type": "object"}),
                "kind": "TOOL",
                "execution_mode": "ONE_SHOT",
                "idempotency": "IDEMPOTENT",
                "effects": ["READ"],
                "base_risk": "LOW",
                "required_scopes": [],
                "required_permissions": [],
                "danger_patterns": [],
            }
        ],
    }


class TestSharedMetadata(unittest.TestCase):
    def test_valid_manifest_and_empty_bind(self):
        manifest = valid_manifest()
        result = validate_tool_manifest_v2(manifest)
        self.assertEqual(result, manifest)
        self.assertIsNot(result, manifest)
        manifest2 = valid_manifest()
        manifest2["exports"][0]["bind"] = {}
        validate_tool_manifest_v2(manifest2)

    def test_wrong_version_missing_root_and_empty_exports(self):
        for mutator in (
            lambda m: m.__setitem__("manifest_version", "1.0"),
            lambda m: m.pop("name"),
            lambda m: m.__setitem__("exports", []),
        ):
            m = valid_manifest()
            mutator(m)
            with self.assertRaises(ToolMetadataError):
                validate_tool_manifest_v2(m)

    def test_duplicate_export_and_name_mismatch(self):
        m = valid_manifest()
        m["exports"].append(copy.deepcopy(m["exports"][0]))
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["name"] = "file.other"
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_invalid_vocabulary(self):
        fields = {
            "kind": "AGENT",
            "execution_mode": "CONTEXT_ONLY",
            "idempotency": "MAYBE",
            "base_risk": "CRITICAL",
        }
        for field, value in fields.items():
            m = valid_manifest()
            m["exports"][0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ToolMetadataError):
                    validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["effects"] = ["READ", "READ"]
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["effects"] = ["NOPE"]
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_missing_export_security_field_does_not_inherit_root(self):
        m = valid_manifest()
        m["exports"][0].pop("effects")
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_bind_collisions_and_json_safety(self):
        m = valid_manifest()
        m["exports"][0]["input_schema"]["properties"]["action"] = {"type": "string"}
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["input_schema"]["required"].append("action")
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["bind"] = {"action": object()}
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_input_schema_contract(self):
        m = valid_manifest()
        m["exports"][0]["input_schema"]["additionalProperties"] = True
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["input_schema"]["required"] = ["missing"]
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["input_schema"] = {
            "type": 5,
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_output_schema_contract(self):
        m = valid_manifest()
        m["exports"][0]["output_schema"] = {"type": "array"}
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["output_schema"] = {"type": "object", "required": []}
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_invalid_danger_pattern_and_duplicate_scopes_permissions(self):
        m = valid_manifest()
        m["exports"][0]["danger_patterns"] = ["["]
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        for field in ("required_scopes", "required_permissions"):
            m = valid_manifest()
            m["exports"][0][field] = ["x", "x"]
            with self.subTest(field=field):
                with self.assertRaises(ToolMetadataError):
                    validate_tool_manifest_v2(m)

    def test_identifier_and_root_types(self):
        m = valid_manifest()
        m["name"] = "File-Tool"
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["expose_root"] = "false"
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["id"] = "File.Read"
        m["exports"][0]["name"] = "File.Read"
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)

    def test_effects_must_be_non_empty_and_bind_keys_non_empty(self):
        m = valid_manifest()
        m["exports"][0]["effects"] = []
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)
        m = valid_manifest()
        m["exports"][0]["bind"] = {"": "read"}
        with self.assertRaises(ToolMetadataError):
            validate_tool_manifest_v2(m)


    def test_all_required_root_fields_are_explicit(self):
        for field in (
            "manifest_version",
            "name",
            "version",
            "description",
            "expose_root",
            "exports",
        ):
            m = valid_manifest()
            m.pop(field)
            with self.subTest(field=field):
                with self.assertRaises(ToolMetadataError):
                    validate_tool_manifest_v2(m)

    def test_all_required_export_fields_are_explicit(self):
        required_fields = (
            "id",
            "version",
            "name",
            "description",
            "bind",
            "input_schema",
            "output_schema",
            "kind",
            "execution_mode",
            "idempotency",
            "effects",
            "base_risk",
            "required_scopes",
            "required_permissions",
            "danger_patterns",
        )
        for field in required_fields:
            m = valid_manifest()
            m["exports"][0].pop(field)
            with self.subTest(field=field):
                with self.assertRaises(ToolMetadataError):
                    validate_tool_manifest_v2(m)

    def test_generic_multi_key_bind_is_valid_and_defensively_copied(self):
        m = valid_manifest()
        m["exports"][0]["bind"] = {
            "action": "read",
            "mode": "binary",
        }

        result = validate_tool_manifest_v2(m)

        self.assertEqual(
            result["exports"][0]["bind"],
            {"action": "read", "mode": "binary"},
        )
        self.assertIsNot(result, m)
        self.assertIsNot(result["exports"], m["exports"])
        self.assertIsNot(result["exports"][0], m["exports"][0])
        self.assertIsNot(
            result["exports"][0]["bind"],
            m["exports"][0]["bind"],
        )

        result["exports"][0]["bind"]["mode"] = "text"
        self.assertEqual(m["exports"][0]["bind"]["mode"], "binary")

    def test_missing_jsonschema_is_deterministic_error(self):
        m = valid_manifest()
        with patch(
            "tools.v1._shared.metadata.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            with self.assertRaisesRegex(ToolMetadataError, "jsonschema is required"):
                validate_tool_manifest_v2(m)


if __name__ == "__main__":
    unittest.main()
