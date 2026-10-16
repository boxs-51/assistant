"""TA-V1-R3 P0-REVOK local revoke/start race oracles.

These tests never spawn a process or access a network. They do not certify
remote revocation, MCP process containment or native R-B/R-C.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from cl.src.core.local_effect_start_gate import (
    EffectStartRevoked,
    LocalEffectStartGate,
)
from cl.src.core.local_capability_executor import LocalCapabilityExecutor


def test_revoke_ack_waits_for_active_effect_lease():
    gate = LocalEffectStartGate()
    started = threading.Event()
    release = threading.Event()

    def effect():
        with gate.effect_lease():
            started.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(effect)
        assert started.wait(5)
        revoked = pool.submit(gate.revoke_and_drain, timeout_seconds=5)
        # The active handler has not completed; ACK must not be published.
        assert not revoked.done()
        release.set()
        running.result(timeout=5)
        epoch = revoked.result(timeout=5)
        assert epoch == 1

    with pytest.raises(EffectStartRevoked):
        with gate.effect_lease():
            raise AssertionError("must not run")


def test_timed_out_drain_is_not_an_ack_and_denies_new_effects():
    gate = LocalEffectStartGate()
    started = threading.Event()
    release = threading.Event()

    def effect():
        with gate.effect_lease():
            started.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(effect)
        assert started.wait(5)
        with pytest.raises(TimeoutError, match="no revoke ACK"):
            gate.revoke_and_drain(timeout_seconds=0.01)
        with pytest.raises(EffectStartRevoked):
            with gate.effect_lease():
                pass
        release.set()
        running.result(timeout=5)

    assert gate.revoke_and_drain(timeout_seconds=0.1) == 1


class _Approve:
    def request_approval(self, *args, **kwargs):
        return True


class _Registry:
    def __init__(self, handler):
        self.tools = {
            "safe.echo": {
                "func": handler,
                "metadata": {"name": "safe.echo", "base_risk": "LOW"},
            },
        }

    def get_tool(self, name):
        return self.tools.get(name)


def test_local_executor_does_not_start_effect_after_gate_revoke():
    calls = []

    def handler(value):
        calls.append(value)
        return value

    gate = LocalEffectStartGate()
    executor = LocalCapabilityExecutor(
        _Registry(handler), hitl=_Approve(), effect_start_gate=gate
    )
    gate.revoke_and_drain()
    with pytest.raises(EffectStartRevoked):
        executor.execute(
            "safe.echo",
            {"value": "no-effect"},
            {"invocation_id": "ta-r3-req", "payload": {}},
            threading.Event(),
        )
    assert calls == []
