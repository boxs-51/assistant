from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image
import uvicorn
from fastapi import FastAPI

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerState,
)
from cl.src.core.client_runtime import ClientRuntime
from cl.src.core.local_capability_executor import (
    LocalCapabilityExecutor,
    LocalExecutionDenied,
)
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.agent.adapters.context import _project_f7t_screenshot_output
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.registration import ClientCapabilityRegistrationService
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import get_websocket_identity
from se.src.transport.gateway.dependencies import get_container
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

    bad_hash = deepcopy(durable)
    bad_hash["data"]["$f7t_media"]["items"][0]["sha256"] = "0" * 64
    assert _project_f7t_screenshot_output(bad_hash) is None

    bad_size = deepcopy(durable)
    bad_size["data"]["$f7t_media"]["items"][0]["size_bytes"] += 1
    assert _project_f7t_screenshot_output(bad_size) is None

class _NetworkRegistry:
    def __init__(self, output):
        export = _screenshot_export()

        def target(**_kwargs):
            return output

        self.tools = {
            CAPABILITY_ID: {
                "func": target,
                "metadata": {
                    "name": export["name"],
                    "version": export["version"],
                    "description": export["description"],
                    "parameters": deepcopy(export["input_schema"]),
                    "input_schema": deepcopy(export["input_schema"]),
                    "output_schema": deepcopy(export["output_schema"]),
                    "kind": export["kind"],
                    "execution_mode": export["execution_mode"],
                    "idempotency": export["idempotency"],
                    "effects": list(export["effects"]),
                    "require_auth": False,
                    "required_scopes": list(export["required_scopes"]),
                    "base_risk": export["base_risk"],
                    "required_permissions": list(export["required_permissions"]),
                    "danger_patterns": list(export["danger_patterns"]),
                    "physical_tool": "desktop_automation",
                    "physical_version": "2.1.0",
                    "bind": deepcopy(export["bind"]),
                    "manifest_version": "2.0",
                },
            }
        }

    def load_all(self):
        return None

    def get_tool(self, capability_id):
        return self.tools.get(capability_id)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until(predicate, timeout=15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition timed out")


def _build_realtime_gateway(owner_id: str, session_id: str):
    catalog = CapabilityCatalog()
    connection_runtime = ConnectionRuntime(
        ClientCapabilityRegistrationService(catalog, None)
    )
    connection_runtime.registration_service.connections = connection_runtime.registry
    identity = Identity(
        user_id=owner_id,
        session_id=session_id,
        auth_type="jwt",
    )
    container = SimpleNamespace(
        connection_runtime=connection_runtime,
        eventing_manager=SimpleNamespace(
            ws_manager=WebSocketConnectionManager(),
        ),
    )
    app = FastAPI()
    app.include_router(events_router.router)
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_websocket_identity] = lambda: identity
    return app, catalog, connection_runtime


def _start_realtime_gateway(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until(lambda: server.started)
    return server, thread, port


@pytest.mark.e2e
def test_near_bound_f7t_result_crosses_real_tcp_websocket_after_durable_terminal_commit(
    tmp_path,
):
    owner_id = "tv1-t11-b-owner"
    session_id = "tv1-t11-b-session"
    client_id = "tv1-t11-b-client"
    invocation_id = "tv1-t11-b-near-bound"

    # Keep the decoded source close to the frozen 8 MiB ceiling. Producer
    # validity is covered separately; this evidence stresses the canonical
    # realtime JSON/base64 path without allocating a second image dependency.
    raw = b"\x89PNG\r\n\x1a\n" + (
        b"tv1-t11-b-transport" * 396_000
    )
    raw = raw[: desktop_tool.MAX_SCREENSHOT_PNG_BYTES - 65_536]
    encoded = base64.b64encode(raw).decode("ascii")
    output = {
        "ok": True,
        "tool": "desktop_automation",
        "action": "screenshot",
        "data": {
            "$f7t_media": {
                "contract": "F7T_INLINE_BASE64_V1",
                "items": [
                    {
                        "ordinal": 0,
                        "media_kind": "image",
                        "mime_type": "image/png",
                        "filename": "desktop-screenshot.png",
                        "encoding": "base64",
                        "size_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "data_base64": encoded,
                    }
                ],
            }
        },
        "error": None,
        "meta": {
            "version": "2.1.0",
            "truncated": False,
            "warnings": [],
        },
    }

    app, catalog, connection_runtime = _build_realtime_gateway(
        owner_id,
        session_id,
    )
    server, thread, port = _start_realtime_gateway(app)
    ledger = ClientInvocationLedger(tmp_path / "client-invocations.sqlite3")
    client = ClientRuntime(
        f"http://127.0.0.1:{port}",
        _NetworkRegistry(output),
        api_key="tv1-t11-b-e2e",
        client_id=client_id,
        owner_id=owner_id,
        invocation_ledger=ledger,
        hitl=_Approval(True),
    )

    try:
        client.start()
        _wait_until(
            lambda: catalog.contains_implementation(
                f"{client.connection_id}:{CAPABILITY_ID}"
            )
        )

        envelope = RealtimeEnvelope(
            type="capability.invoke",
            message_id="tv1-t11-b-near-bound-message",
            connection_id=client.connection_id,
            execution_id="tv1-t11-b-exec",
            invocation_id=invocation_id,
            trace_id="tv1-t11-b-trace",
            payload={
                "capability_id": CAPABILITY_ID,
                "capability_version": "1.0",
                "arguments": {},
            },
        )
        received = asyncio.run(
            connection_runtime.realtime.invoke(
                envelope,
                timeout=30.0,
            )
        )

        assert received == output
        received_item = received["data"]["$f7t_media"]["items"][0]
        assert len(base64.b64decode(received_item["data_base64"], validate=True)) == len(raw)
        assert received_item["sha256"] == hashlib.sha256(raw).hexdigest()

        durable = ledger.get(
            client_id=client_id,
            principal_id=owner_id,
            invocation_id=invocation_id,
        )
        assert durable is not None
        assert durable.state is ClientInvocationLedgerState.TERMINAL
        assert durable.terminal_type == "result"
        assert durable.terminal_payload == {"output": output}
    finally:
        client.stop()
        server.should_exit = True
        thread.join(timeout=5)

