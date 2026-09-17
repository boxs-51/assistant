import os
import re
import tempfile
import difflib
from itertools import islice
from pathlib import Path
from typing import List, Optional, Tuple, Union, Callable, Dict, Any

TOOL_METADATA = {
    "name": "file_tool",
    "description": "Thực hiện các thao tác quản lý tệp tin local bao gồm Đọc (read), Ghi (write), Tìm kiếm (search), và Thay thế (replace).",
    "base_risk": "HIGH",
    "effects": ["READ", "WRITE"],
    "danger_patterns": [
        r"\.env$",
        r"\.pem$",
        r"id_rsa",
        r"config/AGENT\.md$",
        r"\.bashrc$",
        r"\.zshrc$",
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "search", "replace"],
                "description": "Hành động cần thực hiện: 'read', 'write', 'search', 'replace'.",
            },
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Đường dẫn tới một hoặc nhiều tệp tin cần xử lý (Có thể truyền 1 string đơn lẻ hoặc danh sách).",
            },
            "content": {
                "type": "string",
                "description": "Nội dung văn bản cần ghi vào file (Khi action='write').",
            },
            "mode": {
                "type": "string",
                "enum": ["w", "a"],
                "description": "'w' (ghi đè), 'a' (nối thêm). Mặc định là 'w'.",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách các chuỗi/regex cần tìm kiếm hoặc thay thế.",
            },
            "replacements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách các chuỗi thay thế tương ứng với 'queries'.",
            },
            "encoding": {"type": "string", "description": "Bảng mã mã hóa file (Mặc định: 'utf-8')."},
            "start_line": {"type": "integer", "description": "Dòng bắt đầu đọc (tính từ 1)."},
            "num_lines": {"type": "integer", "description": "Số lượng dòng cần đọc."},
            "use_regex": {"type": "boolean", "description": "Bật/tắt Regex (Mặc định: false)."},
            "case_sensitive": {"type": "boolean", "description": "Bật/tắt phân biệt hoa/thường (Mặc định: false)."},
            "max_results_per_file": {"type": "integer", "description": "Giới hạn số lượng kết quả tối đa."},
        },
        "required": ["action"],
    },
}


class FileTool:
    """Class quản lý các thao tác với tệp tin (Đọc, Ghi, Tìm kiếm, Thay thế)."""

    def __init__(
        self, 
        default_encoding: str = "utf-8",
        confirm_callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
        danger_patterns: Optional[List[str]] = None
    ):
        self.default_encoding = default_encoding
        self.confirm_callback = confirm_callback
        self.danger_patterns = danger_patterns or [
            r"\.env$", r"id_rsa", r"\.pem$", r"\.bashrc$", r"\.zshrc$"
        ]

    def _is_dangerous_path(self, file_path: str) -> bool:
        """Kiểm tra đường dẫn có khớp với các pattern nguy hiểm không."""
        path_str = str(Path(file_path).as_posix())
        return any(re.search(pattern, path_str, re.IGNORECASE) for pattern in self.danger_patterns)

    def _generate_diff(self, old_text: str, new_text: str, file_path: str) -> str:
        """Tạo chuỗi Unified Diff chuẩn giống Git."""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm=""
        )
        return "".join(diff)

    def _request_confirmation(
        self,
        action: str,
        file_path: str,
        old_content: str,
        new_content: str
    ) -> bool:
        """Gọi callback xác nhận trước khi thực hiện thay đổi."""
        if not self.confirm_callback:
            return True

        diff_str = self._generate_diff(old_content, new_content, file_path)

        preview_info = {
            "action": action,
            "file_path": file_path,
            "old_content": old_content,
            "new_content": new_content,
            "diff": diff_str,
            "has_changes": old_content != new_content,
            "is_dangerous": self._is_dangerous_path(file_path)
        }

        return self.confirm_callback(preview_info)

    def _atomic_write(self, path: Path, content: str, encoding: str = "utf-8"):
        """Ghi file an toàn (Atomic Write)."""
        dir_name = path.parent
        dir_name.mkdir(parents=True, exist_ok=True)
        
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding=encoding, errors="replace") as tf:
            tf.write(content)
            temp_name = tf.name

        os.replace(temp_name, path)

    def read(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        num_lines: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """Đọc nội dung của một file."""
        enc = encoding or self.default_encoding
        path = Path(file_path)

        if not path.exists():
            return f"Lỗi: File '{path}' không tồn tại."
        if not path.is_file():
            return f"Lỗi: Đường dẫn '{path}' là thư mục, không phải file."
        if start_line is not None and start_line < 1:
            return "Lỗi: 'start_line' phải lớn hơn hoặc bằng 1."
        if num_lines is not None and num_lines < 0:
            return "Lỗi: 'num_lines' không được là số âm."

        try:
            with path.open("r", encoding=enc, errors="replace") as f:
                if start_line is None and num_lines is None:
                    return f.read()

                start = (start_line - 1) if start_line else 0
                stop = (start + num_lines) if num_lines is not None else None
                lines = islice(f, start, stop)
                return "".join(lines)
        except Exception as e:
            return f"Lỗi khi đọc file '{path}': {str(e)}"

    def write(
        self,
        file_path: str,
        content: str,
        mode: str = "w",
        encoding: Optional[str] = None,
    ) -> str:
        """Ghi hoặc nối thêm nội dung vào file."""
        if content is None:
            return "Lỗi: Cần cung cấp 'content' cho thao tác ghi file."
        if mode not in ("w", "a"):
            return "Lỗi: mode phải là 'w' (overwrite) hoặc 'a' (append)."

        enc = encoding or self.default_encoding
        path = Path(file_path)

        old_content = ""
        if path.exists():
            try:
                old_content = path.read_text(encoding=enc, errors="replace")
            except Exception:
                old_content = ""

        new_content = (old_content + content) if mode == "a" else content

        if old_content == new_content:
            return "Bỏ qua: Nội dung mới không có thay đổi so với nội dung hiện tại."

        try:
            if not self._request_confirmation("write", file_path, old_content, new_content):
                return "Đã hủy: Người dùng từ chối áp dụng thay đổi."

            self._atomic_write(path, new_content, encoding=enc)
            return f"Thành công: Đã ghi file '{file_path}' (chế độ: '{mode}')."
        
        except Exception as e:
            return f"Lỗi khi ghi file '{file_path}': {str(e)}"

    def _compile_patterns(
        self,
        queries: List[str],
        replacements: Optional[List[str]] = None,
        use_regex: bool = False,
        case_sensitive: bool = False,
    ) -> Union[List[Tuple[re.Pattern, Optional[str], str]], str]:
        """Biên dịch danh sách truy vấn thành Pattern Regex."""
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = []

        for idx, q in enumerate(queries):
            rep = replacements[idx] if replacements else None
            try:
                pattern = re.compile(q if use_regex else re.escape(q), flags)
                compiled.append((pattern, rep, q))
            except re.error as e:
                return f"Lỗi biểu thức chính quy (regex) tại query '{q}': {str(e)}"

        return compiled

    def search(
        self,
        file_paths: Union[str, List[str]],
        queries: Union[str, List[str]],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        return self._search_or_replace(
            file_paths=file_paths,
            queries=queries,
            is_replace=False,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_results_per_file=max_results_per_file,
            encoding=encoding,
        )

    def replace(
        self,
        file_paths: Union[str, List[str]],
        queries: Union[str, List[str]],
        replacements: Union[str, List[str]],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        return self._search_or_replace(
            file_paths=file_paths,
            queries=queries,
            replacements=replacements,
            is_replace=True,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_results_per_file=max_results_per_file,
            encoding=encoding,
        )

    def _search_or_replace(
        self,
        file_paths: Union[str, List[str]],
        queries: Union[str, List[str]],
        replacements: Optional[Union[str, List[str]]] = None,
        is_replace: bool = False,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        if not queries:
            action_name = "replace" if is_replace else "search"
            return f"Lỗi: Cần cung cấp 'queries' cho action '{action_name}'."

        paths_list = [file_paths] if isinstance(file_paths, str) else file_paths
        queries_list = [queries] if isinstance(queries, str) else queries

        if any(q == "" for q in queries_list):
            return "Lỗi: 'queries' không được chứa chuỗi rỗng."

        replacements_list: Optional[List[str]] = None
        if is_replace:
            if replacements is None:
                return "Lỗi: Action 'replace' yêu cầu phải truyền 'replacements'."
            if isinstance(replacements, str):
                replacements_list = [replacements] * len(queries_list)
            elif isinstance(replacements, list):
                if len(replacements) != len(queries_list):
                    return (
                        f"Lỗi: Số lượng 'replacements' ({len(replacements)}) "
                        f"không khớp với 'queries' ({len(queries_list)})."
                    )
                replacements_list = replacements

        compiled_pairs = self._compile_patterns(
            queries_list, replacements_list, use_regex, case_sensitive
        )
        if isinstance(compiled_pairs, str):
            return compiled_pairs

        enc = encoding or self.default_encoding
        report_output = []

        for file_str in paths_list:
            path = Path(file_str)
            if not path.exists():
                report_output.append(f"❌ File '{file_str}': Không tồn tại.")
                continue
            if not path.is_file():
                report_output.append(f"❌ Đường dẫn '{file_str}': Là thư mục, không phải file.")
                continue

            try:
                with path.open("r", encoding=enc, errors="replace") as f:
                    lines = f.readlines()

                file_matches = []
                modified_lines = lines.copy() if is_replace else []
                match_count = 0

                for line_idx, line in enumerate(lines):
                    line_num = line_idx + 1
                    current_line = line
                    line_modified = False

                    for pattern, rep, original_q in compiled_pairs:
                        if max_results_per_file and match_count >= max_results_per_file:
                            break

                        if pattern.search(current_line):
                            match_count += 1
                            if is_replace:
                                old_text = current_line.rstrip("\r\n")
                                new_line = (
                                    pattern.sub(rep, current_line)
                                    if use_regex
                                    else pattern.sub(lambda _: rep, current_line)
                                )
                                new_text = new_line.rstrip("\r\n")
                                file_matches.append(
                                    f"   - [Dòng {line_num}] Tìm '{original_q}': '{old_text}' ➔ '{new_text}'"
                                )
                                current_line = new_line
                            else:
                                file_matches.append(
                                    f"   - [Dòng {line_num}]: {current_line.rstrip('\r\n')}"
                                )
                            line_modified = True

                    if is_replace and line_modified:
                        modified_lines[line_idx] = current_line

                    if max_results_per_file and match_count >= max_results_per_file:
                        break

                # Thực hiện thay thế có xác nhận
                if is_replace and match_count > 0:
                    old_full_text = "".join(lines)
                    new_full_text = "".join(modified_lines)
                    
                    if not self._request_confirmation("replace", file_str, old_full_text, new_full_text):
                        report_output.append(f"⚠️ File '{file_str}': Người dùng đã hủy thay đổi.")
                        continue

                    self._atomic_write(path, new_full_text, encoding=enc)

                # Tổng hợp báo cáo
                if file_matches:
                    status_suffix = " [ĐÃ CẬP NHẬT]" if is_replace else ""
                    header = f"📄 File: {file_str}{status_suffix}"
                    report_output.append(header)
                    report_output.extend(file_matches)
                else:
                    report_output.append(f"📄 File: {file_str} - Không tìm thấy kết quả phù hợp.")

            except Exception as e:
                report_output.append(f"❌ Lỗi khi xử lý file '{file_str}': {str(e)}")

        return "\n".join(report_output)

    def execute(
        self,
        action: str,
        file_paths: Optional[Union[str, List[str]]] = None,
        content: Optional[str] = None,
        mode: str = "w",
        queries: Optional[Union[str, List[str]]] = None,
        replacements: Optional[Union[str, List[str]]] = None,
        encoding: Optional[str] = None,
        start_line: Optional[int] = None,
        num_lines: Optional[int] = None,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        **kwargs  # Bắt toàn bộ kwargs không dùng tới từ ToolExecutor
    ) -> str:
        """Hàm điều hướng chung cho tất cả các action."""
        # Dung hòa tham số file_paths và file_path
        if file_paths is None:
            file_paths = kwargs.get("file_path")

        if not file_paths:
            return "Lỗi: Tham số 'file_paths' (hoặc 'file_path') không được để trống."

        if action == "read":
            paths_list = [file_paths] if isinstance(file_paths, str) else file_paths
            if len(paths_list) > 1:
                return "Lỗi: Action 'read' hiện tại chỉ hỗ trợ xử lý 1 file duy nhất mỗi lần gọi."
            return self.read(
                file_path=paths_list[0],
                start_line=start_line,
                num_lines=num_lines,
                encoding=encoding,
            )

        elif action == "write":
            paths_list = [file_paths] if isinstance(file_paths, str) else file_paths
            if len(paths_list) > 1:
                return "Lỗi: Action 'write' chỉ hỗ trợ ghi 1 file mỗi lần gọi."
            return self.write(
                file_path=paths_list[0],
                content=content,
                mode=mode,
                encoding=encoding,
            )

        elif action == "search":
            return self.search(
                file_paths=file_paths,
                queries=queries,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
                max_results_per_file=max_results_per_file,
                encoding=encoding,
            )

        elif action == "replace":
            return self.replace(
                file_paths=file_paths,
                queries=queries,
                replacements=replacements,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
                max_results_per_file=max_results_per_file,
                encoding=encoding,
            )

        return f"Lỗi: Action '{action}' không hợp lệ. Chọn 'read', 'write', 'search' hoặc 'replace'."


_default_file_tool = FileTool()


def run(action: str, file_paths: Optional[Union[str, List[str]]] = None, **kwargs) -> str:
    """Hàm entrypoint chuẩn tương thích hoàn toàn với LocalToolManager & ToolExecutor."""
    return _default_file_tool.execute(action=action, file_paths=file_paths, **kwargs)
