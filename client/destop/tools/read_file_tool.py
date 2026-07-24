import os
from typing import Dict, Any, Optional

TOOL_METADATA = {
    "name": "read_file",
    "description": "Đọc nội dung của tệp tin local (TXT, MD, PY, JSON, CPP, v.v.).",
    "base_risk": "LOW",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Đường dẫn tuyệt đối hoặc tương đối tới tệp tin cần đọc."
            },
            "start_line": {
                "type": "integer",
                "description": "Dòng bắt đầu đọc (Mặc định: 1)."
            },
            "num_lines": {
                "type": "integer",
                "description": "Số lượng dòng cần đọc (Giúp tiết kiệm token với file lớn)."
            }
        },
        "required": ["file_path"]
    }
}

def run(file_path: str, start_line: Optional[int] = None, num_lines: Optional[int] = None, context_session: Optional[Any] = None) -> str:
    try:
        if not os.path.exists(file_path):
            return f"❌ Lỗi: Tệp tin `{file_path}` không tồn tại."

        if os.path.isdir(file_path):
            return f"❌ Lỗi: `{file_path}` là thư mục, không phải tệp tin."

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        
        # Xử lý cắt dòng nếu được chỉ định
        if start_line is not None and start_line > 0:
            s_idx = start_line - 1
            e_idx = s_idx + num_lines if num_lines else total_lines
            selected_lines = lines[s_idx:e_idx]
            header = f"📄 [{file_path}] (Dòng {start_line} -> {min(e_idx, total_lines)} / Tổng {total_lines} dòng):\n"
        else:
            selected_lines = lines
            header = f"📄 [{file_path}] (Tổng {total_lines} dòng):\n"

        content = "".join(selected_lines)

        # Lưu thông báo file vừa đọc vào Scratchpad
        if context_session and hasattr(context_session, "scratchpad"):
            context_session.scratchpad["active_file"] = file_path

        return f"{header}```\n{content}\n```"
    except Exception as e:
        return f"❌ Lỗi khi đọc file `{file_path}`: {str(e)}"