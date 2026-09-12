# src/runtime/runtimes/connection/session.py
import time
import asyncio
import structlog
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import WebSocket

from .registry import ConnectionRegistry

logger = structlog.get_logger(__name__)

class ClientSession(BaseModel):
    session_id: str
    user_id: str
    connected_at: float = Field(default_factory=time.time)
    last_ping: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        arbitrary_types_allowed = True

__all__ = ["ClientSession", "ConnectionRegistry"]