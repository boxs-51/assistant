import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path("cl/src/ui/web")
JS_ROOT = ROOT / "js"


def test_cl_ui_2a_source_contract_is_bounded_and_explicit():
    bridge = Path("cl/src/ui/bridge.py").read_text(encoding="utf-8")
    app = (JS_ROOT / "app.js").read_text(encoding="utf-8")
    sidebar = (JS_ROOT / "components/sidebar.js").read_text(encoding="utf-8")
    console = (JS_ROOT / "components/console.js").read_text(encoding="utf-8")
    input_frame = (JS_ROOT / "components/inputFrame.js").read_text(encoding="utf-8")
    index = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "def get_session(self, session_id: str)" in bridge
    assert "window.onConversationBlock" in bridge
    assert "window.onConversationExecutionState" in bridge
    assert '"conversation_id": conversation_id' in bridge
    assert '"execution_id": execution_id' in bridge

    assert "submit_prompt(text, files, conversationId)" in app
    assert "window.onConversationBlock = handleConversationBlock" in app
    assert "shouldAcceptConversationEvent(payload)" in app

    assert "session?.session_id" in sidebar
    assert "session?.title ?? session?.name" in sidebar
    assert "sessionController.selectSession(sessionId)" in sidebar
    assert "btn-new-chat" in index

    assert "replaceConversationHistory" in console
    assert "Number(left.message?.sequence)" in console
    assert "hasUnsentPayload" in input_frame

    assert "execute_gateway_endpoint" not in app
    assert "fileManager.js" not in app


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for CL-UI executable frontend evidence.")
def test_conversation_store_rejects_stale_selection_and_cross_conversation_events(tmp_path):
    harness = tmp_path / "cl_ui_2a_store"
    harness.mkdir()
    (harness / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    shutil.copyfile(JS_ROOT / "state/conversationStore.js", harness / "conversationStore.js")

    script = r"""
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const store = await import("./conversationStore.js");

const a = store.beginConversationSelection("conversation-A");
const b = store.beginConversationSelection("conversation-B");

assert(!store.isCurrentSelection("conversation-A", a.generation), "A must become stale after B selection");
assert(store.isCurrentSelection("conversation-B", b.generation), "B must be the current selection");
assert(!store.markSelectionReady("conversation-A", a.generation), "stale A load must not commit");
assert(store.markSelectionReady("conversation-B", b.generation), "current B load should commit");

assert(store.setConversationExecutionState("conversation-A", "exec-A", "BUSY", 0), "background A execution should be tracked");
assert(store.setConversationExecutionState("conversation-B", "exec-B", "BUSY", 0), "active B execution should be tracked");

assert(!store.shouldAcceptConversationEvent({
  conversation_id: "conversation-A",
  execution_id: "exec-A",
  auth_generation: 0,
}), "inactive A event must not render into B");

assert(store.shouldAcceptConversationEvent({
  conversation_id: "conversation-B",
  execution_id: "exec-B",
  auth_generation: 0,
}), "active B event should be accepted");

assert(!store.setConversationExecutionState("conversation-B", "stale-exec", "TERMINAL", 0), "stale terminal must not replace active execution");
assert(store.getActiveExecutionState().state === "BUSY", "stale terminal must not end active B");
assert(store.setConversationExecutionState("conversation-B", "exec-B", "TERMINAL", 0), "matching terminal should apply");
assert(store.getActiveExecutionState().state === "TERMINAL", "matching terminal should finish B");

const firstNew = store.createNewConversation().conversationId;
const secondNew = store.createNewConversation().conversationId;
assert(firstNew !== secondNew, "New Chat must allocate a distinct conversation id");
const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
assert(uuidV4.test(firstNew) && uuidV4.test(secondNew), "New Chat ids must be UUID v4");

const beforeReset = store.getAuthGeneration();
store.resetConversationState(7);
assert(store.getActiveConversationId() === null, "identity reset must clear active conversation");
assert(store.getAuthGeneration() === 7 && beforeReset === 0, "identity reset must adopt bridge generation");
assert(!store.shouldAcceptConversationEvent({
  conversation_id: secondNew,
  execution_id: "old-exec",
  auth_generation: 0,
}), "old identity generation must be rejected");
assert(!store.setConversationExecutionState(secondNew, "old-exec", "BUSY", 0), "old generation execution must be rejected");
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script],
        cwd=harness,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_cl_ui_2a_does_not_expand_attachment_or_server_authority():
    app = (JS_ROOT / "app.js").read_text(encoding="utf-8")
    input_frame = (JS_ROOT / "components/inputFrame.js").read_text(encoding="utf-8")
    bridge = Path("cl/src/ui/bridge.py").read_text(encoding="utf-8")

    assert "prepare_files_async" not in app
    assert "getReadyPayloads()" in input_frame
    assert "getFailedFilePaths()" in input_frame
    assert "def get_session(self, session_id: str)" in bridge

    forbidden = [
        "create_file",
        "create_folder",
        "paste_item",
    ]
    facade = bridge.split("class UIBridgeJSFacade:", 1)[1].split("class UIBridge:", 1)[0]
    for name in forbidden:
        assert f"def {name}(" not in facade
