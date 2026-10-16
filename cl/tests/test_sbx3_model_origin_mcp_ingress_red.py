"""SBX3 ING-01/02/05/06/08 executable RED-first ingress oracles.

The stdio server is genuine MCP; only model output is deterministic. An
uncertified model-origin tool must be rejected before HITL or its MCP handler.
No test here simulates hostile Python subclasses arriving over JSON.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil

from cl.src.core.agent_engine import AgentEngine
from cl.src.core.content_processor import parse_text_action_fallback
from cl.src.loader.registry import DynamicRegistry
from cl.src.schemas.context import AgentContextSession


FIXTURE = Path(__file__).parent / "fixtures" / "mcp_stdio_sbx3_ingress_server.py"
DANGEROUS = "mcp__ingress__innocent_preview"


def _config(marker: Path, name: str = "ingress"):
    return {
        "enabled": True,
        "command": sys.executable,
        "args": ["-E", str(FIXTURE)],
        "base_risk": "LOW",
        "env": {"SBX3_INGRESS_MARKER": str(marker)},
    }


def _registry(tmp_path: Path, *, names=("ingress",)):
    registry = DynamicRegistry(
        tools_dir=str(tmp_path / "tools"),
        skills_dir=str(tmp_path / "skills"),
        config_dir=str(tmp_path / "settings"),
    )
    # Real ConfigLoader/LocalToolManager wiring, isolated configuration and
    # real MCPManager.start/list_tools. No external or production config.
    registry.config_loader.load_settings = lambda: {
        "tools_config": {},
        "mcp_servers": {n: _config(tmp_path / (n + ".marker"), n) for n in names},
    }
    registry.config_loader.load_constitution = lambda: "SBX3 fixture only"
    registry.local_tool_mgr.load_tools = lambda _: {}
    registry.skill_mgr.load_skills = lambda: {}
    # MCPManager prints a Unicode status glyph; prevent cp1252 console
    # errors on hosted Windows without changing production MCP behavior.
    with contextlib.redirect_stdout(io.StringIO()):
        registry.load_all()
    for n in names:
        assert "mcp__" + n + "__innocent_preview" in registry.tools, (
            "HARNESS_ERROR: real MCP list_tools registration missing"
        )
    return registry


class _AllowHITL:
    def __init__(self):
        self.calls = []

    def request_approval(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True


class _Gateway:
    """Model-output double only. AgentEngine parsing and ToolExecutor are real."""

    def __init__(self, name, *, stream=False, action=None):
        self.name = name
        self.stream = stream
        self.action = action
        self.calls = 0
        self.requests = []

    def send_request(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.stream:
            return self._stream()
        if self.calls > 1:
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(
                    content="complete", tool_calls=None
                ))
            ])
        if self.action is not None:
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(
                    content=self.action, tool_calls=None
                ))
            ])
        return SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(
                content="", tool_calls=[{
                    "id": "ing-json-call",
                    "type": "function",
                    "function": {"name": self.name,
                                 "arguments": json.dumps({"value": "model"})},
                }]
            ))
        ])

    def _stream(self):
        if self.calls > 1:
            yield SimpleNamespace(choices=[
                SimpleNamespace(delta=SimpleNamespace(
                    content="complete", reasoning_content=None, tool_calls=None
                ))
            ])
            return
        delta = SimpleNamespace(
            index=0,
            id="ing-stream-call",
            function=SimpleNamespace(
                name=self.name, arguments=json.dumps({"value": "model"})
            ),
        )
        yield SimpleNamespace(choices=[
            SimpleNamespace(delta=SimpleNamespace(
                content="", reasoning_content=None, tool_calls=[delta]
            ))
        ])


def _model_run(registry, name, *, stream=False, action=None):
    hitl = _AllowHITL()
    gateway = _Gateway(name, stream=stream, action=action)
    session = AgentContextSession()
    engine = AgentEngine(registry, hitl, gateway, mock_mode=False)
    outcome = engine.run_agent_session(
        session=session,
        user_input="Run only the named test tool",
        max_steps=2,
        enable_stream=stream,
        provider_name="test-double",
        model_name="sbx3-test",
    )
    assert "error" not in outcome, "HARNESS_ERROR: AgentEngine aborted: " + str(outcome)
    assert gateway.calls >= 1, "HARNESS_ERROR: inference never ran"
    return session, hitl, gateway, outcome


def _denied(session, hitl, marker: Path):
    messages = [x for x in session.working_messages if x.role == "tool"]
    assert messages, "HARNESS_ERROR: no model tool result or executable denial"
    assert len(messages) == 1
    assert messages[0].content.startswith("AGENT_TOOL_POLICY_DENIED:"), (
        "POLICY_RED: missing pre-HITL Agent process DENY: " + repr(messages[0].content)
    )
    assert hitl.calls == [], "POLICY_RED: HITL cannot substitute for native DENY"
    assert not marker.exists(), "PHYSICAL_RED: real MCP child wrote a marker"


def test_ing_t1_01_real_stdio_model_unary_and_stream_pre_hitl(tmp_path):
    registry = _registry(tmp_path)
    try:
        tool = registry.get_tool(DANGEROUS)
        assert tool is registry.tools[DANGEROUS]
        assert tool["is_mcp"] is True
        for streaming in (False, True):
            session, hitl, gateway, outcome = _model_run(
                registry, DANGEROUS, stream=streaming
            )
            _denied(session, hitl, tmp_path / "ingress.marker")
            assert outcome["steps"] == 2
            assert gateway.calls == 2
    finally:
        registry.shutdown()


def test_ing_t1_02_model_action_fallback_parser_reachability(tmp_path):
    # The production fallback grammar intentionally cannot parse dotted IDs;
    # a parser non-match is not a security DENY.
    action = "Action: mcp__ingress__innocent_preview(value='model')"
    parsed = parse_text_action_fallback(action)
    assert len(parsed) == 1 and parsed[0].function.name == DANGEROUS
    assert parse_text_action_fallback(
        "Action: terminal.run(value='model')"
    ) == []
    registry = _registry(tmp_path)
    try:
        session, hitl, _, _ = _model_run(registry, DANGEROUS, action=action)
        _denied(session, hitl, tmp_path / "ingress.marker")
    finally:
        registry.shutdown()


def test_ing_t1_05_multi_stdio_namespace_suffix_collision_fails_closed(tmp_path):
    registry = _registry(tmp_path, names=("alpha", "beta"))
    try:
        a = registry.get_tool("mcp__alpha__innocent_preview")
        b = registry.get_tool("mcp__beta__innocent_preview")
        assert a is registry.tools["mcp__alpha__innocent_preview"]
        assert b is registry.tools["mcp__beta__innocent_preview"]
        assert a is not b, "HARNESS_ERROR: two real MCP servers conflated"
        assert registry.get_tool("innocent_preview") is None, (
            "POLICY_RED: ambiguous unqualified MCP suffix resolves by load order"
        )
    finally:
        registry.shutdown()


def test_ing_t1_06_json_wire_cannot_materialize_hostile_python_registry_objects(tmp_path):
    registry = _registry(tmp_path)
    try:
        # Only JSON-origin primitives enter the model-call DTO.
        wire = json.loads(json.dumps({
            "name": DANGEROUS, "arguments": {"value": "wire-test"}
        }))
        assert type(wire) is dict
        assert type(wire["arguments"]) is dict
        assert type(wire["name"]) is str
        assert type(registry.tools[DANGEROUS]) is dict
        # A dict-subclass __bool__/__len__ witness is an in-process registry
        # adversary, not claimed reachable from the JSON/MCP wire.
        session, hitl, _, _ = _model_run(registry, wire["name"])
        _denied(session, hitl, tmp_path / "ingress.marker")
    finally:
        registry.shutdown()


def test_ing_t1_08_stdio_bootstrap_allowed_but_model_dispatch_no_extra_child(tmp_path):
    registry = _registry(tmp_path)
    child_pid = None
    try:
        echo = registry.get_tool("mcp__ingress__safe_echo")
        assert echo is not None
        result = echo["func"](value="healthy")
        prefix, pid, value = result.split(":")
        assert (prefix, value) == ("safe", "healthy"), (
            "HARNESS_ERROR: positive MCP stdio control did not execute"
        )
        child_pid = int(pid)
        assert psutil.pid_exists(child_pid), (
            "HARNESS_ERROR: MCP bootstrap child not alive"
        )
        session, hitl, _, _ = _model_run(registry, DANGEROUS)
        _denied(session, hitl, tmp_path / "ingress.marker")
        assert psutil.pid_exists(child_pid), "HARNESS_ERROR: MCP exited during denial"
    finally:
        registry.shutdown()
    assert child_pid is not None
    assert not psutil.pid_exists(child_pid), "HARNESS_ERROR: orphan MCP stdio child"
