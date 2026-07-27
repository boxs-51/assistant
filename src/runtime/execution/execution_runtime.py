"""
The Execution Runtime is responsible for managing the lifecycle of executions.
"""
from __future__ import annotations
import time
import uuid
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from src.domain.schemas.execution import Execution, ExecutionState


class IExecutionStore(ABC):
    """
    Abstract interface for a storage backend for Executions.
    This could be implemented for Redis, a database, or in-memory for testing.
    """
    @abstractmethod
    def create(self, execution: Execution) -> None:
        """Saves a new execution."""
        raise NotImplementedError

    @abstractmethod
    def get(self, execution_id: str) -> Optional[Execution]:
        """Retrieves an execution by its ID."""
        raise NotImplementedError

    @abstractmethod
    def update(self, execution: Execution) -> None:
        """Updates an existing execution."""
        raise NotImplementedError
    
    @abstractmethod
    def list_by_session(self, session_id: str) -> List[Execution]:
        """Lists all executions for a given session."""
        raise NotImplementedError


class ExecutionRuntime:
    """
    Manages the lifecycle of Executions (create, get, update state, cancel).
    This service orchestrates the state changes of an execution, which are
    persisted via an IExecutionStore.
    """
    def __init__(self, store: IExecutionStore):
        self._store = store

    def create_execution(
        self,
        session_id: str,
        request: Dict[str, Any],
        workflow_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Execution:
        """
        Creates a new Execution object, persists it, and returns it.
        This is the starting point for any new trackable task.
        """
        now = time.time()
        execution = Execution(
            execution_id=str(uuid.uuid4()),
            session_id=session_id,
            request=request,
            state=ExecutionState.PENDING,
            workflow_id=workflow_id,
            timeout=timeout,
            created_at=now,
            updated_at=now,
        )
        self._store.create(execution)
        # In a real system, this would also trigger an event or place
        # the execution in a queue for a dispatcher to pick up.
        return execution

    def get_execution(self, execution_id: str) -> Optional[Execution]:
        """Retrieves a single execution by its ID."""
        return self._store.get(execution_id)

    def list_executions(self, session_id: str) -> List[Execution]:
        """Lists all executions associated with a session."""
        return self._store.list_by_session(session_id)

    def request_cancellation(self, execution_id: str) -> Optional[Execution]:
        """
        Flags an execution for cancellation.
        It's up to the worker processing this execution to check this flag
        and act on it.
        """
        execution = self._store.get(execution_id)
        if not execution:
            return None
        
        # We only request cancellation. The worker is responsible for the final state change.
        execution.cancellation_requested = True
        execution.touch()
        self._store.update(execution)
        return execution
        
    def update_state(self, execution_id: str, new_state: ExecutionState) -> Optional[Execution]:
        """
        Updates the state of an execution. This should be called by the
        worker that is processing the execution.
        """
        execution = self._store.get(execution_id)
        if not execution:
            return None
        
        execution.state = new_state
        execution.touch()
        self._store.update(execution)
        return execution
