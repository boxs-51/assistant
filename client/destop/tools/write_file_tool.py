import os
from typing import Dict, Any, Optional

TOOL_METADATA = {
    "name": "write_file",
    "description": "Ghi nội dung vào tệp tin local (Tạo mới, ghi đè hoặc nối thêm).",
    "base_risk": "HIGH", # Mức rủi ro cao -> Cần Human duyệt
    "danger_patterns": [
        r"\.env$", r"\.pem$", r"id_rsa", r"config/AGENT\.md$" # Cảnh báo nếu ghi vào file nhạy cảm
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Đường dẫn tới tệp tin cần ghi."
            },
            "content": {
                "type": "string",
                "description": "Nội dung văn bản cần ghi vào file."
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "'overwrite' để ghi đè/tạo mới, 'append' để nối vào cuối file (Mặc định: 'overwrite')."
            }
        },
        "required": ["file_path", "content"]
    }
}

def run(file_path: str, content: str, mode: str = "overwrite", context_session: Optional[Any] = None) -> str:
    try:
        # Tự động tạo thư mục cha nếu chưa tồn tại
        dir_name = os.path.dirname(file_path)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        file_mode = "a" if mode == "append" else "w"
        
        with open(file_path, file_mode, encoding="utf-8") as f:
            f.write(content)

        action_desc = "Đã nối thêm vào" if mode == "append" else "Đã ghi thành công"
        
        # Cập nhật Scratchpad để các bước sau biết file này vừa thay đổi
        if context_session and hasattr(context_session, "scratchpad"):
            context_session.scratchpad["last_modified_file"] = file_path
            context_session.scratchpad["file_save_status"] = "SUCCESS"

        return f"✅ {action_desc} tệp: `{file_path}` ({len(content)} ký tự)."
    except Exception as e:
        return f"❌ Lỗi khi ghi file `{file_path}`: {str(e)}"