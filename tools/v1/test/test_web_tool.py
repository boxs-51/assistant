import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import time

# Import WebTool từ package web_tool
from tools.v1.web_tool import WebTool, run, TOOL_METADATA


class TestWebToolComprehensive(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """Khởi tạo instance WebTool trước mỗi bài test."""
        self.proxies = ["http://1.1.1.1:8080", "http://2.2.2.2:8080", "http://3.3.3.3:8080"]
        self.tool = WebTool(proxy_list=self.proxies, default_timeout=5, min_content_length=50)

    # ================= 1. TEST QUẢN LÝ PROXY & LATENCY =================

    @patch("tools.v1.web_tool.proxy.cffi_requests")
    def test_proxy_testing_and_latency_sorting(self, mock_cffi):
        """Test kiểm tra Proxy song song và sắp xếp theo Latency tăng dần."""
        def fake_get(url, proxies, timeout, impersonate):
            proxy_url = proxies["http"]
            mock_res = MagicMock()
            mock_res.status_code = 200
            if "1.1.1.1" in proxy_url:
                time.sleep(0.15)  # Chậm nhất
            elif "2.2.2.2" in proxy_url:
                time.sleep(0.02)  # Nhanh nhất
            elif "3.3.3.3" in proxy_url:
                time.sleep(0.08)  # Vừa
            return mock_res

        mock_cffi.get.side_effect = fake_get

        valid_count = self.tool.proxy_manager.refresh_proxy_pool(timeout=2)

        self.assertEqual(valid_count, 3)
        self.assertTrue(self.tool.proxy_manager.valid_proxies[0].startswith("http://2.2.2.2"))
        self.assertTrue(self.tool.proxy_manager.valid_proxies[-1].startswith("http://1.1.1.1"))

    def test_get_fastest_proxy_selection(self):
        """Test chọn ngẫu nhiên Proxy trong Top N IP nhanh nhất."""
        self.tool.proxy_manager.valid_proxies = ["http://fast1:8080", "http://fast2:8080", "http://slow:8080"]
        proxy = self.tool.proxy_manager.get_fastest_proxy(top_n=2)
        self.assertIn(proxy["http"], ["http://fast1:8080", "http://fast2:8080"])
        self.assertNotIn(proxy["http"], ["http://slow:8080"])

    # ================= 2. TEST TÌM KIẾM DUCKDUCKGO =================

    @patch("tools.v1.web_tool.searcher.DDGS")
    async def test_search_success(self, mock_ddgs_cls):
        """Test chức năng tìm kiếm trả về chuỗi Markdown định dạng."""
        mock_ddgs_instance = MagicMock()
        mock_ddgs_cls.return_value.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_instance.text.return_value = [
            {"title": "Python Tutorial", "href": "https://python.org", "body": "Learn Python"}
        ]

        results = await self.tool.search("python programming", max_results=1)
        self.assertIsInstance(results, str)
        self.assertIn("Python Tutorial", results)
        self.assertIn("https://python.org", results)

    async def test_search_empty_query(self):
        """Test tìm kiếm với từ khóa rỗng."""
        res = await self.tool.search("   ")
        self.assertTrue(res.startswith("**Lỗi:**"))

    # ================= 3. TEST CÀO DỮ LIỆU (SCRAPE) =================

    @patch("tools.v1.web_tool.core.extract_clean_content")
    async def test_scrape_static_success(self, mock_extract):
        """Test cào tĩnh thành công bằng curl_cffi và làm sạch nội dung."""
        html = "<html><head><title>Test Page</title></head><body><p>Hello World Content</p></body></html>"
        content = (
            "Hello World Content Long Enough, this is an extended string designed "
            "to surpass two hundred characters in total length. It contains multiple "
            "phrases, descriptive words, and filler sentences to ensure that the "
            "overall content is sufficiently verbose, detailed, and clearly longer "
            "than the required threshold of two hundred characters."
        )
        mock_extract.return_value = content

        with patch.object(self.tool.scraper, "fetch_static", new_callable=AsyncMock, return_value=(html, 200, "OK")):
            res = await self.tool.scrape("https://example.com")
            self.assertIn("Test Page", res)
            self.assertIn("Static (curl_cffi)", res)
            self.assertIn(content, res)

    @patch("tools.v1.web_tool.core.extract_clean_content")
    async def test_scrape_fallback_to_playwright_when_short_content(self, mock_extract):
        """Test tự động nhảy sang Playwright khi nội dung cào tĩnh quá ngắn/rỗng."""
        def fake_extract(html, *args, **kwargs):
            if "Rendered JS Content" in html:
                return "Rendered JS Content is here and very long threshold string"
            return "Too short"

        mock_extract.side_effect = fake_extract
        js_html = "<html><head><title>JS App Title</title></head><body>Rendered JS Content is here and very long threshold string</body></html>"

        with patch.object(self.tool.scraper, "fetch_static", new_callable=AsyncMock, return_value=("<html><body>Empty</body></html>", 200, "OK")):
            with patch.object(self.tool.scraper, "fetch_dynamic_js_stealth", new_callable=AsyncMock, return_value=(js_html, {}, None, "OK")):
                res = await self.tool.scrape("https://js-app.com")

                self.assertIn("Dynamic JS (Playwright)", res)
                self.assertIn("JS App Title", res)
                self.assertIn("Rendered JS Content is here and very long", res)

    async def test_scrape_403_forbidden_removes_proxy(self):
        """Test khi cào tĩnh bị lỗi 403 HTTP, Proxy bị lỗi sẽ tự động bị loại khỏi pool."""
        self.tool.proxy_manager.valid_proxies = ["http://bad-proxy:8080"]

        with patch.object(self.tool.scraper, "fetch_static", new_callable=AsyncMock, return_value=(None, 403, "HTTP 403 Forbidden")):
            await self.tool.scrape("https://blocked.com", max_retries=1)
            self.assertNotIn("http://bad-proxy:8080", self.tool.proxy_manager.valid_proxies)

    async def test_scrape_max_chars_limit(self):
        """Test giới hạn số lượng ký tự đầu ra max_chars."""
        raw_html = "<html><body>" + "A" * 500 + "</body></html>"
        with patch.object(self.tool.scraper, "fetch_static", new_callable=AsyncMock, return_value=(raw_html, 200, "OK")):
            with patch("tools.v1.web_tool.core.extract_clean_content", return_value="A" * 500):
                res = await self.tool.scrape("https://long-text.com", max_chars=100)
                self.assertIn("*(Nội dung đã bị cắt bớt do vượt giới hạn độ dài)*", res)

    # ================= 4. TEST ENTRYPOINT RUN & EXECUTE =================

    @patch.object(WebTool, "search", new_callable=AsyncMock)
    async def test_execute_search_routing(self, mock_search):
        """Test hàm execute điều hướng đúng sang search."""
        mock_search.return_value = "Search Results Markdown"
        res = await self.tool.execute(action="search", query="test query", max_results=3)
        mock_search.assert_called_once_with(query="test query", max_results=3, output_format="markdown")

    @patch.object(WebTool, "scrape", new_callable=AsyncMock)
    async def test_execute_scrape_routing(self, mock_scrape):
        """Test hàm execute điều hướng đúng sang scrape."""
        mock_scrape.return_value = "Scraped Text"
        res = await self.tool.execute(action="scrape", url="https://test.com", force_js=True)
        mock_scrape.assert_called_once_with(
            url="https://test.com",
            force_js=True,
            wait_selector=None,
            timeout=None,
            max_chars=100000,
            captcha_api_key=None,
            output_format="markdown",
            clean_noise=True,
            deduplicate=True,
        )

    def test_run_function_wrapper(self):
        """Test hàm entrypoint global `run()`."""
        with patch("tools.v1.web_tool.WebTool.execute") as mock_exec:
            mock_exec.return_value = "OK"
            res = run("search", query="hello")
            mock_exec.assert_called_once_with(action="search", query="hello")


if __name__ == "__main__":
    unittest.main()