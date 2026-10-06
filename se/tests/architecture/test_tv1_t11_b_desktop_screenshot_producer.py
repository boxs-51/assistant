from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import threading

import pytest
from PIL import Image

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.local_capability_executor import (
    LocalCapabilityExecutor,
    LocalExecutionDenied,
)
from se.src.runtimes.agent.adapters.context import _project_f7t_screenshot_output
from tools.v1 import desktop_tool
from tools.v1._shared.errors import ToolMetadataError
from tools.v1._shared.metadata import validate_tool_manifest_v2


CAPABILITY_ID = "desktop.screenshot"


def _screenshot_export() -> dict:
    manifest = validate_tool_manifest_v2(desktop_tool.TOOL_METADATA)
    return next(item for item in manifest["exports"] if item["id"] == CAPABILITY_ID)


class _FakeBackend:
    FAILSAFE = True
    PAUSE = 0.1

    class FailSafeException(Exception):
        pass

    def __init__(self, *, size=(2, 2)) -> None:
        self._size = size
        self.capture_calls = 0

    def size(self):
        return self._size

    def screenshot(self):
        self.capture_calls += 1
        return Image.new("RGB", self._size, (1, 2, 3))


def test_metadata_v2_freezes_client_only_screenshot_without_changing_default_location():
    screenshot = _screenshot_export()
    assert screenshot["version"] == "1.0"
    assert screenshot["bind"] == {"action": "screenshot"}
    assert screenshot["input_schema"] == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [],
    }
    assert screenshot["idempotency"] == "UNKNOWN"
    assert screenshot["effects"] == ["READ", "PRIVILEGED"]
    assert screenshot["base_risk"] == "HIGH"
    assert screenshot["execution_locations"] == ["CLIENT"]

    historical = deepcopy(desktop_tool.TOOL_METADATA)
    historical["exports"] = historical["exports"][:-1]
    validated = validate_tool_manifest_v2(historical)
    assert all("execution_locations" not in item for item in validated["exports"])

    invalid = deepcopy(desktop_tool.TOOL_METADATA)
    invalid["exports"][-1]["execution_locations"] = ["CLIENT", "SANDBOX"]
    with pytest.raises(ToolMetadataError, match="execution_locations"):
        validate_tool_manifest_v2(invalid)


def test_screenshot_producer_returns_exact_in_memory_f7t_png_envelope():
    backend = _FakeBackend(size=(2, 2))
    tool = desktop_tool.DesktopAutomation()
    tool._pyautogui = backend

    result = tool.screenshot()

    assert result["ok"] is True
    assert result["tool"] == "desktop_automation"
    assert result["action"] == "screenshot"
    assert set(result["data"]) == {"$f7t_media"}
    assert result["meta"] == {
        "version": "2.1.0",
        "truncated": False,
        "warnings": [],
    }

    media = result["data"]["$f7t_media"]
    assert set(media) == {"contract", "items"}
    assert media["contract"] == "F7T_INLINE_BASE64_V1"
    assert len(media["items"]) == 1
    item = media["items"][0]
    assert set(item) == {
        "ordinal",
        "media_kind",
        "mime_type",
        "filename",
        "encoding",
        "size_bytes",
        "sha256",
        "data_base64",
    }
    png_bytes = base64.b64decode(item["data_base64"], validate=True)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png_bytes) == item["size_bytes"]
    assert len(png_bytes) <= desktop_tool.MAX_SCREENSHOT_PNG_BYTES
    assert hashlib.sha256(png_bytes).hexdigest() == item["sha256"]
    assert backend.capture_calls == 1


def test_screenshot_bounds_fail_before_capture():
    backend = _FakeBackend(size=(desktop_tool.MAX_SCREENSHOT_WIDTH + 1, 1))
    tool = desktop_tool.DesktopAutomation()
    tool._pyautogui = backend

    result = tool.screenshot()

    assert result["ok"] is False
    assert result["error"]["code"] == "SCREENSHOT_BOUNDS_EXCEEDED"
    assert backend.capture_calls == 0


class _Registry:
    def __init__(self, target):
        self.tools = {
            CAPABILITY_ID: {
                "func": target,
                "metadata": {
                    "name": CAPABILITY_ID,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                        "required": [],
                    },
                    "base_risk": "HIGH",
                    "danger_patterns": [],
                    "idempotency": "UNKNOWN",
                },
            }
        }

    def get_tool(self, capability_id):
        return self.tools.get(capability_id)


class _Approval:
    def __init__(self, allow: bool):
        self.allow = allow
        self.calls = []

    def request_approval(self, *args):
        self.calls.append(args)
        return self.allow


def test_high_risk_local_approval_precedes_target_and_unknown_is_not_replay_safe():
    target_calls = []

    def target(**kwargs):
        target_calls.append(kwargs)
        return {"ok": True}

    denied = _Approval(False)
    executor = LocalCapabilityExecutor(_Registry(target), denied)
    with pytest.raises(LocalExecutionDenied):
        executor.execute(
            CAPABILITY_ID,
            {},
            {"execution_id": "e", "invocation_id": "i", "trace_id": "t"},
            threading.Event(),
        )
    assert denied.calls
    assert target_calls == []

    allowed = _Approval(True)
    executor = LocalCapabilityExecutor(_Registry(target), allowed)
    assert executor.execute(
        CAPABILITY_ID,
        {},
        {"execution_id": "e", "invocation_id": "i", "trace_id": "t"},
        threading.Event(),
    ) == {"ok": True}
    assert len(target_calls) == 1
    assert CapabilityDispatcher._durable_running_replay_safe("UNKNOWN") is False


def test_model_projection_omits_binary_and_preserves_durable_result():
    backend = _FakeBackend(size=(2, 2))
    tool = desktop_tool.DesktopAutomation()
    tool._pyautogui = backend
    durable = tool.screenshot()
    original = deepcopy(durable)

    projected = _project_f7t_screenshot_output(durable)

    assert durable == original
    assert projected is not None
    assert set(projected) == {"$f7t_media_projection"}
    projection = projected["$f7t_media_projection"]
    assert projection["source_contract"] == "F7T_INLINE_BASE64_V1"
    assert projection["binary_omitted"] is True
    assert "data_base64" not in repr(projected)
    item = projection["items"][0]
    durable_item = durable["data"]["$f7t_media"]["items"][0]
    assert item["size_bytes"] == durable_item["size_bytes"]
    assert item["sha256"] == durable_item["sha256"]

    malformed = deepcopy(durable)
    malformed["data"]["$f7t_media"]["items"][0]["extra"] = "not-strict"
    assert _project_f7t_screenshot_output(malformed) is None
