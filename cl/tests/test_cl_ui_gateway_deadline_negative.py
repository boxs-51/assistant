"""Negative-only CL/UI evidence for socket-idle versus total gateway effect time.

These tests demonstrate *remaining* security/liveness gaps, not production
approval or a safe automatic retry mechanism. No server-side effect executes.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from cl.src.core import gateway_client
from cl.src.core.gateway_client import GatewayEffectOutcomeUnknown, GatewayLLMClient


def test_real_http_trickle_can_outlive_read_inactivity_timeout(monkeypatch):
    """A byte before every read idle timeout defeats a whole-operation bound."""
    calls = []
    response_bytes = b'{"accepted":true}'
    first_byte = threading.Event()
    completion = threading.Event()

    class TrickleGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            try:
                for index, byte in enumerate(response_bytes):
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    if index == 0:
                        first_byte.set()
                    # Drip frequently enough that the HTTP read-idle timeout
                    # is never exceeded despite a much longer total exchange.
                    time.sleep(0.10)
            finally:
                completion.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), TrickleGateway)
    server.daemon_threads = True
    server_thread = threading.Thread(
        target=server.serve_forever, daemon=True
    )
    server_thread.start()
    try:
        monkeypatch.setattr(
            gateway_client, "DIRECT_UI_EFFECT_TIMEOUT", (1.0, 0.8),
        )
        client = GatewayLLMClient(f"http://127.0.0.1:{server.server_port}")
        started = time.monotonic()
        result = client.execute_capability("test-effect", {"arguments": {}})
        elapsed = time.monotonic() - started
        assert result == {"accepted": True}
        assert first_byte.is_set()
        assert elapsed >= 0.8  # Longer than configured 0.8s read inactivity.
        assert calls == ["/v1/capabilities/test-effect/execute"]
        assert completion.wait(2.0)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(2.0)


def test_manual_reissue_after_unknown_is_not_durably_fenced(monkeypatch):
    """No auto-retry != protection against an explicit second UI request."""
    attempts = []

    class Response:
        status_code = 200

        def json(self):
            return {"accepted": True}

    def request(method, url, **kwargs):
        attempts.append((method, url, kwargs["json"]))
        if len(attempts) == 1:
            # The first request *might* have been accepted server-side.
            raise requests.exceptions.ReadTimeout("reply lost")
        return Response()

    monkeypatch.setattr(requests, "request", request)
    client = GatewayLLMClient("http://gateway")
    body = {"arguments": {"operation": "one-way-effect"}}
    with pytest.raises(GatewayEffectOutcomeUnknown):
        client.execute_capability("test-effect", body)

    # Caller submits another explicit action; the client has no durable
    # canonical invocation/receipt key to distinguish it from a new effect.
    assert client.execute_capability("test-effect", body) == {"accepted": True}
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert "invocation_id" not in body
