import re
from itertools import islice
from pathlib import Path
from typing import List, Optional, Tuple, Union


class FileTool:
    """Class quản lý các thao tác với tệp tin (Đọc, Ghi, Tìm kiếm, Thay thế)."""

    def __init__(self, default_encoding: str = "utf-8"):
        self.default_encoding = default_encoding

    # ------------------------------------------------------------------
    # 1. ĐỌC FILE (READ)
    # ------------------------------------------------------------------
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
            with path.open("r", encoding=enc) as f:
                if start_line is None and num_lines is None:
                    return f.read()

                start = (start_line - 1) if start_line else 0
                stop = (start + num_lines) if num_lines is not None else None
                lines = islice(f, start, stop)
                return "".join(lines)
        except Exception as e:
            return f"Lỗi khi đọc file '{path}': {str(e)}"

    # ------------------------------------------------------------------
    # 2. GHI FILE (WRITE)
    # ------------------------------------------------------------------
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

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open(mode=mode, encoding=enc) as f:
                f.write(content)

            act_str = "Ghi đè" if mode == "w" else "Ghi nối tiếp"
            return f"Thành công: Đã {act_str} vào file '{file_path}' ({len(content)} ký tự)."
        except Exception as e:
            return f"Lỗi khi ghi file '{file_path}': {str(e)}"

    # ------------------------------------------------------------------
    # HELPER: BIÊN DỊCH REGEX
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 3. TÌM KIẾM (SEARCH)
    # ------------------------------------------------------------------
    def search(
        self,
        file_paths: Union[str, List[str]],
        queries: Union[str, List[str]],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """Tìm kiếm chuỗi hoặc regex trong một hoặc nhiều file."""
        return self._search_or_replace(
            file_paths=file_paths,
            queries=queries,
            is_replace=False,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_results_per_file=max_results_per_file,
            encoding=encoding,
        )

    # ------------------------------------------------------------------
    # 4. THAY THẾ (REPLACE)
    # ------------------------------------------------------------------
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
        """Tìm kiếm và thay thế chuỗi hoặc regex trong một hoặc nhiều file."""
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

    # ------------------------------------------------------------------
    # LOGIC CHUNG CHO SEARCH & REPLACE
    # ------------------------------------------------------------------
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
        if isinstance(compiled_pairs, str):  # Lỗi khi biên dịch regex
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
                with path.open("r", encoding=enc) as f:
                    lines = f.readlines()

                file_matches = []
                modified_lines = lines.copy() if is_replace else []
                match_count = 0

                for line_idx, line in enumerate(lines):
                    line_num = line_idx + 1
                    current_line = line
                    line_modified = False

                    for pattern, rep, original_q in compiled_pairs:
                        if pattern.search(current_line):
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

                    if line_modified:
                        match_count += 1
                        if is_replace:
                            modified_lines[line_idx] = current_line

                        if max_results_per_file and match_count >= max_results_per_file:
                            break

                # Ghi lại file nếu là chế độ replace và có thay đổi
                if is_replace and match_count > 0:
                    with path.open("w", encoding=enc) as f:
                        f.writelines(modified_lines)

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

    # ------------------------------------------------------------------
    # DISPATCHER / ENTRY POINT (ĐIỀU HƯỚNG BẰNG TEN ACTION)
    # ------------------------------------------------------------------
    def execute(
        self,
        action: str,
        file_paths: Union[str, List[str]],
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
    ) -> str:
        """Hàm điều hướng chung hỗ trợ gọi theo chuỗi action."""
        paths_list = [file_paths] if isinstance(file_paths, str) else file_paths

        if action == "read":
            if len(paths_list) > 1:
                return "Lỗi: Action 'read' chỉ hỗ trợ đọc 1 file mỗi lần."
            return self.read(
                file_path=paths_list[0],
                start_line=start_line,
                num_lines=num_lines,
                encoding=encoding,
            )

        elif action == "write":
            if len(paths_list) > 1:
                return "Lỗi: Action 'write' chỉ hỗ trợ ghi 1 file mỗi lần."
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

        else:
            valid_actions = ("read", "write", "search", "replace")
            return f"Lỗi: Action '{action}' không hợp lệ. Chọn một trong: {valid_actions}"


# ======================================================================
# BẢO TỒN TÍNH TƯƠNG THÍCH (Hàm wrapper gọi qua Class)
# ======================================================================
_default_file_tool = FileTool()


def file_tool(*args, **kwargs) -> str:
    """Hàm wrapper cho phép tương thích ngược với code cũ."""
    return _default_file_tool.execute(*args, **kwargs)