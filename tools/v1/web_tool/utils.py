import re
from urllib.parse import parse_qs, unquote, urlparse
from typing import List

def clean_whitespace(text: str) -> str:
    """Dọn dẹp khoảng trắng thừa, xóa ký tự NBSP và chuẩn hóa xuống dòng."""
    if not text:
        return ""
    text = re.sub(r'[\xa0\u1680\u180e\u2000-\u200b\u202f\u205f\u3000]', ' ', text)
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    return re.sub(r'\n{3,}', '\n\n', cleaned).strip()

def deduplicate_content(text: str) -> str:
    """Khử trùng lặp các đoạn văn, dòng hoặc liên kết bị lặp lại do giao diện trang web."""
    if not text:
        return ""
    lines = text.splitlines()
    seen = set()
    deduped_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        # Khử trùng lặp dòng có độ dài ý nghĩa
        if stripped and len(stripped) > 15:
            if stripped in seen:
                continue
            seen.add(stripped)
        deduped_lines.append(line)
    return "\n".join(deduped_lines)

def clean_ddg_url(raw_url: str) -> str:
    """Làm sạch URL điều hướng từ kết quả DuckDuckGo."""
    raw_url = raw_url.rstrip(")").strip()
    if "/l/?" in raw_url or "uddg=" in raw_url:
        parsed = urlparse(raw_url)
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0]).rstrip(")").strip()
    return raw_url