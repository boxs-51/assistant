import time
from typing import Callable, Optional, Dict, Any

class HITLManager:
    def __init__(self):
        self.approval_callback: Optional[Callable] = None

    def set_approval_callback(self, callback: Callable):
        """Đăng ký hàm UI dùng để mở Dialog/Bar xin ý kiến người dùng"""
        self.approval_callback = callback

    def request_approval(self, action_type: str, name: str, args: str, risk_level: str, reason: str) -> bool:
        """
        Nếu risk_level thuộc nhóm [HIGH, CRITICAL], yêu cầu con người xác nhận.
        """
        if risk_level in ["LOW", "MEDIUM"]:
            return True # Tự động thông qua

        if not self.approval_callback:
            print("⚠️ Cảnh báo: Không có giao diện phê duyệt! Tự động chặn để an toàn.")
            return False

        # Tạm dừng vòng lặp Async/Thread để chờ UI phản hồi
        return self.approval_callback({
            "type": action_type,
            "name": name,
            "args": args,
            "risk_level": risk_level,
            "reason": reason
        })