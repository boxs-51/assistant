from tools.v1.web_scraper import scrape_webpage
from tools.v1.web_search import web_search

# 1. Tìm kiếm web
search_results = web_search("Python programming language", max_results=1)

if isinstance(search_results, list) and search_results:
    first_url = search_results[0]["href"]
    print(f"Đang đọc nội dung từ: {first_url}\n" + "-" * 40)

    # 2. Đọc nội dung bài viết
    content = scrape_webpage(first_url)
    print(content[:1000])  # In 1000 ký tự đầu tiên