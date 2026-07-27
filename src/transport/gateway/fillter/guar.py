import re
from typing import AsyncGenerator
import unicodedata
import os
import yaml
import structlog

logger = structlog.get_logger()
class GuardrailSystem:
    def __init__(self, filter_dir="config/guardrails"):
        self.forbidden_input_patterns = []
        self.redaction_patterns = {}
        self._load_filters_from_directory(filter_dir)

    def _load_filters_from_directory(self, directory: str):
        """Tự động tải các bộ lọc từ các file YAML trong một thư mục."""
        logger.info("Loading guardrail filters", directory=directory)
        if not os.path.isdir(directory):
            logger.warning("Guardrail filter directory not found, skipping.", directory=directory)
            return

        for filename in sorted(os.listdir(directory)):
            if filename.endswith((".yaml", ".yml")):
                filepath = os.path.join(directory, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    filter_config = yaml.safe_load(f)
                    filter_name = filter_config.get('name', filename)
                    self.forbidden_input_patterns.extend(filter_config.get("input_filters", []))
                    self.redaction_patterns.update(filter_config.get("output_redaction", {}))
                    logger.debug("Loaded guardrail filter successfully", filter_name=filter_name)
        logger.info("Finished loading all guardrail filters.")

    def _normalize_text(self, text: str) -> str:
        """
        Nâng cấp: Chuẩn hóa văn bản để chống các kỹ thuật bypass.
        1. Chuyển về chữ thường.
        2. Loại bỏ dấu tiếng Việt.
        3. Chuẩn hóa khoảng trắng.
        4. Loại bỏ các ký tự điều khiển và ký tự ẩn.
        """
        # Loại bỏ dấu
        no_accents = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        # Chuyển về chữ thường
        lowered = no_accents.lower()
        # Loại bỏ ký tự điều khiển và chuẩn hóa khoảng trắng
        no_control_chars = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", lowered)
        normalized_space = re.sub(r"\s+", " ", no_control_chars).strip()
        return normalized_space

    def validate_input(self, user_request: str) -> bool:
        """Kiểm tra yêu cầu đầu vào xem có dấu hiệu tấn công hay không"""
        normalized_input = self._normalize_text(user_request)
        for pattern in self.forbidden_input_patterns:
            if re.search(pattern, normalized_input):
                logger.warning("Malicious input detected", violated_pattern=pattern)
                return False # Block the request
        return True

    def sanitize_output(self, ai_response: str) -> str:
        """
        Nâng cấp: Quét và che mờ (Redact) thông tin nhạy cảm trong đầu ra của AI
        thay vì chặn cứng, giúp duy trì trải nghiệm người dùng.
        """
        sanitized_response = ai_response
        found_sensitive_data = False

        for name, pattern in self.redaction_patterns.items():
            # Sử dụng re.sub với một hàm callback để có thể xử lý logic phức tạp hơn nếu cần
            def redact_match(match):
                nonlocal found_sensitive_data
                found_sensitive_data = True
                # Che toàn bộ chuỗi khớp được
                logger.info("Redacting sensitive data", redaction_type=name)
                return f"[{name.upper()}_REDACTED]"

            sanitized_response = re.sub(pattern, redact_match, sanitized_response, flags=re.IGNORECASE)

        # Xử lý đặc biệt cho các lỗi hệ thống để đưa ra thông báo thân thiện
        if "INTERNAL_SERVER_ERROR" in sanitized_response or "Traceback" in sanitized_response:
            return "Hệ thống gặp sự cố trong quá trình xử lý. Vui lòng thử lại sau."

        return sanitized_response

    async def sanitize_stream(self, stream: AsyncGenerator[str, None], buffer_size: int = 512) -> AsyncGenerator[str, None]:
        """
        Làm sạch dữ liệu từ một stream một cách an toàn, tránh cắt PII giữa các chunk.
        Sử dụng một buffer và một "vùng an toàn" (overlap) để đảm bảo các pattern
        bị chia cắt vẫn được phát hiện.
        """
        buffer = ""
        # Kích thước của vùng an toàn, nên lớn hơn độ dài của PII dài nhất có thể
        overlap_size = 100 

        async for chunk in stream:
            buffer += chunk
            
            # Chỉ xử lý và yield phần buffer lớn hơn kích thước an toàn
            while len(buffer) > overlap_size:
                # Phần buffer an toàn để xử lý
                safe_part = buffer[:-overlap_size]
                yield self.sanitize_output(safe_part)
                # Giữ lại phần cuối của buffer để kiểm tra lần sau
                buffer = buffer[-overlap_size:]
        
        # Xử lý phần buffer còn lại sau khi stream kết thúc
        if buffer:
            yield self.sanitize_output(buffer)