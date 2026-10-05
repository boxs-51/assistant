import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path("cl/src/ui/web/js")


def test_cl_ui_2b_a_source_freezes_text_draft_ownership_without_attachment_expansion():
    store = (ROOT / "state/conversationStore.js").read_text(encoding="utf-8")
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    input_frame = (ROOT / "components/inputFrame.js").read_text(encoding="utf-8")
    file_manager = (ROOT / "components/inputFrame/fileManager.js").read_text(encoding="utf-8")

    assert "draftText: ''" in store
    assert "setConversationDraftText" in store
    assert "getConversationDraftText" in store
    assert "restoreConversationDraftAfterSubmitFailure" in store
    assert "conversations.clear();" in store

    assert "persistActiveTextDraft();" in app
    assert "restoreConversationTextDraft(sessionId);" in app
    assert "setTextDraft('');" in app
    assert "return !hasUnsentAttachments();" in app
    assert "setConversationDraftText(conversationId, text);" in app
    assert "setConversationDraftText(conversationId, '');" in app
    assert "restoreConversationDraftAfterSubmitFailure(" in app
    assert "if (restoreVisibleText)" in app

    assert "export function getTextDraft()" in input_frame
    assert "export function setTextDraft" in input_frame
    assert "export function hasUnsentText()" in input_frame
    assert "export function hasUnsentAttachments()" in input_frame
    assert "tx.value = currentText;" not in input_frame

    # 2B-A is intentionally text-only. Attachment ownership/callback identity
    # remains untouched for the separately gated 2B-B stage.
    assert "const attachedFilesMap = new Map();" in file_manager
    assert "onFilePrepareComplete" in file_manager
    assert "conversation_id" not in file_manager


def test_cl_ui_2b_a_conversation_store_executes_independent_text_drafts_and_auth_reset(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node.js is required for CL-UI-2B-A executable evidence."

    source = ROOT / "state/conversationStore.js"
    harness_root = tmp_path / "cl_ui_2b_a"
    harness_root.mkdir()
    target = harness_root / "conversationStore.mjs"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    harness = r"""
import assert from "node:assert/strict";
import {
  beginConversationSelection,
  createNewConversation,
  getConversationDraftText,
  resetConversationState,
  restoreConversationDraftAfterSubmitFailure,
  setConversationDraftText,
} from "./conversationStore.mjs";

beginConversationSelection("conversation-a");
setConversationDraftText("conversation-a", "draft A");

beginConversationSelection("conversation-b");
setConversationDraftText("conversation-b", "draft B");

assert.equal(getConversationDraftText("conversation-a"), "draft A");
assert.equal(getConversationDraftText("conversation-b"), "draft B");

let visibleDraft = getConversationDraftText("conversation-b");
const restoreInactive = restoreConversationDraftAfterSubmitFailure(
  "conversation-a",
  "failed draft A",
);
if (restoreInactive) visibleDraft = "failed draft A";
assert.equal(restoreInactive, false);
assert.equal(visibleDraft, "draft B");
assert.equal(getConversationDraftText("conversation-a"), "failed draft A");
assert.equal(getConversationDraftText("conversation-b"), "draft B");

beginConversationSelection("conversation-a");
const restoreActive = restoreConversationDraftAfterSubmitFailure(
  "conversation-a",
  "active failed draft A",
);
assert.equal(restoreActive, true);
assert.equal(getConversationDraftText("conversation-a"), "active failed draft A");

const created = createNewConversation();
assert.equal(getConversationDraftText(created.conversationId), "");
setConversationDraftText(created.conversationId, "new draft");
assert.equal(getConversationDraftText(created.conversationId), "new draft");

resetConversationState(7);
assert.equal(getConversationDraftText("conversation-a"), "");
assert.equal(getConversationDraftText("conversation-b"), "");
assert.equal(getConversationDraftText(created.conversationId), "");

console.log(JSON.stringify({ ok: true }));
"""
    harness_path = harness_root / "harness.mjs"
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
        "CL-UI-2B-A executable draft-store evidence failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {"ok": True}


def test_cl_ui_2b_a_switch_contract_keeps_attachment_queue_as_guard():
    app = (ROOT / "app.js").read_text(encoding="utf-8")

    persist_index = app.index("persistActiveTextDraft();")
    select_index = app.index("const selection = beginConversationSelection(sessionId);")
    assert persist_index < select_index

    assert "return !hasUnsentAttachments();" in app
    assert "hasUnsentPayload" not in app
    assert "fileManager.js" not in app

    # Load failure may restore only the selected conversation's own draft;
    # it must never restore the outgoing conversation text implicitly.
    error_block = app.split("} catch (error) {", 1)[1]
    assert "restoreConversationTextDraft(sessionId);" in error_block
