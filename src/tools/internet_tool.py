from tools.base_tool import BaseTool
import requests

class FetchWebTool(BaseTool):
    def __init__(self):
        # Định nghĩa Schema cho các tham số đầu vào
        params = {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Địa chỉ URL đầy đủ của trang web cần lấy dữ liệu (ví dụ: https://example.com)"
                }
            },
            "required": ["url"]
        }
        super().__init__(
            name="fetch_web_content",
            description="Lấy toàn bộ nội dung văn bản thô từ một trang web cụ thể khi người dùng cung cấp link.",
            parameters=params
        )

    def execute(self, url: str) -> str:
        try:
            # Giả lập hoặc gọi thật API/Cào dữ liệu
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                # Trả về tạm thời 500 ký tự đầu để demo
                return response.text[:500] 
            return f"Lỗi: Không thể truy cập trang web. Mã lỗi {response.status_code}"
        except Exception as e:
            return f"Lỗi hệ thống khi cào web: {str(e)}"