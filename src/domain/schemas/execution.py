from __future__ import annotations
import time
from enum import Enum
from typing import Dict, Any, Optional

from .base import GatewayBaseModel


class ExecutionState(str, Enum):
    """Enumeration for the state of an execution."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


class Execution(GatewayBaseModel):
    """
    Represents a single, trackable execution of a workflow or task within a session.
    """
    execution_id: str
    session_id: str
    
    workflow_id: Optional[str] = None
    current_node: Optional[str] = None
    
    state: ExecutionState = ExecutionState.PENDING
    
    # Context and payload
    request: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None

    # Timing
    created_at: float
    updated_at: float
    
    # Control
    timeout: Optional[int] = None # In seconds
    retry_policy: Optional[Dict[str, Any]] = None # e.g., {"max_retries": 3, "backoff": "exponential"}
    cancellation_requested: bool = False

    def touch(self) -> None:
        """Updates the updated_at timestamp to the current time."""
        self.updated_at = time.time()
