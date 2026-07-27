# transport.py - Part of the Connection Runtime
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Coroutine, Callable, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import structlog


if TYPE_CHECKING:
    from .client_registry import ClientRegistry, ClientInfo

logger = structlog.get_logger(__name__)

MessageHandler = Callable[[WebSocket, str], Coroutine[Any, Any, None]]

class TransportManager:
    """Manages the WebSocket transport for client connections."""

    def __init__(
        self,
        app: FastAPI,
        on_connect: Callable[[WebSocket], Coroutine[Any, Any, ClientInfo]],
        on_disconnect: Callable[[WebSocket], Coroutine[Any, Any, None]],
        on_message: MessageHandler,
    ):
        self.app = app
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message = on_message
        self.endpoint_path = "/ws/v1/connect"

    def setup_routes(self):
        """Adds the WebSocket endpoint to the FastAPI application."""
        self.app.add_api_websocket_route(self.endpoint_path, self._websocket_endpoint)
        logger.info("WebSocket endpoint configured.", path=self.endpoint_path)

    async def _websocket_endpoint(self, websocket: WebSocket):
        """The main WebSocket connection handler."""
        client_info = None
        try:
            client_info = await self._on_connect(websocket)
            if not client_info:
                # Connection was rejected
                return

            while True:
                data = await websocket.receive_text()
                await self._on_message(websocket, data)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected.", client_id=client_info.client_id if client_info else "Unknown")
        except Exception as e:
            logger.exception("An error occurred in the WebSocket handler.", exc_info=e, client_id=client_info.client_id if client_info else "Unknown")
        finally:
            if client_info:
                await self._on_disconnect(websocket)

    def start(self):
        """Starts the transport manager's services."""
        self.setup_routes()
        logger.info("TransportManager started.")
    
    def stop(self):
        """Stops the transport manager's services."""
        # Route removal is not straightforward in FastAPI, but for a clean shutdown,
        # the server itself will be stopped. Logging for clarity.
        logger.info("TransportManager stopped. WebSocket endpoint will no longer be active after server shutdown.")

