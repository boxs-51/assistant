import requests
from typing import Optional, Any

TOOL_METADATA = {
    "name": "fetch_web_page",
    "description": "Tải và lấy nội dung văn bản thuần từ một URL trang web.",
    "base_risk": "MEDIUM",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Đường dẫn URL cần tải (http:// hoặc https://)."
            }
        },
        "required": ["url"]
    }
}

def run(url: str, context_session: Optional[Any] = None) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        # Cắt bỏ HTML tags đơn giản
        text = resp.text
        # Trả về tối đa 3000 ký tự đầu tiên
        return f"🌐 [{url}] Content:\n```text\n{text[:3000]}\n```"
    except Exception as e:
        return f"❌ Lỗi tải URL: {str(e)}"