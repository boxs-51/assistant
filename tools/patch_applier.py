import argparse
import os
import sys


class PatchApplyError(Exception):
    """Ngoại lệ tùy chỉnh cho các lỗi khi áp dụng patch."""

    pass


class PatchAction:
    def __init__(self, action_type: str, filepath: str):
        self.action_type = action_type  # "ADD", "UPDATE", hoặc "DELETE"
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


def format_rejected_hunks(filepath: str, failed_hunks: list[list[str]]) -> str:
    """Tạo nội dung định dạng diff/rej cho các hunk bị lỗi để người dùng tự sửa."""
    lines = [f"--- a/{filepath}", f"+++ b/{filepath}"]
    for idx, hunk in enumerate(failed_hunks, 1):
        lines.append(f"@@ - Hunk {idx} thất bại @@")
        lines.extend(hunk)
    return "\n".join(lines) + "\n"


def apply_hunks_to_content(
    filepath: str,
    original_text: str,
    hunks: list[list[str]],
    allow_rejects: bool = False,
) -> tuple[str, list[list[str]]]:
    """Áp dụng các hunk vào nội dung file. Trả về (nội_dung_mới, danh_sách_hunk_thất_bại)."""
    file_lines = (
        original_text.replace("\r\n", "\n").splitlines() if original_text else []
    )
    search_idx = 0
    failed_hunks: list[list[str]] = []

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

        match_idx = find_sequence(file_lines, old_lines, start_idx=search_idx)
        if match_idx == -1:
            match_idx = find_sequence(file_lines, old_lines, start_idx=0)

        if match_idx == -1:
            match_idx = find_sequence_fuzzy(file_lines, old_lines)

        if match_idx == -1:
            if not allow_rejects:
                ctx_snippet = "\n".join(f"  {line}" for line in old_lines)
                raise PatchApplyError(
                    f"Lỗi Hunk #{hunk_idx} tại file '{filepath}': Không tìm thấy đoạn context trong mã nguồn gốc:\n{ctx_snippet}"
                )
            else:
                failed_hunks.append(hunk)
        else:
            file_lines[match_idx : match_idx + len(old_lines)] = new_lines
            search_idx = match_idx + len(new_lines)

    return "\n".join(file_lines), failed_hunks


def parse_patch(content: str) -> list[PatchAction]:
    """Phân tích file patch để bóc tách hành động Add/Update/Delete và các đường dẫn file."""
    actions: list[PatchAction] = []
    content = content.replace("\xa0", " ")
    lines = content.splitlines()
    i = 0
    current_action: PatchAction | None = None

    while i < len(lines):
        line = lines[i]

        if line.startswith("*** Add File:"):
            if current_action:
                current_action.finish_hunk()
                actions.append(current_action)

            filepath = line.replace("*** Add File:", "").strip()
            if not filepath:
                raise PatchApplyError(
                    "Lỗi cú pháp patch: Đường dẫn file rỗng sau '*** Add File:'"
                )
            current_action = PatchAction("ADD", filepath)
            i += 1
            continue

        if line.startswith("*** Update File:"):
            if current_action:
                current_action.finish_hunk()
                actions.append(current_action)

            filepath = line.replace("*** Update File:", "").strip()
            if not filepath:
                raise PatchApplyError(
                    "Lỗi cú pháp patch: Đường dẫn file rỗng sau '*** Update File:'"
                )
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

        if line.startswith("diff --git") or line.startswith("iff --git"):
            if current_action:
                current_action.finish_hunk()
                if (
                    current_action.add_lines
                    or current_action.hunks
                    or current_action.current_hunk
                    or current_action.action_type == "DELETE"
                ):
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

        if line.startswith("new file mode"):
            if current_action:
                current_action.action_type = "ADD"
            i += 1
            continue

        if line.startswith("deleted file mode"):
            if current_action:
                current_action.action_type = "DELETE"
            i += 1
            continue

        if line.startswith("--- "):
            if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                src_line = line
                tgt_line = lines[i + 1]

                raw_src = src_line[4:].split("\t")[0].strip()
                if raw_src.startswith("a/") or raw_src.startswith("b/"):
                    raw_src = raw_src[2:]

                raw_tgt = tgt_line[4:].split("\t")[0].strip()
                if raw_tgt.startswith("b/") or raw_tgt.startswith("a/"):
                    raw_tgt = raw_tgt[2:]

                is_add = src_line.startswith("--- /dev/null")
                is_delete = tgt_line.startswith("+++ /dev/null")

                if is_delete:
                    action_type = "DELETE"
                    target_path = raw_src
                elif is_add:
                    action_type = "ADD"
                    target_path = raw_tgt
                else:
                    action_type = "UPDATE"
                    target_path = raw_tgt

                if not target_path or target_path == "/dev/null":
                    raise PatchApplyError(
                        "Lỗi cú pháp patch: Không xác định được đường dẫn file hợp lệ trong Unified Diff"
                    )

                if current_action and current_action.filepath == target_path:
                    current_action.action_type = action_type
                else:
                    if current_action:
                        current_action.finish_hunk()
                        if (
                            current_action.add_lines
                            or current_action.hunks
                            or current_action.current_hunk
                            or current_action.action_type == "DELETE"
                        ):
                            actions.append(current_action)

                    current_action = PatchAction(action_type, target_path)

                i += 2
                continue

        if current_action:
            if current_action.action_type == "ADD":
                if line.startswith("@@"):
                    i += 1
                    continue
                if line.startswith("index ") or line.startswith("new file mode"):
                    i += 1
                    continue

                if line.startswith("+") or line.startswith(" "):
                    current_action.add_lines.append(line[1:])
                elif line == "":
                    current_action.add_lines.append("")
                else:
                    current_action.add_lines.append(line)

            elif current_action.action_type in ("UPDATE", "DELETE"):
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
        if (
            current_action.add_lines
            or current_action.hunks
            or current_action.current_hunk
            or current_action.action_type == "DELETE"
        ):
            actions.append(current_action)

    return actions


def reverse_patch_actions(actions: list[PatchAction]) -> list[PatchAction]:
    """Đảo ngược danh sách các PatchAction (Hoàn tác bản patch)."""
    reversed_actions: list[PatchAction] = []

    # Duyệt ngược lại các hành động
    for action in reversed(actions):
        rev_action = PatchAction("", action.filepath)

        if action.action_type == "ADD":
            # File được thêm -> Hoàn tác: Xóa file
            rev_action.action_type = "DELETE"
            rev_action.add_lines = action.add_lines[:]

        elif action.action_type == "DELETE":
            # File bị xóa -> Hoàn tác: Tạo lại file với nội dung cũ
            rev_action.action_type = "ADD"
            restored_lines: list[str] = []

            if action.add_lines:
                restored_lines = action.add_lines[:]
            elif action.hunks:
                for hunk in action.hunks:
                    for line in hunk:
                        if line.startswith("-"):
                            restored_lines.append(line[1:])
                        elif line.startswith(" "):
                            restored_lines.append(line[1:])
                        elif not line.startswith("+"):
                            restored_lines.append(line)

            rev_action.add_lines = restored_lines

        elif action.action_type == "UPDATE":
            # File được sửa -> Hoàn tác: Đảo dấu '+' và '-' trong tất cả các hunk
            rev_action.action_type = "UPDATE"
            for hunk in action.hunks:
                rev_hunk: list[str] = []
                for line in hunk:
                    if line.startswith("+"):
                        rev_hunk.append("-" + line[1:])
                    elif line.startswith("-"):
                        rev_hunk.append("+" + line[1:])
                    else:
                        rev_hunk.append(line)
                rev_action.hunks.append(rev_hunk)

        reversed_actions.append(rev_action)

    return reversed_actions


def apply_custom_patch(
    patch_path: str,
    check_only: bool = False,
    force: bool = False,
    reverse: bool = False,
) -> bool:
    """Xử lý file patch theo các chế độ: Apply, Reverse (Unapply), Check Mode, Atomic Default Mode, và Force Mode."""
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

    # ĐẢO NGƯỢC PATCH NẾU Ở CHẾ ĐỘ REVERSE
    if reverse:
        print("🔄 Đã bật chế độ HOÀN TÁC (Reverse Mode). Đang đảo ngược bản patch...")
        actions = reverse_patch_actions(actions)

    # BƯỚC 1: Mô phỏng xử lý trên bộ nhớ (Pre-check)
    prepared_changes = []
    has_errors = False

    for action in actions:
        item = {
            "action": action,
            "filepath": action.filepath,
            "action_type": action.action_type,
            "content_to_write": None,
            "failed_hunks": [],
            "error": None,
        }

        try:
            if action.action_type == "ADD":
                item["content_to_write"] = "\n".join(action.add_lines)

            elif action.action_type == "DELETE":
                if not os.path.exists(action.filepath) and not force:
                    item["error"] = (
                        f"Không thể DELETE vì file không tồn tại: '{action.filepath}'"
                    )
                    has_errors = True

            elif action.action_type == "UPDATE":
                if not os.path.exists(action.filepath):
                    raise PatchApplyError(
                        f"Không thể UPDATE vì file chưa tồn tại: '{action.filepath}'"
                    )

                with open(action.filepath, "r", encoding="utf-8") as f:
                    original_text = f.read()

                updated_text, failed_hunks = apply_hunks_to_content(
                    action.filepath, original_text, action.hunks, allow_rejects=force
                )

                item["content_to_write"] = updated_text
                item["failed_hunks"] = failed_hunks

                if failed_hunks and not force:
                    has_errors = True

        except (PatchApplyError, OSError) as e:
            item["error"] = str(e)
            has_errors = True

        prepared_changes.append(item)

    # BƯỚC 2: Chế độ --check
    if check_only:
        print("🔍 --- KẾT QUẢ KIỂM TRA (CHECK MODE) ---")
        for item in prepared_changes:
            fp = item["filepath"]
            if item["error"]:
                print(f"❌ [LỖI] {fp}: {item['error']}")
            elif item["failed_hunks"]:
                print(
                    f"⚠️ [LỖI HUNK] {fp}: {len(item['failed_hunks'])} hunk bị thất bại."
                )
            else:
                print(f"✅ [OK] {fp} ({item['action_type']})")

        if has_errors:
            print(
                "\n❌ Kiểm tra thất bại: Thao tác chứa lỗi và không thể thực hiện sạch hoàn toàn."
            )
            return False
        print("\n🎉 Kiểm tra thành công: Tất cả các thay đổi đều hợp lệ!")
        return True

    # Chế độ Mặc định (Không có --force)
    if has_errors and not force:
        print("❌ BÁO LỖI: Phát hiện lỗi trong quá trình phân tích/khớp patch!")
        print(
            "🛑 Mặc định script sẽ HỦY BỎ toàn bộ thao tác (không có file nào bị chỉnh sửa)."
        )
        print("\nChi tiết các file bị lỗi:")
        for item in prepared_changes:
            if item["error"]:
                print(f"   - [{item['filepath']}]: {item['error']}")
            elif item["failed_hunks"]:
                print(
                    f"   - [{item['filepath']}]: {len(item['failed_hunks'])} hunk không tìm thấy context."
                )

        print("\n💡 Gợi ý:")
        print("   - Chạy `--check` để kiểm tra trước các file.")
        print(
            "   - Chạy `--force` (hoặc `-f`) để bỏ qua lỗi, áp dụng các phần khớp được và tạo file .rej chứa vị trí bị lỗi để tự sửa."
        )
        return False

    # BƯỚC 3: Ghi thay đổi ra đĩa
    success_count = 0
    rej_count = 0
    failed_count = 0

    for item in prepared_changes:
        filepath = item["filepath"]
        dir_name = os.path.dirname(filepath)

        try:
            if item["action_type"] == "DELETE":
                if os.path.exists(filepath):
                    os.remove(filepath)
                    print(f"✅ Đã xóa: {filepath}")
                    success_count += 1
                else:
                    print(f"⚠️ Không thể xóa (file không tồn tại): {filepath}")
                    failed_count += 1
                continue

            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            if item["content_to_write"] is not None and not (
                item["error"] and item["action_type"] == "UPDATE"
            ):
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(item["content_to_write"])

            if item["failed_hunks"]:
                rej_filepath = f"{filepath}.rej"
                rej_content = format_rejected_hunks(
                    filepath, item["failed_hunks"]
                )
                with open(rej_filepath, "w", encoding="utf-8") as f:
                    f.write(rej_content)
                print(
                    f"⚠️ Đã áp dụng một phần: {filepath} (Đã tạo file lỗi: {rej_filepath})"
                )
                rej_count += 1

            elif item["error"]:
                rej_filepath = f"{filepath}.rej"
                rej_content = format_rejected_hunks(
                    filepath,
                    item["action"].hunks
                    if item["action"].hunks
                    else [[l] for l in item["action"].add_lines],
                )
                with open(rej_filepath, "w", encoding="utf-8") as f:
                    f.write(f"# Lỗi: {item['error']}\n" + rej_content)
                print(
                    f"❌ Bị lỗi [{filepath}]: {item['error']} (Đã tạo file lỗi: {rej_filepath})"
                )
                failed_count += 1

            else:
                act_str = (
                    "tạo mới" if item["action_type"] == "ADD" else "cập nhật"
                )
                print(f"✅ Đã {act_str}: {filepath}")
                success_count += 1

        except OSError as e:
            print(f"❌ Lỗi khi xử lý file [{filepath}]: {e}")
            failed_count += 1

    print("\n" + "=" * 50)
    print("📊 Kết quả thao tác patch:")
    print(f"   - Thành công: {success_count}/{len(actions)} file")
    if rej_count > 0:
        print(f"   - Thực hiện một phần (xuất file .rej): {rej_count} file")
    if failed_count > 0:
        print(f"   - Thất bại hoàn toàn: {failed_count} file")

    return failed_count == 0 and rej_count == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Công cụ áp dụng hoặc hoàn tác file patch cho dự án."
    )
    parser.add_argument(
        "patch_path",
        type=str,
        help="Đường dẫn tới file .patch cần áp dụng hoặc hoàn tác",
    )
    parser.add_argument(
        "-R",
        "--reverse",
        action="store_true",
        help="Chế độ hoàn tác (Unapply/Reverse): Đảo ngược bản patch để khôi phục mã nguồn về trạng thái ban đầu.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Chế độ kiểm tra (Dry-run): Chỉ kiểm tra xem patch có khớp sạch không, không chỉnh sửa file.",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Bỏ qua lỗi: Vẫn áp dụng các đoạn khớp thành công và tạo file <filepath>.rej chứa các đoạn bị lỗi để tự sửa.",
    )

    args = parser.parse_args()
    success = apply_custom_patch(
        args.patch_path,
        check_only=args.check,
        force=args.force,
        reverse=args.reverse,
    )
    if not success:
        sys.exit(1)