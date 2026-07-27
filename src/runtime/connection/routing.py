# routing.py - Part of the Connection Runtime
from __future__ import annotations
from typing import Callable, Coroutine, Any, Dict

import structlog
from fastapi import WebSocket


logger = structlog.get_logger(__name__)

# Type hint for a handler function: it takes a WebSocket and a payload, and returns a coroutine.
MessageHandler = Callable[[WebSocket, Dict[str, Any]], Coroutine[Any, Any, None]]

class RoutingManager:
    """Manages routing of incoming messages to appropriate handlers."""

    def __init__(self, default_handler: MessageHandler | None = None):
        """
        Initializes the RoutingManager.

        Args:
            default_handler: A handler to call when no specific handler is found for a message type.
        """
        self._handlers: Dict[str, MessageHandler] = {}
        self._default_handler = default_handler or self._unhandled_message
        logger.info("RoutingManager initialized.")

    def register(self, message_type: str, handler: MessageHandler):
        """
        Registers a handler for a specific message type.
        
        Args:
            message_type: The `type` field in the incoming message JSON.
            handler: The async function to call to handle this message type.
        """
        if message_type in self._handlers:
            logger.warning("Overwriting handler for message type.", message_type=message_type)
        self._handlers[message_type] = handler
        logger.info("Message handler registered.", message_type=message_type)

    async def route(self, websocket: WebSocket, message: Dict[str, Any]):
        """
        Routes a message to the appropriate registered handler.

        Args:
            websocket: The WebSocket connection the message came from.
            message: The parsed JSON message dictionary.
        """
        message_type = message.get("type")
        payload = message.get("payload", {})
        
        handler = self._handlers.get(message_type)

        if handler:
            logger.debug("Routing message to handler.", message_type=message_type)
            await handler(websocket, payload)
        else:
            logger.warning("No handler found for message type, using default.", message_type=message_type)
            await self._default_handler(websocket, message) # Pass the full message to the default handler

    async def _unhandled_message(self, websocket: WebSocket, message: Dict[str, Any]):
        """Default handler for messages that don't have a registered handler."""
        logger.warning("Received unhandled message.", message=message)
        # Optionally, send an error back to the client
        # await websocket.send_json({
        #     "type": "ERROR",
        #     "payload": {
        #         "message": f"Unknown message type: {message.get('type')}"
        #     }
        # })

