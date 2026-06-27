class ModelSelector:
    """
    Lớp này thay thế SmartRouter cũ, với logic đơn giản hơn: chỉ chọn tên model
    để gửi đến AI Gateway, thay vì chọn provider.
    """
    def __init__(self, config: dict):
        routing_config = config.get("model_routing", {})
        self.high_complexity_keywords = routing_config.get("high_complexity_keywords", [])
        self.default_local_model = routing_config.get("default_local_model", "local-model")
        self.default_cloud_model = routing_config.get("default_cloud_model", "cloud-model")
        self.locked_model = None

    def lock_model(self, model_name: str = None):
        """Khóa việc lựa chọn vào một model cụ thể, hoặc mở khóa nếu là None."""
        self.locked_model = model_name

    def select_model(self, user_request: str, active_tasks: list = []) -> str:
        """Chọn model phù hợp dựa trên độ phức tạp của yêu cầu."""
        if self.locked_model:
            return self.locked_model

        # Nếu đang trong một chuỗi tác vụ, ưu tiên model mạnh hơn để duy trì ngữ cảnh
        if len(active_tasks) > 0:
            return self.default_cloud_model

        normalized_req = user_request.lower()
        # Nếu yêu cầu chứa từ khóa phức tạp, dùng model cloud
        if any(kw in normalized_req for kw in self.high_complexity_keywords):
            return self.default_cloud_model
        
        # Mặc định dùng model local cho các tác vụ đơn giản
        return self.default_local_model