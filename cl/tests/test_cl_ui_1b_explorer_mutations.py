import inspect
from pathlib import Path

from cl.src.ui.bridge import UIBridgeJSFacade
from cl.src.ui.workspace import WorkspaceManager


ROOT = Path("cl/src/ui/web/js")


def test_cl_ui_1b_facade_exposes_only_bounded_workspace_mutations():
    actual = {
        name
        for name, value in inspect.getmembers(UIBridgeJSFacade, inspect.isfunction)
        if not name.startswith("_")
    }
    for name in {"get_workspace_info", "create_file", "create_folder", "paste_item"}:
        assert name in actual

    for name in {"set_window", "render_block", "execute_gateway_endpoint", "encode_files_async"}:
        assert name not in actual


def test_workspace_create_rename_delete_and_path_fences(tmp_path):
    workspace = WorkspaceManager(tmp_path)
    assert workspace.get_workspace_info() == {"name": tmp_path.name}

    folder = workspace.create_folder("", "docs")
    assert folder["success"] is True
    docs = Path(folder["path"])
    assert docs.is_dir()

    created = workspace.create_file(str(docs), "note.txt")
    assert created["success"] is True
    note = Path(created["path"])
    assert note.is_file()

    duplicate = workspace.create_file(str(docs), "note.txt")
    assert duplicate["success"] is False

    for bad_name in ("../escape.txt", ".", ".."):
        assert workspace.create_file(str(docs), bad_name)["success"] is False
        assert workspace.create_folder(str(docs), bad_name)["success"] is False

    outside = workspace.create_file(str(tmp_path.parent), "outside.txt")
    assert outside["success"] is False

    renamed = workspace.rename_item(str(note), "renamed.txt")
    assert renamed["success"] is True
    renamed_path = Path(renamed["new_path"])
    assert renamed_path.name == "renamed.txt"
    assert renamed_path.is_file()

    assert workspace.rename_item(str(renamed_path), "../escape.txt")["success"] is False
    assert workspace.delete_item(str(tmp_path))["success"] is False

    deleted = workspace.delete_item(str(renamed_path))
    assert deleted["success"] is True
    assert not renamed_path.exists()


def test_workspace_paste_rejects_directory_into_own_descendant(tmp_path):
    workspace = WorkspaceManager(tmp_path)
    source = workspace.create_folder("", "source")
    assert source["success"] is True
    child = workspace.create_folder(source["path"], "child")
    assert child["success"] is True

    for action in ("copy", "cut"):
        result = workspace.paste_item(action, source["path"], child["path"])
        assert result["success"] is False
        assert "chính nó" in result["error"] or "thư mục con" in result["error"]

    assert Path(source["path"]).is_dir()
    assert workspace.paste_item("invalid", source["path"], "")["success"] is False


def test_sidebar_workspace_mutations_are_fail_closed_and_report_results():
    sidebar = (ROOT / "components/sidebar.js").read_text(encoding="utf-8")

    assert "async function runWorkspaceMutation" in sidebar
    assert "typeof operation !== 'function'" in sidebar
    assert "result.success !== true" in sidebar
    assert "setExplorerStatus(result.error, true)" in sidebar
    assert "await ExplorerPage.load();" in sidebar

    assert "const pendingClipboard = clipboard;" in sidebar
    assert "result.success && pendingClipboard.action === 'cut'" in sidebar
    assert "clipboard = null;" in sidebar

    assert "let settled = false;" in sidebar
    assert "if (settled) return;" in sidebar
    assert "if (e.key === 'Escape') {" in sidebar
    assert "settled = true;" in sidebar
    assert "let submitted = false;" in sidebar
    assert "if (submitted) return;" in sidebar

    assert "window.pywebview.api.create_file(targetFolder, val)" not in sidebar
    assert "window.pywebview.api.create_folder(targetFolder, val)" not in sidebar
    assert "await window.pywebview.api.paste_item" not in sidebar
    assert "await window.pywebview.api.rename_file_content" not in sidebar
    assert "await window.pywebview.api.delete_file_content" not in sidebar
