from tools.v1._shared.metadata import validate_tool_manifest_v2
from tools.v1 import file_tool
from tools.v1 import find_by_glob
from tools.v1 import terminal_tool


T9_B_IDS = (
    "file.read",
    "file.search",
    "file.write",
    "file.append",
    "file.replace",
    "glob.find",
    "terminal.run",
    "terminal.launch",
)


def _exports(module):
    manifest = validate_tool_manifest_v2(module.TOOL_METADATA)
    assert manifest["expose_root"] is False
    return {item["id"]: item for item in manifest["exports"]}


def test_t9_b_manifests_publish_exact_logical_ids_and_strict_schemas():
    exports = {}
    for module in (file_tool, find_by_glob, terminal_tool):
        exports.update(_exports(module))

    assert tuple(exports) == T9_B_IDS
    assert len(exports) == len(T9_B_IDS)

    for capability_id, export in exports.items():
        assert export["name"] == capability_id
        assert export["version"] == "1.0"
        schema = export["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "action" not in schema["properties"]
        assert "action" not in schema["required"]
        assert not set(export["bind"]).intersection(schema["properties"])

    assert exports["file.write"]["bind"] == {"action": "write", "mode": "w"}
    assert exports["file.append"]["bind"] == {"action": "write", "mode": "a"}
    assert "mode" not in exports["file.write"]["input_schema"]["properties"]
    assert "mode" not in exports["file.append"]["input_schema"]["properties"]
    assert exports["glob.find"]["bind"] == {}
    assert exports["terminal.run"]["bind"] == {"action": "run"}
    assert exports["terminal.launch"]["bind"] == {"action": "launch"}
    assert "timeout" not in exports["terminal.launch"]["input_schema"]["properties"]
    assert "encoding" not in exports["terminal.launch"]["input_schema"]["properties"]


def test_t9_b_effects_idempotency_and_physical_versions_are_frozen():
    file_exports = _exports(file_tool)
    glob_exports = _exports(find_by_glob)
    terminal_exports = _exports(terminal_tool)

    assert file_tool.TOOL_METADATA["version"] == file_tool.FILE_TOOL_VERSION
    assert find_by_glob.TOOL_METADATA["version"] == find_by_glob.GLOB_TOOL_VERSION
    assert terminal_tool.TOOL_METADATA["version"] == terminal_tool.TERMINAL_TOOL_VERSION

    assert file_exports["file.read"]["idempotency"] == "IDEMPOTENT"
    assert file_exports["file.search"]["idempotency"] == "IDEMPOTENT"
    assert file_exports["file.write"]["idempotency"] == "IDEMPOTENT"
    assert file_exports["file.append"]["idempotency"] == "NON_IDEMPOTENT"
    assert file_exports["file.replace"]["idempotency"] == "UNKNOWN"
    assert glob_exports["glob.find"]["idempotency"] == "IDEMPOTENT"
    assert terminal_exports["terminal.run"]["idempotency"] == "UNKNOWN"
    assert terminal_exports["terminal.launch"]["idempotency"] == "NON_IDEMPOTENT"

    assert file_exports["file.read"]["effects"] == ["READ"]
    assert file_exports["file.write"]["effects"] == ["WRITE"]
    assert glob_exports["glob.find"]["effects"] == ["READ"]
    assert terminal_exports["terminal.run"]["effects"] == [
        "EXECUTE",
        "EXTERNAL_SIDE_EFFECT",
    ]
