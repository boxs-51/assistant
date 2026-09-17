from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

import requests

from .capability_dispatcher import CapabilityDispatcher
from .capability_runtime import CapabilityRuntime
from .auth_session import AuthSessionStore
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
        session_store: Optional[AuthSessionStore] = None,
    ) -> None:
        self.registry = registry

        self.client_id = client_id
        self.owner_id = owner_id
        self.principal_type: Optional[str] = None
        self._external_api_key = bool(api_key)
        self.session_store = session_store or AuthSessionStore()

        self.gateway = GatewayLLMClient(
            gateway_url,
            api_key=api_key,
        )
        if not self._external_api_key:
            self._load_saved_session()

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

            settings = getattr(self.registry, "settings", None)
            if settings is not None and not settings:
                self.registry.load_all()

            if not self.owner_id:
                identity = self._restore_or_create_identity()
                self.owner_id = identity.get("user_id") or identity.get("id")
                self.principal_type = identity.get("principal_type", "user")

                if not self.owner_id:
                    raise RuntimeError(
                        "Unable to determine owner_id."
                    )

                self.capabilities.owner_id = (
                    self.owner_id
                )

            # GatewayRealtimeClient owns a copy of the HTTP headers. Refresh it
            # after restoring or issuing a user/guest token and before connect.
            self.realtime.update_headers(self.gateway.headers)
            self.realtime.connect()

            self.capabilities.register()

            self._started = True
            self._ready = True

    def _load_saved_session(self) -> None:
        saved = self.session_store.load()
        if not saved:
            return
        self.gateway.set_access_token(saved["access_token"])
        self.gateway.refresh_token_value = saved.get("refresh_token")

    def _restore_or_create_identity(self) -> dict:
        if self.gateway.access_token:
            try:
                identity = self.gateway.current_session()
                self._persist_session(identity)
                return identity
            except requests.HTTPError:
                if self.gateway.refresh_token_value and not self._external_api_key:
                    try:
                        self.gateway.refresh_token(self.gateway.refresh_token_value)
                        identity = self.gateway.current_session()
                        self._persist_session(identity)
                        return identity
                    except requests.HTTPError:
                        pass
                if self._external_api_key:
                    raise
                self.gateway.clear_access_token()
                self.gateway.refresh_token_value = None
                self.session_store.clear()

        guest = self.gateway.create_guest()
        identity = self.gateway.current_session()
        self._persist_session(identity, expires_in=guest.get("expires_in"))
        return identity

    def _persist_session(self, identity: dict, *, expires_in: Optional[int] = None) -> None:
        if self._external_api_key:
            return
        self.session_store.save({
            "access_token": self.gateway.access_token,
            "refresh_token": self.gateway.refresh_token_value,
            "principal_type": identity.get("principal_type", "user"),
            "user_id": identity.get("user_id") or identity.get("id"),
            "expires_at": time.time() + expires_in if expires_in else None,
        })

    def _activate_authenticated_identity(self, tokens: dict) -> dict:
        identity = self.gateway.current_session()
        self.owner_id = identity["user_id"]
        self.principal_type = identity.get("principal_type", "user")
        self.capabilities.owner_id = self.owner_id
        self._persist_session(identity)
        self.realtime.update_headers(self.gateway.headers)

        if self._ready:
            self.realtime.close()
            self._ready = False
        self.start()
        return {"user": identity, "tokens": tokens}

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
        return self._activate_authenticated_identity(tokens)

    def register(self, payload: dict) -> dict:
        return self.gateway.register(payload)

    def verify_registration(self, payload: dict) -> dict:
        tokens = self.gateway.verify_registration(payload)
        return self._activate_authenticated_identity(tokens)

    def logout(self) -> dict:
        refresh_token = self.gateway.refresh_token_value
        if refresh_token:
            self.gateway.logout(refresh_token)
        self.realtime.close()
        self._ready = False
        self.owner_id = None
        self.principal_type = None
        self.gateway.clear_access_token()
        self.gateway.refresh_token_value = None
        if not self._external_api_key:
            self.session_store.clear()
        self.start()
        return {
            "user": {
                "user_id": self.owner_id,
                "principal_type": self.principal_type,
            }
        }

    def initiate_password_reset(self, payload: dict) -> dict:
        return self.gateway.initiate_password_reset(payload)

    def confirm_password_reset(self, payload: dict) -> dict:
        return self.gateway.confirm_password_reset(payload)

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
