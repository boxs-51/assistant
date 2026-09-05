import difflib
from pathlib import Path


def _generate_colored_diff(
    old_content: str, new_content: str, file_path: str
) -> str:
    """Tạo chuỗi diff có màu ANSI giữa nội dung cũ và mới."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )
    if not diff:
        return ""

    colored_lines = []
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            colored_lines.append(f"\033[92m{line}\033[0m")  # Xanh lá (+)
        elif line.startswith("-") and not line.startswith("---"):
            colored_lines.append(f"\033[91m{line}\033[0m")  # Đỏ (-)
        elif line.startswith("@"):
            colored_lines.append(f"\033[36m{line}\033[0m")  # Cyan (@@)
        else:
            colored_lines.append(line)
    return "".join(colored_lines)


def write_file(
    file_path: str,
    content: str,
    mode: str = "w",
    encoding: str = "utf-8",
    confirm_overwrite: bool = True,
) -> str:
    """Ghi nội dung vào tệp tin. Tự động tạo thư mục cha nếu chưa tồn tại.

    Args:
        file_path (str): Đường dẫn tệp tin cần ghi.
        content (str): Nội dung văn bản cần ghi.
        mode (str): 'w' (ghi đè) hoặc 'a' (ghi nối tiếp). Mặc định 'w'.
        encoding (str): Bảng mã (mặc định: 'utf-8').
        confirm_overwrite (bool): Nếu True và mode='w', hiển thị Diff màu và
          hỏi xác nhận trước khi ghi đè file đã tồn tại.

    Returns:
        str: Thông báo kết quả thực thi.
    """
    if mode not in ("w", "a"):
        return "Lỗi: mode phải là 'w' (overwrite) hoặc 'a' (append)."

    try:
        path = Path(file_path)

        # Kiểm tra Diff và xin xác nhận nếu file đã tồn tại ở chế độ ghi đè ('w')
        if mode == "w" and path.is_file() and confirm_overwrite:
            old_content = path.read_text(encoding=encoding)

            if old_content == content:
                return f"Bỏ qua: Nội dung mới trùng khớp hoàn toàn với file '{file_path}'."

            diff_text = _generate_colored_diff(old_content, content, file_path)

            print(
                f"\n\033[1m=== NỘI DUNG THAY ĐỔI (DIFF): {file_path} ===\033[0m"
            )
            print(diff_text)
            print("\033[1m" + "=" * 50 + "\033[0m")

            user_choice = (
                input(f"👉 Xác nhận ghi đè vào '{file_path}'? [y/N]: ")
                .strip()
                .lower()
            )
            if user_choice not in ("y", "yes"):
                return f"Đã hủy: Người dùng từ chối ghi đè file '{file_path}'."

        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, mode=mode, encoding=encoding) as f:
            f.write(content)

        action = "Ghi đè" if mode == "w" else "Ghi nối tiếp"
        return f"Thành công: Đã {action} vào file '{file_path}' ({len(content)} ký tự)."
    except Exception as e:
        return f"Lỗi khi ghi file '{file_path}': {str(e)}"