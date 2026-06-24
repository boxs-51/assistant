class SmartRouter:
    def __init__(self, config):
        self.config = config
        # Trạng thái hạ tầng hiện tại để bảo vệ chuyển đổi (Context Switching Protection)
        self.current_provider = "local" 
        self.locked_provider = False # Khóa hạ tầng nếu đang trong chuỗi đa tác vụ phức tạp

    def route_request(self, user_request: str, active_tasks: list = []) -> str:
        """Định tuyến với cơ chế bảo vệ ngữ cảnh"""
        # 1. Bảo vệ chuyển đổi: Nếu đang giải quyết chuỗi task phức tạp, giữ nguyên Provider
        if self.locked_provider or len(active_tasks) > 0:
            return self.current_provider

        # 2. Định tuyến thông minh (Nâng cấp sau này bằng LLM siêu nhỏ / Embedding classifier)
        lowered_req = user_request.lower()
        high_complexity_keywords = ["phân tích sâu", "tổng hợp", "chiến lược", "so sánh", "code"]
        
        if any(kw in lowered_req for kw in high_complexity_keywords):
            target = "cloud"
        else:
            target = "local"
            
        self.current_provider = target
        return target

    def execute_with_fallback(self, provider: str, generate_func_local, generate_func_cloud, prompt: str):
        """Cơ chế Fallback: Nếu Cloud sập -> gọi Local, nếu Local quá tải -> gọi Cloud"""
        try:
            if provider == "cloud":
                return generate_func_cloud(prompt)
            return generate_func_local(prompt)
        except Exception as e:
            print(f"⚠️ [Router] Lỗi hạ tầng {provider}: {e}. Đang kích hoạt Fallback...")
            fallback_provider = "local" if provider == "cloud" else "cloud"
            try:
                if fallback_provider == "cloud":
                    return generate_func_cloud(prompt)
                return generate_func_local(prompt)
            except Exception as fallback_e:
                return f"Lỗi hệ thống nghiêm trọng: Cả hai hạ tầng đều không phản hồi. Lỗi: {fallback_e}"