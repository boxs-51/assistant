import asyncio
import random
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

from .config import DEFAULT_IMPERSONATE_PROFILES
from .extractors import extract_clean_content, extract_tables_and_charts
from .formatters import format_scrape_results, format_search_results
from .proxy import ProxyManager
from .scraper import WebScraper
from .searcher import WebSearcher
from .utils import clean_whitespace

class WebTool:
    """Class điều phối toàn bộ tương tác Web cho AI Agent."""

    def __init__(
        self,
        proxy_list: Optional[List[str]] = None,
        impersonate_profiles: Optional[List[str]] = None,
        default_timeout: int = 10,
        max_response_bytes: int = 10 * 1024 * 1024,
        min_content_length: int = 200,
        default_max_chars: int = 100000,
        captcha_api_key: Optional[str] = None,
        max_concurrency: int = 5,
    ):
        self.default_timeout = default_timeout
        self.min_content_length = min_content_length
        self.default_max_chars = default_max_chars
        self.captcha_api_key = captcha_api_key
        self.profiles = impersonate_profiles or DEFAULT_IMPERSONATE_PROFILES

        self.proxy_manager = ProxyManager(proxy_list)
        self.searcher = WebSearcher(default_timeout=default_timeout)
        self.scraper = WebScraper(max_response_bytes=max_response_bytes, max_concurrency=max_concurrency)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Giải phóng triệt để tài nguyên browser và session."""
        if hasattr(self, "scraper") and self.scraper:
            await self.scraper.close()

    async def search(self, query: str, max_results: int = 5, output_format: str = "markdown") -> str:
        """Tìm kiếm thông tin và xuất kết quả theo định dạng yêu cầu."""
        if not query or not query.strip():
            return "**Lỗi:** Từ khóa tìm kiếm không được để trống."

        results = await self.searcher.search(query=query, max_results=max_results)
        return format_search_results(query=query, results=results, fmt=output_format)

    async def scrape(
        self,
        url: str,
        force_js: bool = False,
        wait_selector: Optional[str] = None,
        timeout: Optional[int] = None,
        max_chars: Optional[int] = None,
        captcha_api_key: Optional[str] = None,
        output_format: str = "markdown",
        max_retries: int = 3,
        clean_noise: bool = True,
        deduplicate: bool = True,
    ) -> str:
        """Cào dữ liệu từ URL và xuất kết quả theo định dạng yêu cầu."""
        url = url.rstrip(")").strip()
        if not url or not url.startswith(("http://", "https://")):
            return "**Lỗi:** URL không hợp lệ (phải bắt đầu bằng `http://` hoặc `https://`)."

        req_timeout = timeout or self.default_timeout
        char_limit = max_chars or self.default_max_chars
        active_captcha_key = captcha_api_key or self.captcha_api_key

        if self.proxy_manager.raw_proxy_list and not self.proxy_manager.valid_proxies:
            self.proxy_manager.refresh_proxy_pool()

        raw_html = None
        raw_html_js = None
        structured_data = None
        method_used = "Static (curl_cffi)"
        used_proxy_dict = self.proxy_manager.get_fastest_proxy()
        used_proxy_str = used_proxy_dict["http"] if used_proxy_dict else "Direct (No Proxy)"

        if not force_js:
            for attempt in range(1, max_retries + 1):
                profile = random.choice(self.profiles)
                html, status, err_msg = await self.scraper.fetch_static(url, req_timeout, profile, used_proxy_dict)

                if status == 403:
                    if used_proxy_dict and used_proxy_dict["http"] in self.proxy_manager.valid_proxies:
                        self.proxy_manager.valid_proxies.remove(used_proxy_dict["http"])
                    used_proxy_dict = self.proxy_manager.get_fastest_proxy()
                    used_proxy_str = used_proxy_dict["http"] if used_proxy_dict else "Direct (No Proxy)"
                    continue

                if html:
                    raw_html = html
                    break

        clean_markdown = (
            await asyncio.to_thread(extract_clean_content, raw_html, url, clean_noise, deduplicate) if raw_html else None
        )

        if force_js or not clean_markdown or len(clean_markdown) < self.min_content_length:
            method_used = "Dynamic JS (Playwright)"
            raw_html_js, cookie, structured_data, js_err = await self.scraper.fetch_dynamic_js_stealth(
                url,
                req_timeout + 20,
                wait_selector,
                proxy=used_proxy_str if used_proxy_str != "Direct (No Proxy)" else None,
                captcha_api_key=active_captcha_key
            )
            if raw_html_js:
                clean_markdown = await asyncio.to_thread(
                    extract_clean_content, raw_html_js, url, clean_noise, deduplicate
                )

        if not structured_data and (raw_html or raw_html_js):
            structured_data = await extract_tables_and_charts(raw_html_js or raw_html)

        if not clean_markdown:
            return f"**Lỗi cào dữ liệu (`{url}`):** Không thể trích xuất nội dung từ trang web này."

        if len(clean_markdown) > char_limit:
            clean_markdown = clean_markdown[:char_limit] + "\n\n*(Nội dung đã bị cắt bớt do vượt giới hạn độ dài)*"

        page_title = "Không có tiêu đề"
        final_html = raw_html_js or raw_html
        if final_html:
            try:
                def _parse_title():
                    soup = BeautifulSoup(final_html, "html.parser")
                    return clean_whitespace(soup.title.string) if soup.title and soup.title.string else None
                title_res = await asyncio.to_thread(_parse_title)
                if title_res:
                    page_title = title_res
            except Exception:
                pass

        return format_scrape_results(
            url=url,
            title=page_title,
            method=method_used,
            proxy=used_proxy_str,
            content=clean_markdown,
            structured_data=structured_data,
            fmt=output_format
        )
    
    async def scrape_many(
        self,
        urls: List[str],
        force_js: bool = False,
        wait_selector: Optional[str] = None,
        timeout: Optional[int] = None,
        max_chars: Optional[int] = None,
        captcha_api_key: Optional[str] = None,
        output_format: str = "markdown",
        clean_noise: bool = True,
        deduplicate: bool = True,
    ) -> List[str]:
        """Cào dữ liệu từ hàng loạt URL song song với asyncio.gather()."""
        tasks = [
            self.scrape(
                url=u,
                force_js=force_js,
                wait_selector=wait_selector,
                timeout=timeout,
                max_chars=max_chars,
                captcha_api_key=captcha_api_key,
                output_format=output_format,
                clean_noise=clean_noise,
                deduplicate=deduplicate,
            )
            for u in urls
        ]
        return await asyncio.gather(*tasks)

    async def execute(
        self,
        action: str,
        query: Optional[str] = None,
        url: Optional[str] = None,
        urls: Optional[List[str]] = None,
        output_format: str = "markdown",
        force_js: bool = False,
        wait_selector: Optional[str] = None,
        max_results: int = 5,
        max_chars: int = 100000,
        timeout: Optional[int] = None,
        captcha_api_key: Optional[str] = None,
        clean_noise: bool = True,
        deduplicate: bool = True,
        **kwargs,
    ) -> str:
        try:
            if action == "search":
                if not query:
                    return "**Lỗi:** Action `search` yêu cầu tham số `query`."
                return await self.search(query=query, max_results=max_results, output_format=output_format)

            elif action in ("scrape", "scrape_webpage", "read"):
                if not url:
                    return f"**Lỗi:** Action `{action}` yêu cầu tham số `url`."
                return await self.scrape(
                    url=url,
                    force_js=force_js,
                    wait_selector=wait_selector,
                    timeout=timeout,
                    max_chars=max_chars,
                    captcha_api_key=captcha_api_key,
                    output_format=output_format,
                    clean_noise=clean_noise,
                    deduplicate=deduplicate,
                )
            elif action == "scrape_many":
                if not urls:
                    return "**Lỗi:** Action `scrape_many` yêu cầu danh sách tham số `urls`."
                return await self.scrape_many(
                    urls=urls,
                    force_js=force_js,
                    wait_selector=wait_selector,
                    timeout=timeout,
                    max_chars=max_chars,
                    captcha_api_key=captcha_api_key,
                    output_format=output_format,
                    clean_noise=clean_noise,
                    deduplicate=deduplicate,
                )
            else:
                return f"**Lỗi:** Action `{action}` không hợp lệ. Chọn `search`, `scrape` hoặc `scrape_many`."
        finally:
            await self.close()
