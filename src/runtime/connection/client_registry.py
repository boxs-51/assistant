# client_registry.py - Part of the Connection Runtime
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import structlog


logger = structlog.get_logger(__name__)

@dataclass
class ClientInfo:
    """Stores information about a connected client."""
    client_id: str
    client_type: str # e.g., 'desktop', 'cli', 'browser'
    address: str
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)

class ClientRegistry:
    """Manages the lifecycle and state of connected clients."""

    def __init__(self):
        self._clients: Dict[str, ClientInfo] = {}
        logger.info("ClientRegistry initialized.")

    def register(self, client_info: ClientInfo) -> None:
        """Registers a new client or updates an existing one."""
        if client_info.client_id in self._clients:
            logger.warning("Client re-registering.", client_id=client_info.client_id)
        else:
            logger.info("New client registered.", client_id=client_info.client_id, client_type=client_info.client_type)
        self._clients[client_info.client_id] = client_info

    def deregister(self, client_id: str) -> Optional[ClientInfo]:
        """Deregisters a client and returns its information."""
        if client_id in self._clients:
            logger.info("Client deregistered.", client_id=client_id)
            return self._clients.pop(client_id)
        logger.warning("Attempted to deregister a non-existent client.", client_id=client_id)
        return None

    def get(self, client_id: str) -> Optional[ClientInfo]:
        """Retrieves information for a specific client."""
        return self._clients.get(client_id)

    def update_heartbeat(self, client_id: str) -> bool:
        """Updates the last heartbeat time for a client."""
        client = self.get(client_id)
        if client:
            client.last_heartbeat = time.time()
            logger.debug("Heartbeat updated.", client_id=client_id)
            return True
        logger.warning("Heartbeat received for non-existent client.", client_id=client_id)
        return False

    def list_all(self) -> list[ClientInfo]:
        """Returns a list of all registered clients."""
        return list(self._clients.values())

    def __len__(self) -> int:
        return len(self._clients)
