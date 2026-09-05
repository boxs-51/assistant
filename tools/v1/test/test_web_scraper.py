import unittest
from unittest.mock import MagicMock, patch
import requests
from tools.v1.web_scraper import scrape_webpage


class TestWebScraper(unittest.TestCase):

    def test_invalid_url_format(self):
        result = scrape_webpage("ftp://example.com")
        self.assertTrue(result.startswith("Lỗi: URL không hợp lệ"))

    @patch("tools.v1.web_scraper.requests.get")
    def test_scrape_success_and_filter_tags(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html>
            <head><style>body { color: red; }</style></head>
            <body>
                <header>Thanh Điều Hướng</header>
                <script>console.log('ignore me');</script>
                <h1>Tiêu đề bài viết</h1>
                <p>Nội dung chi tiết của trang web.</p>
                <footer>Chân trang</footer>
            </body>
        </html>
        """
        mock_response.encoding = "utf-8"
        mock_get.return_value = mock_response

        result = scrape_webpage("https://example.com")

        # Kiểm tra nội dung chính được giữ lại
        self.assertIn("Tiêu đề bài viết", result)
        self.assertIn("Nội dung chi tiết của trang web.", result)

        # Kiểm tra các thẻ rác đã bị lọc bỏ
        self.assertNotIn("console.log", result)
        self.assertNotIn("Thanh Điều Hướng", result)
        self.assertNotIn("Chân trang", result)

    @patch("tools.v1.web_scraper.requests.get")
    def test_scrape_timeout(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()
        result = scrape_webpage("https://example.com")
        self.assertIn("Timeout", result)


if __name__ == "__main__":
    unittest.main()