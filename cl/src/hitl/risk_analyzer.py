import re
from typing import Dict, Any, Tuple

class RiskAnalyzer:
    DEFAULT_RISK_LEVEL = "MEDIUM"  # Mức rủi ro an toàn mặc định khi thiếu thông tin

    def __init__(self, constitution_text: str):
        self.constitution = constitution_text

    def evaluate_action(self, tool_meta: Dict[str, Any], tool_args: str) -> Tuple[str, str]:
        tool_name = tool_meta.get("name", "").lower()
        
        # 1. LẤY BASE_RISK VỚI CƠ CHẾ FALLBACK
        # Nếu không khai báo base_risk, gán bằng DEFAULT_RISK_LEVEL ("MEDIUM")
        base_risk = tool_meta.get("base_risk")
        is_fallback = False
        
        if not base_risk:
            base_risk = self.DEFAULT_RISK_LEVEL
            is_fallback = True

        # 2. KIỂM TRA PATTERN NGUY HIỂM BẤT KỂ CÓ BASE_RISK HAY KHÔNG
        danger_patterns = tool_meta.get("danger_patterns", [])
        # Bổ sung các pattern nguy hiểm toàn cục (Global Danger Patterns)
        global_danger_patterns = [
            r"\b(delete|drop|remove|truncate|destroy|format)\b",
            r"\b(rm\s+-|del\s+/|sudo|chmod|chown)\b",
            r"\b(exec|eval|system)\b"
        ]
        
        all_patterns = danger_patterns + global_danger_patterns
        for pattern in all_patterns:
            if re.search(pattern, tool_args, re.IGNORECASE):
                return "CRITICAL", f"Phát hiện hành vi nguy hại cao khớp với pattern: `{pattern}`"

        # 3. SUY LUẬN THEO TIỀN TỐ TÊN TOOL (Nếu phải dùng Fallback)
        if is_fallback:
            if any(tool_name.startswith(prefix) for prefix in ["get_", "read_", "list_", "check_", "fetch_"]):
                return "LOW", "Không có base_risk, nhưng Tool thuộc nhóm đọc dữ liệu an toàn (read-only)."
            elif any(tool_name.startswith(prefix) for prefix in ["write_", "execute_", "run_", "delete_", "update_"]):
                return "HIGH", "Không có base_risk, nhưng Tool thuộc nhóm ghi/thực thi dữ liệu (write/exec)."

        reason = f"Đánh giá dựa trên base_risk ({base_risk})" if not is_fallback else f"Thiếu base_risk -> Dùng mức mặc định ({base_risk})."
        return base_risk, reason