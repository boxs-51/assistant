import re

class GuardrailSystem:
    def __init__(self):
        # Bộ từ khóa chặn Prompt Injection phổ biến
        self.forbidden_input_patterns = [
            r"ignore previous instructions",
            r"bỏ qua các lệnh trước đó",
            r"system prompt",
            r"bí mật hệ thống",
            r"từ giờ hãy đóng vai làm"
        ]
        # Bộ từ khóa chặn rò rỉ thông tin nhạy cảm ở đầu ra
        self.forbidden_output_patterns = [
            r"sk-[a-zA-Z0-9]{48}", # Định dạng OpenAI API Key
            r"password\s*=\s*['\"][^'\"]+['\"]",
            r"INTERNAL_SERVER_ERROR"
        ]

    def verify_input(self, user_request: str) -> bool:
        """Kiểm tra yêu cầu đầu vào xem có dấu hiệu tấn công hay không"""
        lowered_input = user_request.lower()
        for pattern in self.forbidden_input_patterns:
            if re.search(pattern, lowered_input):
                print(f"🚨 [Guardrail] CẢNH BÁO: Phát hiện Input độc hại vi phạm pattern '{pattern}'!")
                return False
        return True

    def verify_output(self, ai_response: str) -> str:
        """Kiểm tra đầu ra của AI trước khi gửi cho người dùng"""
        # 1. Kiểm tra rò rỉ key/mật khẩu
        for pattern in self.forbidden_output_patterns:
            if re.search(pattern, ai_response):
                print(f"🚨 [Guardrail] CHẶN ĐỨNG: AI cố tình rò rỉ thông tin bảo mật!")
                return "Xin lỗi, tôi không thể xử lý yêu cầu này do vi phạm chính sách bảo mật đầu ra."
        
        # 2. Xử lý fallback nếu AI bị văng code lỗi thô
        if "traceback (most recent call last)" in ai_response.lower():
            return "Hệ thống gặp sự cố xử lý logic, tôi đang điều chỉnh lại cấu trúc."
            
        return ai_response