from typing import Dict, List, Union

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


def web_search(
    query: str, max_results: int = 5
) -> Union[List[Dict[str, str]], str]:
    """Tìm kiếm thông tin trên web thông qua DuckDuckGo.

    Args:
        query (str): Từ khóa hoặc câu hỏi cần tìm kiếm.
        max_results (int): Số lượng kết quả tối đa cần lấy (mặc định: 5).

    Returns:
        Union[List[Dict[str, str]], str]: Danh sách các dict gồm (title, href,
        body) hoặc thông báo lỗi.
    """
    if DDGS is None:
        return "Lỗi: Thư viện 'duckduckgo_search' chưa được cài đặt. Vui lòng chạy 'pip install duckduckgo-search'."

    if not query or not query.strip():
        return "Lỗi: Từ khóa tìm kiếm không được để trống."

    try:
        results = []
        with DDGS() as ddgs:
            response = ddgs.text(query, max_results=max_results)
            for item in response:
                results.append({
                    "title": item.get("title", ""),
                    "href": item.get("href", ""),
                    "body": item.get("body", ""),
                })
        return results
    except Exception as e:
        return f"Lỗi khi thực hiện tìm kiếm web: {str(e)}"