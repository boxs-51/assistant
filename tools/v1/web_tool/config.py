import logging

logger = logging.getLogger(__name__)

DEFAULT_IMPERSONATE_PROFILES = [
    "chrome120",
    "chrome119",
    "edge120",
    "edge101",
    "safari17_0",
]

TOOL_METADATA = {
    "name": "web_tool",
    "description": "Công cụ truy cập Web toàn diện: Tìm kiếm từ khóa hoặc trích xuất nội dung văn bản tối ưu từ URL dưới dạng Markdown, JSON hoặc Text.",
    "base_risk": "MEDIUM",
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "scrape", "scrape_many"],
                "description": "Thao tác cần thực hiện: 'search' để tìm kiếm, 'scrape' để trích xuất URL, 'scrape_many' để cào nhiều URL song song.",
            },
            "query": {
                "type": "string",
                "description": "Từ khóa tìm kiếm (Bắt buộc với action='search').",
            },
            "url": {
                "type": "string",
                "description": "Đường dẫn URL cần cào (Bắt buộc với action='scrape').",
            },
            "urls": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "Danh sách URL cần cào song song (Bắt buộc với action='scrape_many').",
            },
            "output_format": {
                "type": "string",
                "enum": ["markdown", "json", "text"],
                "description": "Định dạng kết quả trả về (Mặc định: 'markdown').",
            },
            "force_js": {
                "type": "boolean",
                "description": "Ép buộc sử dụng Playwright render JavaScript ngay từ đầu.",
            },
            "wait_selector": {
                "type": "string",
                "description": "CSS/XPath Selector cần chờ hiển thị trước khi lấy nội dung.",
            },
            "max_results": {
                "type": "integer",
                "description": "Số lượng kết quả tìm kiếm tối đa (Mặc định: 5).",
            },
            "max_chars": {
                "type": "integer",
                "description": "Số lượng ký tự tối đa cắt lấy (Mặc định: 100000).",
            },
            "timeout": {
                "type": "integer",
                "description": "Thời gian chờ tối đa tính bằng giây (Mặc định: 10).",
            },
            "clean_noise": {
                "type": "boolean",
                "description": "Lọc tự động các phần tử quảng cáo, cookie consent, nav, header/footer.",
                "default": True,
            },
            "deduplicate": {
                "type": "boolean",
                "description": "Tự động phát hiện và loại bỏ các đoạn văn/dòng bị trùng lặp.",
                "default": True,
            },
        },
        "required": ["action"],
    },
}