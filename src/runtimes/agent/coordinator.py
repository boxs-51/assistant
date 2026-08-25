import time
import uuid
from typing import Dict, List, Optional

from ...agent.registry import AgentRegistry
from ...domain.schemas.agent import AgentDefinition
from ...domain.schemas.identity import Identity
from ...domain.schemas.multi_agent import (
    AgentMessage,
    AgentMessageType,
    AgentSession,
    AgentSessionStatus,
    AgentTask,
    AgentTaskStatus,
)


class MultiAgentCoordinator:
    """Opt-in control plane for multi-agent sessions and delegation."""

    def __init__(self, agent_registry: AgentRegistry):
        self.agent_registry = agent_registry
        self._sessions: Dict[str, AgentSession] = {}
        self._tasks: Dict[str, AgentTask] = {}
        self._messages: Dict[str, List[AgentMessage]] = {}

    def _require_session(self, session_id: str, identity: Identity) -> AgentSession:
        session = self._sessions.get(session_id)
        if not session or session.owner_user_id != identity.user_id:
            raise LookupError("Agent session not found or access denied.")
        return session

    def _require_agent(self, agent_id: str) -> AgentDefinition:
        agent = self.agent_registry.get(agent_id)
        if not agent:
            raise LookupError(f"Agent '{agent_id}' is not registered.")
        return agent

    def create_session(self, identity: Identity, agent_ids: Optional[List[str]] = None) -> AgentSession:
        selected_agents = agent_ids or []
        for agent_id in selected_agents:
            self._require_agent(agent_id)
        now = time.time()
        session = AgentSession(
            session_id=f"as_{uuid.uuid4().hex}",
            owner_user_id=identity.user_id or "anonymous",
            agent_ids=selected_agents,
            created_at=now,
            updated_at=now,
        )
        self._sessions[session.session_id] = session
        self._messages[session.session_id] = []
        return session

    def add_agent(self, session_id: str, agent_id: str, identity: Identity) -> AgentSession:
        session = self._require_session(session_id, identity)
        self._require_agent(agent_id)
        if agent_id not in session.agent_ids:
            session.agent_ids.append(agent_id)
            session.updated_at = time.time()
        return session

    def send_message(
        self,
        session_id: str,
        sender_id: str,
        message_type: AgentMessageType,
        payload: dict,
        identity: Identity,
        recipient_id: Optional[str] = None,
    ) -> AgentMessage:
        session = self._require_session(session_id, identity)
        if sender_id != identity.user_id and sender_id not in session.agent_ids:
            raise PermissionError("Sender is not a member of this agent session.")
        if recipient_id and recipient_id not in session.agent_ids:
            raise PermissionError("Recipient is not a member of this agent session.")
        message = AgentMessage(
            message_id=f"msg_{uuid.uuid4().hex}",
            session_id=session_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            message_type=message_type,
            payload=payload,
            created_at=time.time(),
        )
        self._messages[session_id].append(message)
        session.updated_at = time.time()
        return message

    def list_messages(self, session_id: str, identity: Identity) -> List[AgentMessage]:
        self._require_session(session_id, identity)
        return list(self._messages[session_id])

    def create_task(
        self,
        session_id: str,
        assigned_agent_id: str,
        task_input: dict,
        identity: Identity,
        parent_task_id: Optional[str] = None,
    ) -> AgentTask:
        session = self._require_session(session_id, identity)
        self._require_agent(assigned_agent_id)
        if assigned_agent_id not in session.agent_ids:
            raise PermissionError("Assigned agent is not a member of this session.")
        now = time.time()
        task = AgentTask(
            task_id=f"task_{uuid.uuid4().hex}",
            session_id=session_id,
            created_by=identity.user_id or "anonymous",
            assigned_agent_id=assigned_agent_id,
            parent_task_id=parent_task_id,
            status=AgentTaskStatus.ASSIGNED,
            input=task_input,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str, identity: Identity) -> AgentTask:
        task = self._tasks.get(task_id)
        if not task:
            raise LookupError("Agent task not found.")
        self._require_session(task.session_id, identity)
        return task

    def cancel_task(self, task_id: str, identity: Identity) -> AgentTask:
        task = self.get_task(task_id, identity)
        task.status = AgentTaskStatus.CANCELLED
        task.updated_at = time.time()
        return task

    def close_session(self, session_id: str, identity: Identity) -> AgentSession:
        session = self._require_session(session_id, identity)
        session.status = AgentSessionStatus.CANCELLED
        session.updated_at = time.time()
        return session
