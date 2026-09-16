from __future__ import annotations

import threading
import uuid
from typing import Optional

from .capability_dispatcher import CapabilityDispatcher
from .capability_runtime import CapabilityRuntime
from .gateway_client import GatewayLLMClient
from .realtime_client import GatewayRealtimeClient


class ClientRuntime:
    """
    Unified client lifecycle.

    Application code should not manually manage:
        - WebSocket connection
        - connection.register
        - capability.register
        - receiver loop
        - capability.invoke
        - capability.cancel
    """

    def __init__(
        self,
        gateway_url: str,
        registry,
        *,
        api_key: str = "",
        client_id: str = "desktop-client",
        owner_id: Optional[str] = None,
    ) -> None:
        self.registry = registry

        self.client_id = client_id
        self.owner_id = owner_id

        self.gateway = GatewayLLMClient(
            gateway_url,
            api_key=api_key,
        )

        self.connection_id = (
            f"cl-{uuid.uuid4().hex}"
        )

        self.realtime = GatewayRealtimeClient(
            gateway_url,
            self.gateway.headers,
            connection_id=self.connection_id,
            client_id=self.client_id,
            on_message=self._handle_realtime_message,
            on_disconnect=self._handle_disconnect,
        )

        self.dispatcher = CapabilityDispatcher(
            registry,
            self.realtime,
        )

        self.capabilities = CapabilityRuntime(
            registry,
            self.realtime,
            client_id=self.client_id,
            owner_id=self.owner_id or "",
            dispatcher=self.dispatcher,
        )

        self._started = False
        self._ready = False
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        return self._ready

    def start(self) -> None:
        with self._lock:
            if self._ready:
                return

            self.registry.load_all()

            if not self.owner_id:
                user = self.gateway.current_user()
                self.owner_id = (
                    user.get("user_id")
                    or user.get("id")
                )

                if not self.owner_id:
                    raise RuntimeError(
                        "Unable to determine owner_id."
                    )

                self.capabilities.owner_id = (
                    self.owner_id
                )

            self.realtime.connect()

            self.capabilities.register()

            self._started = True
            self._ready = True

    def _handle_realtime_message(
        self,
        envelope,
    ) -> None:
        self.capabilities.handle_message(
            envelope
        )

    def _handle_disconnect(
        self,
        error,
    ) -> None:
        with self._lock:
            self._ready = False

        self.dispatcher.fail_all(
            "Realtime connection disconnected."
        )

    def stop(self) -> None:
        with self._lock:
            self._ready = False
            self._started = False

        self.capabilities.shutdown()
        self.realtime.close()

    def chat(self, payload):
        return self.gateway.send_request(
            payload
        )

    def health(self):
        return self.gateway.health()

    def readiness(self):
        return self.gateway.readiness()

    def login(self, payload: dict) -> dict:
        tokens = self.gateway.login(payload)

        user = self.gateway.current_user()
        self.owner_id = user["id"]
        self.capabilities.owner_id = self.owner_id

        self.realtime.update_headers(self.gateway.headers)

        if not self._ready:
            self.start()

        return {
            "user": user,
            "tokens": tokens,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        self.stop()