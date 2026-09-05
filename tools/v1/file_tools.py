import re
from itertools import islice
from pathlib import Path
from typing import List, Optional, Union


def file_tool(
    action: str,
    file_paths: Union[str, List[str]],
    content: Optional[str] = None,
    mode: str = "w",
    queries: Optional[Union[str, List[str]]] = None,
    replacements: Optional[Union[str, List[str]]] = None,
    encoding: str = "utf-8",
    start_line: Optional[int] = None,
    num_lines: Optional[int] = None,
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_results_per_file: Optional[int] = None,
) -> str:
    """Tool hợp nhất thao tác với tệp tin (Đọc, Ghi, Tìm kiếm, Thay thế).

    Args:
        action (str): Hành động thực hiện: 'read', 'write', 'search', hoặc 'replace'.
        file_paths (str | List[str]): 1 hoặc nhiều đường dẫn tệp tin.
        content (str, optional): Nội dung văn bản dùng cho 'write'.
        mode (str): Mode ghi file 'w' (ghi đè) hoặc 'a' (ghi tiếp) cho 'write'.
        queries (str | List[str], optional): Chuỗi/regex tìm kiếm cho 'search'/'replace'.
        replacements (str | List[str], optional): Chuỗi thay thế cho 'replace'.
        encoding (str): Bảng mã (mặc định: 'utf-8').
        start_line (int, optional): Dòng bắt đầu đọc cho 'read' (1-indexed).
        num_lines (int, optional): Số dòng cần đọc cho 'read'.
        use_regex (bool): Sử dụng regex cho 'search'/'replace'.
        case_sensitive (bool): Phân biệt chữ hoa/thường cho 'search'/'replace'.
        max_results_per_file (int, optional): Giới hạn dòng kết quả cho 'search'/'replace'.

    Returns:
        str: Kết quả xử lý hoặc thông báo lỗi.
    """
    valid_actions = ("read", "write", "search", "replace")
    if action not in valid_actions:
        return f"Lỗi: Action '{action}' không hợp lệ. Chọn một trong: {valid_actions}"

    paths_list = [file_paths] if isinstance(file_paths, str) else file_paths

    # ==================== 1. HÀNH ĐỘNG: READ ====================
    if action == "read":
        if len(paths_list) > 1:
            return "Lỗi: Action 'read' chỉ hỗ trợ đọc 1 file mỗi lần."
        path = Path(paths_list[0])

        if not path.exists():
            return f"Lỗi: File '{path}' không tồn tại."
        if not path.is_file():
            return f"Lỗi: Đường dẫn '{path}' là thư mục, không phải file."
        if start_line is not None and start_line < 1:
            return "Lỗi: 'start_line' phải lớn hơn hoặc bằng 1."
        if num_lines is not None and num_lines < 0:
            return "Lỗi: 'num_lines' không được là số âm."

        try:
            with path.open("r", encoding=encoding) as f:
                if start_line is None and num_lines is None:
                    return f.read()

                start = (start_line - 1) if start_line else 0
                stop = (start + num_lines) if num_lines is not None else None
                lines = islice(f, start, stop)
                return "".join(lines)
        except Exception as e:
            return f"Lỗi khi đọc file '{path}': {str(e)}"

    # ==================== 2. HÀNH ĐỘNG: WRITE ====================
    elif action == "write":
        if len(paths_list) > 1:
            return "Lỗi: Action 'write' chỉ hỗ trợ ghi 1 file mỗi lần."
        if content is None:
            return "Lỗi: Cần cung cấp 'content' cho action 'write'."
        if mode not in ("w", "a"):
            return "Lỗi: mode phải là 'w' (overwrite) hoặc 'a' (append)."

        file_str = paths_list[0]
        path = Path(file_str)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, mode=mode, encoding=encoding) as f:
                f.write(content)

            act_str = "Ghi đè" if mode == "w" else "Ghi nối tiếp"
            return f"Thành công: Đã {act_str} vào file '{file_str}' ({len(content)} ký tự)."
        except Exception as e:
            return f"Lỗi khi ghi file '{file_str}': {str(e)}"

    # ==================== 3 & 4. HÀNH ĐỘNG: SEARCH HOẶC REPLACE ====================
    else:  # action in ('search', 'replace')
        if not queries:
            return f"Lỗi: Cần cung cấp 'queries' cho action '{action}'."

        queries_list = [queries] if isinstance(queries, str) else queries
        if any(q == "" for q in queries_list):
            return "Lỗi: 'queries' không được chứa chuỗi rỗng."

        is_replace_mode = action == "replace"
        replacements_list: Optional[List[str]] = None

        if is_replace_mode:
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

        # Biên dịch Regex/Chuỗi
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled_pairs = []

        for idx, q in enumerate(queries_list):
            rep = replacements_list[idx] if replacements_list else None
            try:
                pattern = re.compile(q if use_regex else re.escape(q), flags)
                compiled_pairs.append((pattern, rep, q))
            except re.error as e:
                return f"Lỗi biểu thức chính quy (regex) tại query '{q}': {str(e)}"

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
                with path.open("r", encoding=encoding) as f:
                    lines = f.readlines()

                file_matches = []
                modified_lines = lines.copy() if is_replace_mode else []
                match_count = 0

                for line_idx, line in enumerate(lines):
                    line_num = line_idx + 1
                    current_line = line
                    line_modified = False

                    for pattern, rep, original_q in compiled_pairs:
                        if pattern.search(current_line):
                            if is_replace_mode:
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
                        if is_replace_mode:
                            modified_lines[line_idx] = current_line

                        if max_results_per_file and match_count >= max_results_per_file:
                            break

                # Ghi file nếu là action='replace'
                if is_replace_mode and match_count > 0:
                    with path.open("w", encoding=encoding) as f:
                        f.writelines(modified_lines)

                # Tổng hợp báo cáo
                if file_matches:
                    status_suffix = " [ĐÃ CẬP NHẬT]" if is_replace_mode else ""
                    header = f"📄 File: {file_str}{status_suffix}"
                    report_output.append(header)
                    report_output.extend(file_matches)
                else:
                    report_output.append(f"📄 File: {file_str} - Không tìm thấy kết quả phù hợp.")

            except Exception as e:
                report_output.append(f"❌ Lỗi khi xử lý file '{file_str}': {str(e)}")

        return "\n".join(report_output)