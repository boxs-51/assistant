import platform
import os
import sys
from typing import Optional, Any

TOOL_METADATA = {
    "name": "get_sys_info",
    "description": "Lấy thông tin môi trường hệ điều hành, thư mục hiện tại, phiên bản Python.",
    "base_risk": "LOW",
    "parameters": {"type": "object", "properties": {}}
}

def run(context_session: Optional[Any] = None) -> str:
    info = {
        "OS": f"{platform.system()} {platform.release()} ({platform.architecture()[0]})",
        "Python": sys.version.split()[0],
        "CWD": os.getcwd(),
        "User": os.getlogin() if hasattr(os, "getlogin") else "Unknown"
    }
    res = "🖥️ **SYSTEM INFO**:\n"
    for k, v in info.items():
        res += f"- **{k}**: `{v}`\n"
    return res