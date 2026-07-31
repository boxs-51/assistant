# src/runtime/runtimes/connection/session.py
import time
import asyncio
import structlog
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import WebSocket

logger = structlog.get_logger(__name__)

class ClientSession(BaseModel):
    session_id: str
    user_id: str
    connected_at: float = Field(default_factory=time.time)
    last_ping: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        arbitrary_types_allowed = True

class ConnectionRegistry:
    """Quản lý active connections và WebSocket handles."""
    def __init__(self):
        self._sessions: Dict[str, ClientSession] = {}
        self._sockets: Dict[str, WebSocket] = {}

    def register(self, session_id: str, user_id: str, websocket: Optional[WebSocket] = None, metadata: Optional[Dict[str, Any]] = None):
        session = ClientSession(
            session_id=session_id,
            user_id=user_id,
            metadata=metadata or {}
        )
        self._sessions[session_id] = session
        if websocket:
            self._sockets[session_id] = websocket
        logger.info("Client session registered", session_id=session_id, user_id=user_id)

    def unregister(self, session_id: str):
        self._sessions.pop(session_id, None)
        self._sockets.pop(session_id, None)
        logger.info("Client session unregistered", session_id=session_id)

    def get_session(self, session_id: str) -> Optional[ClientSession]:
        return self._sessions.get(session_id)

    def get_socket(self, session_id: str) -> Optional[WebSocket]:
        return self._sockets.get(session_id)

    def update_ping(self, session_id: str):
        if session := self._sessions.get(session_id):
            session.last_ping = time.time()