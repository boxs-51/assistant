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
