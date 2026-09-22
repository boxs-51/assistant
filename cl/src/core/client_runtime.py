from __future__ import annotations

import random
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from types import GeneratorType
from typing import Optional

import requests

from .auth_session import AuthSessionStore
from .capability_dispatcher import CapabilityDispatcher
from .capability_runtime import CapabilityRuntime
from .client_invocation_ledger import ClientInvocationLedger
from .gateway_client import GatewayLLMClient
from .installation_identity import InstallationIdentityStore
from .realtime_client import GatewayRealtimeClient
from .resume_ticket import (
    PendingResumeEntry,
    PendingResumeTicket,
    ResumeProtocolOutcome,
    ResumeTicketState,
)


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
        invocation_ledger: Optional[ClientInvocationLedger] = None,
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
        if invocation_ledger is not None:
            self.invocation_ledger = invocation_ledger
        elif self.installation_store is not None:
            self.invocation_ledger = ClientInvocationLedger(
                self.installation_store.path.with_name(
                    "client-invocations.sqlite3"
                )
            )
        else:
            self.invocation_ledger = ClientInvocationLedger()
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
            invocation_ledger=self.invocation_ledger,
            client_id=self.client_id,
            principal_id=self.owner_id,
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
        self._pending_resume_tickets: dict[
            tuple[str, str], PendingResumeEntry
        ] = {}
        self._execution_resume_watermarks: dict[str, int] = {}
        self._confirmed_capability_ids: frozenset[str] = frozenset()
        self._resume_condition = threading.Condition(self._lock)
        self._resume_worker_thread: Optional[threading.Thread] = None
        self._resume_worker_stop = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def state(self) -> ClientRuntimeState:
        return self._state

    @property
    def pending_resume_tickets(self) -> tuple[PendingResumeTicket, ...]:
        with self._lock:
            return tuple(
                entry.ticket
                for entry in self._pending_resume_tickets.values()
                if not entry.terminal
            )

    @property
    def confirmed_capability_ids(self) -> frozenset[str]:
        with self._lock:
            return self._confirmed_capability_ids

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
        self._confirmed_capability_ids = frozenset()
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
            self.dispatcher.set_identity(
                self.client_id,
                self.owner_id,
            )
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
            registration_ack = self.capabilities.register()
            self._confirmed_capability_ids = self._confirmed_from_registration_ack(
                registration_ack
            )
            self._started = True
            self._ready = True
            self._state = ClientRuntimeState.READY
            # A new capability-ACKed transport generation is a fresh
            # eligibility signal for previously deferred (but not uncertain)
            # requests. Lost-ACK retries remain RETRY_SAME_REQUEST.
            for entry in self._pending_resume_tickets.values():
                if entry.state is ResumeTicketState.WAIT_REFRESH:
                    entry.state = ResumeTicketState.OBSERVED
            self._ensure_resume_worker_locked()
            self._resume_condition.notify_all()

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
        self._persist_session(identity)
        self._drain_current_generation()
        self.owner_id = identity["user_id"]
        self.principal_type = identity.get("principal_type", "user")
        self.capabilities.owner_id = self.owner_id
        self.dispatcher.set_identity(
            self.client_id,
            self.owner_id,
        )
        self.start()
        return {"user": identity, "tokens": tokens}

    def _drain_current_generation(self) -> None:
        self._state = ClientRuntimeState.DRAINING
        self._suppress_reconnect = True
        try:
            self.dispatcher.reset_principal()
            self.realtime.close()
            with self._lock:
                self._ready = False
                self._confirmed_capability_ids = frozenset()
                self._invalidate_resume_state_locked()
        finally:
            self._suppress_reconnect = False

    def _handle_realtime_message(self, envelope, generation=None) -> None:
        if generation is not None and generation != self._generation:
            return
        message_type = envelope.get("type")
        if message_type == "execution.waiting":
            self._ingest_waiting_payload(envelope.get("payload") or {})
            return
        # Resume outcomes are consumed by the transport waiter owned by the
        # ClientRuntime resume worker. The receiver thread must never block on
        # them or start a second attempt.
        if message_type in {
            "execution.resume.accepted",
            "execution.resume.rejected",
            "execution.resume.failed",
        }:
            return
        self.capabilities.handle_message(envelope)

    def _handle_disconnect(self, error, generation=None) -> None:
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            self._ready = False
            self._confirmed_capability_ids = frozenset()
            self._state = ClientRuntimeState.DISCONNECTED
            for entry in self._pending_resume_tickets.values():
                if entry.state is ResumeTicketState.IN_FLIGHT:
                    entry.state = ResumeTicketState.RETRY_SAME_REQUEST
            self._resume_condition.notify_all()
        # Do not cancel local work merely because its transport disappeared.
        # The dispatcher keeps the terminal outcome and replays it after the
        # next connection generation, avoiding duplicate side effects.
        if not self._suppress_reconnect and not self._stopping:
            self._schedule_reconnect()

    @staticmethod
    def _confirmed_from_registration_ack(message) -> frozenset[str]:
        capabilities = (message.get("payload") or {}).get("capabilities") or ()
        confirmed: set[str] = set()
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "")
            if state not in {"ENABLED", "DEGRADED"}:
                continue
            capability_id = item.get("capability_id")
            if capability_id:
                confirmed.add(str(capability_id))
        return frozenset(confirmed)

    def _invalidate_resume_state_locked(self) -> None:
        self._pending_resume_tickets.clear()
        self._execution_resume_watermarks.clear()
        self._confirmed_capability_ids = frozenset()
        self._resume_condition.notify_all()

    def _ingest_waiting_payload(self, payload) -> bool:
        try:
            ticket = PendingResumeTicket.from_payload(payload)
        except (TypeError, ValueError):
            # Compatibility payloads that do not carry authoritative revision
            # remain visible to callers but cannot enter auto-resume state.
            return False

        with self._lock:
            watermark = self._execution_resume_watermarks.get(ticket.execution_id)
            if watermark is not None and ticket.revision < watermark:
                return False

            same_revision = [
                entry
                for entry in self._pending_resume_tickets.values()
                if (
                    entry.ticket.execution_id == ticket.execution_id
                    and entry.ticket.revision == ticket.revision
                    and entry.ticket.checkpoint_id != ticket.checkpoint_id
                    and not entry.terminal
                )
            ]
            if same_revision:
                for entry in same_revision:
                    entry.state = ResumeTicketState.CONFLICT
                self._resume_condition.notify_all()
                return False

            if watermark is None or ticket.revision > watermark:
                self._advance_resume_watermark_locked(
                    ticket.execution_id,
                    ticket.revision,
                )

            existing = self._pending_resume_tickets.get(ticket.key)
            if existing is not None:
                if existing.ticket != ticket:
                    existing.state = ResumeTicketState.CONFLICT
                    self._resume_condition.notify_all()
                    return False
                existing.freshness_epoch += 1
                if existing.state is ResumeTicketState.WAIT_REFRESH:
                    existing.state = ResumeTicketState.OBSERVED
                self._classify_resume_entry_locked(existing)
                self._resume_condition.notify_all()
                return True

            entry = PendingResumeEntry(
                ticket=ticket,
                principal_id=self.owner_id,
            )
            self._pending_resume_tickets[ticket.key] = entry
            self._classify_resume_entry_locked(entry)
            self._ensure_resume_worker_locked()
            self._resume_condition.notify_all()
            return True

    def _advance_resume_watermark_locked(
        self,
        execution_id: str,
        revision: int,
    ) -> None:
        current = self._execution_resume_watermarks.get(execution_id)
        if current is not None and revision <= current:
            return
        self._execution_resume_watermarks[execution_id] = revision
        for entry in self._pending_resume_tickets.values():
            if (
                entry.ticket.execution_id == execution_id
                and entry.ticket.revision < revision
                and not entry.terminal
            ):
                entry.state = ResumeTicketState.SUPERSEDED

    @staticmethod
    def _ticket_expired(ticket: PendingResumeTicket) -> bool:
        expires_at = ticket.wait_expires_at
        if expires_at is None:
            return False
        return expires_at <= datetime.now(timezone.utc)

    def _classify_resume_entry_locked(self, entry: PendingResumeEntry) -> None:
        if entry.terminal:
            return
        ticket = entry.ticket
        if self._ticket_expired(ticket):
            entry.state = ResumeTicketState.EXPIRED
            return
        if (
            ticket.revision
            != self._execution_resume_watermarks.get(ticket.execution_id)
            or entry.principal_id != self.owner_id
            or ticket.origin_client_id != self.client_id
            or ticket.wait_reason != "CONNECTION"
            or not ticket.auto_resume_allowed
            or not ticket.pending_capability_ids
        ):
            entry.state = ResumeTicketState.BLOCKED
            return
        if (
            not self._ready
            or self._state is not ClientRuntimeState.READY
            or not set(ticket.pending_capability_ids).issubset(
                self._confirmed_capability_ids
            )
        ):
            # Capability readiness is a hard fence even for lost-ACK retries.
            # The request identity itself remains preserved on the entry, so
            # becoming eligible later still retries the same request id.
            entry.state = ResumeTicketState.BLOCKED
            return
        if entry.state not in {
            ResumeTicketState.RETRY_SAME_REQUEST,
            ResumeTicketState.WAIT_REFRESH,
        }:
            entry.state = ResumeTicketState.ELIGIBLE

    def _ensure_resume_worker_locked(self) -> None:
        if self._resume_worker_thread and self._resume_worker_thread.is_alive():
            return
        self._resume_worker_stop = False
        self._resume_worker_thread = threading.Thread(
            target=self._resume_worker_loop,
            name="client-runtime-resume",
            daemon=True,
        )
        self._resume_worker_thread.start()

    def _next_resume_key_locked(self):
        if not self._ready or self._state is not ClientRuntimeState.READY:
            return None
        for key in sorted(self._pending_resume_tickets):
            entry = self._pending_resume_tickets[key]
            self._classify_resume_entry_locked(entry)
            if entry.state in {
                ResumeTicketState.ELIGIBLE,
                ResumeTicketState.RETRY_SAME_REQUEST,
            }:
                return key
        return None

    def _claim_resume_attempt_locked(self, key):
        entry = self._pending_resume_tickets.get(key)
        if entry is None:
            return None
        self._classify_resume_entry_locked(entry)
        if entry.state not in {
            ResumeTicketState.ELIGIBLE,
            ResumeTicketState.RETRY_SAME_REQUEST,
        }:
            return None
        resume_request_id = entry.ensure_resume_request_id()
        entry.state = ResumeTicketState.IN_FLIGHT
        entry.attempt_generation = self._generation
        entry.attempt_connection_id = self.connection_id
        return (
            entry.ticket,
            resume_request_id,
            self._generation,
            self.realtime,
        )

    def _resume_worker_loop(self) -> None:
        while True:
            with self._resume_condition:
                while not self._resume_worker_stop:
                    key = self._next_resume_key_locked()
                    if key is not None:
                        break
                    self._resume_condition.wait(timeout=0.5)
                if self._resume_worker_stop:
                    return
                attempt = self._claim_resume_attempt_locked(key)
            if attempt is None:
                continue
            self._execute_resume_attempt(key, attempt, propagate=False)

    def _execute_resume_attempt(self, key, attempt, *, propagate: bool):
        ticket, resume_request_id, generation, realtime = attempt
        try:
            message = realtime.resume_execution(
                ticket.execution_id,
                ticket.checkpoint_id,
                resume_request_id,
            )
            outcome = ResumeProtocolOutcome.from_envelope(message)
        except Exception:
            with self._lock:
                entry = self._pending_resume_tickets.get(key)
                if (
                    entry is not None
                    and not entry.terminal
                    and entry.active_resume_request_id == resume_request_id
                ):
                    entry.state = ResumeTicketState.RETRY_SAME_REQUEST
                    self._resume_condition.notify_all()
            if propagate:
                raise
            # Avoid a hot loop when ACK timeout occurs without a disconnect.
            time.sleep(0.25)
            return None

        settlement_retry = False
        with self._lock:
            entry = self._pending_resume_tickets.get(key)
            if (
                entry is None
                or entry.active_resume_request_id != resume_request_id
            ):
                return message
            self._apply_resume_outcome_locked(entry, outcome)
            settlement_retry = (
                entry.state is ResumeTicketState.RETRY_SAME_REQUEST
                and outcome.code == "RESUME_CONFLICT"
                and outcome.claim_id is not None
            )
            self._resume_condition.notify_all()
        if settlement_retry and not propagate:
            # The same consumed claim is still settling its durable handoff.
            # Back off instead of hammering the server while preserving rr.
            time.sleep(0.25)
        return message

    def _apply_resume_outcome_locked(
        self,
        entry: PendingResumeEntry,
        outcome: ResumeProtocolOutcome,
    ) -> None:
        ticket = entry.ticket
        if (
            outcome.execution_id != ticket.execution_id
            or outcome.checkpoint_id != ticket.checkpoint_id
            or outcome.resume_request_id != entry.active_resume_request_id
        ):
            entry.state = ResumeTicketState.CONFLICT
            return

        key = ticket.key
        if outcome.kind == "ACCEPTED":
            if outcome.accepted_revision is None:
                entry.state = ResumeTicketState.CONFLICT
                return
            self._advance_resume_watermark_locked(
                ticket.execution_id,
                outcome.accepted_revision,
            )
            entry.state = ResumeTicketState.ACCEPTED
            self._pending_resume_tickets.pop(key, None)
            return

        if outcome.kind == "FAILED":
            # FAILED is emitted only after resume authority was acquired. Even
            # if recovery checkpointing itself failed and no recovery_revision
            # is available, C1 is stale because WAITING@N already advanced at
            # least to RUNNING@N+1.
            authority_floor = (
                outcome.recovery_revision
                if outcome.recovery_revision is not None
                else ticket.revision + 1
            )
            self._advance_resume_watermark_locked(
                ticket.execution_id,
                authority_floor,
            )
            entry.last_outcome_code = outcome.code
            entry.state = ResumeTicketState.FAILED
            self._pending_resume_tickets.pop(key, None)
            return

        entry.last_outcome_code = outcome.code
        if (
            outcome.code == "RESUME_CONFLICT"
            and outcome.retryable
            and outcome.claim_id is not None
        ):
            # Same durable claim is already CONSUMED but its supervisor handoff
            # outcome has not settled yet. This is ACK/settlement uncertainty,
            # not a new logical attempt: preserve the exact request id.
            entry.state = ResumeTicketState.RETRY_SAME_REQUEST
            return

        entry.clear_resume_request_for_new_attempt()
        if outcome.code == "CLAIM_EXPIRED":
            # The old request is known not to own authority. A new request ID
            # may be allocated only if the ticket remains currently eligible.
            entry.state = ResumeTicketState.OBSERVED
            self._classify_resume_entry_locked(entry)
            return
        if outcome.retryable:
            entry.state = ResumeTicketState.WAIT_REFRESH
            return
        entry.state = ResumeTicketState.REJECTED
        self._pending_resume_tickets.pop(key, None)

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
            self._confirmed_capability_ids = frozenset()
            self._resume_worker_stop = True
            self._resume_condition.notify_all()
            worker = self._resume_worker_thread
        # Closing the transport wakes a worker blocked on resume ACK.
        self.realtime.close()
        if worker and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        with self._lock:
            if self._resume_worker_thread is worker:
                self._resume_worker_thread = None
        self.capabilities.shutdown()

    def chat(self, payload):
        if self._ready:
            if hasattr(payload, "model_copy"):
                payload = payload.model_copy(update={"connection_id": self.connection_id})
            elif isinstance(payload, dict):
                payload = {**payload, "connection_id": self.connection_id}
        result = self.gateway.send_request(payload)
        if isinstance(result, dict):
            self._ingest_waiting_payload(result)
            return result
        if isinstance(result, GeneratorType):
            upstream = result

            def _wrapped():
                for item in upstream:
                    if isinstance(item, dict):
                        self._ingest_waiting_payload(item)
                    yield item

            return _wrapped()
        return result

    def health(self):
        return self.gateway.health()

    def readiness(self):
        return self.gateway.readiness()

    def resume_execution(self, execution_id: str, checkpoint_id: str):
        """Manually trigger the same ticket state machine used by auto-resume."""

        key = (execution_id, checkpoint_id)
        with self._lock:
            if not self._ready:
                raise RuntimeError("Client runtime is not READY.")
            entry = self._pending_resume_tickets.get(key)
            if entry is None or entry.terminal:
                raise KeyError(f"Unknown pending resume ticket: {key!r}")
            self._classify_resume_entry_locked(entry)
            if entry.state is ResumeTicketState.WAIT_REFRESH:
                entry.state = ResumeTicketState.ELIGIBLE
            attempt = self._claim_resume_attempt_locked(key)
            if attempt is None:
                raise RuntimeError("Pending resume ticket is not eligible.")
        return self._execute_resume_attempt(key, attempt, propagate=True)

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
        self.dispatcher.set_identity(
            self.client_id,
            None,
        )
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
