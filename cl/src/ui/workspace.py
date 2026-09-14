import os
import shutil
import logging

logger = logging.getLogger(__name__)

class WorkspaceManager:
    def __init__(self, raw_workspace_dir):
        self._workspace_root = self._canonical_workspace_root(raw_workspace_dir)

    @staticmethod
    def _canonical_workspace_root(path: str) -> str:
        if not path:
            raise ValueError("Workspace directory không được để trống")
        root = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        if not os.path.isdir(root):
            raise NotADirectoryError(f"Workspace directory không tồn tại: {root}")
        return os.path.normcase(os.path.normpath(root))

    def _canonicalize(self, path: str, allow_missing: bool = False, reject_root: bool = False) -> str:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Path không hợp lệ")
        raw_path = os.path.expanduser(path.strip())
        candidate = raw_path if os.path.isabs(raw_path) else os.path.join(self._workspace_root, raw_path)
        canonical = os.path.normcase(os.path.normpath(os.path.realpath(os.path.abspath(candidate))))

        try:
            inside = os.path.commonpath([self._workspace_root, canonical]) == self._workspace_root
        except ValueError:
            inside = False

        if not inside:
            raise PermissionError("Path nằm ngoài workspace")
        if reject_root and canonical == self._workspace_root:
            raise PermissionError("Không thao tác phá hủy trên workspace root")
        if not allow_missing and not os.path.exists(canonical):
            raise FileNotFoundError(f"Path không tồn tại: {path}")
            
        return canonical

    def get_files(self) -> list:
        def _scan_dir(path, max_depth=2, current_depth=0):
            if current_depth > max_depth: return []
            items = []
            for entry in sorted(os.listdir(path)):
                if entry.startswith(".") or entry in {"__pycache__", "node_modules", "venv"}: continue
                full_path = os.path.join(path, entry)
                if os.path.islink(full_path): continue
                try:
                    canonical = self._canonicalize(full_path)
                    rel_path = os.path.relpath(canonical, self._workspace_root)
                    rel_path = "" if rel_path == "." else rel_path
                    if os.path.isdir(canonical):
                        items.append({"name": entry, "type": "folder", "path": canonical, "relative_path": rel_path, "children": _scan_dir(canonical, max_depth, current_depth + 1)})
                    else:
                        items.append({"name": entry, "type": "file", "path": canonical, "relative_path": rel_path})
                except Exception: continue
            return items
        return _scan_dir(self._workspace_root)

    def read_file(self, path: str) -> dict:
        try:
            canonical = self._canonicalize(path)
            with open(canonical, "r", encoding="utf-8") as f:
                return {"success": True, "path": canonical, "content": f.read()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_file(self, path: str, content: str) -> dict:
        try:
            canonical = self._canonicalize(path, allow_missing=True, reject_root=True)
            with open(canonical, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "message": "Đã lưu", "path": canonical}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def paste_item(self, action: str, path: str, target_path: str = "") -> dict:
        try:
            if action not in {"copy", "cut"}:
                return {"success": False, "error": "Action không hợp lệ"}

            # Strict P0 sandbox: both source and mutation target must stay in workspace.
            source = self._canonicalize(
                path,
                True,
            )
            if os.path.isfile(source):
                source_is_file = True
            elif os.path.isdir(source):
                source_is_file = False
            else:
                return {"success": False, "error": "Nguồn không hợp lệ"}

            if not target_path:
                target_dir = self._workspace_root
            elif os.path.isfile(target_path):
                target_dir = os.path.dirname(
                    self._canonicalize(target_path)
                )
            else:
                target_dir = self._canonicalize(target_path)

            target_dir = self._canonicalize(target_dir)

            base_name = os.path.basename(source)
            dest_path = os.path.join(target_dir, base_name)
            dest_path = self._canonicalize(dest_path,True)

            if action == "copy" and os.path.exists(dest_path):
                name, ext = os.path.splitext(base_name)
                counter = 1
                while os.path.exists(dest_path):
                    dest_path = os.path.join(target_dir, f"{name}_copy_{counter}{ext}")
                    dest_path = self._canonicalize(dest_path, True)
                    counter += 1

            if os.path.realpath(source) == os.path.realpath(dest_path):
                return {"success": True, "dest_path": dest_path}

            if action == "copy":
                if source_is_file:
                    shutil.copy2(source, dest_path)
                else:
                    # Preserve links instead of following them across filesystem boundaries.
                    shutil.copytree(source, dest_path, symlinks=True)
            else:
                shutil.move(source, dest_path)

            return {"success": True, "dest_path": dest_path}

        except Exception as e:
            logger.error("Lỗi khi %s file: %s", action, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def rename_item(self, old_path: str, new_name: str) -> dict:
        try:
            if not isinstance(new_name, str) or not new_name.strip():
                return {"success": False, "error": "Tên mới không hợp lệ"}
            if new_name in {".", ".."} or os.path.basename(new_name) != new_name:
                return {"success": False, "error": "Tên mới chứa path traversal"}

            old_canonical = self._canonicalize_workspace_path(
                old_path,
                reject_workspace_root=True,
            )
            parent_dir = self._canonicalize(os.path.dirname(old_canonical))
            new_path = self._canonicalize_workspace_path(
                os.path.join(parent_dir, new_name),
                allow_missing_leaf=True,
            )

            if os.path.exists(new_path) and old_canonical != new_path:
                return {"success": False, "error": f"Tên '{new_name}' đã tồn tại"}

            os.rename(old_canonical, new_path)
            return {"success": True, "new_path": new_path}

        except Exception as e:
            logger.error("Lỗi khi đổi tên: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def delete_item(self, path: str) -> dict:
        try:
            canonical = self._canonicalize(
                path,
                reject_workspace_root=True,
            )

            if os.path.isdir(canonical):
                shutil.rmtree(canonical)
            else:
                os.remove(canonical)

            return {"success": True}

        except Exception as e:
            logger.error("Lỗi khi xóa: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}