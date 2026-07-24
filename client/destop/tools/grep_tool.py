import os
import re
from typing import Optional, Any

TOOL_METADATA = {
    "name": "grep_search",
    "description": "Tìm kiếm chuỗi văn bản hoặc Regex pattern bên trong các tệp tin của dự án.",
    "base_risk": "LOW",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Từ khóa hoặc RegEx pattern cần tìm kiếm."
            },
            "path": {
                "type": "string",
                "description": "Thư mục hoặc file cần quét (Mặc định: '.')."
            },
            "file_pattern": {
                "type": "string",
                "description": "Đuôi file cần lọc (e.g., '.py', '.cpp', '.h')."
            }
        },
        "required": ["query"]
    }
}

def run(query: str, path: str = ".", file_pattern: Optional[str] = None, context_session: Optional[Any] = None) -> str:
    matches = []
    try:
        pattern = re.compile(query, re.IGNORECASE)
        for root, _, files in os.walk(path):
            # Bỏ qua các thư mục ẩn/rác
            if any(p in root for p in [".git", "__pycache__", "build", "node_modules", ".vs"]):
                continue
                
            for file in files:
                if file_pattern and not file.endswith(file_pattern):
                    continue
                
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for idx, line in enumerate(f, 1):
                            if pattern.search(line):
                                matches.append(f"{os.path.normpath(full_path)}:{idx}: {line.strip()}")
                                if len(matches) >= 100: # Giới hạn 100 kết quả
                                    break
                except Exception:
                    continue
                if len(matches) >= 100:
                    break

        if not matches:
            return f"🔍 Không tìm thấy khớp nào cho query: `{query}`"

        res = f"🔎 Tìm thấy {len(matches)} vị trí khớp:\n" + "\n".join(matches[:50])
        if len(matches) > 50:
            res += f"\n... và {len(matches) - 50} kết quả khác."
        return res
    except Exception as e:
        return f"❌ Lỗi Grep: {str(e)}"