from typing import Dict, List, Optional
import structlog

from ..domain.schemas.agent import AgentDefinition

logger = structlog.get_logger(__name__)

class AgentRegistry:
    """Quản lý việc lưu trữ và truy xuất các định nghĩa Agent đã được đăng ký."""
    def __init__(self):
        self._agents: Dict[str, AgentDefinition] = {}
        self._loader = None
        logger.info("AgentRegistry initialized.")

    def register(self, definition: AgentDefinition):
        """Đăng ký một định nghĩa Agent mới hoặc cập nhật định nghĩa đã có."""
        self._agents[definition.name] = definition
        logger.info("Agent registered/updated successfully", agent_name=definition.name)

    def get(self, name: str) -> Optional[AgentDefinition]:
        agent = self._agents.get(name)
        if agent is None and self._loader is not None:
            agent = self._loader(name)
        return agent

    def get_loaded(self, name: str) -> Optional[AgentDefinition]:
        """Return an already materialized agent without triggering lazy load."""
        return self._agents.get(name)

    def set_loader(self, loader):
        self._loader = loader

    def list_all(self) -> List[AgentDefinition]:
        return list(self._agents.values())

    def remove(self, name: str) -> Optional[AgentDefinition]:
        return self._agents.pop(name, None)
