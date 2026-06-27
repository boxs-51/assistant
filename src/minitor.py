import time
import json
from typing import Dict, Any, Set

class ExecutionMonitor:
    def __init__(self, max_steps: int = 10, max_duration_secs: int = 60, max_repeated_calls: int = 2):
        # Cấu hình các ngưỡng bảo vệ
        self.max_steps = max_steps
        self.max_duration_secs = max_duration_secs
        self.max_repeated_calls = max_repeated_calls
        
        # Các biến trạng thái của chu kỳ hiện tại
        self.current_step = 0
        self.start_time = 0.0
        self.execution_fingerprints = [] # Nâng cấp: Lưu lại "dấu vân tay" của mỗi hành động
        
        # Danh sách các công cụ nhạy cảm BẮT BUỘC phải hỏi ý kiến con người (HITL)
        self.sensitive_tools = ["delete_file", "execute_sql_write", "send_email", "pay_invoice"]
        
        # Nâng cấp: Các key trong tool_args cần được che giấu khi hiển thị
        self.sensitive_arg_keys: Set[str] = {"password", "secret", "token", "api_key", "credit_card", "cvv"}

    def reset(self):
        """Reset lại trạng thái của Monitor để chuẩn bị cho một chu kỳ yêu cầu mới."""
        self.current_step = 0
        self.start_time = time.time()
        self.execution_fingerprints.clear()

    def _create_fingerprint(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Tạo một 'dấu vân tay' duy nhất cho một hành động gọi tool."""
        # Sắp xếp các key của arguments để đảm bảo thứ tự không ảnh hưởng đến hash
        sorted_args = json.dumps(tool_args, sort_keys=True)
        return f"{tool_name}:{sorted_args}"

    def _mask_sensitive_args(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Che giấu các giá trị của tham số nhạy cảm trước khi hiển thị."""
        masked_args = {}
        for key, value in tool_args.items():
            if key.lower() in self.sensitive_arg_keys:
                masked_args[key] = "[HIDDEN]"
            else:
                masked_args[key] = value
        return masked_args

    def validate_and_route_execution(self, tool_name: str, tool_args: Dict[str, Any], mode: str = "hybrid") -> str:
        """
        Quyết định luồng thực thi: 'approve' (chạy luôn), 'hitl' (chờ người duyệt), 'reject' (chặn)
        mode: 'auto' (tự động hoàn toàn), 'manual' (hỏi mọi thứ), 'hybrid' (hỏi khi gặp tool nhạy cảm)
        """
        self.current_step += 1
        
        # 1. Bảo vệ chống quá thời gian (Timeout Guard)
        if (time.time() - self.start_time) > self.max_duration_secs:
            print(f"🛑 [Monitor] Phát hiện Timeout! Chu kỳ đã chạy quá {self.max_duration_secs} giây.")
            return "reject_timeout"

        # 2. Bảo vệ chống vòng lặp vô tận (Step Count)
        if self.current_step > self.max_steps:
            print(f"🛑 [Monitor] Phát hiện vòng lặp! Chu kỳ đã vượt quá {self.max_steps} bước.")
            return "reject_loop"

        # 3. Nâng cấp: Bảo vệ chống lặp lại hành động (Fingerprint Loop Detection)
        fingerprint = self._create_fingerprint(tool_name, tool_args)
        # Đếm số lần fingerprint này xuất hiện trong các bước gần nhất
        recent_history = self.execution_fingerprints[-(self.max_repeated_calls - 1):]
        if all(fp == fingerprint for fp in recent_history) and len(recent_history) == self.max_repeated_calls - 1:
            print(f"🛑 [Monitor] Phát hiện lặp lại hành động! Tool '{tool_name}' được gọi với cùng tham số {self.max_repeated_calls} lần liên tiếp.")
            return "reject_loop"
        self.execution_fingerprints.append(fingerprint)

        # 4. Định tuyến theo cơ chế Human-in-the-loop (HITL)
        if mode == "manual":
            return "hitl"
            
        if mode == "hybrid":
            if tool_name in self.sensitive_tools:
                return "hitl"
        
        # Mặc định là 'approve' cho mode 'auto' hoặc 'hybrid' với tool không nhạy cảm
        return "approve"

    def get_approval_context(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Nâng cấp: Chuẩn bị context để gửi đi chờ duyệt, đã che giấu thông tin nhạy cảm."""
        return {
            "tool_name": tool_name,
            "tool_args": self._mask_sensitive_args(tool_args)
        }