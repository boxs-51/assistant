import threading
import uuid
from typing import Any, Dict, Optional, Tuple

import structlog
from .tool_executor import ExecutionContext

logger = structlog.get_logger(__name__)

class AgentSessionManager:
    """Quản lý cách ly Session theo conversation_id, khóa đa luồng và Lifecycle Execution Context."""

    def __init__(self):
        self._session_registry: Dict[str, Any] = {}
        self._conversation_locks: Dict[str, threading.RLock] = {}
        self._registry_lock = threading.RLock()

        self._executions: Dict[str, ExecutionContext] = {}
        self._executions_lock = threading.RLock()

    def get_or_create_conversation_lock(self, conversation_id: str) -> threading.RLock:
        with self._registry_lock:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = threading.RLock()
                self._conversation_locks[conversation_id] = lock
            return lock

    def get_or_create_session(
        self,
        conversation_id: str,
        session: Optional[Any] = None,
    ) -> Any:
        with self._registry_lock:
            existing = self._session_registry.get(conversation_id)

            if existing is not None:
                if session is not None and existing is not session:
                    logger.warning(
                        "Ignoring different session object for an already-registered conversation",
                        conversation_id=conversation_id,
                    )
                return existing

            if session is None:
                from ..schemas.context import AgentContextSession
                session = AgentContextSession()

            for attr, value in (
                ("conversation_id", conversation_id),
                ("session_id", getattr(session, "session_id", None)),
            ):
                try:
                    if attr == "conversation_id":
                        setattr(session, attr, value)
                    elif value is None and not getattr(session, attr, None):
                        setattr(session, attr, f"session_{uuid.uuid4().hex}")
                except Exception:
                    pass

            self._session_registry[conversation_id] = session
            return session

    def resolve_identity(
        self,
        session: Optional[Any],
        conversation_id: Optional[str],
        execution_id: Optional[str],
    ) -> Tuple[str, str]:
        derived_conversation_id = (
            conversation_id
            or getattr(session, "conversation_id", None)
            or getattr(session, "session_id", None)
            or f"conversation_{uuid.uuid4().hex}"
        )

        derived_execution_id = execution_id or f"exec_{uuid.uuid4().hex}"

        return (str(derived_conversation_id), str(derived_execution_id))

    def register_execution(self, execution_context: ExecutionContext):
        with self._executions_lock:
            self._executions[execution_context.execution_id] = execution_context

    def unregister_execution(self, execution_id: str):
        with self._executions_lock:
            self._executions.pop(execution_id, None)

    def cancel_execution(self, execution_id: str) -> bool:
        with self._executions_lock:
            execution_context = self._executions.get(execution_id)

        if execution_context is None:
            return False

        execution_context.cancellation_event.set()

        logger.info(
            "Agent execution cancellation requested",
            execution_id=execution_id,
            conversation_id=execution_context.conversation_id,
        )
        return True

    def get_execution(self, execution_id: str) -> Optional[ExecutionContext]:
        with self._executions_lock:
            return self._executions.get(execution_id)