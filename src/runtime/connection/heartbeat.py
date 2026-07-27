# heartbeat.py - Part of the Connection Runtime
from __future__ import annotations
import asyncio
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .client_registry import ClientRegistry

logger = structlog.get_logger(__name__)

class HeartbeatManager:
    """Periodically checks for stale clients and removes them."""

    def __init__(
        self,
        client_registry: ClientRegistry,
        check_interval: float = 5.0,
        timeout: float = 30.0,
    ):
        """
        Initializes the HeartbeatManager.

        Args:
            client_registry: The registry to manage.
            check_interval: How often (in seconds) to check for stale clients.
            timeout: The number of seconds without a heartbeat to consider a client stale.
        """
        self.client_registry = client_registry
        self.check_interval = check_interval
        self.timeout = timeout
        self._task: asyncio.Task | None = None
        self._running = False

    async def _checker_task(self):
        """The background task that runs the checks."""
        logger.info("Heartbeat checker task started.", interval=self.check_interval, timeout=self.timeout)
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                stale_clients = []
                now = time.time()
                for client in self.client_registry.list_all():
                    if now - client.last_heartbeat > self.timeout:
                        stale_clients.append(client.client_id)
                
                for client_id in stale_clients:
                    logger.warning("Client timed out due to missed heartbeats.", client_id=client_id)
                    self.client_registry.deregister(client_id)

            except asyncio.CancelledError:
                logger.info("Heartbeat checker task was cancelled.")
                break
            except Exception:
                logger.exception("Heartbeat checker task encountered an unhandled error.")
    
    def start(self):
        """Starts the background checker task."""
        if self._task is None:
            self._running = True
            self._task = asyncio.create_task(self._checker_task())
        else:
            logger.warning("HeartbeatManager already running.")

    def stop(self):
        """Stops the background checker task."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Heartbeat checker task stopped.")
