from typing import Dict, List, Optional
import structlog

from ..schemas.agent import AgentDefinition

logger = structlog.get_logger(__name__)

class AgentRegistry:
    """Quản lý việc lưu trữ và truy xuất các định nghĩa Agent đã được đăng ký."""
    def __init__(self):
        self._agents: Dict[str, AgentDefinition] = {}
        logger.info("AgentRegistry initialized.")

    def register(self, definition: AgentDefinition):
        """Đăng ký một định nghĩa Agent mới hoặc cập nhật định nghĩa đã có."""
        self._agents[definition.name] = definition
        logger.info("Agent registered/updated successfully", agent_name=definition.name)

    def get(self, name: str) -> Optional[AgentDefinition]:
        return self._agents.get(name)