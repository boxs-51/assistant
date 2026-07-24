import os
from typing import Optional, Any

TOOL_METADATA = {
    "name": "list_directory",
    "description": "Liệt kê danh sách tệp tin và thư mục con bên trong một thư mục chỉ định.",
    "base_risk": "LOW",
    "parameters": {
        "type": "object",
        "properties": {
            "dir_path": {
                "type": "string",
                "description": "Đường dẫn thư mục cần xem (Mặc định: '.')."
            }
        }
    }
}

def run(dir_path: str = ".", context_session: Optional[Any] = None) -> str:
    try:
        if not os.path.exists(dir_path):
            return f"❌ Thư mục `{dir_path}` không tồn tại."
            
        items = os.listdir(dir_path)
        dirs = [f"[DIR]  {i}" for i in items if os.path.isdir(os.path.join(dir_path, i))]
        files = [f"[FILE] {i}" for i in items if os.path.isfile(os.path.join(dir_path, i))]
        
        result = sorted(dirs) + sorted(files)
        output = f"📁 Danh mục `{os.path.abspath(dir_path)}` ({len(result)} mục):\n"
        output += "\n".join(result[:100])
        return output
    except Exception as e:
        return f"❌ Lỗi liệt kê thư mục: {str(e)}"