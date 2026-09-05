from pathlib import Path
import pytest
import unittest
from tools.v1.file_tools import file_tool


# ==============================================================================
# 1. TEST CÁC CẤU HÌNH KHÔNG HỢP LỆ BAN ĐẦU
# ==============================================================================

def test_invalid_action(tmp_path):
    """Kiểm tra báo lỗi khi truyền action không hợp lệ."""
    f = tmp_path / "test.txt"
    f.write_text("content", encoding="utf-8")
    result = file_tool("unknown_action", str(f))
    assert "Lỗi: Action 'unknown_action' không hợp lệ" in result


# ==============================================================================
# 2. TEST ACTION: READ
# ==============================================================================

def test_read_full_content(tmp_path):
    """Đọc toàn bộ nội dung file."""
    f = tmp_path / "sample.txt"
    content = "Dòng 1\nDòng 2\nDòng 3"
    f.write_text(content, encoding="utf-8")

    assert file_tool("read", str(f)) == content


def test_read_line_slice(tmp_path):
    """Đọc một phân đoạn dòng bằng start_line và num_lines."""
    f = tmp_path / "sample.txt"
    f.write_text("Dòng 1\nDòng 2\nDòng 3\nDòng 4", encoding="utf-8")

    result = file_tool("read", str(f), start_line=2, num_lines=2)
    assert result == "Dòng 2\nDòng 3\n"


def test_read_multiple_files_error(tmp_path):
    """Báo lỗi khi truyền danh sách nhiều file cho action read."""
    files = [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]
    result = file_tool("read", files)
    assert "Lỗi: Action 'read' chỉ hỗ trợ đọc 1 file" in result


def test_read_non_existent_file(tmp_path):
    """Báo lỗi khi đọc file không tồn tại."""
    result = file_tool("read", str(tmp_path / "not_found.txt"))
    assert "không tồn tại" in result


def test_read_directory_error(tmp_path):
    """Báo lỗi khi đường dẫn trỏ tới thư mục chứ không phải file."""
    result = file_tool("read", str(tmp_path))
    assert "là thư mục, không phải file" in result


def test_read_invalid_start_line(tmp_path):
    """Báo lỗi khi start_line < 1."""
    f = tmp_path / "sample.txt"
    f.write_text("Hello", encoding="utf-8")
    result = file_tool("read", str(f), start_line=0)
    assert "'start_line' phải lớn hơn hoặc bằng 1" in result


def test_read_invalid_num_lines(tmp_path):
    """Báo lỗi khi num_lines < 0."""
    f = tmp_path / "sample.txt"
    f.write_text("Hello", encoding="utf-8")
    result = file_tool("read", str(f), num_lines=-5)
    assert "'num_lines' không được là số âm" in result


# ==============================================================================
# 3. TEST ACTION: WRITE
# ==============================================================================

def test_write_new_file_creates_parents(tmp_path):
    """Tạo file mới và tự động tạo thư mục cha nếu chưa có."""
    f = tmp_path / "sub_dir" / "new_file.txt"
    result = file_tool("write", str(f), content="Xin chào Python")
    
    assert "Thành công" in result
    assert f.is_file()
    assert f.read_text(encoding="utf-8") == "Xin chào Python"


def test_write_append_mode(tmp_path):
    """Ghi nối tiếp nội dung (mode='a')."""
    f = tmp_path / "append.txt"
    f.write_text("Nội dung cũ. ", encoding="utf-8")

    result = file_tool("write", str(f), content="Nội dung mới.", mode="a")
    assert "Thành công" in result
    assert f.read_text(encoding="utf-8") == "Nội dung cũ. Nội dung mới."


def test_write_overwrite_existing_file(tmp_path):
    """Ghi đè file đã tồn tại."""
    f = tmp_path / "overwrite.txt"
    f.write_text("Cũ", encoding="utf-8")

    result = file_tool("write", str(f), content="Mới", mode="w")
    assert "Thành công" in result
    assert f.read_text(encoding="utf-8") == "Mới"


def test_write_multiple_files_error(tmp_path):
    """Báo lỗi khi ghi nhiều file cùng lúc."""
    result = file_tool("write", [str(tmp_path / "1.txt"), str(tmp_path / "2.txt")], content="a")
    assert "chỉ hỗ trợ ghi 1 file mỗi lần" in result


def test_write_missing_content_error(tmp_path):
    """Báo lỗi khi không cung cấp tham số content."""
    result = file_tool("write", str(tmp_path / "1.txt"), content=None)
    assert "Cần cung cấp 'content'" in result


def test_write_invalid_mode_error(tmp_path):
    """Báo lỗi khi truyền mode khác 'w' hoặc 'a'."""
    result = file_tool("write", str(tmp_path / "1.txt"), content="a", mode="r+")
    assert "mode phải là 'w' (overwrite) hoặc 'a' (append)" in result


# ==============================================================================
# 4. TEST ACTION: SEARCH
# ==============================================================================

def test_search_basic_multi_files(tmp_path):
    """Tìm kiếm từ khóa trong nhiều file."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_text("Python là ngôn ngữ tuyệt vời\nJava cũng ổn", encoding="utf-8")
    f2.write_text("Học Python căn bản", encoding="utf-8")

    result = file_tool("search", [str(f1), str(f2)], queries="python")
    assert "[Dòng 1]: Python là ngôn ngữ" in result
    assert "[Dòng 1]: Học Python căn bản" in result


def test_search_regex_and_case_sensitive(tmp_path):
    """Tìm kiếm bằng Regex và phân biệt hoa/thường."""
    f = tmp_path / "f.txt"
    f.write_text("Code 123\ncode 456\nKhông chứa số", encoding="utf-8")

    # Chỉ tìm chữ 'code' thường theo sau bởi chữ số
    result = file_tool(
        "search", str(f), queries=r"code \d+", use_regex=True, case_sensitive=True
    )
    assert "[Dòng 2]: code 456" in result
    assert "[Dòng 1]: Code 123" not in result


def test_search_max_results_per_file(tmp_path):
    """Giới hạn số kết quả trả về trong 1 file."""
    f = tmp_path / "f.txt"
    f.write_text("test\ntest\ntest\ntest", encoding="utf-8")

    result = file_tool("search", str(f), queries="test", max_results_per_file=2)
    assert result.count("Dòng") == 2


def test_search_no_match(tmp_path):
    """Trường hợp không tìm thấy kết quả."""
    f = tmp_path / "f.txt"
    f.write_text("Chỉ có tiếng Việt", encoding="utf-8")

    result = file_tool("search", str(f), queries="English")
    assert "Không tìm thấy kết quả phù hợp" in result


def test_search_missing_queries_error(tmp_path):
    """Báo lỗi khi queries bị thiếu hoặc là chuỗi rỗng."""
    result1 = file_tool("search", str(tmp_path / "f.txt"), queries=None)
    assert "Cần cung cấp 'queries'" in result1

    result2 = file_tool("search", str(tmp_path / "f.txt"), queries="")
    assert "Lỗi: Cần cung cấp 'queries' cho action 'search'." in result2


def test_search_invalid_regex_error(tmp_path):
    """Báo lỗi khi cấu trúc Regex sai cú pháp."""
    f = tmp_path / "f.txt"
    f.write_text("data", encoding="utf-8")
    result = file_tool("search", str(f), queries="[Chưa đóng ngoặc", use_regex=True)
    assert "Lỗi biểu thức chính quy (regex)" in result


# ==============================================================================
# 5. TEST ACTION: REPLACE
# ==============================================================================

def test_replace_basic(tmp_path):
    """Thay thế chuỗi đơn giản trong nhiều file."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_text("host = localhost\nport = 8080", encoding="utf-8")
    f2.write_text("db_host = localhost", encoding="utf-8")

    result = file_tool(
        "replace", [str(f1), str(f2)], queries="localhost", replacements="127.0.0.1"
    )

    assert "[ĐÃ CẬP NHẬT]" in result
    assert f1.read_text(encoding="utf-8") == "host = 127.0.0.1\nport = 8080"
    assert f2.read_text(encoding="utf-8") == "db_host = 127.0.0.1"


def test_replace_multiple_queries_and_replacements(tmp_path):
    """Thay thế đồng thời nhiều cặp (query -> replacement)."""
    f = tmp_path / "f.txt"
    f.write_text("foo bar baz", encoding="utf-8")

    result = file_tool(
        "replace",
        str(f),
        queries=["foo", "baz"],
        replacements=["hello", "world"],
    )

    assert f.read_text(encoding="utf-8") == "hello bar world"


def test_replace_regex(tmp_path):
    """Thay thế nâng cao bằng Regex."""
    f = tmp_path / "f.txt"
    f.write_text("Price: 100USD\nPrice: 200USD", encoding="utf-8")

    result = file_tool(
        "replace",
        str(f),
        queries=r"(\d+)USD",
        replacements=r"$\1",
        use_regex=True,
    )

    assert f.read_text(encoding="utf-8") == "Price: $100\nPrice: $200"


def test_replace_missing_replacements_error(tmp_path):
    """Báo lỗi khi thiếu replacements trong action replace."""
    f = tmp_path / "f.txt"
    f.write_text("content", encoding="utf-8")

    result = file_tool("replace", str(f), queries="content", replacements=None)
    assert "Action 'replace' yêu cầu phải truyền 'replacements'" in result


def test_replace_mismatched_length_error(tmp_path):
    """Báo lỗi khi số lượng queries và replacements không bằng nhau."""
    f = tmp_path / "f.txt"
    f.write_text("content", encoding="utf-8")

    result = file_tool(
        "replace",
        str(f),
        queries=["q1", "q2"],
        replacements=["r1"],
    )
    assert "không khớp với 'queries'" in result

if __name__ == "__main__":
    unittest.main()