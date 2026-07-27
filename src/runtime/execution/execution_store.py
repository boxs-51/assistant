# execution_store.py - Part of the Execution Runtime
from __future__ import annotations
from typing import Dict, Optional

import structlog

from ...domain.schemas.execution import Execution

logger = structlog.get_logger(__name__)

class ExecutionStore:
    """Manages the in-memory state of active executions."""

    def __init__(self):
        self._executions: Dict[str, Execution] = {}
        logger.info("ExecutionStore initialized.")

    def add(self, execution: Execution) -> None:
        """Adds a new execution to the store."""
        if execution.execution_id in self._executions:
            logger.warning("Execution already exists in store.", execution_id=execution.execution_id)
            return
        self._executions[execution.execution_id] = execution
        logger.info("Execution added to store.", execution_id=execution.execution_id)

    def get(self, execution_id: str) -> Optional[Execution]:
        """Retrieves an execution from the store."""
        return self._executions.get(execution_id)

    def remove(self, execution_id: str) -> Optional[Execution]:
        """Removes a completed or failed execution from the store."""
        if execution_id in self._executions:
            logger.info("Execution removed from store.", execution_id=execution_id)
            return self._executions.pop(execution_id)
        return None
    
    def list_all(self) -> list[Execution]:
        """Returns a list of all active executions."""
        return list(self._executions.values())
