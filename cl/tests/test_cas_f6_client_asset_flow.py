import base64
import json
from pathlib import Path

import pytest
import requests

from cl.src.core.gateway_client import GatewayLLMClient
from cl.src.ui.bridge import UIBridge


class _Response:
    status_code = 200
    text = ""
    content = b""

    def __init__(self, payload=None, *, content=b"", status_code=200):
        self.payload = payload or {}
        self.content = content
        self.status_code = status_code

    def json(self):
        return self.payload


class _Hitl:
    def set_approval_callback(self, callback):
        self.callback = callback


class _Registry:
    settings = {"loaded": True}
    tools = {}
    skills = {}

    def execute_slash_command(self, _text):
        raise AssertionError("slash command not expected")


class _Gateway:
    def __init__(self):
        self.upload_calls = []
        self.metadata_calls = []
        self.content_calls = []

    def upload_asset(self, file_path):
        self.upload_calls.append(file_path)
        return {
            "asset_id": "asset-1",
            "filename": Path(file_path).name,
            "mime_type": "text/plain",
            "size_bytes": 5,
            "state": "READY",
            "uri": "asset://asset-1",
        }

    def asset_metadata(self, asset_id):
        self.metadata_calls.append(asset_id)
        return {
            "asset_id": asset_id,
            "filename": "note.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "state": "READY",
            "uri": f"asset://{asset_id}",
        }

    def asset_content(self, asset_id, byte_range=None):
        self.content_calls.append((asset_id, byte_range))
        return b"hello"


class _ClientRuntime:
    ready = True

    def __init__(self):
        self.chat_calls = []

    def chat(self, payload):
        self.chat_calls.append(payload)
        return iter(())


class _Engine:
    approval_timeout_seconds = 0.01

    def __init__(self, gateway):
        self.gateway_client = gateway
        self.registry = _Registry()
        self.workspace_dir = "."
        self.offline_calls = []

    def run_agent_session(self, **kwargs):
        self.offline_calls.append(kwargs)


def _bridge():
    gateway = _Gateway()
    hitl = _Hitl()
    runtime = _ClientRuntime()
    bridge = UIBridge(_Engine(gateway), hitl, runtime)
    return bridge, gateway, runtime


def test_gateway_client_uploads_canonical_asset_with_existing_auth(monkeypatch, tmp_path):
    captured = {}
    file_path = tmp_path / "note.txt"
    file_path.write_text("hello", encoding="utf-8")

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Response({
            "asset_id": "asset-1",
            "filename": "note.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "state": "READY",
            "uri": "asset://asset-1",
        }, status_code=201)

    monkeypatch.setattr(requests, "request", request)
    client = GatewayLLMClient("http://gateway", api_key="token")

    descriptor = client.upload_asset(file_path)

    assert descriptor["asset_id"] == "asset-1"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/assets")
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert "Content-Type" not in captured["headers"]
    assert set(captured["files"]) == {"file"}
    assert captured["files"]["file"][0] == "note.txt"


def test_gateway_client_asset_content_uses_authenticated_range_header(monkeypatch):
    captured = {}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Response(content=b"ell")

    monkeypatch.setattr(requests, "request", request)
    client = GatewayLLMClient("http://gateway", api_key="token")

    content = client.asset_content("asset-1", byte_range="bytes=1-3")

    assert content == b"ell"
    assert captured["url"].endswith("/v1/assets/asset-1/content")
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["headers"]["Range"] == "bytes=1-3"


def test_online_message_content_uses_only_canonical_asset_identity():
    parts = UIBridge._online_message_content("hello", [{
        "asset_id": "asset-1",
        "source": "asset",
        "uri": "asset://asset-1",
        "filename": "note.txt",
        "mime_type": "text/plain",
        "size": 5,
    }])

    assert parts[0] == {"type": "text", "text": "hello"}
    attachment = parts[1]["data"]["attachment"]
    assert attachment == {
        "asset_id": "asset-1",
        "source": "asset",
        "uri": "asset://asset-1",
        "filename": "note.txt",
        "mime_type": "text/plain",
        "size": 5,
    }
    assert "path" not in attachment
    assert "base64_data" not in attachment
    assert "provider_file_id" not in attachment


@pytest.mark.parametrize("forbidden", [
    {"path": "C:/note.txt"},
    {"base64_data": "aGVsbG8="},
    {"b64_data": "aGVsbG8="},
    {"provider_file_id": "provider-1"},
])
def test_online_message_content_rejects_legacy_or_provider_identity(forbidden):
    payload = {
        "asset_id": "asset-1",
        "source": "asset",
        "uri": "asset://asset-1",
        "filename": "note.txt",
        "mime_type": "text/plain",
        **forbidden,
    }

    with pytest.raises(ValueError):
        UIBridge._online_message_content("", [payload])


def test_online_prepare_uploads_asset_and_emits_canonical_payload_only():
    bridge, gateway, _runtime = _bridge()
    js_calls = []
    bridge._eval_js = js_calls.append

    bridge._upload_asset_worker("C:/tmp/note.txt")

    assert gateway.upload_calls == ["C:/tmp/note.txt"]
    complete = next(call for call in js_calls if "onFilePrepareComplete" in call)
    event = json.loads(complete[complete.index("(") + 1: complete.rindex(")")])
    assert event["path"] == "C:/tmp/note.txt"
    assert event["payload"]["asset_id"] == "asset-1"
    assert event["payload"]["source"] == "asset"
    assert event["payload"]["uri"] == "asset://asset-1"
    assert "path" not in event["payload"]
    assert "base64_data" not in event["payload"]
    assert "provider_file_id" not in event["payload"]


def test_local_offline_file_preparation_preserves_file_encoder(monkeypatch):
    bridge, gateway, _runtime = _bridge()
    encoded = []
    monkeypatch.setattr(bridge.encoder, "encode_async", lambda files: encoded.extend(files))
    bridge.set_chat_preferences({
        "provider": "mock",
        "model": "mock-chat",
        "execution_mode": "LOCAL_OFFLINE",
        "agent_enabled": False,
        "agent_id": None,
    })

    bridge.prepare_files_async(["C:/tmp/note.txt"])

    assert encoded == ["C:/tmp/note.txt"]
    assert gateway.upload_calls == []


def test_authenticated_asset_content_resolution_is_transient_base64():
    bridge, gateway, _runtime = _bridge()

    result = bridge.read_asset_content("asset-1")

    assert result["success"] is True
    assert result["data"]["asset_id"] == "asset-1"
    assert result["data"]["base64_data"] == base64.b64encode(b"hello").decode("ascii")
    assert gateway.metadata_calls == ["asset-1"]
    assert gateway.content_calls == [("asset-1", None)]


def test_submit_prompt_sends_canonical_attachment_online(monkeypatch):
    bridge, _gateway, runtime = _bridge()

    class ImmediateThread:
        def __init__(self, target, daemon, args=()):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("cl.src.ui.bridge.threading.Thread", ImmediateThread)

    bridge.submit_prompt(
        "hello",
        files=[{
            "asset_id": "asset-1",
            "source": "asset",
            "uri": "asset://asset-1",
            "filename": "note.txt",
            "mime_type": "text/plain",
            "size": 5,
        }],
        conversation_id="conversation-1",
    )

    request = runtime.chat_calls[0]
    message_content = request.messages[0].content
    assert message_content[0].type.value == "text"
    assert message_content[0].text == "hello"
    assert message_content[1].type.value == "file"
    assert message_content[1].data.attachment.asset_id == "asset-1"
    assert message_content[1].data.attachment.source == "asset"
    assert message_content[1].data.attachment.uri == "asset://asset-1"
    assert message_content[1].data.attachment.base64_data is None
    assert message_content[1].data.attachment.provider_file_id is None


def test_ui_source_freezes_partial_failure_retry_and_canonical_rendering():
    root = Path("cl/src/ui/web/js")
    file_manager = (root / "components/inputFrame/fileManager.js").read_text(encoding="utf-8")
    input_frame = (root / "components/inputFrame.js").read_text(encoding="utf-8")
    normalizer = (root / "components/console/normalizer.js").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    file_block = (root / "components/console/blocks/fileBlock.js").read_text(encoding="utf-8")
    image_block = (root / "components/console/blocks/imageBlock.js").read_text(encoding="utf-8")
    media_block = (root / "components/console/blocks/mediaBlock.js").read_text(encoding="utf-8")

    assert "api.prepare_files_async" in file_manager
    assert "api.encode_files_async" not in file_manager
    assert "retryFile" in file_manager
    assert "onFilePrepareComplete" in file_manager
    assert "getFailedFilePaths" in input_frame
    assert "window.confirm" in input_frame
    assert "tiếp tục chỉ với nội dung/tệp READY" in input_frame

    assert "asset_id: assetId" in normalizer
    assert "asset://${assetId}" in normalizer
    assert "isCanonicalAsset ? null" in normalizer
    assert "source: isCanonicalAsset" in normalizer

    assert "read_asset_content" in app
    assert "createCanonicalAssetObjectUrl" in app
    assert "resolveCanonicalAssetContent" in file_block
    assert "createCanonicalAssetObjectUrl" in image_block
    assert "createCanonicalAssetObjectUrl" in media_block
