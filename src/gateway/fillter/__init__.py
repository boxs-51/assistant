from typing import AsyncGenerator

from ..config import settings
from ...guardrail.guar import GuardrailSystem

class InputGuardrailAdapter:
    """Adapter để tích hợp Input Guardrail của hệ thống lõi vào Gateway."""
    def __init__(self, guardrail_system: GuardrailSystem):
        self.system = guardrail_system

    def validate(self, text: str) -> bool:
        """Ủy quyền việc kiểm tra cho GuardrailSystem."""
        if not settings.security.enable_input_guardrail:
            return True
        return self.system.validate_input(text)

class OutputGuardrailAdapter:
    """Adapter để tích hợp Output Guardrail của hệ thống lõi vào Gateway."""
    def __init__(self, guardrail_system: GuardrailSystem):
        self.system = guardrail_system

    def sanitize(self, text: str) -> str:
        """Ủy quyền việc làm sạch cho GuardrailSystem."""
        if not settings.security.enable_output_guardrail:
            return text
        return self.system.sanitize_output(text)

    async def sanitize_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """Ủy quyền việc làm sạch stream cho GuardrailSystem."""
        if not settings.security.enable_output_guardrail:
            async for chunk in stream:
                yield chunk
        else:
            async for sanitized_chunk in self.system.sanitize_stream(stream):
                yield sanitized_chunk
