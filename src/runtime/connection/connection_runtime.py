# connection_runtime.py - Part of the Connection Runtime
import json
import uuid
from typing import Dict, Any

import structlog
from fastapi import FastAPI, WebSocket

from runtime.kernel.context import RuntimeContext
from runtime.kernel.runtime import Runtime

from .client_registry import ClientInfo, ClientRegistry
from .heartbeat import HeartbeatManager
from .routing import RoutingManager
from .transport import TransportManager

logger = structlog.get_logger(__name__)


class ConnectionRuntime(Runtime):
    """
    Implements the Connection Runtime.

    This runtime is responsible for managing the lifecycle of client connections,
    handling transport-level concerns, and performing initial routing of
    incoming messages.
    """

    client_registry: ClientRegistry
    heartbeat_manager: HeartbeatManager
    transport_manager: TransportManager
    routing_manager: RoutingManager

    _ws_to_client_id: Dict[WebSocket, str]

    async def initialize(self, context: RuntimeContext) -> None:
        """Initializes the connection runtime."""
        self.context = context
        logger.info(f"Connection Runtime '{self.context.runtime_id}' initializing...")

        # Initialize components
        self.client_registry = ClientRegistry()
        self.heartbeat_manager = HeartbeatManager(self.client_registry)
        self.routing_manager = RoutingManager()
        self._ws_to_client_id = {}

        # Register message handlers
        self.routing_manager.register("REGISTER", self._handle_register_message)
        self.routing_manager.register("HEARTBEAT", self._handle_heartbeat_message)

        # Assuming the FastAPI app is available in the context
        app = self.context.get_service("fastapi_app")
        if not isinstance(app, FastAPI):
            raise TypeError("Could not find FastAPI app in runtime context")

        self.transport_manager = TransportManager(
            app=app,
            on_connect=self._handle_connect,
            on_disconnect=self._handle_disconnect,
            on_message=self._handle_message,
        )

        logger.info(f"Connection Runtime '{self.context.runtime_id}' initialized.")

    async def start(self) -> None:
        """Starts the connection runtime's services."""
        logger.info(f"Connection Runtime '{self.context.runtime_id}' starting...")
        self.heartbeat_manager.start()
        self.transport_manager.start()
        logger.info(f"Connection Runtime '{self.context.runtime_id}' started.")

    async def stop(self) -> None:
        """Stops the connection runtime's services."""
        logger.info(f"Connection Runtime '{self.context.runtime_id}' stopping...")
        self.heartbeat_manager.stop()
        self.transport_manager.stop()
        logger.info(f"Connection Runtime '{self.context.runtime_id}' stopped.")

    async def dispose(self) -> None:
        """Disposes of the connection runtime's resources."""
        logger.info(f"Connection Runtime '{self.context.runtime_id}' disposing...")
        self._ws_to_client_id.clear()
        logger.info(f"Connection Runtime '{self.context.runtime_id}' disposed.")

    # Transport Callbacks
    # -------------------

    async def _handle_connect(self, websocket: WebSocket) -> ClientInfo | None:
        """Handles a new client connection."""
        await websocket.accept()
        logger.info("New WebSocket connection accepted, awaiting registration.")
        return None

    async def _handle_disconnect(self, websocket: WebSocket):
        """Handles a client disconnection."""
        client_id = self._ws_to_client_id.pop(websocket, None)
        if client_id:
            self.client_registry.deregister(client_id)
        else:
            logger.warning("Disconnected client was not registered or already removed.")

    async def _handle_message(self, websocket: WebSocket, data: str):
        """Receives a message, parses it, and routes it."""
        try:
            message = json.loads(data)
            await self.routing_manager.route(websocket, message)
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON message.", data=data)
        except Exception:
            logger.exception("Error processing message.")

    # Message Handlers (Registered with RoutingManager)
    # ------------------------------------------------

    async def _handle_register_message(self, websocket: WebSocket, payload: Dict[str, Any]):
        """Handles a 'REGISTER' message."""
        logger.info("Handling REGISTER message.", payload=payload)
        
        client_id = payload.get("client_id") or str(uuid.uuid4())
        client_type = payload.get("client_type", "unknown")
        
        info = ClientInfo(
            client_id=client_id,
            client_type=client_type,
            address=f"{websocket.client.host}:{websocket.client.port}",
        )
        self.client_registry.register(info)
        self._ws_to_client_id[websocket] = client_id
        
        await websocket.send_json({"type": "REGISTER_ACK", "payload": {"client_id": client_id}})

    async def _handle_heartbeat_message(self, websocket: WebSocket, payload: Dict[str, Any]):
        """Handles a 'HEARTBEAT' message."""
        client_id = payload.get("client_id")
        if client_id and self.client_registry.update_heartbeat(client_id):
            logger.debug("Heartbeat processed.", client_id=client_id)
        else:
            logger.warning("Heartbeat from unknown or unregistered client.", client_id=client_id)


