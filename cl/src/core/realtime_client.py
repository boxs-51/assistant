from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

import websocket


class RealtimeConnectionError(RuntimeError):
    pass


class RealtimeHandshakeError(RealtimeConnectionError):
    pass


class GatewayRealtimeClient:
    """
    Persistent synchronous WebSocket transport.

    Invariant:
        Exactly one receiver thread owns websocket.recv().

    No other caller is allowed to consume inbound frames directly.
    """

    def __init__(
        self,
        base_url: str,
        headers: Dict[str, str],
        *,
        connection_id: Optional[str] = None,
        session_id: Optional[str] = None,
        client_id: str = "client",
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_disconnect: Optional[Callable[[BaseException | None], None]] = None,
        timeout: float = 30.0,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers)

        self.connection_id = (
            connection_id or f"cl-{uuid.uuid4().hex}"
        )
        self.session_id = (
            session_id or f"session-{uuid.uuid4().hex}"
        )
        self.client_id = client_id

        self.timeout = timeout
        self.heartbeat_interval = heartbeat_interval

        self._ws_url = (
            self.base_url
            .replace("https://", "wss://")
            .replace("http://", "ws://")
            + "/v1/events/ws"
        )

        self.ws: Optional[websocket.WebSocket] = None

        self._on_message = on_message
        self._on_disconnect = on_disconnect

        self._receiver_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        self._stop_event = threading.Event()
        self._connected = threading.Event()
        self._registered = threading.Event()
        self._capabilities_registered = threading.Event()

        self._state_lock = threading.RLock()
        self._send_lock = threading.Lock()

        self._inbound_condition = threading.Condition()
        self._inbound: Deque[Dict[str, Any]] = deque()

        self._disconnect_error: Optional[BaseException] = None

    def update_headers(self, headers: Dict[str, str]) -> None:
        self.headers = dict(headers)
        
    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def is_registered(self) -> bool:
        return self._registered.is_set()

    @property
    def capabilities_registered(self) -> bool:
        return self._capabilities_registered.is_set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, *, wait_timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Establish socket, start receiver, perform connection.register
        and wait for connection.registered.

        The receiver is started BEFORE the register message is sent.
        """

        with self._state_lock:
            if self.is_connected:
                return {
                    "type": "connection.registered",
                    "connection_id": self.connection_id,
                    "payload": {"state": "ACTIVE"},
                }

            self._stop_event.clear()
            self._registered.clear()
            self._capabilities_registered.clear()
            self._disconnect_error = None
            authorization = self.headers.get("Authorization")
            if not authorization:
                raise RuntimeError(
                    "Realtime connection requires an authenticated user or guest session."
                )

            self.ws = websocket.create_connection(
                self._ws_url,
                header=[
                    f"Authorization: {authorization}",
                ],
                timeout=self.timeout,
            )

            self._connected.set()

            self._receiver_thread = threading.Thread(
                target=self._receiver_loop,
                name="gateway-realtime-receiver",
                daemon=True,
            )
            self._receiver_thread.start()

            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="gateway-realtime-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

            self.send(
                "connection.register",
                {
                    "client_id": self.client_id,
                    "connection_id": self.connection_id,
                    "session_id": self.session_id,
                },
            )

        timeout = (
            self.timeout
            if wait_timeout is None
            else wait_timeout
        )

        message = self._wait_for_message(
            lambda item: (
                item.get("type") == "connection.registered"
                and item.get("connection_id") == self.connection_id
                and item.get("payload", {}).get("state") == "ACTIVE"
            ),
            timeout,
        )

        if message is None:
            self.close()
            raise RealtimeHandshakeError(
                "Timed out waiting for connection.registered."
            )

        self._registered.set()
        return message

    def close(self) -> None:
        """
        Idempotent close.

        Closing the socket causes recv() to unblock and lets the receiver
        thread terminate naturally.
        """

        with self._state_lock:
            if (
                self.ws is None
                and self._receiver_thread is None
                and self._heartbeat_thread is None
            ):
                return

            self._stop_event.set()
            self._connected.clear()
            self._registered.clear()
            self._capabilities_registered.clear()

            ws = self.ws
            self.ws = None

        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

        current = threading.current_thread()

        receiver = self._receiver_thread
        if receiver and receiver is not current:
            receiver.join(timeout=2.0)

        heartbeat = self._heartbeat_thread
        if heartbeat and heartbeat is not current:
            heartbeat.join(timeout=2.0)

        with self._state_lock:
            self._receiver_thread = None
            self._heartbeat_thread = None

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        invocation_id: Optional[str] = None,
        *,
        execution_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        with self._send_lock:
            if self.ws is None or not self.is_connected:
                raise RealtimeConnectionError(
                    "Realtime connection is not open."
                )

            envelope = {
                "protocol_version": 1,
                "type": event_type,
                "message_id": f"msg-{uuid.uuid4().hex}",
                "session_id": self.session_id,
                "connection_id": self.connection_id,
                "invocation_id": invocation_id,
                "execution_id": execution_id,
                "trace_id": trace_id,
                "payload": payload or {},
            }

            try:
                self.ws.send(
                    json.dumps(
                        envelope,
                        ensure_ascii=False,
                    )
                )
            except Exception as exc:
                self._mark_disconnected(exc)
                raise RealtimeConnectionError(
                    "Failed to send realtime message."
                ) from exc

    def send_result(
        self,
        invocation_id: str,
        result: Any,
        *,
        execution_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        # SE resolves the COMPLETE payload as the result.
        self.send(
            "capability.result",
            {
                "output": result,
            },
            invocation_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )

    def send_error(
        self,
        invocation_id: str,
        *,
        code: str,
        message: str,
        details: Optional[Any] = None,
        retryable: bool = False,
        execution_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }

        if details is not None:
            payload["details"] = details

        self.send(
            "capability.error",
            payload,
            invocation_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )

    def send_cancelled(
        self,
        invocation_id: str,
        *,
        execution_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.send(
            "capability.cancelled",
            {},
            invocation_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )

    def send_reconciliation(
        self,
        invocation_id: str,
        payload: Dict[str, Any],
        *,
        execution_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.send(
            "capability.reconciliation",
            payload,
            invocation_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )

    def resume_execution(
        self,
        execution_id: str,
        checkpoint_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not self.capabilities_registered:
            raise RealtimeConnectionError(
                "Capabilities must be registered before execution.resume."
            )
        self.send(
            "execution.resume",
            {
                "execution_id": execution_id,
                "checkpoint_id": checkpoint_id,
                "connection_id": self.connection_id,
            },
            execution_id=execution_id,
        )
        message = self._wait_for_message(
            lambda item: (
                item.get("type") == "execution.resume.accepted"
                and item.get("connection_id") == self.connection_id
                and item.get("execution_id") == execution_id
            ),
            self.timeout if timeout is None else timeout,
        )
        if message is None:
            raise RealtimeHandshakeError("Timed out waiting for resume acknowledgement.")
        return message

    # ------------------------------------------------------------------
    # Incoming message handling
    # ------------------------------------------------------------------

    def _receiver_loop(self) -> None:
        error: Optional[BaseException] = None

        try:
            while not self._stop_event.is_set():
                ws = self.ws

                if ws is None:
                    break

                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                if raw is None:
                    break

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")

                message = json.loads(raw)

                self._push_inbound(message)

                if self._on_message is not None:
                    try:
                        self._on_message(message)
                    except Exception:
                        # User callback must never kill the transport loop.
                        pass

        except Exception as exc:
            error = exc

        finally:
            self._mark_disconnected(error)

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(
            self.heartbeat_interval
        ):
            if not self.is_connected:
                return

            try:
                self.send(
                    "connection.heartbeat",
                    {},
                )
            except Exception:
                return

    def _push_inbound(
        self,
        message: Dict[str, Any],
    ) -> None:
        with self._inbound_condition:
            self._inbound.append(message)
            self._inbound_condition.notify_all()

    def _wait_for_message(
        self,
        predicate: Callable[[Dict[str, Any]], bool],
        timeout: float,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + timeout

        with self._inbound_condition:
            while True:
                # Kiểm tra ngắt kết nối đột ngột
                if not self.is_connected or self._stop_event.is_set():
                    if self._disconnect_error:
                        raise RealtimeConnectionError(
                            f"Connection lost: {self._disconnect_error}"
                        ) from self._disconnect_error
                    raise RealtimeConnectionError("Connection disconnected while waiting.")

                for message in list(self._inbound):
                    # 1. Nếu đúng tin nhắn mong đợi -> Trả về kết quả
                    if predicate(message):
                        try:
                            self._inbound.remove(message)
                        except ValueError:
                            pass
                        return message

                    # 2. FAST-FAIL: Nếu Server trả về tin nhắn LỖI -> Báo lỗi ngay lập tức
                    if message.get("status") == "error" or message.get("type") == "error":
                        try:
                            self._inbound.remove(message)
                        except ValueError:
                            pass
                        err_msg = (
                            message.get("message")
                            or message.get("payload", {}).get("message")
                            or "Unknown server error"
                        )
                        raise RealtimeHandshakeError(f"Server execution error: {err_msg}")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

                self._inbound_condition.wait(timeout=remaining)

    # ------------------------------------------------------------------
    # Registration acknowledgement
    # ------------------------------------------------------------------

    def mark_capabilities_registered(
        self,
        message: Dict[str, Any],
    ) -> None:
        if (
            message.get("type") == "capability.registered"
            and message.get("connection_id") == self.connection_id
            and "capabilities" in message.get("payload", {})
        ):
            self._capabilities_registered.set()

    def wait_capabilities_registered(
        self,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        effective_timeout = (
            self.timeout
            if timeout is None
            else timeout
        )

        message = self._wait_for_message(
            lambda item: (
                item.get("type") == "capability.registered"
                and item.get("connection_id") == self.connection_id
                and "capabilities" in item.get(
                    "payload",
                    {},
                )
            ),
            effective_timeout,
        )

        if message is None:
            raise RealtimeHandshakeError(
                "Timed out waiting for capability registration acknowledgement."
            )

        self._capabilities_registered.set()
        return message

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def _mark_disconnected(
        self,
        error: Optional[BaseException],
    ) -> None:
        should_notify = False

        with self._state_lock:
            if self._connected.is_set():
                should_notify = True

            self._connected.clear()
            self._registered.clear()

            if error is not None:
                self._disconnect_error = error

        with self._inbound_condition:
            self._inbound_condition.notify_all()

        if (
            should_notify
            and self._on_disconnect is not None
        ):
            try:
                self._on_disconnect(error)
            except Exception:
                pass
