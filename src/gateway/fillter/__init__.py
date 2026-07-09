from typing import AsyncGenerator

from ..config import settings
from ...guardrail.guar import GuardrailSystem

class InputFillter:
    """Adapter để tích hợp Input của hệ thống lõi vào Gateway."""
    def __init__(self, guardrail_system: GuardrailSystem):
        self.system = guardrail_system

    def validate(self, text: str) -> bool:
        """Ủy quyền việc kiểm tra cho GuardrailSystem."""
        if not settings.fillter.enable_input_fillter:
            return True
        return self.system.validate_input(text)

class OutputFillter:
    """Adapter để tích hợp Output của hệ thống lõi vào Gateway."""
    def __init__(self, guardrail_system: GuardrailSystem):
        self.system = guardrail_system

    def sanitize(self, text: str) -> str:
        """Ủy quyền việc làm sạch cho GuardrailSystem."""
        if not settings.fillter.enable_output_fillter:
            return text
        return self.system.sanitize_output(text)

    async def sanitize_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """Ủy quyền việc làm sạch stream cho GuardrailSystem."""
        if not settings.fillter.enable_output_fillter:
            async for chunk in stream:
                yield chunk
        else:
            async for sanitized_chunk in self.system.sanitize_stream(stream):
                yield sanitized_chunk
