from __future__ import annotations

import argparse
import ast
import codecs
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


class PatchApplyError(Exception):
    """Lỗi patch có thể trình bày trực tiếp cho người dùng."""


class PatchParseError(PatchApplyError):
    """Patch không đúng cú pháp hoặc chứa cấu trúc không được hỗ trợ."""


class PatchSafetyError(PatchApplyError):
    """Patch vi phạm ràng buộc an toàn đường dẫn / filesystem."""


@dataclass
class PatchHunk:
    header: str
    lines: list[str] = field(default_factory=list)
    old_start: Optional[int] = None
    old_count: Optional[int] = None
    new_start: Optional[int] = None
    new_count: Optional[int] = None
    old_no_newline: bool = False
    new_no_newline: bool = False
    source_line: int = 0


@dataclass
class PatchAction:
    action_type: str  # ADD | UPDATE | DELETE
    filepath: str
    hunks: list[PatchHunk] = field(default_factory=list)
    add_lines: list[str] = field(default_factory=list)
    add_final_newline: bool = True
    patch_format: str = "custom"  # custom | unified
    source_line: int = 0
    file_mode: Optional[int] = None


@dataclass
class TextFile:
    text_lf: str
    newline: str = "\n"
    encoding: str = "utf-8"
    mode: Optional[int] = None
    mixed_newlines: bool = False

    def encode(self) -> bytes:
        text = self.text_lf if self.newline == "\n" else self.text_lf.replace("\n", self.newline)
        return text.encode(self.encoding)


@dataclass
class VirtualFile:
    initial_exists: bool
    initial_bytes: Optional[bytes]
    initial_mode: Optional[int]
    current_exists: bool
    current: Optional[TextFile]


@dataclass
class ActionResult:
    action: PatchAction
    path: Path
    status: str  # OK | NOOP | PARTIAL | ERROR
    message: str = ""
    failed_hunks: list[PatchHunk] = field(default_factory=list)


@dataclass
class Plan:
    root: Path
    results: list[ActionResult]
    files: dict[Path, VirtualFile]
    has_errors: bool
    has_partials: bool


_HUNK_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@(?P<suffix>.*)$"
)
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _normalize_patch_text(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")


def _strip_git_prefix(path: str) -> str:
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        path = _unquote_git_path(path)
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _unquote_git_path(value: str) -> str:
    """Giải quote tối thiểu của git path; không cố giải mọi C-style escape hiếm gặp."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, str) else value[1:-1]
        except (SyntaxError, ValueError):
            return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _parse_diff_git_paths(line: str) -> tuple[Optional[str], Optional[str]]:
    # Hỗ trợ path có khoảng trắng khi được quote theo output git chuẩn.
    rest = line[len("diff --git ") :].strip()
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\S+', rest)
    if len(tokens) < 2:
        return None, None
    return _strip_git_prefix(tokens[0]), _strip_git_prefix(tokens[1])


def _parse_header_path(raw: str) -> str:
    # Unified diff chuẩn tách timestamp bằng TAB. Với path không quote và có spaces,
    # không thể phân biệt an toàn; giữ nguyên thay vì split theo space.
    raw = raw.rstrip("\n")
    if "\t" in raw:
        raw = raw.split("\t", 1)[0]
    return _strip_git_prefix(raw.strip())


def _parse_hunk_header(line: str, source_line: int) -> PatchHunk:
    m = _HUNK_RE.match(line)
    if not m:
        # Custom apply-patch cho phép @@ không có tọa độ.
        return PatchHunk(header=line, source_line=source_line)
    old_start = int(m.group("old_start"))
    new_start = int(m.group("new_start"))
    old_count = int(m.group("old_count") or "1")
    new_count = int(m.group("new_count") or "1")
    return PatchHunk(
        header=line,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        source_line=source_line,
    )


def _validate_hunk_counts(hunk: PatchHunk) -> None:
    if hunk.old_count is None or hunk.new_count is None:
        return
    old_seen = 0
    new_seen = 0
    for line in hunk.lines:
        if not line:
            # Bare empty line is accepted only for custom format and is context.
            old_seen += 1
            new_seen += 1
        elif line[0] == " ":
            old_seen += 1
            new_seen += 1
        elif line[0] == "-":
            old_seen += 1
        elif line[0] == "+":
            new_seen += 1
        elif line[0] == "\\":
            continue
        else:
            old_seen += 1
            new_seen += 1
    if old_seen != hunk.old_count or new_seen != hunk.new_count:
        raise PatchParseError(
            f"Hunk tại dòng patch {hunk.source_line} khai báo "
            f"-{hunk.old_count}/+{hunk.new_count} nhưng nội dung thực tế là "
            f"-{old_seen}/+{new_seen}."
        )


def _hunk_seen_counts(hunk: PatchHunk) -> tuple[int, int]:
    old_seen = 0
    new_seen = 0
    for line in hunk.lines:
        if line.startswith(" "):
            old_seen += 1
            new_seen += 1
        elif line.startswith("-"):
            old_seen += 1
        elif line.startswith("+"):
            new_seen += 1
        elif line.startswith("\\"):
            continue
        else:
            # Bare context chỉ dành cho custom patch.
            old_seen += 1
            new_seen += 1
    return old_seen, new_seen


def _hunk_is_complete(hunk: PatchHunk) -> bool:
    if hunk.old_count is None or hunk.new_count is None:
        return False
    old_seen, new_seen = _hunk_seen_counts(hunk)
    return old_seen == hunk.old_count and new_seen == hunk.new_count


def parse_patch(content: str) -> list[PatchAction]:
    """Parse OpenAI-style custom patch và unified/git diff theo hướng fail-closed."""
    content = _normalize_patch_text(content)
    # splitlines() tránh tạo một dòng rỗng giả chỉ vì patch kết thúc bằng newline.
    lines = content.splitlines()
    actions: list[PatchAction] = []
    current: Optional[PatchAction] = None
    current_hunk: Optional[PatchHunk] = None
    diff_old_path: Optional[str] = None
    diff_new_path: Optional[str] = None

    def finish_hunk() -> None:
        nonlocal current_hunk
        if current is not None and current_hunk is not None:
            _validate_hunk_counts(current_hunk)
            current.hunks.append(current_hunk)
        current_hunk = None

    def finish_action() -> None:
        nonlocal current, current_hunk, diff_old_path, diff_new_path
        if current is None:
            return
        finish_hunk()
        if current.action_type == "UPDATE" and not current.hunks:
            raise PatchParseError(
                f"UPDATE '{current.filepath}' tại dòng {current.source_line} không có hunk nào."
            )
        if current.action_type == "ADD" and current.hunks and not current.add_lines:
            # Unified ADD: chuyển + lines thành nội dung file.
            add: list[str] = []
            final_newline = True
            for h in current.hunks:
                for dl in h.lines:
                    if dl.startswith("+"):
                        add.append(dl[1:])
                    elif dl.startswith(" "):
                        add.append(dl[1:])
                    elif dl.startswith("\\"):
                        continue
                    elif dl == "":
                        add.append("")
                    elif not dl.startswith("-"):
                        add.append(dl)
                if h.new_no_newline:
                    final_newline = False
            current.add_lines = add
            current.add_final_newline = final_newline
        actions.append(current)
        current = None
        current_hunk = None
        diff_old_path = None
        diff_new_path = None

    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1

        if line in ("*** Begin Patch", "*** End Patch"):
            if line == "*** End Patch":
                finish_action()
            i += 1
            continue

        if line.startswith("*** Add File:"):
            finish_action()
            path = line[len("*** Add File:") :].strip()
            if not path:
                raise PatchParseError(f"Đường dẫn ADD rỗng tại dòng {line_no}.")
            current = PatchAction("ADD", path, patch_format="custom", source_line=line_no)
            i += 1
            continue

        if line.startswith("*** Update File:"):
            finish_action()
            path = line[len("*** Update File:") :].strip()
            if not path:
                raise PatchParseError(f"Đường dẫn UPDATE rỗng tại dòng {line_no}.")
            current = PatchAction("UPDATE", path, patch_format="custom", source_line=line_no)
            i += 1
            continue

        if line.startswith("*** Delete File:"):
            finish_action()
            path = line[len("*** Delete File:") :].strip()
            if not path:
                raise PatchParseError(f"Đường dẫn DELETE rỗng tại dòng {line_no}.")
            current = PatchAction("DELETE", path, patch_format="custom", source_line=line_no)
            i += 1
            continue

        if line.startswith("diff --git "):
            finish_action()
            diff_old_path, diff_new_path = _parse_diff_git_paths(line)
            if not diff_new_path and not diff_old_path:
                raise PatchParseError(f"Không đọc được 'diff --git' tại dòng {line_no}.")
            # Chưa tạo action cho đến khi gặp ---/+++ hoặc mode.
            i += 1
            continue

        if line.startswith("new file mode"):
            finish_hunk()
            path = diff_new_path or diff_old_path
            if not path:
                raise PatchParseError(f"'new file mode' không có path tại dòng {line_no}.")
            mode_text = line.split()[-1]
            mode = int(mode_text[-3:], 8) if mode_text.isdigit() and len(mode_text) >= 3 else None
            if current is None:
                current = PatchAction("ADD", path, patch_format="unified", source_line=line_no, file_mode=mode)
            else:
                current.action_type = "ADD"
                current.file_mode = mode
            i += 1
            continue

        if line.startswith("deleted file mode"):
            finish_hunk()
            path = diff_old_path or diff_new_path
            if not path:
                raise PatchParseError(f"'deleted file mode' không có path tại dòng {line_no}.")
            mode_text = line.split()[-1]
            mode = int(mode_text[-3:], 8) if mode_text.isdigit() and len(mode_text) >= 3 else None
            if current is None:
                current = PatchAction("DELETE", path, patch_format="unified", source_line=line_no, file_mode=mode)
            else:
                current.action_type = "DELETE"
                current.file_mode = mode
            i += 1
            continue

        if (
            line.startswith("--- ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
            and (current_hunk is None or _hunk_is_complete(current_hunk))
        ):
            if current_hunk is not None:
                finish_hunk()
            src_raw = line[4:]
            tgt_raw = lines[i + 1][4:]
            src = _parse_header_path(src_raw)
            tgt = _parse_header_path(tgt_raw)
            is_add = src == "/dev/null"
            is_delete = tgt == "/dev/null"
            if is_add:
                action_type = "ADD"
                target = tgt
            elif is_delete:
                action_type = "DELETE"
                target = src
            else:
                action_type = "UPDATE"
                target = tgt
            if not target or target == "/dev/null":
                raise PatchParseError(f"Path unified diff không hợp lệ tại dòng {line_no}.")
            if current is not None:
                # Nếu action được tạo trước bởi new/deleted mode cùng path, giữ lại.
                if current.filepath != target:
                    finish_action()
                    current = PatchAction(action_type, target, patch_format="unified", source_line=line_no)
                else:
                    current.action_type = action_type
            else:
                current = PatchAction(action_type, target, patch_format="unified", source_line=line_no)
            i += 2
            continue

        if line.startswith("old mode ") or (line.startswith("new mode ") and not line.startswith("new file mode ")):
            raise PatchParseError(
                f"Mode-change patch chưa được hỗ trợ an toàn (dòng {line_no}). "
                "Tool từ chối thay vì âm thầm bỏ qua chmod."
            )

        if (
            line.startswith("rename from ")
            or line.startswith("rename to ")
            or line.startswith("copy from ")
            or line.startswith("copy to ")
        ):
            raise PatchParseError(
                f"Rename/copy diff chưa được hỗ trợ an toàn (dòng {line_no}). "
                "Hãy tách thành DELETE + ADD rõ ràng."
            )
        if line == "GIT binary patch" or line.startswith("Binary files "):
            raise PatchParseError(
                f"Binary patch chưa được hỗ trợ (dòng {line_no}); tool chỉ xử lý text UTF-8."
            )

        if current is not None and line.startswith("@@"):
            finish_hunk()
            current_hunk = _parse_hunk_header(line, line_no)
            i += 1
            continue

        if current is not None:
            if current.action_type == "ADD" and current.patch_format == "custom" and current_hunk is None:
                # Custom Add File: '+' là marker; bare line vẫn được chấp nhận để tương thích tool cũ.
                if line.startswith("+"):
                    current.add_lines.append(line[1:])
                elif line.startswith("\\ No newline at end of file"):
                    current.add_final_newline = False
                elif line.startswith("*** "):
                    raise PatchParseError(f"Directive custom không nhận diện tại dòng {line_no}: {line}")
                else:
                    current.add_lines.append(line)
                i += 1
                continue

            if current_hunk is not None:
                if line.startswith("\\ No newline at end of file"):
                    prev = current_hunk.lines[-1] if current_hunk.lines else ""
                    if prev.startswith("-"):
                        current_hunk.old_no_newline = True
                    elif prev.startswith("+"):
                        current_hunk.new_no_newline = True
                    elif prev.startswith(" ") or prev == "":
                        current_hunk.old_no_newline = True
                        current_hunk.new_no_newline = True
                    i += 1
                    continue
                if current.patch_format == "unified":
                    if not line or line[0] not in (" ", "+", "-"):
                        raise PatchParseError(
                            f"Dòng hunk unified không hợp lệ tại dòng {line_no}: {line!r}"
                        )
                current_hunk.lines.append(line)
                i += 1
                continue

            # Metadata unified diff trước hunk.
            if current.patch_format == "unified" and (
                line.startswith("index ")
                or line.startswith("old mode ")
                or line.startswith("new mode ")
                or line.startswith("similarity index ")
            ):
                i += 1
                continue

        i += 1

    finish_action()
    if not actions:
        raise PatchParseError("Không tìm thấy action patch hợp lệ.")
    return actions


def _hunk_old_new(hunk: PatchHunk) -> tuple[list[str], list[str]]:
    old: list[str] = []
    new: list[str] = []
    for line in hunk.lines:
        if line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("+"):
            new.append(line[1:])
        elif line.startswith(" "):
            old.append(line[1:])
            new.append(line[1:])
        elif line.startswith("\\"):
            continue
        else:
            # Custom patch cũ đôi khi context không có prefix.
            old.append(line)
            new.append(line)
    return old, new


def reverse_patch_actions(actions: list[PatchAction]) -> list[PatchAction]:
    reversed_actions: list[PatchAction] = []
    for action in reversed(actions):
        if action.action_type == "ADD":
            rev = PatchAction(
                "DELETE",
                action.filepath,
                patch_format=action.patch_format,
                source_line=action.source_line,
                file_mode=action.file_mode,
            )
            # Gắn expected content vào add_lines để DELETE reverse có thể verify chính xác.
            rev.add_lines = action.add_lines[:]
            rev.add_final_newline = action.add_final_newline
            reversed_actions.append(rev)
            continue

        if action.action_type == "DELETE":
            restored: list[str] = []
            final_newline = True
            if action.hunks:
                for h in action.hunks:
                    old, _ = _hunk_old_new(h)
                    restored.extend(old)
                    if h.old_no_newline:
                        final_newline = False
            elif action.add_lines:
                restored = action.add_lines[:]
                final_newline = action.add_final_newline
            else:
                raise PatchApplyError(
                    f"Không thể reverse DELETE '{action.filepath}': patch không chứa nội dung gốc để khôi phục."
                )
            rev = PatchAction(
                "ADD",
                action.filepath,
                add_lines=restored,
                add_final_newline=final_newline,
                patch_format=action.patch_format,
                source_line=action.source_line,
                file_mode=action.file_mode,
            )
            reversed_actions.append(rev)
            continue

        if action.action_type == "UPDATE":
            rev = PatchAction(
                "UPDATE",
                action.filepath,
                patch_format=action.patch_format,
                source_line=action.source_line,
            )
            for h in action.hunks:
                rh = PatchHunk(
                    header=h.header,
                    old_start=h.new_start,
                    old_count=h.new_count,
                    new_start=h.old_start,
                    new_count=h.old_count,
                    old_no_newline=h.new_no_newline,
                    new_no_newline=h.old_no_newline,
                    source_line=h.source_line,
                )
                for line in h.lines:
                    if line.startswith("+"):
                        rh.lines.append("-" + line[1:])
                    elif line.startswith("-"):
                        rh.lines.append("+" + line[1:])
                    else:
                        rh.lines.append(line)
                rev.hunks.append(rh)
            reversed_actions.append(rev)
            continue

        raise PatchApplyError(f"Action không hỗ trợ reverse: {action.action_type}")
    return reversed_actions


def _detect_text_file(raw: bytes, path: Path, allow_mixed_newlines: bool) -> TextFile:
    encoding = "utf-8"
    if raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise PatchApplyError(
            f"'{path}' không phải UTF-8/UTF-8-BOM (lỗi byte {exc.start}). Tool từ chối sửa để tránh hỏng encoding."
        ) from exc

    crlf = text.count("\r\n")
    tmp = text.replace("\r\n", "")
    lf = tmp.count("\n")
    cr = tmp.count("\r")
    styles = sum(bool(x) for x in (crlf, lf, cr))
    mixed = styles > 1
    if mixed and not allow_mixed_newlines:
        raise PatchApplyError(
            f"'{path}' dùng mixed newline (CRLF/LF/CR). Mặc định từ chối để không chuẩn hóa nhầm toàn file; "
            "dùng --allow-mixed-newlines nếu bạn chấp nhận chuẩn hóa theo kiểu chiếm đa số."
        )
    if crlf >= lf and crlf >= cr and crlf:
        newline = "\r\n"
    elif lf >= cr and lf:
        newline = "\n"
    elif cr:
        newline = "\r"
    else:
        newline = os.linesep
    text_lf = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextFile(text_lf=text_lf, newline=newline, encoding=encoding, mixed_newlines=mixed)


def _candidate_indices(
    target: list[str], pattern: list[str], *, trailing_space_fuzzy: bool = False
) -> list[int]:
    if not pattern:
        return []
    n, m = len(target), len(pattern)
    if m > n:
        return []
    if trailing_space_fuzzy:
        p = [x.rstrip(" \t") for x in pattern]
        return [
            i
            for i in range(n - m + 1)
            if [x.rstrip(" \t") for x in target[i : i + m]] == p
        ]
    return [i for i in range(n - m + 1) if target[i : i + m] == pattern]


def _select_match(
    *,
    filepath: str,
    hunk_index: int,
    lines: list[str],
    old_lines: list[str],
    hunk: PatchHunk,
    search_cursor: int,
    line_delta: int,
    max_offset: int,
    trailing_space_fuzzy: bool,
) -> tuple[int, bool]:
    """Trả về (index, used_fuzzy). Không bao giờ tự chọn giữa các match mơ hồ."""
    def expected_index() -> int:
        assert hunk.old_start is not None
        # Unified diff: old_count=0 nghĩa là chèn SAU old_start dòng cũ.
        base = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
        return max(0, base + line_delta)

    if not old_lines:
        if hunk.old_start is None:
            raise PatchApplyError(
                f"Hunk #{hunk_index} của '{filepath}' chỉ chèn dòng nhưng header '@@' không có tọa độ. "
                "Không thể xác định vị trí chèn một cách an toàn."
            )
        expected = max(0, min(len(lines), expected_index()))
        return expected, False

    exact = _candidate_indices(lines, old_lines)
    expected: Optional[int] = None
    if hunk.old_start is not None:
        expected = expected_index()
        if expected in exact:
            return expected, False

        if exact:
            distances = sorted((abs(i - expected), i) for i in exact)
            best_dist = distances[0][0]
            best = [i for d, i in distances if d == best_dist]
            if len(best) == 1 and (max_offset < 0 or best_dist <= max_offset):
                return best[0], False
            if best_dist > max_offset >= 0:
                raise PatchApplyError(
                    f"Hunk #{hunk_index} của '{filepath}' chỉ khớp cách vị trí dự kiến {best_dist} dòng "
                    f"(giới hạn {max_offset}). Có thể source đã lệch quá xa."
                )
            raise PatchApplyError(
                f"Hunk #{hunk_index} của '{filepath}' mơ hồ: có nhiều match cách vị trí dự kiến như nhau: {best}."
            )
    else:
        after = [i for i in exact if i >= search_cursor]
        if len(after) == 1:
            return after[0], False
        if len(after) > 1:
            raise PatchApplyError(
                f"Hunk #{hunk_index} của '{filepath}' mơ hồ: context xuất hiện {len(after)} lần "
                f"sau dòng {search_cursor + 1}. Hãy tăng context hoặc dùng unified diff có số dòng."
            )
        if len(exact) == 1:
            return exact[0], False
        if len(exact) > 1:
            raise PatchApplyError(
                f"Hunk #{hunk_index} của '{filepath}' mơ hồ: context xuất hiện {len(exact)} lần trong file."
            )

    if not trailing_space_fuzzy:
        raise PatchApplyError(
            f"Hunk #{hunk_index} của '{filepath}' không tìm thấy context chính xác. "
            "Tool không tự bỏ indentation/whitespace. Có thể thử --fuzzy-trailing-space nếu chỉ lệch space cuối dòng."
        )

    fuzzy = _candidate_indices(lines, old_lines, trailing_space_fuzzy=True)
    if hunk.old_start is not None:
        expected = expected_index()
        if expected in fuzzy:
            return expected, True
        if fuzzy:
            distances = sorted((abs(i - expected), i) for i in fuzzy)
            best_dist = distances[0][0]
            best = [i for d, i in distances if d == best_dist]
            if len(best) == 1 and (max_offset < 0 or best_dist <= max_offset):
                return best[0], True
            raise PatchApplyError(
                f"Hunk #{hunk_index} của '{filepath}' chỉ fuzzy-match nhưng kết quả mơ hồ: {best}."
            )
    else:
        after = [i for i in fuzzy if i >= search_cursor]
        if len(after) == 1:
            return after[0], True
        if len(after) > 1:
            raise PatchApplyError(
                f"Hunk #{hunk_index} của '{filepath}' fuzzy-match {len(after)} vị trí; từ chối chọn bừa."
            )
    raise PatchApplyError(f"Hunk #{hunk_index} của '{filepath}' không khớp kể cả fuzzy trailing-space.")


def apply_hunks_to_text(
    filepath: str,
    original_text_lf: str,
    hunks: list[PatchHunk],
    *,
    allow_rejects: bool,
    max_offset: int,
    trailing_space_fuzzy: bool,
) -> tuple[str, list[PatchHunk], list[str]]:
    lines = original_text_lf.split("\n")
    failed: list[PatchHunk] = []
    warnings: list[str] = []
    search_cursor = 0
    line_delta = 0

    for idx, hunk in enumerate(hunks, 1):
        old_lines, new_lines = _hunk_old_new(hunk)
        try:
            match, used_fuzzy = _select_match(
                filepath=filepath,
                hunk_index=idx,
                lines=lines,
                old_lines=old_lines,
                hunk=hunk,
                search_cursor=search_cursor,
                line_delta=line_delta,
                max_offset=max_offset,
                trailing_space_fuzzy=trailing_space_fuzzy,
            )
            if used_fuzzy:
                warnings.append(f"hunk #{idx} dùng fuzzy trailing-space tại dòng {match + 1}")
            if old_lines and lines[match : match + len(old_lines)] != old_lines:
                # Fuzzy chỉ bỏ trailing spaces khi tìm. Khi thay, giữ semantic indentation nhưng sẽ
                # thay toàn block bằng nội dung patch; đây là hành vi đã được opt-in.
                if not trailing_space_fuzzy:
                    raise PatchApplyError(f"Hunk #{idx} thay đổi source sau bước match.")

            had_final_newline = bool(lines) and lines[-1] == ""
            logical_eof = len(lines) - 1 if had_final_newline else len(lines)
            touches_eof = match + len(old_lines) == logical_eof

            lines[match : match + len(old_lines)] = new_lines

            # Chỉ thay trạng thái final-newline khi patch có marker explicit. Nếu không,
            # giữ nguyên newline semantics của source hiện tại.
            if touches_eof and (hunk.old_no_newline or hunk.new_no_newline):
                want_final_newline = not hunk.new_no_newline
                has_final_newline = bool(lines) and lines[-1] == ""
                if want_final_newline and not has_final_newline:
                    lines.append("")
                elif not want_final_newline and has_final_newline:
                    lines.pop()

            search_cursor = match + len(new_lines)
            line_delta += len(new_lines) - len(old_lines)
        except PatchApplyError:
            if not allow_rejects:
                raise
            failed.append(hunk)

    return "\n".join(lines), failed, warnings


def _desired_add_text(action: PatchAction) -> str:
    text = "\n".join(action.add_lines)
    if action.add_final_newline and (action.add_lines or text):
        text += "\n"
    return text


def _format_rejected_hunks(filepath: str, hunks: Iterable[PatchHunk]) -> str:
    out = [f"--- a/{filepath}", f"+++ b/{filepath}"]
    for h in hunks:
        out.append(h.header or "@@ rejected @@")
        out.extend(h.lines)
    return "\n".join(out) + "\n"


def _safe_relative_path(root: Path, raw_path: str) -> Path:
    raw = raw_path.strip()
    if not raw or "\x00" in raw:
        raise PatchSafetyError("Đường dẫn patch rỗng hoặc chứa NUL.")
    if raw.startswith("/") or raw.startswith("\\\\") or _DRIVE_RE.match(raw):
        raise PatchSafetyError(f"Từ chối đường dẫn tuyệt đối/UNC: '{raw_path}'.")

    # Patch git dùng '/', custom trên Windows có thể dùng '\\'. Chuẩn hóa cả hai.
    parts = [p for p in re.split(r"[\\/]+", raw) if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PatchSafetyError(f"Từ chối path traversal '..': '{raw_path}'.")
    candidate = root.joinpath(*parts)
    root_resolved = root.resolve()
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PatchSafetyError(f"Đường dẫn thoát khỏi --root: '{raw_path}'.") from exc
    if candidate.exists() and candidate.is_symlink():
        raise PatchSafetyError(f"Từ chối sửa symlink trực tiếp: '{raw_path}'.")
    return candidate


def _load_virtual_file(path: Path, allow_mixed_newlines: bool) -> VirtualFile:
    if not path.exists():
        return VirtualFile(False, None, None, False, None)
    if not path.is_file():
        raise PatchSafetyError(f"'{path}' tồn tại nhưng không phải regular file.")
    raw = path.read_bytes()
    mode = stat.S_IMODE(path.stat().st_mode)
    tf = _detect_text_file(raw, path, allow_mixed_newlines)
    tf.mode = mode
    return VirtualFile(True, raw, mode, True, tf)


def build_plan(
    actions: list[PatchAction],
    *,
    root: Path,
    force: bool,
    max_offset: int,
    trailing_space_fuzzy: bool,
    allow_mixed_newlines: bool,
) -> Plan:
    files: dict[Path, VirtualFile] = {}
    results: list[ActionResult] = []
    has_errors = False
    has_partials = False

    for action in actions:
        try:
            path = _safe_relative_path(root, action.filepath)
            vf = files.get(path)
            if vf is None:
                vf = _load_virtual_file(path, allow_mixed_newlines)
                files[path] = vf

            if action.action_type == "ADD":
                desired = _desired_add_text(action)
                if vf.current_exists:
                    assert vf.current is not None
                    if vf.current.text_lf == desired:
                        if force:
                            results.append(ActionResult(action, path, "NOOP", "file đã có đúng nội dung"))
                            continue
                        raise PatchApplyError(
                            f"ADD '{action.filepath}' nhưng file đã tồn tại (dù nội dung có thể giống). "
                            "Mặc định không overwrite file hiện hữu."
                        )
                    if not force:
                        raise PatchApplyError(
                            f"ADD '{action.filepath}' nhưng file đã tồn tại. Dùng --force chỉ khi bạn thật sự muốn overwrite."
                        )
                    base = vf.current
                    vf.current = TextFile(
                        text_lf=desired,
                        newline=base.newline,
                        encoding=base.encoding,
                        mode=base.mode,
                        mixed_newlines=False,
                    )
                    vf.current_exists = True
                    results.append(ActionResult(action, path, "PARTIAL", "--force overwrite file ADD đã tồn tại"))
                    has_partials = True
                    continue

                vf.current = TextFile(
                    text_lf=desired, newline="\n", encoding="utf-8", mode=action.file_mode
                )
                vf.current_exists = True
                results.append(ActionResult(action, path, "OK"))
                continue

            if action.action_type == "UPDATE":
                if not vf.current_exists or vf.current is None:
                    raise PatchApplyError(f"UPDATE '{action.filepath}' nhưng file không tồn tại.")
                updated, failed, warnings = apply_hunks_to_text(
                    action.filepath,
                    vf.current.text_lf,
                    action.hunks,
                    allow_rejects=force,
                    max_offset=max_offset,
                    trailing_space_fuzzy=trailing_space_fuzzy,
                )
                vf.current = TextFile(
                    text_lf=updated,
                    newline=vf.current.newline,
                    encoding=vf.current.encoding,
                    mode=vf.current.mode,
                    mixed_newlines=False,
                )
                if failed:
                    has_partials = True
                    results.append(
                        ActionResult(
                            action,
                            path,
                            "PARTIAL",
                            "; ".join(warnings) if warnings else "một số hunk bị reject",
                            failed_hunks=failed,
                        )
                    )
                else:
                    results.append(ActionResult(action, path, "OK", "; ".join(warnings)))
                continue

            if action.action_type == "DELETE":
                if not vf.current_exists or vf.current is None:
                    raise PatchApplyError(f"DELETE '{action.filepath}' nhưng file không tồn tại.")

                if not action.hunks and not action.add_lines:
                    if not force:
                        raise PatchApplyError(
                            f"DELETE '{action.filepath}' không chứa expected content/hunk để xác minh. "
                            "Mặc định từ chối unverified delete; dùng --force nếu thực sự muốn xóa theo path."
                        )
                    vf.current_exists = False
                    vf.current = None
                    has_partials = True
                    results.append(
                        ActionResult(action, path, "PARTIAL", "--force unverified DELETE theo path")
                    )
                    continue

                # Reverse của ADD mang expected content trong add_lines.
                if action.add_lines:
                    expected = _desired_add_text(action)
                    if vf.current.text_lf != expected:
                        raise PatchApplyError(
                            f"DELETE(reverse ADD) '{action.filepath}' bị từ chối vì file hiện tại đã khác nội dung patch từng tạo."
                        )

                if action.hunks:
                    candidate, failed, warnings = apply_hunks_to_text(
                        action.filepath,
                        vf.current.text_lf,
                        action.hunks,
                        allow_rejects=force,
                        max_offset=max_offset,
                        trailing_space_fuzzy=trailing_space_fuzzy,
                    )
                    if failed:
                        has_partials = True
                        results.append(
                            ActionResult(action, path, "PARTIAL", "DELETE bị reject hunk; file không bị xóa", failed)
                        )
                        # Không xóa file nếu validation delete không hoàn chỉnh.
                        continue
                    if candidate != "":
                        raise PatchApplyError(
                            f"DELETE '{action.filepath}' không phủ toàn bộ nội dung file; sau khi áp hunk vẫn còn {len(candidate)} ký tự."
                        )
                    if warnings:
                        # Nếu delete chỉ khớp fuzzy, vẫn cho phép vì user đã opt-in rõ ràng.
                        pass

                vf.current_exists = False
                vf.current = None
                results.append(ActionResult(action, path, "OK"))
                continue

            raise PatchApplyError(f"Action không hỗ trợ: {action.action_type}")

        except (PatchApplyError, OSError) as exc:
            has_errors = True
            # Nếu path resolve thất bại, dùng root/path giả chỉ để report.
            report_path = root / action.filepath
            results.append(ActionResult(action, report_path, "ERROR", str(exc)))
            if not force:
                # Vẫn tiếp tục plan các action sau để report đầy đủ, nhưng không commit.
                continue

    return Plan(root=root, results=results, files=files, has_errors=has_errors, has_partials=has_partials)


def _default_file_mode() -> int:
    # CLI đơn luồng; đọc umask rồi khôi phục ngay.
    old = os.umask(0)
    os.umask(old)
    return 0o666 & ~old


def _verify_snapshot(plan: Plan, path: Path, vf: VirtualFile) -> None:
    # Chống TOCTOU cơ bản: path phải vẫn nằm trong root, không biến thành symlink,
    # và bytes phải đúng snapshot đã dùng để lập plan.
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(plan.root)
    except ValueError as exc:
        raise PatchSafetyError(f"Path đổi ra ngoài root trước commit: '{path}'.") from exc
    if path.is_symlink():
        raise PatchSafetyError(f"Path biến thành symlink trước commit: '{path}'.")

    if vf.initial_exists:
        if not path.is_file():
            raise PatchApplyError(f"Source đổi trạng thái trước commit: '{path}' không còn là file.")
        now = path.read_bytes()
        if now != vf.initial_bytes:
            raise PatchApplyError(
                f"Source changed after planning: '{path}'. Từ chối ghi đè thay đổi ngoài patch."
            )
    else:
        if path.exists() or path.is_symlink():
            raise PatchApplyError(
                f"Path được tạo sau planning: '{path}'. Từ chối overwrite file mới xuất hiện."
            )


def _write_temp_bytes(parent: Path, data: bytes, mode: Optional[int]) -> Path:
    fd, name = tempfile.mkstemp(prefix=".patch_applier.", suffix=".tmp", dir=str(parent))
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(path, mode if mode is not None else _default_file_mode())
        return path
    except Exception:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def commit_plan(plan: Plan) -> None:
    """Commit nhiều file bằng temp+replace và rollback best-effort nếu commit giữa chừng lỗi."""
    changed: list[tuple[Path, VirtualFile]] = []
    for path, vf in plan.files.items():
        final_bytes = vf.current.encode() if vf.current_exists and vf.current is not None else None
        if vf.initial_exists:
            if vf.current_exists and final_bytes == vf.initial_bytes:
                continue
            changed.append((path, vf))
        elif vf.current_exists:
            changed.append((path, vf))

    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    created_dirs: list[Path] = []

    try:
        for path, vf in changed:
            _verify_snapshot(plan, path, vf)
            if vf.current_exists and vf.current is not None:
                if not path.parent.exists():
                    missing: list[Path] = []
                    cur = path.parent
                    while not cur.exists() and cur != plan.root.parent:
                        missing.append(cur)
                        cur = cur.parent
                    path.parent.mkdir(parents=True, exist_ok=True)
                    created_dirs.extend(reversed(missing))
                # Verify lại sau mkdir để tránh parent bị đổi thành symlink giữa hai bước.
                _verify_snapshot(plan, path, vf)
                mode = vf.initial_mode if vf.initial_exists else vf.current.mode
                staged[path] = _write_temp_bytes(path.parent, vf.current.encode(), mode)

        for path, vf in changed:
            _verify_snapshot(plan, path, vf)
            if vf.initial_exists:
                fd, backup_name = tempfile.mkstemp(prefix=".patch_applier.", suffix=".bak", dir=str(path.parent))
                os.close(fd)
                backup = Path(backup_name)
                backup.unlink(missing_ok=True)
                os.replace(path, backup)
                backups[path] = backup

            # Đánh dấu ngay sau khi original đã được bảo toàn (hoặc ADD chưa có original),
            # để mọi lỗi ở os.replace(final) đều đi qua rollback.
            committed.append(path)
            if vf.current_exists:
                os.replace(staged[path], path)

        # Thành công: xóa backup.
        for backup in backups.values():
            backup.unlink(missing_ok=True)

    except Exception as exc:
        rollback_errors: list[str] = []
        # Xóa trạng thái final đã commit, rồi restore file ban đầu nếu có.
        for path in reversed(committed):
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
                backup = backups.get(path)
                if backup is not None and backup.exists():
                    os.replace(backup, path)
            except Exception as rb_exc:
                rollback_errors.append(f"{path}: {rb_exc}")

        # Có thể lỗi sau khi đã move original sang backup nhưng trước khi append committed.
        for path, backup in backups.items():
            if backup.exists() and not path.exists():
                try:
                    os.replace(backup, path)
                except Exception as rb_exc:
                    rollback_errors.append(f"{path}: {rb_exc}")

        extra = ""
        if rollback_errors:
            extra = " Rollback cũng gặp lỗi: " + " | ".join(rollback_errors)
        raise PatchApplyError(f"Commit thất bại: {exc}.{extra}") from exc

    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)
        # Không xóa .bak ở finally: nếu rollback thất bại, backup phải được giữ lại để cứu dữ liệu.
        # Khi commit thành công backup đã bị xóa; khi rollback thành công os.replace đã move nó về source.
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()  # chỉ xóa nếu còn rỗng
            except OSError:
                pass


def write_rejects(plan: Plan) -> None:
    grouped: dict[Path, list[tuple[str, PatchHunk]]] = {}
    for result in plan.results:
        for hunk in result.failed_hunks:
            grouped.setdefault(result.path, []).append((result.action.filepath, hunk))

    for path, entries in grouped.items():
        rej = Path(str(path) + ".rej")
        rej.parent.mkdir(parents=True, exist_ok=True)
        out: list[str] = []
        for filepath, hunk in entries:
            out.extend([f"--- a/{filepath}", f"+++ b/{filepath}", hunk.header or "@@ rejected @@"])
            out.extend(hunk.lines)
        rej.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def print_plan(plan: Plan, *, check_only: bool) -> None:
    print("🔍 --- KẾT QUẢ KIỂM TRA ---" if check_only else "🧩 --- KẾ HOẠCH PATCH ---")
    for result in plan.results:
        marker = {"OK": "✅", "NOOP": "⏭️", "PARTIAL": "⚠️", "ERROR": "❌"}[result.status]
        msg = f" — {result.message}" if result.message else ""
        print(f"{marker} [{result.status}] {result.action.filepath} ({result.action.action_type}){msg}")
        if result.failed_hunks:
            print(f"    reject hunks: {len(result.failed_hunks)}")


def apply_custom_patch(
    patch_path: str,
    *,
    check_only: bool = False,
    force: bool = False,
    reverse: bool = False,
    root: str | os.PathLike[str] = ".",
    max_offset: int = 200,
    fuzzy_trailing_space: bool = False,
    allow_mixed_newlines: bool = False,
) -> bool:
    patch_file = Path(patch_path)
    if not patch_file.is_file():
        print(f"❌ Không tìm thấy patch: {patch_file}")
        return False
    try:
        content = patch_file.read_text(encoding="utf-8-sig")
        actions = parse_patch(content)
        if reverse:
            actions = reverse_patch_actions(actions)
        root_path = Path(root).resolve()
        plan = build_plan(
            actions,
            root=root_path,
            force=force,
            max_offset=max_offset,
            trailing_space_fuzzy=fuzzy_trailing_space,
            allow_mixed_newlines=allow_mixed_newlines,
        )
        print_plan(plan, check_only=check_only)

        if check_only:
            if plan.has_errors or plan.has_partials:
                print("\n❌ CHECK thất bại/không sạch: apply mặc định sẽ không được phép.")
                return False
            print("\n🎉 CHECK sạch: cùng planner này sẽ được dùng khi apply.")
            return True

        if plan.has_errors and not force:
            print("\n🛑 Có lỗi. Atomic default: không file nào được thay đổi.")
            return False

        commit_plan(plan)
        if force and plan.has_partials:
            write_rejects(plan)

        ok = not plan.has_errors and not plan.has_partials
        if ok:
            print("\n🎉 Apply thành công và commit an toàn.")
        else:
            print("\n⚠️ Apply ở --force đã hoàn tất phần có thể áp dụng; xem các .rej và lỗi ở trên.")
        return ok

    except (PatchApplyError, OSError) as exc:
        print(f"❌ {exc}")
        return False


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Safe patch applier: hỗ trợ custom *** Add/Update/Delete File và unified/git diff; "
            "match fail-closed, kiểm tra ambiguity, path safety, staged transaction và rollback."
        )
    )
    p.add_argument("patch_path", help="Đường dẫn file .patch")
    p.add_argument("-R", "--reverse", action="store_true", help="Hoàn tác patch")
    p.add_argument("--check", action="store_true", help="Dry-run bằng cùng planner với apply")
    p.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Best-effort: cho phép partial hunks/.rej và overwrite ADD hiện hữu. Dùng cẩn thận.",
    )
    p.add_argument(
        "--root",
        default=".",
        help="Root dự án. Mọi path trong patch bắt buộc nằm bên trong root (mặc định: cwd).",
    )
    p.add_argument(
        "--max-offset",
        type=int,
        default=200,
        help="Khoảng lệch tối đa (dòng) khi relocation hunk có tọa độ; -1 = không giới hạn (mặc định 200).",
    )
    p.add_argument(
        "--fuzzy-trailing-space",
        action="store_true",
        help="Cho phép fuzzy chỉ với space/tab CUỐI dòng. Không bao giờ bỏ indentation đầu dòng.",
    )
    p.add_argument(
        "--allow-mixed-newlines",
        action="store_true",
        help="Cho phép file mixed CRLF/LF; file sẽ được chuẩn hóa theo kiểu newline chiếm đa số.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    success = apply_custom_patch(
        args.patch_path,
        check_only=args.check,
        force=args.force,
        reverse=args.reverse,
        root=args.root,
        max_offset=args.max_offset,
        fuzzy_trailing_space=args.fuzzy_trailing_space,
        allow_mixed_newlines=args.allow_mixed_newlines,
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
