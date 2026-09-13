import json
from typing import Any, Dict, List, Optional

def format_search_results(query: str, results: List[Dict[str, str]], fmt: str = "markdown") -> str:
    """Định dạng kết quả tìm kiếm theo định dạng được chỉ định (markdown, json, text)."""
    fmt = fmt.lower()
    if fmt == "json":
        return json.dumps({
            "query": query.strip(),
            "total": len(results),
            "results": results
        }, ensure_ascii=False, indent=2)

    if fmt == "text":
        lines = [f"KẾT QUẢ TÌM KIẾM CHÓ: \"{query.strip()}\" ({len(results)} kết quả)\n"]
        for idx, item in enumerate(results, 1):
            lines.append(f"{idx}. {item['title']}")
            lines.append(f"   URL: {item['href']}")
            lines.append(f"   Mô tả: {item['body'] or 'Không có mô tả'}\n")
        return "\n".join(lines).strip()

    # Default Markdown
    if not results:
        return f"### Kết quả tìm kiếm cho: `{query}`\n\n*Không tìm thấy kết quả phù hợp.*"

    md_output = [f"# Kết quả tìm kiếm: \"{query.strip()}\" ({len(results)} kết quả)\n"]
    for idx, item in enumerate(results, 1):
        md_output.append(f"### {idx}. [{item['title']}]({item['href']})")
        if item['body']:
            md_output.append(f"> {item['body']}\n")
        else:
            md_output.append("> *(Không có mô tả ngắn)*\n")

    return "\n".join(md_output).strip()

def format_scrape_results(
    url: str,
    title: str,
    method: str,
    proxy: str,
    content: str,
    structured_data: Optional[Dict[str, Any]] = None,
    fmt: str = "markdown"
) -> str:
    """Định dạng kết quả cào trang web theo định dạng được chỉ định."""
    fmt = fmt.lower()
    tables = structured_data.get("tables", []) if structured_data else []
    charts = structured_data.get("charts", []) if structured_data else []

    if fmt == "json":
        return json.dumps({
            "title": title,
            "url": url,
            "method": method,
            "proxy": proxy,
            "content": content,
            "structured_data": {
                "tables": tables,
                "charts": charts
            }
        }, ensure_ascii=False, indent=2)

    if fmt == "text":
        text_out = [
            f"Tiêu đề: {title}",
            f"URL: {url}",
            f"Phương pháp: {method}",
            f"Proxy: {proxy}",
            "=" * 40,
            content
        ]
        return "\n".join(text_out)

    # Default Markdown
    tables_md = ""
    if tables:
        table_list = []
        for idx, matrix in enumerate(tables, 1):
            if not matrix or not matrix[0]:
                continue
            header = "| "  " | ".join(matrix[0]) + " |"
            separator = "| "  " | ".join(["---"] * len(matrix[0])) + " |"
            body = "\n".join(["| " + " | ".join(row) + " |" for row in matrix[1:]])
            table_list.append(f"**Bảng {idx}:**\n{header}\n{separator}\n{body}")
        if table_list:
            tables_md = "\n\n"  "\n\n".join(table_list)

    return (
        f"# {title}\n\n"
        f"- **URL:** {url}\n"
        f"- **Phương pháp cào:** {method}\n"
        f"- **Proxy:** `{proxy}`\n"
        f"{tables_md}\n\n"
        f"---\n\n"
        f"{content}"
    )