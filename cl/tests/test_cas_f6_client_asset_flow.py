import base64
import json
import shutil
import subprocess
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


def test_online_prepare_surfaces_upload_failure_without_legacy_fallback(monkeypatch):
    bridge, gateway, _runtime = _bridge()
    js_calls = []
    bridge._eval_js = js_calls.append
    monkeypatch.setattr(
        gateway,
        "upload_asset",
        lambda _path: (_ for _ in ()).throw(RuntimeError("upload failed")),
    )
    encoded = []
    monkeypatch.setattr(bridge.encoder, "encode_async", lambda files: encoded.extend(files))

    bridge._upload_asset_worker("C:/tmp/broken.txt")

    assert encoded == []
    error_call = next(call for call in js_calls if "onFilePrepareError" in call)
    event = json.loads(error_call[error_call.index("(") + 1: error_call.rindex(")")])
    assert event["path"] == "C:/tmp/broken.txt"
    assert "upload failed" in event["error"]


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


def test_bounded_media_content_read_allows_small_asset():
    bridge, gateway, _runtime = _bridge()

    result = bridge.read_asset_content(
        "asset-1",
        max_bytes=32 * 1024 * 1024,
    )

    assert result["success"] is True
    assert gateway.metadata_calls == ["asset-1"]
    assert gateway.content_calls == [("asset-1", None)]


def test_bounded_media_content_read_rejects_oversized_before_body(monkeypatch):
    bridge, gateway, _runtime = _bridge()
    monkeypatch.setattr(
        gateway,
        "asset_metadata",
        lambda asset_id: {
            "asset_id": asset_id,
            "filename": "large.mp4",
            "mime_type": "video/mp4",
            "size_bytes": (32 * 1024 * 1024) + 1,
            "state": "READY",
            "uri": f"asset://{asset_id}",
        },
    )

    result = bridge.read_asset_content(
        "asset-1",
        max_bytes=32 * 1024 * 1024,
    )

    assert result["success"] is False
    assert "bounded in-memory content limit" in result["error"]
    assert gateway.content_calls == []


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
    console_js = (root / "components/console.js").read_text(encoding="utf-8")

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
    assert "case \'file\':" in console_js
    assert "canonical-media-load" in media_block
    assert "MAX_CANONICAL_MEDIA_INLINE_BYTES" in media_block
    assert "maxBytes" in app
    assert "TextDecoder" in file_block


def test_file_queue_executes_multifile_failure_retry_and_explicit_continue(tmp_path):
    node = shutil.which("node")
    assert node is not None, (
        "Node.js is required for CAS-F6 executable UI state-machine evidence."
    )

    source_root = Path("cl/src/ui/web/js")
    harness_root = tmp_path / "cas_f6_js_harness"
    component_root = harness_root / "components"
    input_root = component_root / "inputFrame"
    input_root.mkdir(parents=True)

    shutil.copyfile(
        source_root / "components/inputFrame.js",
        component_root / "inputFrame.js",
    )
    shutil.copyfile(
        source_root / "components/inputFrame/fileManager.js",
        input_root / "fileManager.js",
    )

    (input_root / "inputLayout.js").write_text(
        "\n".join([
            "export function isElementVisible() { return true; }",
            "export function updateInputLayout() {}",
            "export function toggleExpand() {}",
            "",
        ]),
        encoding="utf-8",
    )
    (input_root / "mentionDetector.js").write_text(
        "export function handleMentionDetection() {}\n",
        encoding="utf-8",
    )
    (harness_root / "package.json").write_text(
        '{"type":"module"}\n',
        encoding="utf-8",
    )

    harness = r"""
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

class FakeClassList {
  constructor() {
    this.values = new Set();
  }
  add(...names) {
    names.forEach((name) => this.values.add(name));
  }
  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }
  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor(id = null) {
    this.id = id;
    this.listeners = {};
    this.classList = new FakeClassList();
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.parentNode = null;
    this.value = "";
    this.disabled = false;
    this.innerText = "";
    this._innerHTML = "";
    this._selectors = new Map();
  }

  addEventListener(type, callback) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(callback);
  }

  async trigger(type, event = {}) {
    const payload = {
      preventDefault() {},
      stopPropagation() {},
      ...event,
    };
    for (const callback of this.listeners[type] || []) {
      await callback(payload);
    }
  }

  dispatchEvent(event) {
    const type = event?.type || event;
    for (const callback of this.listeners[type] || []) {
      callback(event);
    }
  }

  querySelector(selector) {
    if (!this._selectors.has(selector)) {
      this._selectors.set(selector, new FakeElement());
    }
    return this._selectors.get(selector);
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter(
      (child) => child !== this,
    );
    this.parentNode = null;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    if (value === "") {
      this.children.forEach((child) => {
        child.parentNode = null;
      });
      this.children = [];
    }
  }

  get innerHTML() {
    return this._innerHTML;
  }
}

const elements = new Map();
for (const id of [
  "user-input",
  "btn-send",
  "btn-attach",
  "input-main-area",
  "btn-expand-input",
  "input-container",
  "chips-wrapper",
]) {
  elements.set(id, new FakeElement(id));
}

globalThis.document = {
  getElementById(id) {
    return elements.get(id) || null;
  },
  createElement() {
    return new FakeElement();
  },
  addEventListener() {},
};

globalThis.Event = class {
  constructor(type) {
    this.type = type;
  }
};

globalThis.alert = () => {};

const prepareCalls = [];
const legacyEncodeCalls = [];
let selectedPaths = ["C:/tmp/ready-a.txt", "C:/tmp/fail-b.txt"];

globalThis.window = {
  pywebview: {
    api: {
      async open_file_picker() {
        return selectedPaths;
      },
      async prepare_files_async(paths) {
        prepareCalls.push([...paths]);
      },
      async encode_files_async(paths) {
        legacyEncodeCalls.push([...paths]);
      },
    },
  },
  confirm() {
    return false;
  },
};

const inputFrame = await import("./components/inputFrame.js");
const fileManager = await import("./components/inputFrame/fileManager.js");

const submissions = [];
inputFrame.initInputFrame(async (text, files) => {
  submissions.push({ text, files });
});

const attachButton = elements.get("btn-attach");
const sendButton = elements.get("btn-send");
const textInput = elements.get("user-input");

await attachButton.trigger("click");
assert(
  prepareCalls.length === 1
    && JSON.stringify(prepareCalls[0]) === JSON.stringify(selectedPaths),
  "two selected files must enter canonical preparation together",
);

const readyA = {
  asset_id: "asset-a",
  source: "asset",
  uri: "asset://asset-a",
  filename: "ready-a.txt",
  mime_type: "text/plain",
};
window.onFilePrepareComplete({
  path: selectedPaths[0],
  payload: readyA,
});
window.onFilePrepareError({
  path: selectedPaths[1],
  error: "upload failed",
});

assert(
  submissions.length === 0,
  "READY+FAILED partial state must never auto-submit",
);
assert(
  JSON.stringify(fileManager.getFailedFilePaths()) ===
    JSON.stringify([selectedPaths[1]]),
  "failed file must remain in queue",
);
assert(
  fileManager.getReadyPayloads().length === 1,
  "READY payload must remain available beside a failed item",
);

window.confirm = () => false;
textInput.value = "cancelled partial send";
await sendButton.trigger("click");

assert(
  submissions.length === 0,
  "Cancel must not submit a reduced READY subset",
);
assert(
  JSON.stringify(fileManager.getFailedFilePaths()) ===
    JSON.stringify([selectedPaths[1]]),
  "Cancel must preserve the failed item for retry",
);

fileManager.retryFile(selectedPaths[1], inputFrame.updateSendButtonState);
assert(
  prepareCalls.length === 2
    && JSON.stringify(prepareCalls[1]) ===
      JSON.stringify([selectedPaths[1]]),
  "retry must invoke canonical preparation again for only the failed path",
);
assert(
  fileManager.hasFilesEncoding(),
  "retry must transition ERROR back into preparing/encoding state",
);

const readyB = {
  asset_id: "asset-b",
  source: "asset",
  uri: "asset://asset-b",
  filename: "fail-b.txt",
  mime_type: "text/plain",
};
window.onFilePrepareComplete({
  path: selectedPaths[1],
  payload: readyB,
});

assert(
  fileManager.getFailedFilePaths().length === 0,
  "successful retry must clear FAILED state",
);
assert(
  fileManager.getReadyPayloads().length === 2,
  "successful retry must reach READY alongside the original READY file",
);

textInput.value = "retry succeeded";
await sendButton.trigger("click");
assert(
  submissions.length === 1 && submissions[0].files.length === 2,
  "after retry success, submit must carry both canonical READY payloads",
);

submissions.length = 0;
fileManager.clearAllFiles();
selectedPaths = ["C:/tmp/ready-c.txt", "C:/tmp/fail-d.txt"];
await attachButton.trigger("click");

const readyC = {
  asset_id: "asset-c",
  source: "asset",
  uri: "asset://asset-c",
  filename: "ready-c.txt",
  mime_type: "text/plain",
};
window.onFilePrepareComplete({
  path: selectedPaths[0],
  payload: readyC,
});
window.onFilePrepareError({
  path: selectedPaths[1],
  error: "upload failed again",
});

assert(
  submissions.length === 0,
  "second READY+FAILED partial state must also wait for explicit user action",
);

let confirmCalls = 0;
window.confirm = () => {
  confirmCalls += 1;
  return true;
};
textInput.value = "explicit partial continue";
await sendButton.trigger("click");

assert(confirmCalls === 1, "partial continue must require explicit confirmation");
assert(submissions.length === 1, "confirmed partial continue must submit once");
assert(
  submissions[0].files.length === 1
    && submissions[0].files[0].asset_id === "asset-c",
  "confirmed partial continue must send exactly the READY canonical payload",
);
assert(
  submissions[0].files.every((item) => item.asset_id !== "asset-d"),
  "failed file must never appear in the submitted payload",
);
assert(
  legacyEncodeCalls.length === 0,
  "ONLINE failure/retry must never invoke legacy encode_files_async fallback",
);

console.log(JSON.stringify({
  ok: true,
  prepareCalls,
  legacyEncodeCalls,
}));
"""

    harness_path = harness_root / "cas_f6_state_machine.mjs"
    harness_path.write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [node, str(harness_path)],
        cwd=harness_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, (
        "CAS-F6 executable UI state-machine harness failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["ok"] is True
    assert result["legacyEncodeCalls"] == []


def test_renderer_executes_file_dispatch_text_decode_and_lazy_media(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node.js is required for CAS-F6 renderer evidence."

    source_root = Path("cl/src/ui/web/js")
    root = tmp_path / "renderer"
    components = root / "components"
    console_dir = components / "console"
    blocks = console_dir / "blocks"
    utils = root / "utils"
    blocks.mkdir(parents=True)
    utils.mkdir(parents=True)

    shutil.copyfile(source_root / "components/console.js", components / "console.js")
    shutil.copyfile(
        source_root / "components/console/blocks/fileBlock.js",
        blocks / "fileBlock.js",
    )
    shutil.copyfile(
        source_root / "components/console/blocks/mediaBlock.js",
        blocks / "mediaBlock.js",
    )

    (console_dir / "normalizer.js").write_text(
        "export function normalizeToContentParts(data) { return data.parts || [data]; }\n",
        encoding="utf-8",
    )
    (console_dir / "streamHandler.js").write_text(
        "export class StreamManager { constructor() {} flushStream() {} handleStreamChunk() {} }\n",
        encoding="utf-8",
    )
    (blocks / "textBlock.js").write_text(
        "export function createTextBlock() { return null; }\n",
        encoding="utf-8",
    )
    (blocks / "urlBlock.js").write_text(
        "export function createUrlBlock() { return null; }\n",
        encoding="utf-8",
    )
    (blocks / "imageBlock.js").write_text(
        "export function createImageBlock() { return null; }\n",
        encoding="utf-8",
    )
    (blocks / "thoughtBlock.js").write_text(
        "export function createThoughtBlock() { return null; }\n"
        "export function finishThoughtBlock() {}\n",
        encoding="utf-8",
    )
    (utils / "security.js").write_text(
        "export function escapeHtml(v) { return String(v ?? ''); }\n"
        "export function safeHttpUrl(v) { return v || null; }\n",
        encoding="utf-8",
    )
    (utils / "download.js").write_text(
        "export function triggerFileDownload(...args) { globalThis.downloadCalls.push(args); }\n",
        encoding="utf-8",
    )
    (utils / "fileIcons.js").write_text(
        "export function getFileIcon() { return 'FILE'; }\n",
        encoding="utf-8",
    )
    (components / "editor.js").write_text(
        "export function openFileInEditor(...args) { globalThis.editorCalls.push(args); }\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")

    harness = r"""
import { TextDecoder as NodeTextDecoder } from "node:util";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

globalThis.TextDecoder = globalThis.TextDecoder || NodeTextDecoder;
globalThis.atob = globalThis.atob || function(value) {
  return Buffer.from(value, "base64").toString("binary");
};

class FakeElement {
  constructor(id) {
    this.id = id || null;
    this.className = "";
    this.listeners = {};
    this.children = [];
    this.parentNode = null;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.disabled = false;
    this.innerText = "";
    this.src = "";
    this.selectors = new Map();
  }
  addEventListener(type, callback) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(callback);
  }
  async trigger(type) {
    const event = { preventDefault() {}, stopPropagation() {} };
    for (const callback of this.listeners[type] || []) {
      await callback(event);
    }
  }
  querySelector(selector) {
    if (!this.selectors.has(selector)) {
      this.selectors.set(selector, new FakeElement());
    }
    return this.selectors.get(selector);
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter(
      function(child) { return child !== this; }.bind(this)
    );
    this.parentNode = null;
  }
  load() {
    this.loadCalls = (this.loadCalls || 0) + 1;
  }
  set innerHTML(value) {
    this.html = value;
    [
      ".btn-view-file",
      ".btn-download-file",
      ".custom-audio-player",
      ".custom-video-player",
      ".canonical-media-load"
    ].forEach((selector) => {
      if (value.includes(selector.slice(1))) {
        this.selectors.set(selector, new FakeElement());
      }
    });
  }
  get innerHTML() {
    return this.html || "";
  }
}

const consoleElement = new FakeElement("console");
const documentRoot = new FakeElement("root");
documentRoot.contains = function() { return true; };

globalThis.document = {
  documentElement: documentRoot,
  getElementById(id) {
    if (id === "console") return consoleElement;
    return null;
  },
  createElement() {
    return new FakeElement();
  },
  addEventListener() {},
  querySelectorAll() {
    return [];
  }
};

globalThis.MutationObserver = class {
  observe() {}
  disconnect() {}
};
globalThis.URL = { revokeObjectURL() {} };
globalThis.editorCalls = [];
globalThis.downloadCalls = [];

const textBase64 = Buffer.from("hello ✓", "utf8").toString("base64");
let mediaResolveCalls = 0;
let mediaResolveOptions = null;
globalThis.window = {
  async resolveCanonicalAssetContent() {
    return {
      base64_data: textBase64,
      mime_type: "text/plain; charset=utf-8"
    };
  },
  async createCanonicalAssetObjectUrl(_attachment, options) {
    mediaResolveCalls += 1;
    mediaResolveOptions = options;
    return { url: "blob:canonical-media", content: { mime_type: "video/mp4" } };
  }
};

const consoleModule = await import("./components/console.js");
consoleModule.renderBlock({
  role: "user",
  type: "file",
  data: {
    attachment: {
      asset_id: "asset-text",
      source: "asset",
      uri: "asset://asset-text",
      filename: "note.txt",
      mime_type: "text/plain; charset=utf-8",
      size: 9
    }
  }
});

assert(consoleElement.children.length === 1, "file part must render");
const fileBlock = consoleElement.children[0];
assert(fileBlock.className.includes("msg-file-part"), "file must use file renderer");

await fileBlock.querySelector(".btn-view-file").trigger("click");
assert(editorCalls.length === 1, "text preview must open once");
assert(editorCalls[0][3] === "hello ✓", "text preview must decode UTF-8 base64");

await fileBlock.querySelector(".btn-download-file").trigger("click");
assert(downloadCalls.length === 1, "download must execute once");
assert(
  downloadCalls[0][1] === "base64:" + textBase64,
  "download must retain base64 transport"
);

const mediaModule = await import("./components/console/blocks/mediaBlock.js");
const mediaBlock = mediaModule.createMediaBlock("assistant", "video", {
  asset_id: "asset-video",
  source: "asset",
  uri: "asset://asset-video",
  filename: "large.mp4",
  mime_type: "video/mp4",
  size: 268435456
});

assert(mediaBlock !== null, "canonical media block must render");
assert(mediaResolveCalls === 0, "media must not resolve eagerly on render");

const loadButton = mediaBlock.querySelector(".canonical-media-load");
const player = mediaBlock.querySelector(".custom-video-player");
await loadButton.trigger("click");
assert(mediaResolveCalls === 1, "explicit load must resolve once");
assert(
  mediaResolveOptions && mediaResolveOptions.maxBytes === 33554432,
  "canonical media load must pass the fixed 32 MiB in-memory bound"
);
assert(player.src === "blob:canonical-media", "resolved media must attach on demand");
assert((player.loadCalls || 0) === 1, "player must load once");

await loadButton.trigger("click");
assert(mediaResolveCalls === 1, "repeat load must not duplicate resolution");

console.log(JSON.stringify({
  ok: true,
  editorCalls: editorCalls.length,
  downloadCalls: downloadCalls.length,
  mediaResolveCalls: mediaResolveCalls
}));
"""

    harness_path = root / "renderer.mjs"
    harness_path.write_text(harness, encoding="utf-8")
    completed = subprocess.run(
        [node, str(harness_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, (
        "CAS-F6 renderer harness failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "editorCalls": 1,
        "downloadCalls": 1,
        "mediaResolveCalls": 1,
    }
