import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from cl.src.ui.bridge import UIBridgeJSFacade


ROOT = Path("cl/src/ui/web")
JS_ROOT = ROOT / "js"


def test_webview_facade_is_exact_least_privilege_allowlist():
    expected = {
        "login",
        "register",
        "verify_registration",
        "initiate_password_reset",
        "confirm_password_reset",
        "logout",
        "get_app_snapshot",
        "set_chat_preferences",
        "list_models",
        "activate_skill",
        "deactivate_skill",
        "execute_tool",
        "save_agent",
        "run_agent",
        "get_agent_task_status",
        "cancel_agent_task",
        "read_asset_content",
        "submit_prompt",
        "get_sessions",
        "get_session",
        "prepare_files_async",
        "respond_approval",
        "get_workspace_info",
        "get_workspace_files",
        "create_file",
        "create_folder",
        "paste_item",
        "read_file_content",
        "save_file_content",
        "rename_file_content",
        "delete_file_content",
        "open_file_picker",
    }
    actual = {
        name
        for name, value in inspect.getmembers(UIBridgeJSFacade, inspect.isfunction)
        if not name.startswith("_")
    }
    assert actual == expected
    assert "set_window" not in actual
    assert "render_block" not in actual
    assert "execute_gateway_endpoint" not in actual
    assert "encode_files_async" not in actual


def test_privileged_page_has_no_remote_script_style_or_font_dependencies():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    lowered = index.lower()
    assert "https://" not in lowered
    assert "http://" not in lowered
    assert "cdnjs" not in lowered
    assert "fonts.googleapis" not in lowered
    assert "marked.min.js" not in lowered
    assert "highlight.min.js" not in lowered


def test_main_uses_facade_and_debug_is_opt_in():
    source = Path("cl/src/main.py").read_text(encoding="utf-8")
    assert "UIBridgeJSFacade(api)" in source
    assert "js_api=js_api" in source
    assert 'os.getenv("CL_WEBVIEW_DEBUG", "")' in source
    assert "webview.start(debug=debug_enabled)" in source
    assert "webview.start(debug=True)" not in source


def test_dynamic_privileged_dom_paths_do_not_interpolate_untrusted_html():
    approval = (JS_ROOT / "components/approvalBar.js").read_text(encoding="utf-8")
    sidebar = (JS_ROOT / "components/sidebar.js").read_text(encoding="utf-8")
    file_manager = (JS_ROOT / "components/inputFrame/fileManager.js").read_text(encoding="utf-8")
    citations = (JS_ROOT / "components/console/blocks/citationBlock.js").read_text(encoding="utf-8")
    sanitizer = (JS_ROOT / "utils/sanitizer.js").read_text(encoding="utf-8")

    assert "alertDiv.innerHTML = req.content" not in approval
    assert "fileListDiv.innerHTML" not in approval
    assert "await responder(choice, activeApprovalId)" in approval
    assert "hideApprovalBar(approvalId)" in approval

    assert 'value="${currentName}"' not in sidebar
    assert '>${item.name}</span>' not in sidebar
    assert '>${session.name' not in sidebar

    assert 'title="${fileName}"' not in file_manager
    assert '>${fileName}</span>' not in file_manager

    assert "bodyElem.innerHTML.replace" not in citations
    assert "normalizeCitationSourceType" in citations
    assert "ALLOWED_TAGS" in sanitizer
    assert "from '../config.js'" not in sanitizer


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for CL-UI executable frontend evidence.")
def test_local_markdown_renderer_and_approval_surface_are_inert(tmp_path):
    harness = tmp_path / "cl_ui_1a"
    harness.mkdir()
    (harness / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    shutil.copyfile(JS_ROOT / "utils/security.js", harness / "security.js")

    sanitizer_source = (JS_ROOT / "utils/sanitizer.js").read_text(encoding="utf-8")
    sanitizer_source = sanitizer_source.replace("./security.js", "./security.js")
    (harness / "sanitizer.js").write_text(sanitizer_source, encoding="utf-8")
    shutil.copyfile(JS_ROOT / "components/approvalBar.js", harness / "approvalBar.js")

    script = r"""
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

globalThis.window = {
  location: { href: "file:///app/index.html" },
};

const { renderMarkdownSafe } = await import("./sanitizer.js");
const payload = '<img src=x onerror="window.pwned=1"><span id="approval-bar" style="position:fixed">spoof</span> [bad](javascript:alert(1)) [safe](https://example.com/a)';
const html = renderMarkdownSafe(payload);
assert(!html.includes("<img"), "raw img markup must not survive");
assert(!/<[^>]+\sonerror\s*=/i.test(html), "event handler attributes must not survive as markup");
assert(!html.includes('id="approval-bar"'), "privileged ids must not survive");
assert(!html.includes("javascript:"), "javascript URLs must not survive");
assert(html.includes('target="_blank"'), "safe links must open outside the privileged page");
assert(html.includes('rel="noopener noreferrer"'), "safe links must be isolated");

class ClassList {
  constructor() { this.values = new Set(); }
  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}

class FakeElement {
  constructor(id = null) {
    this.id = id;
    this.className = "";
    this.classList = new ClassList();
    this.style = {};
    this.children = [];
    this.textContent = "";
    this.disabled = false;
    this.listeners = {};
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = [...children]; }
  async trigger(type) {
    const result = this.listeners[type]?.({ preventDefault() {}, stopPropagation() {} });
    if (result?.then) await result;
  }
}

const ids = [
  "btn-approve", "btn-reject", "approval-bar", "approval-badge",
  "approval-title", "approval-target", "approval-custom-content",
  "approval-message", "approval-actions",
];
const elements = new Map(ids.map(id => [id, new FakeElement(id)]));

globalThis.document = {
  getElementById(id) { return elements.get(id) || null; },
  createElement() { return new FakeElement(); },
};

const calls = [];
window.pywebview = {
  api: {
    async respond_approval(choice, approvalId) {
      calls.push([choice, approvalId]);
      return true;
    },
  },
};

const approval = await import("./approvalBar.js");
approval.initApprovalBar();
approval.showApprovalBar({
  approval_id: "approval-A",
  mode: "alert",
  content: '<img src=x onerror="window.pwned=1">',
  reason: '<b id="approval-bar">reason</b>',
});
const content = elements.get("approval-custom-content").children[0];
assert(content.textContent.includes("<img"), "approval content must be rendered as inert text");
assert(elements.get("approval-message").textContent.includes("<b"), "approval reason must be inert text");

await elements.get("btn-approve").trigger("click");
await new Promise(resolve => setTimeout(resolve, 0));
assert(calls.length === 1, "one approval response expected");
assert(calls[0][0] === true && calls[0][1] === "approval-A", "exact approval id must round-trip");
assert(elements.get("btn-approve").disabled === true, "decision buttons must enter pending state");

assert(approval.hideApprovalBar("stale-id") === false, "stale hide must be rejected");
assert(!elements.get("approval-bar").classList.contains("hidden"), "stale hide must not hide active approval");
assert(approval.hideApprovalBar("approval-A") === true, "matching hide must succeed");
assert(elements.get("approval-bar").classList.contains("hidden"), "matching approval must be hidden");
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script],
        cwd=harness,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for CL-UI executable frontend evidence.")
def test_untrusted_source_renderers_are_executable_and_inert(tmp_path):
    harness = tmp_path / "cl_ui_1a_sources"
    harness.mkdir()
    (harness / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")

    shutil.copyfile(JS_ROOT / "utils/security.js", harness / "security.js")

    sanitizer_source = (JS_ROOT / "utils/sanitizer.js").read_text(encoding="utf-8")
    (harness / "sanitizer.js").write_text(
        sanitizer_source.replace("./security.js", "./security.js"),
        encoding="utf-8",
    )

    gateway_source = (JS_ROOT / "components/gatewayPanel.js").read_text(encoding="utf-8")
    gateway_source = gateway_source.replace("../utils/security.js", "./security.js")
    gateway_source += "\nexport { renderSkills as __testRenderSkills, state as __testGatewayState };\n"
    (harness / "gatewayPanel.js").write_text(gateway_source, encoding="utf-8")

    citation_source = (
        JS_ROOT / "components/console/blocks/citationBlock.js"
    ).read_text(encoding="utf-8")
    citation_source = citation_source.replace("../../../utils/security.js", "./security.js")
    citation_source = citation_source.replace("../../../utils/sanitizer.js", "./sanitizer.js")
    citation_source = citation_source.replace("./textBlock.js", "./textBlock.js")
    (harness / "citationBlock.js").write_text(citation_source, encoding="utf-8")
    (harness / "textBlock.js").write_text(
        "export function enhanceCodeBlocks() {}\n",
        encoding="utf-8",
    )

    sidebar_source = (JS_ROOT / "components/sidebar.js").read_text(encoding="utf-8")
    sidebar_source = sidebar_source.replace("./editor.js", "./editor.js")
    sidebar_source = sidebar_source.replace("../utils/fileIcons.js", "./fileIcons.js")
    (harness / "sidebar.js").write_text(sidebar_source, encoding="utf-8")
    (harness / "editor.js").write_text(
        "export function openFileInEditor() {}\n",
        encoding="utf-8",
    )
    (harness / "fileIcons.js").write_text(
        "export function getFileIcon() { return 'FILE'; }\n",
        encoding="utf-8",
    )

    shutil.copyfile(
        JS_ROOT / "components/inputFrame/fileManager.js",
        harness / "fileManager.js",
    )

    script = r"""
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

class ClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach(name => this.values.add(name)); }
  remove(...names) { names.forEach(name => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    if (force === true) { this.values.add(name); return true; }
    if (force === false) { this.values.delete(name); return false; }
    if (this.values.has(name)) { this.values.delete(name); return false; }
    this.values.add(name); return true;
  }
}

let lastReplacement = null;

class FakeElement {
  constructor(tagName = 'div', id = null) {
    this.tagName = String(tagName).toUpperCase();
    this.id = id;
    this.nodeType = 1;
    this.className = '';
    this.classList = new ClassList();
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.childNodes = this.children;
    this.listeners = {};
    this._queries = new Map();
    this._textContent = '';
    this._innerHTML = '';
    this.disabled = false;
    this.parentNode = null;
  }
  set textContent(value) {
    this._textContent = String(value ?? '');
    this.children = [];
    this.childNodes = this.children;
  }
  get textContent() {
    if (this._textContent) return this._textContent;
    return this.children.map(child => child.textContent || child.nodeValue || '').join('');
  }
  set innerText(value) { this.textContent = value; }
  get innerText() { return this.textContent; }
  set innerHTML(value) { this._innerHTML = String(value ?? ''); }
  get innerHTML() { return this._innerHTML; }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    this.childNodes = this.children;
    return child;
  }
  prepend(child) {
    child.parentNode = this;
    this.children.unshift(child);
    this.childNodes = this.children;
    return child;
  }
  replaceChildren(...children) {
    this.children = [];
    this.childNodes = this.children;
    children.forEach(child => this.appendChild(child));
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  querySelector(selector) {
    if (!this._queries.has(selector)) {
      const tag = selector.includes('input') ? 'input'
        : selector.includes('button') ? 'button'
        : selector.includes('strong') ? 'strong'
        : 'span';
      this._queries.set(selector, new FakeElement(tag));
    }
    return this._queries.get(selector);
  }
  querySelectorAll(_selector) { return []; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  matches(selector) {
    return selector.split(',').some(part => {
      const token = part.trim();
      if (!token) return false;
      if (token.startsWith('.')) return this.className.split(/\s+/).includes(token.slice(1));
      return this.tagName.toLowerCase() === token.toLowerCase();
    });
  }
  closest(_selector) { return null; }
  focus() {}
  select() {}
  remove() {}
}

class FakeText {
  constructor(value) {
    this.nodeType = 3;
    this.nodeValue = String(value);
    this.parentNode = null;
  }
  get textContent() { return this.nodeValue; }
  replaceWith(fragment) { lastReplacement = fragment; }
}

class FakeFragment extends FakeElement {
  constructor() { super('fragment'); this.nodeType = 11; }
}

const elements = new Map();
const getElement = id => {
  if (!elements.has(id)) elements.set(id, new FakeElement('div', id));
  return elements.get(id);
};

globalThis.window = {
  location: { href: 'file:///app/index.html' },
  pywebview: { api: {} },
};
globalThis.confirm = () => false;
globalThis.document = {
  body: new FakeElement('body'),
  documentElement: new FakeElement('html'),
  getElementById(id) { return getElement(id); },
  createElement(tag) { return new FakeElement(tag); },
  createTextNode(value) { return new FakeText(value); },
  createDocumentFragment() { return new FakeFragment(); },
  querySelectorAll(_selector) { return []; },
  querySelector(_selector) { return null; },
  addEventListener() {},
};

// Gateway rendering: execute the actual production renderSkills() body.
const gateway = await import('./gatewayPanel.js');
const skillsView = new FakeElement('div');
const gatewayPanel = {
  querySelector(selector) {
    if (selector === '[data-view="skills"]') return skillsView;
    return new FakeElement('div');
  },
};

const renderRisk = rawRisk => {
  gateway.__testGatewayState.snapshot = {
    skills: [],
    capabilities: [{
      kind: 'SKILL',
      capability_id: 'server-skill',
      definition: {
        name: 'server-skill',
        description: '<img src=x onerror="window.pwned=1">',
        metadata: { base_risk: rawRisk },
      },
    }],
  };
  gateway.__testRenderSkills(gatewayPanel);
  return skillsView.innerHTML;
};

for (const [raw, expected] of [
  ['LOW', 'low'],
  ['MEDIUM', 'medium'],
  ['HIGH', 'high'],
  ['CRITICAL', 'critical'],
]) {
  const html = renderRisk(raw);
  assert(html.includes('class="risk ' + expected + '"'), raw + ' must map to exactly ' + expected);
  assert(html.includes('>' + expected.toUpperCase() + '</span>'), raw + ' label must be canonical');
}

for (const raw of [
  'high approval-bar bar-high',
  'critical hidden',
  'high" onmouseover="window.pwned=1',
  '<img src=x onerror="window.pwned=1">',
  '',
]) {
  const html = renderRisk(raw);
  assert(html.includes('class="risk medium"'), 'malformed risk must use medium fallback: ' + raw);
  assert(!html.includes('approval-bar'), 'risk payload must not inject approval-bar');
  assert(!html.includes('bar-high'), 'risk payload must not inject bar-high');
  assert(!html.includes('class="risk critical hidden"'), 'risk payload must not inject hidden');
  assert(!html.includes('onmouseover='), 'risk payload must not inject event attributes');
  assert(!html.includes('<img src=x'), 'risk payload must not inject DOM');
  assert(html.includes('>MEDIUM</span>'), 'malformed risk label must be canonical MEDIUM');
}

// Citation rendering: execute appendCitationsToBlock on real citationBlock.js.
const citations = await import('./citationBlock.js');

const citationBlock = citation => {
  lastReplacement = null;
  const body = new FakeElement('div');
  body.childNodes = [new FakeText('[1]')];
  body.children = body.childNodes;
  const block = new FakeElement('div');
  block.dataset = {};
  block.querySelector = selector => selector === '.msg-body' ? body : null;
  block.addEventListener = () => {};
  citations.appendCitationsToBlock(block, [citation]);
  assert(lastReplacement, 'citation marker must be replaced');
  return lastReplacement.children.find(child => child.nodeType === 1);
};

const maliciousCitation = citationBlock({
  index: 1,
  source_type: 'web approval-bar bar-high',
  title: '<img src=x onerror="window.pwned=1">',
  snippet: '" onmouseover="window.pwned=1',
  url: 'https://example.com/safe',
});
assert(maliciousCitation.className === 'citation-chip citation-web', 'citation class must be normalized');
assert(maliciousCitation.target === '_blank', 'safe citation must open outside privileged page');
assert(maliciousCitation.rel === 'noopener noreferrer', 'safe citation must isolate opener');
assert(maliciousCitation.href === 'https://example.com/safe', 'safe citation href must be canonical');
assert(!maliciousCitation.className.includes('approval-bar'), 'citation must not inherit arbitrary class tokens');
assert(maliciousCitation.children.some(child => child.textContent.includes('<img')), 'citation title must remain inert text');

for (const unsafeUrl of [
  'javascript:alert(1)',
  'data:text/html,<script>alert(1)</script>',
  'blob:https://example.com/evil',
]) {
  const element = citationBlock({
    index: 1,
    source_type: 'web hidden approval-bar',
    title: '<b onclick="window.pwned=1">unsafe</b>',
    url: unsafeUrl,
  });
  assert(element.tagName === 'SPAN', unsafeUrl + ' must not render as a link');
  assert(element.className === 'citation-chip citation-file', unsafeUrl + ' must use inert citation class');
  assert(!element.href, unsafeUrl + ' must not retain href');
}

// Sidebar: execute actual SessionsPage and ExplorerPage rendering.
const sidebar = await import('./sidebar.js');

const sessionParent = new FakeElement('div');
const sessionPayload = '<img src=x onerror="window.pwned=1"> approval-bar';
sidebar.SessionsPage.renderSessions([{ name: sessionPayload }], sessionParent);
const sessionEl = sessionParent.children[0];
assert(sessionEl.querySelector('.session-title').textContent === sessionPayload, 'session name must remain text');
assert(!sessionEl.innerHTML.includes(sessionPayload), 'session payload must not be interpolated into markup');

const workspacePayload = '<img src=x onerror="window.pwned=1"> workspace';
const filePayload = '<svg onload="window.pwned=1">.txt';
const folderPayload = '<div class="approval-bar">folder</div>';
window.pywebview.api.get_workspace_info = async () => ({ name: workspacePayload });
window.pywebview.api.get_workspace_files = async () => ([
  { type: 'folder', name: folderPayload, path: 'folder', children: [] },
  { type: 'file', name: filePayload, path: 'file.txt' },
]);

const explorerTree = getElement('explorer-tree');
const explorerPage = getElement('page-explorer');
explorerPage.addEventListener = () => {};
await sidebar.ExplorerPage.load();

const rootEl = explorerTree.children[0];
assert(rootEl.querySelector('.tree-item-name strong').textContent === workspacePayload, 'workspace root must remain text');
assert(!rootEl.innerHTML.includes(workspacePayload), 'workspace root payload must not be markup');

const rootChildren = explorerTree.children[1];
const renderedItems = rootChildren.children.filter(child => child.className?.includes('tree-item'));
const folderEl = renderedItems.find(child => child.className.includes('folder'));
const fileEl = renderedItems.find(child => child.className.includes('file'));
assert(folderEl.querySelector('.tree-item-name').textContent === folderPayload, 'folder name must remain text');
assert(fileEl.querySelector('.tree-item-name').textContent === filePayload, 'file name must remain text');
assert(!folderEl.innerHTML.includes(folderPayload), 'folder payload must not be interpolated into markup');
assert(!fileEl.innerHTML.includes(filePayload), 'file payload must not be interpolated into markup');

// Attachment chip: execute actual addFilesToQueue().
const fileManager = await import('./fileManager.js');
const chips = getElement('chips-wrapper');
window.pywebview.api.prepare_files_async = async () => ({ success: true });
const attachmentPayload = '<img src=x onerror="window.pwned=1">.txt';
fileManager.addFilesToQueue(['/tmp/' + attachmentPayload]);
const chip = chips.children[0];
const chipName = chip.querySelector('.chip-name');
assert(chipName.textContent === attachmentPayload, 'attachment filename must remain text');
assert(chipName.title === attachmentPayload, 'attachment title must use DOM property');
assert(!chip.innerHTML.includes(attachmentPayload), 'attachment payload must not be interpolated into markup');

// Markdown external URL schemes: execute actual local renderer.
const sanitizer = await import('./sanitizer.js');
const linkHtml = sanitizer.renderMarkdownSafe(
  '[safe](https://example.com/a) [js](javascript:alert(1)) [data](data:text/html,boom) [blob](blob:https://example.com/id)'
);
assert(linkHtml.includes('href="https://example.com/a"'), 'safe HTTP(S) link must survive');
assert(linkHtml.includes('target="_blank"'), 'safe HTTP(S) link must open externally');
assert(linkHtml.includes('rel="noopener noreferrer"'), 'safe HTTP(S) link must isolate opener');
assert(!linkHtml.includes('javascript:'), 'javascript link must be rejected');
assert(!linkHtml.includes('data:text/html'), 'data link must be rejected');
assert(!linkHtml.includes('blob:https://'), 'blob external link must be rejected');
"""

    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script],
        cwd=harness,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
