from __future__ import annotations

import pytest
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from tools.v1 import desktop_tool, window_tool
from tools.v1._shared.metadata import validate_tool_manifest_v2


WINDOW_IDS = (
    "window.list",
    "window.find",
    "window.geometry",
    "window.focus",
    "window.close",
    "window.minimize",
    "window.maximize",
    "window.restore",
)
DESKTOP_IDS = (
    "desktop.screen_info",
    "desktop.mouse_move",
    "desktop.mouse_click",
    "desktop.mouse_drag",
    "desktop.mouse_scroll",
    "desktop.type_text",
    "desktop.press_key",
    "desktop.hotkey",
)


def _exports(module):
    manifest = validate_tool_manifest_v2(module.TOOL_METADATA)
    assert manifest["expose_root"] is False
    return {item["id"]: item for item in manifest["exports"]}


def _validate(schema, instance):
    validator_cls = validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(instance)


def test_t9_c_manifests_publish_exact_ids_and_strict_schemas():
    window = _exports(window_tool)
    desktop = _exports(desktop_tool)

    assert tuple(window) == WINDOW_IDS
    assert tuple(desktop) == DESKTOP_IDS

    for capability_id, export in {**window, **desktop}.items():
        assert export["name"] == capability_id
        assert export["version"] == "1.0"
        schema = export["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "action" not in schema["properties"]
        assert not set(export["bind"]).intersection(schema["properties"])

    assert window["window.geometry"]["bind"] == {"action": "get_geometry"}
    assert desktop["desktop.screen_info"]["bind"] == {"action": "get_screen_info"}
    assert window["window.close"]["idempotency"] == "NON_IDEMPOTENT"
    assert desktop["desktop.mouse_move"]["base_risk"] == "LOW"
    assert desktop["desktop.mouse_click"]["base_risk"] == "HIGH"


@pytest.mark.parametrize(
    "capability_id",
    [
        "window.geometry",
        "window.focus",
        "window.close",
        "window.minimize",
        "window.maximize",
        "window.restore",
    ],
)
def test_single_target_window_schemas_require_at_least_one_selector(capability_id):
    schema = _exports(window_tool)[capability_id]["input_schema"]

    with pytest.raises(ValidationError):
        _validate(schema, {})
    _validate(schema, {"title_query": "Editor"})
    _validate(schema, {"window_handle": 10})
    _validate(schema, {"pid": 20})
    _validate(
        schema,
        {"title_query": "Editor", "window_handle": 10, "pid": 20},
    )


def test_desktop_coordinate_pair_schemas_do_not_publish_half_pairs():
    exports = _exports(desktop_tool)

    click = exports["desktop.mouse_click"]["input_schema"]
    _validate(click, {})
    _validate(click, {"x": 1, "y": 2})
    with pytest.raises(ValidationError):
        _validate(click, {"x": 1})
    with pytest.raises(ValidationError):
        _validate(click, {"y": 2})

    drag = exports["desktop.mouse_drag"]["input_schema"]
    _validate(drag, {"x": 10, "y": 20})
    _validate(drag, {"x": 10, "y": 20, "start_x": 1, "start_y": 2})
    with pytest.raises(ValidationError):
        _validate(drag, {"x": 10, "y": 20, "start_x": 1})
    with pytest.raises(ValidationError):
        _validate(drag, {"x": 10, "y": 20, "start_y": 2})


def test_desktop_scroll_schema_rejects_zero_and_preserves_hard_bounds():
    schema = _exports(desktop_tool)["desktop.mouse_scroll"]["input_schema"]

    with pytest.raises(ValidationError):
        _validate(schema, {"clicks": 0})
    _validate(schema, {"clicks": -10_000})
    _validate(schema, {"clicks": 10_000})
