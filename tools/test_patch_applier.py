import os
import pytest
from patch_applier import apply_custom_patch, apply_hunks_to_content, PatchApplyError


# ---------------------------------------------------------------------------
# 1. Test Dạng 1: Custom Add File (*** Add File:)
# ---------------------------------------------------------------------------
def test_custom_add_file(tmp_path):
    patch_content = """
*** Add File: src/hello.py
+def say_hello():
+    print("Hello World")
*** End Patch
"""
    patch_file = tmp_path / "test_add.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    # Chuyển thư mục làm việc tạm thời vào tmp_path
    os.chdir(tmp_path)
    apply_custom_patch(str(patch_file))

    target_file = tmp_path / "src" / "hello.py"
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == 'def say_hello():\n    print("Hello World")'


# ---------------------------------------------------------------------------
# 2. Test Dạng 2: Custom Update File (*** Update File:)
# ---------------------------------------------------------------------------
def test_custom_update_file(tmp_path):
    # Tạo file ban đầu
    target_file = tmp_path / "src" / "config.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        'VERSION = "1.0"\nDEBUG = True\nALLOWED_HOSTS = ["*"]\n',
        encoding="utf-8",
    )

    patch_content = """
*** Update File: src/config.py
@@
 VERSION = "1.0"
-DEBUG = True
+DEBUG = False
 ALLOWED_HOSTS = ["*"]
+PORT = 8000
"""
    patch_file = tmp_path / "test_update.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)
    apply_custom_patch(str(patch_file))

    updated_text = target_file.read_text(encoding="utf-8")
    assert 'DEBUG = False' in updated_text
    assert 'PORT = 8000' in updated_text
    assert 'DEBUG = True' not in updated_text


# ---------------------------------------------------------------------------
# 3. Test Dạng 3: Git Unified Diff Add File (--- /dev/null +++ b/...)
# ---------------------------------------------------------------------------
def test_git_diff_add_file(tmp_path):
    patch_content = """
--- /dev/null
+++ b/src/contracts/result.py
@@
+from __future__ import annotations
+from pydantic import BaseModel
+
+class AgentExecutionResult(BaseModel):
+    execution_id: str
"""
    patch_file = tmp_path / "git_add.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)
    apply_custom_patch(str(patch_file))

    target_file = tmp_path / "src" / "contracts" / "result.py"
    assert target_file.exists()
    assert "class AgentExecutionResult(BaseModel):" in target_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Test Dạng 4: Git Unified Diff Update File (--- a/... +++ b/...)
# ---------------------------------------------------------------------------
def test_git_diff_update_file(tmp_path):
    target_file = tmp_path / "src" / "contracts" / "__init__.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        'from .events import CorrelationContext\n\n__all__ = [\n    "CorrelationContext",\n]\n',
        encoding="utf-8",
    )

    patch_content = """
--- a/src/contracts/__init__.py
+++ b/src/contracts/__init__.py
@@ -1,5 +1,7 @@
 from .events import CorrelationContext
+from .result import AgentExecutionResult

 __all__ = [
     "CorrelationContext",
+    "AgentExecutionResult",
 ]
"""
    patch_file = tmp_path / "git_update.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)
    apply_custom_patch(str(patch_file))

    updated_text = target_file.read_text(encoding="utf-8")
    assert "from .result import AgentExecutionResult" in updated_text
    assert '"AgentExecutionResult",' in updated_text


# ---------------------------------------------------------------------------
# 5. Test Dạng 5: Patch Hỗn Hợp (Nhiều File Add + Update trong 1 File Patch)
# ---------------------------------------------------------------------------
def test_mixed_patch_formats(tmp_path):
    # File hiện có cần update
    existing_file = tmp_path / "main.py"
    existing_file.write_text('def main():\n    pass\n', encoding="utf-8")

    mixed_patch = """
*** Add File: utils/helper.py
+def clean_string(s: str) -> str:
+    return s.strip()

*** Update File: main.py
@@
 def main():
-    pass
+    print("App Started")
"""
    patch_file = tmp_path / "mixed.patch"
    patch_file.write_text(mixed_patch, encoding="utf-8")

    os.chdir(tmp_path)
    apply_custom_patch(str(patch_file))

    # Kiểm tra file được tạo mới
    assert (tmp_path / "utils" / "helper.py").exists()
    # Kiểm tra file đã được update
    assert 'print("App Started")' in existing_file.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Các test case thành công đã có
# ---------------------------------------------------------------------------
def test_custom_add_file(tmp_path):
    patch_content = """
*** Add File: src/hello.py
+def say_hello():
+    print("Hello World")
*** End Patch
"""
    patch_file = tmp_path / "test_add.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)
    assert apply_custom_patch(str(patch_file)) is True
    target_file = tmp_path / "src" / "hello.py"
    assert target_file.exists()


# ---------------------------------------------------------------------------
# BỔ SUNG: Kiểm tra các trường hợp XỬ LÝ LỖI
# ---------------------------------------------------------------------------

# 1. Lỗi: File patch không tồn tại
def test_error_patch_file_not_found(tmp_path):
    os.chdir(tmp_path)
    result = apply_custom_patch("non_existent_patch.patch")
    assert result is False


# 2. Lỗi: UPDATE trên file chưa từng tồn tại
def test_error_update_non_existent_target_file(tmp_path):
    patch_content = """
*** Update File: missing_folder/missing_file.py
@@
 def hello():
-    pass
+    print("Hi")
"""
    patch_file = tmp_path / "test_missing_target.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)
    # Lệnh áp dụng phải trả về False do thất bại
    result = apply_custom_patch(str(patch_file))
    assert result is False


# 3. Lỗi: Context Mismatch (Mã gốc bị thay đổi, patch không khớp)
def test_error_context_mismatch(tmp_path):
    # File gốc chứa code khác hẳn so với patch kì vọng
    target_file = tmp_path / "app.py"
    target_file.write_text('def start():\n    print("Old App")\n', encoding="utf-8")

    patch_content = """
*** Update File: app.py
@@
 def start():
-    print("Expected String")
+    print("New String")
"""
    patch_file = tmp_path / "test_mismatch.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)

    # Hàm trực tiếp phải bắn PatchApplyError
    with pytest.raises(PatchApplyError) as exc_info:
        apply_hunks_to_content(
            "app.py",
            target_file.read_text(encoding="utf-8"),
            [
                [
                    " def start():\n",
                    '-    print("Expected String")\n',
                    '+    print("New String")\n',
                ]
            ],
        )
    assert "Không tìm thấy đoạn context" in str(exc_info.value)

    # Hàm apply_custom_patch phải xử lý lỗi và trả về False
    result = apply_custom_patch(str(patch_file))
    assert result is False


# 4. Lỗi: Phân tích đường dẫn rỗng / Cú pháp hỏng
def test_error_invalid_patch_syntax(tmp_path):
    patch_content = """
*** Add File: 
+print("No filename specified")
"""
    patch_file = tmp_path / "bad_syntax.patch"
    patch_file.write_text(patch_content, encoding="utf-8")

    os.chdir(tmp_path)
    result = apply_custom_patch(str(patch_file))
    assert result is False