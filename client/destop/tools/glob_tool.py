import glob
import os
from typing import Dict, Any, Optional

TOOL_METADATA = {
    "name": "glob_files",
    "description": "Tìm kiếm danh sách tệp tin/thư mục dựa trên mẫu đường dẫn (glob pattern).",
    "base_risk": "LOW",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Mẫu glob cần tìm (e.g., '**/*.py', 'src/*.cpp', 'config/*.json')."
            },
            "root_dir": {
                "type": "string",
                "description": "Thư mục gốc để tìm kiếm (Mặc định là thư mục làm việc hiện tại '.')."
            },
            "recursive": {
                "type": "boolean",
                "description": "Tìm kiếm đệ quy trong các thư mục con (Mặc định: True)."
            }
        },
        "required": ["pattern"]
    }
}

def run(pattern: str, root_dir: str = ".", recursive: bool = True, context_session: Optional[Any] = None) -> str:
    try:
        search_path = os.path.join(root_dir, pattern) if root_dir != "." else pattern
        files = glob.glob(search_path, recursive=recursive)
        
        # Lọc lấy các file chuẩn (loại bỏ thư mục nếu cần)
        formatted_files = [os.path.normpath(f) for f in files]
        
        if not formatted_files:
            return f"🔍 Không tìm thấy tệp nào khớp với pattern: `{pattern}`"

        # Cập nhật thông tin vào Scratchpad của Session nếu có
        if context_session and hasattr(context_session, "scratchpad"):
            context_session.scratchpad["last_glob_matches"] = formatted_files[:20]

        result_text = f"📂 Tìm thấy {len(formatted_files)} tệp tin:\n"
        result_text += "\n".join([f"- {f}" for f in formatted_files[:50]]) # Giới hạn hiển thị 50 file
        if len(formatted_files) > 50:
            result_text += f"\n... và {len(formatted_files) - 50} tệp khác."
            
        return result_text
    except Exception as e:
        return f"❌ Lỗi khi thực thi glob: {str(e)}"