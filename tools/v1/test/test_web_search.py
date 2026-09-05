import unittest
from unittest.mock import MagicMock, patch
from tools.v1.web_search import web_search


class TestWebSearch(unittest.TestCase):

    def test_empty_query(self):
        res = web_search("")
        self.assertTrue(isinstance(res, str) and res.startswith("Lỗi:"))

    @patch("tools.v1.web_search.DDGS")
    def test_successful_search(self, mock_ddgs_class):
        mock_instance = MagicMock()
        mock_ddgs_class.return_value.__enter__.return_value = mock_instance
        mock_instance.text.return_value = [
            {
                "title": "Python Documentation",
                "href": "https://python.org",
                "body": "Official Python docs",
            }
        ]

        results = web_search("python docs", max_results=1)

        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Python Documentation")
        self.assertEqual(results[0]["href"], "https://python.org")

    @patch("tools.v1.web_search.DDGS")
    def test_search_exception(self, mock_ddgs_class):
        mock_instance = MagicMock()
        mock_ddgs_class.return_value.__enter__.return_value = mock_instance
        mock_instance.text.side_effect = Exception("Mất kết nối mạng")

        res = web_search("python")
        self.assertTrue(isinstance(res, str) and res.startswith("Lỗi khi"))


if __name__ == "__main__":
    unittest.main()