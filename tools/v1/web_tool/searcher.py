import asyncio
import logging
from typing import Dict, List
from bs4 import BeautifulSoup

try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    AsyncSession = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from .utils import clean_ddg_url, clean_whitespace

logger = logging.getLogger(__name__)

class WebSearcher:
    """Quản lý các phương thức tìm kiếm web qua DDGS API và HTML Fallback."""

    def __init__(self, default_timeout: int = 10):
        self.default_timeout = default_timeout

    async def _search_fallback_html(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        if not AsyncSession:
            return []
        try:
            url = "https://html.duckduckgo.com/html/"
            data = {"q": query.strip()}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://html.duckduckgo.com/",
            }
            async with AsyncSession(impersonate="chrome120") as session:
                res = await session.post(url, data=data, headers=headers, timeout=self.default_timeout)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, "html.parser")
                    results = []
                    for result_div in soup.select(".result:not(.result--ad)"):
                        a_tag = result_div.select_one("a.result__a")
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        title = a_tag.get_text(strip=True)
                        clean_href = clean_ddg_url(href)
                        snip_elem = result_div.select_one(".result__snippet")
                        snippet = snip_elem.get_text(strip=True) if snip_elem else ""

                        if clean_href and title and clean_href.startswith("http"):
                            results.append({
                                "title": clean_whitespace(title),
                                "href": clean_href.strip(),
                                "body": clean_whitespace(snippet)
                            })
                            if len(results) >= max_results:
                                break
                    return results
        except Exception as e:
            logger.error(f"Lỗi Fallback Search: {e}")
        return []

    async def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        results = []
        if DDGS is not None:
            try:
                def _sync_ddgs():
                    with DDGS() as ddgs:
                        return list(ddgs.text(query.strip(), max_results=max_results))
                response = await asyncio.to_thread(_sync_ddgs)
                for item in response:
                    results.append({
                        "title": clean_whitespace(item.get("title", "")),
                        "href": item.get("href", "").strip(),
                        "body": clean_whitespace(item.get("body", "")),
                    })
            except Exception:
                results = []

        if not results:
            results = await self._search_fallback_html(query=query, max_results=max_results)

        return results