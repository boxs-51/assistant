import os
import shutil
from typing import Optional, Any

TOOL_METADATA = {
    "name": "delete_path",
    "description": "Xóa một tệp tin hoặc toàn bộ một thư mục local.",
    "base_risk": "CRITICAL", # Mức nguy hiểm cao nhất
    "danger_patterns": [r".*"], # Tất cả các hành vi xóa đều phải phê duyệt
    "parameters": {
        "type": "object",
        "properties": {
            "target_path": {
                "type": "string",
                "description": "Đường dẫn file hoặc thư mục cần xóa."
            }
        },
        "required": ["target_path"]
    }
}

def run(target_path: str, context_session: Optional[Any] = None) -> str:
    try:
        if not os.path.exists(target_path):
            return f"⚠️ Đường dẫn `{target_path}` không tồn tại để xóa."

        if os.path.isfile(target_path):
            os.remove(target_path)
            return f"🗑️ Đã xóa tệp tin: `{target_path}`"
        elif os.path.isdir(target_path):
            shutil.rmtree(target_path)
            return f"🗑️ Đã xóa toàn bộ thư mục: `{target_path}`"
        return "❌ Lỗi loại đường dẫn không hợp lệ."
    except Exception as e:
        return f"❌ Lỗi khi xóa: {str(e)}"