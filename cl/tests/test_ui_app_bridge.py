import inspect
import threading
import time

from cl.src.ui.bridge import UIBridge


class _Hitl:
    def set_approval_callback(self, callback):
        self.callback = callback

    def request_approval(self, *args, **kwargs):
        return True


class _Registry:
    def __init__(self):
        self.settings = {"loaded": True}
        self.skills = {
            "review": {
                "name": "review",
                "description": "Review workflow",
                "version": "1.0",
                "base_risk": "LOW",
                "path": "private/path",
                "content": None,
                "mtime_ns": 1,
                "loaded": False,
            }
        }
        self.tools = {
            "echo": {
                "metadata": {
                    "description": "Echo tool",
                    "base_risk": "LOW",
                    "parameters": {"type": "object"},
                },
                "func": lambda **kwargs: kwargs,
                "is_mcp": False,
            }
        }

    def activate_skill(self, name):
        self.skills[name] = {**self.skills[name], "loaded": True, "content": "Review carefully"}
        return dict(self.skills[name])

    def deactivate_skill(self, name):
        self.skills[name] = {**self.skills[name], "loaded": False, "content": None}
        return True

    def get_tool(self, name):
        return self.tools.get(name)


class _Gateway:
    def __init__(self):
        self.registered_skills = []
        self.registered_agents = []

    def health(self):
        return {"status": "ok"}

    def list_capabilities(self, kind=None):
        return []

    def list_agents(self):
        return []

    def get_sessions(self):
        return [{"session_id": "session-1", "title": "Session 1"}]

    def get_session(self, session_id):
        return {
            "session_id": session_id,
            "title": "Session 1",
            "status": "ACTIVE",
            "messages": [
                {"id": "m1", "role": "user", "content": "hello", "sequence": 1},
            ],
        }

    def register_skill(self, payload):
        self.registered_skills.append(payload)
        return {"capability_id": payload["name"]}

    def execute_capability(self, capability_id, payload):
        return {"capability_id": capability_id, "output": payload["arguments"]}

    def register_capability_agent(self, payload):
        self.registered_agents.append(payload)
        return {"capability_id": payload["name"]}

    def create_agent_session(self, agent_ids):
        return {"session_id": "session-1", "agent_ids": agent_ids}

    def create_agent_task(self, payload):
        return {"task_id": "task-1", **payload}

    def start_agent_task(self, task_id):
        return {"task_id": task_id, "status": "RUNNING"}

    def get_agent_task(self, task_id):
        return {"task_id": task_id, "status": "COMPLETED", "output": {"text": "done"}}

    def cancel_agent_task(self, task_id):
        return {"task_id": task_id, "status": "CANCELLED"}


class _ClientRuntime:
    ready = True

    def __init__(self, gateway):
        self.gateway = gateway
        self.auth_calls = []
        self.chat_calls = []
        self.start_calls = 0

    def start(self):
        self.start_calls += 1
        self.ready = True

    def login(self, payload):
        self.auth_calls.append(("login", payload))
        return {"user": {"id": "user-1"}}

    def register(self, payload):
        self.auth_calls.append(("register", payload))
        return {"status": "success"}

    def verify_registration(self, payload):
        self.auth_calls.append(("verify_registration", payload))
        return {"user": {"id": "user-1"}}

    def initiate_password_reset(self, payload):
        self.auth_calls.append(("initiate_password_reset", payload))
        return {"status": "success"}

    def confirm_password_reset(self, payload):
        self.auth_calls.append(("confirm_password_reset", payload))
        return {"status": "success"}

    def chat(self, payload):
        self.chat_calls.append(payload)
        return iter(())


class _Engine:
    approval_timeout_seconds = 0.01

    def __init__(self, registry, gateway, hitl, workspace_dir):
        self.registry = registry
        self.gateway_client = gateway
        self.hitl = hitl
        self.workspace_dir = str(workspace_dir)


def _bridge():
    registry = _Registry()
    gateway = _Gateway()
    hitl = _Hitl()
    engine = _Engine(registry, gateway, hitl, ".")
    return UIBridge(engine, hitl, _ClientRuntime(gateway)), registry, gateway


def test_app_snapshot_does_not_expose_skill_path_or_content():
    bridge, _, _ = _bridge()

    snapshot = bridge.get_app_snapshot()

    assert snapshot["gateway"]["connected"] is True
    assert snapshot["skills"][0]["name"] == "review"
    assert "path" not in snapshot["skills"][0]
    assert "content" not in snapshot["skills"][0]
    assert snapshot["tools"][0]["name"] == "echo"


def test_skill_tool_and_agent_app_actions_hide_endpoint_orchestration():
    bridge, registry, gateway = _bridge()

    activated = bridge.activate_skill("review")
    assert activated["success"] is True
    assert registry.skills["review"]["loaded"] is True
    assert gateway.registered_skills[0]["instruction"] == "Review carefully"

    executed = bridge.execute_tool("echo", {"text": "hello"})
    assert executed["success"] is True
    assert executed["data"]["output"] == {"text": "hello"}

    saved = bridge.save_agent({
        "name": "reviewer",
        "goal": "Review",
        "instruction": "Be careful",
        "skills": ["review"],
        "tools": ["echo"],
    })
    assert saved["success"] is True

    run = bridge.run_agent({"agent_id": "reviewer", "prompt": "Review this"})
    assert run["success"] is True
    assert run["data"]["session"]["session_id"] == "session-1"
    assert run["data"]["task"]["status"] == "RUNNING"
    assert bridge.get_agent_task_status("task-1")["data"]["status"] == "COMPLETED"
    assert bridge.cancel_agent_task("task-1")["data"]["status"] == "CANCELLED"


def test_execute_tool_rejects_a_missing_tool_name_without_raising():
    bridge, _, gateway = _bridge()

    result = bridge.execute_tool()

    assert result == {"success": False, "error": "Tool name is required."}
    assert gateway.registered_skills == []


def test_execute_tool_signature_does_not_shadow_javascript_arguments_object():
    parameter_names = list(inspect.signature(UIBridge.execute_tool).parameters)

    assert parameter_names == ["self", "tool_name", "tool_arguments"]
    assert "arguments" not in parameter_names


def test_account_actions_are_exposed_through_bridge_without_leaking_exceptions():
    bridge, _, _ = _bridge()

    assert bridge.register({"email": "user@example.com", "password": "secret1"})["success"] is True
    assert bridge.verify_registration({"email": "user@example.com", "otp": "123456"})["success"] is True
    assert bridge.initiate_password_reset({"email": "user@example.com"})["success"] is True
    assert bridge.confirm_password_reset({
        "email": "user@example.com",
        "otp": "123456",
        "new_password": "secret2",
    })["success"] is True

    assert [name for name, _ in bridge._client_runtime.auth_calls] == [
        "register",
        "verify_registration",
        "initiate_password_reset",
        "confirm_password_reset",
    ]


def test_sidebar_session_load_bootstraps_auth_before_request():
    bridge, _, _ = _bridge()
    bridge._client_runtime.ready = False

    sessions = bridge.get_sessions()

    assert bridge._client_runtime.start_calls == 1
    assert sessions == [{"session_id": "session-1", "title": "Session 1"}]


def test_session_detail_bootstraps_runtime_and_preserves_exact_identity():
    bridge, _, _ = _bridge()
    bridge._client_runtime.ready = False

    session = bridge.get_session("session-1")

    assert bridge._client_runtime.start_calls == 1
    assert session["session_id"] == "session-1"
    assert session["messages"][0]["sequence"] == 1


def test_scoped_render_block_carries_conversation_and_execution_identity():
    bridge, _, _ = _bridge()
    calls = []

    class Window:
        def evaluate_js(self, source):
            calls.append(source)
            return True

    bridge.set_window(Window())
    bridge.render_block(
        role="assistant",
        btype="stream_content",
        data={"text": "hello"},
        conversation_id="conversation-1",
        execution_id="exec-1",
    )

    assert len(calls) == 1
    assert "window.onConversationBlock" in calls[0]
    assert '"conversation_id": "conversation-1"' in calls[0]
    assert '"execution_id": "exec-1"' in calls[0]


def test_same_conversation_busy_state_is_published_only_after_serialization_lock():
    bridge, _, _ = _bridge()
    runtime = bridge._client_runtime
    first_chat_started = threading.Event()
    release_first = threading.Event()
    chat_calls = []

    def blocking_chat(payload):
        chat_calls.append(payload)
        if len(chat_calls) == 1:
            first_chat_started.set()
            assert release_first.wait(1.0)
        return iter(())

    runtime.chat = blocking_chat
    states = []
    bridge._emit_conversation_execution_state = (
        lambda cid, eid, state, generation:
        states.append((cid, eid, state, generation))
    )

    first = bridge.submit_prompt("first", conversation_id="conversation-1")
    assert first_chat_started.wait(1.0)
    second = bridge.submit_prompt("second", conversation_id="conversation-1")

    time.sleep(0.05)
    busy = [item for item in states if item[2] == "BUSY"]
    assert [item[1] for item in busy] == [first["execution_id"]]

    release_first.set()
    deadline = time.monotonic() + 1.0
    while len([item for item in states if item[2] == "TERMINAL"]) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    busy = [item for item in states if item[2] == "BUSY"]
    terminal = [item for item in states if item[2] == "TERMINAL"]
    assert [item[1] for item in busy] == [first["execution_id"], second["execution_id"]]
    assert [item[1] for item in terminal] == [first["execution_id"], second["execution_id"]]
    assert len(chat_calls) == 2


def test_queued_old_identity_execution_fails_closed_before_chat_dispatch():
    bridge, _, _ = _bridge()
    runtime = bridge._client_runtime
    first_chat_started = threading.Event()
    release_first = threading.Event()
    chat_calls = []

    def blocking_chat(payload):
        chat_calls.append(payload)
        if len(chat_calls) == 1:
            first_chat_started.set()
            assert release_first.wait(1.0)
        return iter(())

    runtime.chat = blocking_chat
    states = []
    bridge._emit_conversation_execution_state = (
        lambda cid, eid, state, generation:
        states.append((cid, eid, state, generation))
    )

    first = bridge.submit_prompt("first", conversation_id="conversation-1")
    assert first_chat_started.wait(1.0)
    second = bridge.submit_prompt("second", conversation_id="conversation-1")

    old_sessions = bridge.sessions
    assert bridge.login({"email": "next@example.com"})["success"] is True
    assert bridge.sessions is not old_sessions

    release_first.set()
    deadline = time.monotonic() + 1.0
    while not any(item[1] == first["execution_id"] and item[2] == "TERMINAL" for item in states) and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)

    assert len(chat_calls) == 1
    assert not any(item[1] == second["execution_id"] for item in states)


def test_submit_prompt_uses_server_agent_runtime_by_default(monkeypatch):
    bridge, _, _ = _bridge()

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("cl.src.ui.bridge.threading.Thread", ImmediateThread)

    bridge.submit_prompt("hello", conversation_id="conversation-1")

    request = bridge._client_runtime.chat_calls[0]
    assert request.agent_enabled is True
    assert request.agent_id == "agent-coordinator"
    assert request.session_id == "conversation-1"
    assert request.messages[0].content == "hello"


def test_submit_prompt_can_disable_server_agent(monkeypatch):
    bridge, _, _ = _bridge()

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("cl.src.ui.bridge.threading.Thread", ImmediateThread)
    updated = bridge.set_chat_preferences({
        "provider": "mock",
        "model": "mock-chat",
        "agent_enabled": False,
        "agent_id": None,
    })
    assert updated["success"] is True

    bridge.submit_prompt("hello", conversation_id="conversation-1")

    request = bridge._client_runtime.chat_calls[0]
    assert request.agent_enabled is False
    assert request.agent_id is None


def test_submit_prompt_passes_configured_agent_time_budgets(monkeypatch):
    bridge, _, _ = _bridge()

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("cl.src.ui.bridge.threading.Thread", ImmediateThread)
    updated = bridge.set_chat_preferences({
        "provider": "mock",
        "model": "mock-chat",
        "agent_limits": {
            "timeout_seconds": 600,
            "iteration_timeout_seconds": 420,
            "inference_timeout_seconds": 90,
            "tool_timeout_seconds": 330,
        },
    })
    assert updated["success"] is True

    bridge.submit_prompt("run a five minute task", conversation_id="conversation-1")
    request = bridge._client_runtime.chat_calls[0]
    assert request.agent_limits.timeout_seconds == 600
    assert request.agent_limits.tool_timeout_seconds == 330
