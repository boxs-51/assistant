class SmartRouter:
    def __init__(self, config):
        self.config = config

    def route_request(self, user_request: str) -> str:
        # Logic phân loại đơn giản dựa trên từ khóa hoặc độ dài. 
        # Có thể nâng cấp lên dùng một mô hình LLM siêu nhỏ tại đây.
        lowered_req = user_request.lower()
        if "phân tích sâu" in lowered_req or "tổng hợp báo cáo tài chính" in lowered_req:
            return "cloud"
        return "local"