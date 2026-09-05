import argparse
import os
import sys


class PatchApplyError(Exception):
    """Ngoại lệ tùy chỉnh cho các lỗi khi áp dụng patch."""

    pass


class PatchAction:
    def __init__(self, action_type: str, filepath: str):
        self.action_type = action_type  # "ADD" hoặc "UPDATE"
        self.filepath = filepath
        self.add_lines: list[str] = []
        self.hunks: list[list[str]] = []
        self.current_hunk: list[str] = []

    def finish_hunk(self) -> None:
        if self.current_hunk:
            self.hunks.append(self.current_hunk)
            self.current_hunk = []


def find_sequence(
    target: list[str], pattern: list[str], start_idx: int = 0
) -> int:
    """Tìm vị trí xuất hiện chính xác của chuỗi mẫu pattern trong file target."""
    n, m = len(target), len(pattern)
    if m == 0:
        return -1
    for i in range(start_idx, n - m + 1):
        if target[i : i + m] == pattern:
            return i
    return -1


def find_sequence_fuzzy(target: list[str], pattern: list[str]) -> int:
    """Tìm kiếm linh hoạt (bỏ qua khoảng trắng đầu/cuối dòng) nếu không khớp chính xác."""
    n, m = len(target), len(pattern)
    if m == 0:
        return -1
    norm_pattern = [p.strip() for p in pattern]
    for i in range(0, n - m + 1):
        norm_target = [t.strip() for t in target[i : i + m]]
        if norm_target == norm_pattern:
            return i
    return -1


def apply_hunks_to_content(
    filepath: str, original_text: str, hunks: list[list[str]]
) -> str:
    """Áp dụng các khối thay đổi (hunks) vào nội dung file có sẵn."""
    file_lines = (
        original_text.replace("\r\n", "\n").splitlines() if original_text else []
    )
    search_idx = 0

    for hunk_idx, hunk in enumerate(hunks, 1):
        if not hunk:
            continue

        old_lines: list[str] = []
        new_lines: list[str] = []

        for line in hunk:
            if line.startswith("-"):
                old_lines.append(line[1:])
            elif line.startswith("+"):
                new_lines.append(line[1:])
            elif line.startswith(" "):
                old_lines.append(line[1:])
                new_lines.append(line[1:])
            else:
                old_lines.append(line)
                new_lines.append(line)

        if not old_lines:
            file_lines[search_idx:search_idx] = new_lines
            search_idx += len(new_lines)
            continue

        # Tìm vị trí khớp chính xác
        match_idx = find_sequence(file_lines, old_lines, start_idx=search_idx)
        if match_idx == -1:
            # Thử lại từ đầu file
            match_idx = find_sequence(file_lines, old_lines, start_idx=0)

        # Nếu không khớp chính xác, thử tìm kiếm linh hoạt (Fuzzy Match)
        if match_idx == -1:
            match_idx = find_sequence_fuzzy(file_lines, old_lines)

        # Kiểm tra lỗi: Nếu vẫn không tìm thấy đoạn context
        if match_idx == -1:
            ctx_snippet = "\n".join(f"  {line}" for line in old_lines)
            raise PatchApplyError(
                f"Lỗi Hunk #{hunk_idx} tại file '{filepath}': Không tìm thấy đoạn context trong mã nguồn gốc:\n{ctx_snippet}"
            )

        file_lines[match_idx : match_idx + len(old_lines)] = new_lines
        search_idx = match_idx + len(new_lines)

    return "\n".join(file_lines)


def parse_patch(content: str) -> list[PatchAction]:
    """Phân tích file patch để bóc tách hành động Add/Update và các đường dẫn file."""
    actions: list[PatchAction] = []
    # 1. Chuẩn hóa ký tự NO-BREAK SPACE (\xa0) thành dấu cách thường
    content = content.replace("\xa0", " ")
    lines = content.splitlines()
    i = 0
    current_action: PatchAction | None = None

    while i < len(lines):
        line = lines[i]

        # Xử lý cú pháp custom "*** Add File:"
        if line.startswith("*** Add File:"):
            if current_action:
                current_action.finish_hunk()
                actions.append(current_action)

            filepath = line.replace("*** Add File:", "").strip()
            if not filepath:
                raise PatchApplyError("Lỗi cú pháp patch: Đường dẫn file rỗng sau '*** Add File:'")
            current_action = PatchAction("ADD", filepath)
            i += 1
            continue

        # Xử lý cú pháp custom "*** Update File:"
        if line.startswith("*** Update File:"):
            if current_action:
                current_action.finish_hunk()
                actions.append(current_action)

            filepath = line.replace("*** Update File:", "").strip()
            if not filepath:
                raise PatchApplyError("Lỗi cú pháp patch: Đường dẫn file rỗng sau '*** Update File:'")
            current_action = PatchAction("UPDATE", filepath)
            i += 1
            continue

        if line.startswith("*** End Patch"):
            if current_action:
                current_action.finish_hunk()
                actions.append(current_action)
                current_action = None
            i += 1
            continue

        # Xử lý "diff --git" hoặc lỗi copy mất chữ "iff --git"
        if line.startswith("diff --git") or line.startswith("iff --git"):
            if current_action:
                current_action.finish_hunk()
                # Chỉ lưu action cũ nếu nó thực sự có nội dung/hunk
                if current_action.add_lines or current_action.hunks or current_action.current_hunk:
                    actions.append(current_action)
                current_action = None

            parts = line.split()
            target_path = None
            for part in reversed(parts):
                if part.startswith("b/"):
                    target_path = part[2:]
                    break
                elif part.startswith("a/"):
                    target_path = part[2:]
                    break

            if target_path:
                current_action = PatchAction("UPDATE", target_path)

            i += 1
            continue

        # Đổi trạng thái sang ADD nếu gặp "new file mode"
        if line.startswith("new file mode"):
            if current_action:
                current_action.action_type = "ADD"
            i += 1
            continue

        # Xử lý cặp dòng standard unified diff "--- " và "+++ "
        if line.startswith("--- "):
            if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                src_line = line
                tgt_line = lines[i + 1]

                raw_tgt = tgt_line[4:].split("\t")[0].strip()
                if raw_tgt.startswith("b/") or raw_tgt.startswith("a/"):
                    raw_tgt = raw_tgt[2:]

                if not raw_tgt:
                    raise PatchApplyError("Lỗi cú pháp patch: Không xác định được đường dẫn file trong Unified Diff")

                is_add = src_line.startswith("--- /dev/null")

                # Nếu current_action đã được tạo bởi diff --git trước đó và trùng file -> Tái sử dụng
                if current_action and current_action.filepath == raw_tgt:
                    if is_add:
                        current_action.action_type = "ADD"
                else:
                    if current_action:
                        current_action.finish_hunk()
                        if current_action.add_lines or current_action.hunks or current_action.current_hunk:
                            actions.append(current_action)

                    action_type = "ADD" if is_add else "UPDATE"
                    current_action = PatchAction(action_type, raw_tgt)

                i += 2
                continue

        # Bóc tách nội dung dòng dựa trên action_type
        if current_action:
            if current_action.action_type == "ADD":
                if line.startswith("@@"):
                    i += 1
                    continue
                # Bỏ qua các dòng header/metadata thừa nếu có
                if line.startswith("index ") or line.startswith("new file mode"):
                    i += 1
                    continue

                if line.startswith("+") or line.startswith(" "):
                    current_action.add_lines.append(line[1:])
                elif line == "":
                    current_action.add_lines.append("")
                else:
                    current_action.add_lines.append(line)

            elif current_action.action_type == "UPDATE":
                if line.startswith("@@"):
                    current_action.finish_hunk()
                elif (
                    line.startswith("index ")
                    or line.startswith("new file mode")
                    or line.startswith("deleted file mode")
                ):
                    pass
                else:
                    current_action.current_hunk.append(line)

        i += 1

    if current_action:
        current_action.finish_hunk()
        if current_action.add_lines or current_action.hunks or current_action.current_hunk:
            actions.append(current_action)

    return actions


def apply_custom_patch(patch_path: str) -> bool:
    """Đọc file patch và thực thi tạo mới/chỉnh sửa file tương ứng (trả về True nếu thành công hoàn toàn)."""
    # 1. Kiểm tra tồn tại file patch
    if not os.path.exists(patch_path):
        print(f"❌ Lỗi: Không tìm thấy file patch tại: {patch_path}")
        return False

    try:
        with open(patch_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"❌ Lỗi khi đọc file patch: {e}")
        return False

    try:
        actions = parse_patch(content)
    except PatchApplyError as e:
        print(f"❌ Lỗi phân tích cú pháp patch: {e}")
        return False

    if not actions:
        print("⚠️ Cảnh báo: Không tìm thấy khối patch hợp lệ nào trong file.")
        return False

    success_count = 0
    failed_files: list[str] = []

    # 2. Duyệt qua các hành động patch
    for action in actions:
        try:
            dir_name = os.path.dirname(action.filepath)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            if action.action_type == "ADD":
                file_content = "\n".join(action.add_lines)
                with open(action.filepath, "w", encoding="utf-8") as f:
                    f.write(file_content)
                print(f"✅ Đã tạo mới: {action.filepath}")
                success_count += 1

            elif action.action_type == "UPDATE":
                # Kiểm tra sự tồn tại của file trước khi UPDATE
                if not os.path.exists(action.filepath):
                    raise PatchApplyError(
                        f"Không thể UPDATE vì file chưa tồn tại: '{action.filepath}'"
                    )

                with open(action.filepath, "r", encoding="utf-8") as f:
                    original_text = f.read()

                updated_content = apply_hunks_to_content(
                    action.filepath, original_text, action.hunks
                )

                with open(action.filepath, "w", encoding="utf-8") as f:
                    f.write(updated_content)

                print(f"✅ Đã cập nhật: {action.filepath}")
                success_count += 1

        except (PatchApplyError, OSError) as e:
            print(f"❌ Thất bại [{action.filepath}]: {e}")
            failed_files.append(action.filepath)

    # 3. Tổng kết kết quả
    print("\n" + "=" * 50)
    print(f"📊 Kết quả áp dụng patch:")
    print(f"   - Thành công: {success_count}/{len(actions)} file")
    if failed_files:
        print(f"   - Thất bại ({len(failed_files)} file): {', '.join(failed_files)}")
        return False

    print("🎉 Tất cả thay đổi đã được áp dụng thành công!")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Công cụ áp dụng file patch cho dự án."
    )
    parser.add_argument(
        "patch_path",
        type=str,
        help="Đường dẫn tới file .patch cần áp dụng",
    )

    args = parser.parse_args()
    success = apply_custom_patch(args.patch_path)
    if not success:
        sys.exit(1)