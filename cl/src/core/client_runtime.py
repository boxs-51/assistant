from __future__ import annotations

import random
import threading
import time
import uuid
from enum import Enum
from typing import Optional

import requests

from .auth_session import AuthSessionStore
from .capability_dispatcher import CapabilityDispatcher
from .capability_runtime import CapabilityRuntime
from .gateway_client import GatewayLLMClient
from .installation_identity import InstallationIdentityStore
from .realtime_client import GatewayRealtimeClient


class ClientRuntimeState(str, Enum):
    STOPPED = "STOPPED"
    AUTH_READY = "AUTH_READY"
    CONNECTING = "CONNECTING"
    REGISTERING_CAPABILITIES = "REGISTERING_CAPABILITIES"
    READY = "READY"
    DISCONNECTED = "DISCONNECTED"
    BACKOFF = "BACKOFF"
    DRAINING = "DRAINING"


class ClientRuntime:
    """Own auth, realtime generations, capability registration and reconnect."""

    def __init__(
        self,
        gateway_url: str,
        registry,
        *,
        api_key: str = "",
        client_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        session_store: Optional[AuthSessionStore] = None,
        installation_store: Optional[InstallationIdentityStore] = None,
        hitl=None,
    ) -> None:
        self.registry = registry
        self._gateway_url = gateway_url
        self.owner_id = owner_id
        self.principal_type: Optional[str] = None
        self._external_api_key = bool(api_key)
        injected_session_store = session_store is not None
        self.session_store = session_store or AuthSessionStore()
        self.installation_store = installation_store
        if client_id:
            self.client_id = client_id
        elif installation_store is not None:
            self.client_id = installation_store.load_or_create()
        elif injected_session_store:
            self.client_id = f"client-{uuid.uuid4().hex}"
        else:
            self.installation_store = InstallationIdentityStore()
            self.client_id = self.installation_store.load_or_create()
        self.gateway = GatewayLLMClient(gateway_url, api_key=api_key)
        if not self._external_api_key:
            self._load_saved_session()

        self._lock = threading.RLock()
        self._generation = 0
        self.connection_id = f"cl-{uuid.uuid4().hex}"
        self.realtime = self._build_realtime(self.connection_id, self._generation)
        self.dispatcher = CapabilityDispatcher(
            registry,
            self.realtime,
            hitl=hitl,
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
        self._generation_started = False
        self._state = ClientRuntimeState.STOPPED
        self._reconnect_thread: Optional[threading.Thread] = None
        self._suppress_reconnect = False
        self._stopping = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def state(self) -> ClientRuntimeState:
        return self._state

    def _build_realtime(self, connection_id: str, generation: int):
        return GatewayRealtimeClient(
            self._gateway_url,
            self.gateway.headers,
            connection_id=connection_id,
            client_id=self.client_id,
            on_message=lambda envelope: self._handle_realtime_message(
                envelope, generation
            ),
            on_disconnect=lambda error: self._handle_disconnect(error, generation),
        )

    def _replace_realtime_generation(self) -> None:
        old = self.realtime
        old.close()
        self._generation += 1
        self.connection_id = f"cl-{uuid.uuid4().hex}"
        self.realtime = self._build_realtime(self.connection_id, self._generation)
        self.dispatcher.set_realtime(self.realtime)
        self.capabilities.realtime = self.realtime

    def start(self) -> None:
        with self._lock:
            if self._ready:
                return
            self._stopping = False
            self._suppress_reconnect = False
            settings = getattr(self.registry, "settings", None)
            if settings is not None and not settings:
                self.registry.load_all()
            if not self.owner_id:
                identity = self._restore_or_create_identity()
                self.owner_id = identity.get("user_id") or identity.get("id")
                self.principal_type = identity.get("principal_type", "user")
                if not self.owner_id:
                    raise RuntimeError("Unable to determine owner_id.")
                self.capabilities.owner_id = self.owner_id
            self._state = ClientRuntimeState.AUTH_READY
            if self._generation_started:
                self._replace_realtime_generation()
            else:
                self._generation_started = True
            self.realtime.update_headers(self.gateway.headers)
            self._state = ClientRuntimeState.CONNECTING
            self.realtime.connect()
            self._state = ClientRuntimeState.REGISTERING_CAPABILITIES
            self.capabilities.register()
            self._started = True
            self._ready = True
            self._state = ClientRuntimeState.READY

    def _load_saved_session(self) -> None:
        saved = self.session_store.load()
        if saved:
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
        self._drain_current_generation()
        self.start()
        return {"user": identity, "tokens": tokens}

    def _drain_current_generation(self) -> None:
        self._state = ClientRuntimeState.DRAINING
        self._suppress_reconnect = True
        try:
            self.dispatcher.reset_principal()
            self.realtime.close()
            self._ready = False
        finally:
            self._suppress_reconnect = False

    def _handle_realtime_message(self, envelope, generation=None) -> None:
        if generation is not None and generation != self._generation:
            return
        self.capabilities.handle_message(envelope)

    def _handle_disconnect(self, error, generation=None) -> None:
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            self._ready = False
            self._state = ClientRuntimeState.DISCONNECTED
        # Do not cancel local work merely because its transport disappeared.
        # The dispatcher keeps the terminal outcome and replays it after the
        # next connection generation, avoiding duplicate side effects.
        if not self._suppress_reconnect and not self._stopping:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        with self._lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                name="client-runtime-reconnect",
                daemon=True,
            )
            self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        delay = 0.25
        while not self._stopping:
            self._state = ClientRuntimeState.BACKOFF
            time.sleep(delay + random.uniform(0.0, delay * 0.2))
            if self._stopping:
                return
            try:
                self.start()
                return
            except Exception:
                delay = min(delay * 2.0, 30.0)

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._suppress_reconnect = True
            self._ready = False
            self._started = False
            self._state = ClientRuntimeState.STOPPED
        self.capabilities.shutdown()
        self.realtime.close()

    def chat(self, payload):
        if self._ready:
            if hasattr(payload, "model_copy"):
                payload = payload.model_copy(update={"connection_id": self.connection_id})
            elif isinstance(payload, dict):
                payload = {**payload, "connection_id": self.connection_id}
        return self.gateway.send_request(payload)

    def health(self):
        return self.gateway.health()

    def readiness(self):
        return self.gateway.readiness()

    def resume_execution(self, execution_id: str, checkpoint_id: str):
        if not self._ready:
            raise RuntimeError("Client runtime is not READY.")
        return self.realtime.resume_execution(execution_id, checkpoint_id)

    def login(self, payload: dict) -> dict:
        return self._activate_authenticated_identity(self.gateway.login(payload))

    def register(self, payload: dict) -> dict:
        return self.gateway.register(payload)

    def verify_registration(self, payload: dict) -> dict:
        return self._activate_authenticated_identity(
            self.gateway.verify_registration(payload)
        )

    def logout(self) -> dict:
        refresh_token = self.gateway.refresh_token_value
        if refresh_token:
            self.gateway.logout(refresh_token)
        self._drain_current_generation()
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

    def __exit__(self, exc_type, exc, tb):
        self.stop()
