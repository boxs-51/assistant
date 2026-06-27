import re
from typing import AsyncGenerator

# Giả lập, bạn có thể tích hợp GuardrailSystem đã có ở đây
class InputGuardrail:
    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in [r"ignore previous instructions"]]

    def validate(self, text: str) -> bool:
        for pattern in self.patterns:
            if pattern.search(text):
                return False
        return True

class OutputGuardrail:
    def __init__(self):
        self.redaction_patterns = {
            "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
            "API_KEY": re.compile(r"sk-[a-zA-Z0-9]{20,}")
        }

    def sanitize(self, text: str) -> str:
        for name, pattern in self.redaction_patterns.items():
            text = pattern.sub(f"[{name}_REDACTED]", text)
        return text

    async def sanitize_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """Làm sạch dữ liệu từ một stream."""
        buffer = ""
        async for chunk in stream:
            buffer += chunk
            # Tạm thời làm sạch buffer, thực tế cần logic phức tạp hơn để không cắt giữa chừng PII
            sanitized_buffer = self.sanitize(buffer)
            yield sanitized_buffer
            buffer = "" # Reset buffer
        
        if buffer: # Xử lý phần còn lại
            yield self.sanitize(buffer)
