from typing import Dict, List, Optional, Union
import requests
from bs4 import BeautifulSoup

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


class WebTool:
    """Class quản lý các thao tác tương tác Web (Tìm kiếm & Cào dữ liệu trang web)."""

    def __init__(
        self,
        default_user_agent: Optional[str] = None,
        default_timeout: int = 10,
    ):
        self.default_timeout = default_timeout
        self.user_agent = default_user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    # ------------------------------------------------------------------
    # 1. TÌM KIẾM WEB (SEARCH)
    # ------------------------------------------------------------------
    def search(
        self, query: str, max_results: int = 5
    ) -> Union[List[Dict[str, str]], str]:
        """Tìm kiếm thông tin trên web thông qua DuckDuckGo."""
        if DDGS is None:
            return (
                "Lỗi: Thư viện 'duckduckgo_search' chưa được cài đặt. "
                "Vui lòng chạy 'pip install duckduckgo-search'."
            )

        if not query or not query.strip():
            return "Lỗi: Từ khóa tìm kiếm không được để trống."

        try:
            results = []
            with DDGS() as ddgs:
                response = ddgs.text(query.strip(), max_results=max_results)
                for item in response:
                    results.append({
                        "title": item.get("title", ""),
                        "href": item.get("href", ""),
                        "body": item.get("body", ""),
                    })

            if not results:
                return f"Thông báo: Không tìm thấy kết quả nào phù hợp cho từ khóa '{query}'."

            return results
        except Exception as e:
            return f"Lỗi khi thực hiện tìm kiếm web: {str(e)}"

    # ------------------------------------------------------------------
    # 2. TRÍCH XUẤT NỘI DUNG TRANG WEB (SCRAPE / READ)
    # ------------------------------------------------------------------
    def scrape(
        self, url: str, timeout: Optional[int] = None
    ) -> str:
        """Tải và trích xuất nội dung văn bản sạch từ một URL trang web."""
        if not url or not url.startswith(("http://", "https://")):
            return "Lỗi: URL không hợp lệ (phải bắt đầu bằng http:// hoặc https://)."

        headers = {"User-Agent": self.user_agent}
        req_timeout = timeout if timeout is not None else self.default_timeout

        try:
            response = requests.get(url, headers=headers, timeout=req_timeout)
            response.raise_for_status()

            # Tự động khắc phục lỗi font chữ/encoding
            if response.encoding is None or response.encoding.upper() == "ISO-8859-1":
                response.encoding = response.apparent_encoding

            soup = BeautifulSoup(response.text, "html.parser")

            # Loại bỏ các thẻ rác không chứa nội dung chính
            unwanted_tags = [
                "script", "style", "nav", "footer", "header",
                "noscript", "svg", "form", "aside", "iframe"
            ]
            for element in soup(unwanted_tags):
                element.decompose()

            # Lấy văn bản và dọn dẹp dòng trống/khoảng trắng thừa
            text = soup.get_text(separator="\n")
            lines = (line.strip() for line in text.splitlines())
            clean_text = "\n".join(chunk for chunk in lines if chunk)

            if not clean_text:
                return f"Cảnh báo: Trang web '{url}' không có nội dung văn bản để trích xuất."

            return clean_text

        except requests.exceptions.Timeout:
            return f"Lỗi: Kết nối tới '{url}' bị quá thời gian chờ (Timeout)."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "N/A"
            reason = e.response.reason if e.response is not None else ""
            return f"Lỗi HTTP khi truy cập '{url}': {status} {reason}"
        except requests.exceptions.RequestException as e:
            return f"Lỗi khi tải trang '{url}': {str(e)}"
        except Exception as e:
            return f"Lỗi không xác định khi đọc trang '{url}': {str(e)}"

    # ------------------------------------------------------------------
    # DISPATCHER / ENTRY POINT (ĐIỀU HƯỚNG BẰNG TÊN ACTION)
    # ------------------------------------------------------------------
    def execute(
        self,
        action: str,
        query: Optional[str] = None,
        url: Optional[str] = None,
        max_results: int = 5,
        timeout: Optional[int] = None,
    ) -> Union[List[Dict[str, str]], str]:
        """Hàm điều hướng chung cho AI Agent hoặc gọi động theo action."""
        if action == "search":
            if not query:
                return "Lỗi: Action 'search' yêu cầu tham số 'query'."
            return self.search(query=query, max_results=max_results)

        elif action in ("scrape", "scrape_webpage", "read"):
            if not url:
                return f"Lỗi: Action '{action}' yêu cầu tham số 'url'."
            return self.scrape(url=url, timeout=timeout)

        else:
            return f"Lỗi: Action '{action}' không hợp lệ. Chọn 'search' hoặc 'scrape'."


# ======================================================================
# BẢO TỒN TÍNH TƯƠNG THÍCH NGUỢC (Hàm Wrappers)
# ======================================================================
_default_web_tool = WebTool()


def web_search(query: str, max_results: int = 5) -> Union[List[Dict[str, str]], str]:
    """Hàm wrapper cho tìm kiếm (tương thích code cũ)."""
    return _default_web_tool.search(query=query, max_results=max_results)


def scrape_webpage(url: str, timeout: int = 10) -> str:
    """Hàm wrapper cho cào web (tương thích code cũ)."""
    return _default_web_tool.scrape(url=url, timeout=timeout)


def web_tool(action: str, **kwargs) -> Union[List[Dict[str, str]], str]:
    """Hàm wrapper dạng dispatcher chung."""
    return _default_web_tool.execute(action=action, **kwargs)