import asyncio
import concurrent.futures
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from cl.src.mcp_client.mcp_adapter import MCPManager


SERVER = Path(__file__).parent / "fixtures" / "mcp_stdio_ownership_server.py"


def _server_config():
    return {
        "enabled": True,
        "command": sys.executable,
        # Ignore PYTHON* instrumentation inherited from the pytest parent.
        # The exit gate intentionally runs the parent with tracemalloc/asyncio
        # debug enabled; propagating those flags into every Python MCP child can
        # turn a transport test into a multi-minute interpreter-startup test.
        "args": ["-E", str(SERVER)],
        "base_risk": "LOW",
    }


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("Timed out waiting for condition")



@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific stdio ownership regression")
def test_windows_owner_runtime_uses_dedicated_anyio_portal_thread():
    manager = MCPManager(
        startup_timeout_seconds=5.0,
        shutdown_timeout_seconds=5.0,
    )
    caller_thread_id = threading.get_ident()
    owner_thread = None
    try:
        loop = manager._ensure_owner_loop()
        owner_thread = manager._thread
        assert loop is not None
        assert owner_thread is not None
        assert owner_thread.is_alive()
        assert manager.is_running
        assert manager.owner_thread_id is not None
        assert manager.owner_thread_id != caller_thread_id
    finally:
        manager.shutdown()

    assert not manager.is_running
    assert owner_thread is not None
    assert not owner_thread.is_alive()


def test_shutdown_timeout_force_cancels_stuck_portal_task(monkeypatch):
    manager = MCPManager(
        startup_timeout_seconds=2.0,
        shutdown_timeout_seconds=1.0,
    )
    owner_thread = None

    async def never_close():
        await asyncio.Event().wait()

    manager._ensure_owner_loop()
    owner_thread = manager._thread
    assert owner_thread is not None and owner_thread.is_alive()
    monkeypatch.setattr(manager, "_close_all", never_close)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="forced portal shutdown"):
        manager.shutdown(timeout_seconds=1.0)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert not manager.is_running
    assert not owner_thread.is_alive()

def test_real_stdio_call_uses_dedicated_owner_loop_and_reaps_child(monkeypatch):
    # Regression: diagnostics belong to the pytest parent, not to the spawned
    # Python MCP fixture.  -E in _server_config() must isolate the child.
    monkeypatch.setenv("PYTHONASYNCIODEBUG", "1")
    monkeypatch.setenv("PYTHONTRACEMALLOC", "25")
    manager = MCPManager(
        startup_timeout_seconds=10.0,
        shutdown_timeout_seconds=10.0,
    )
    child_pid = None
    worker_thread_id = None

    try:
        tools = manager.load_mcp_servers({"ownership": _server_config()})
        handler = tools["mcp__ownership__echo"]["func"]

        def invoke_from_worker():
            nonlocal worker_thread_id
            worker_thread_id = threading.get_ident()
            return handler(value="hello", cancel_event=threading.Event())

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(invoke_from_worker).result(timeout=10.0)

        pid_text, value = result.split(":", 1)
        child_pid = int(pid_text)

        assert value == "hello"
        assert manager.is_running
        assert manager.owner_thread_id is not None
        assert manager.owner_thread_id != worker_thread_id
        assert psutil.pid_exists(child_pid)

        with pytest.raises(RuntimeError, match="transactional registry reload"):
            manager.load_mcp_servers({"ownership": _server_config()})
    finally:
        manager.shutdown()

    assert not manager.is_running
    assert child_pid is not None
    assert not psutil.pid_exists(child_pid)


def test_real_stdio_cancel_handoff_unblocks_worker_before_shutdown(tmp_path):
    manager = MCPManager(
        startup_timeout_seconds=10.0,
        shutdown_timeout_seconds=10.0,
        cancellation_poll_seconds=0.01,
    )
    cancel_event = threading.Event()
    marker = tmp_path / "mcp-started.txt"

    try:
        tools = manager.load_mcp_servers({"ownership": _server_config()})
        handler = tools["mcp__ownership__wait_for_cancel"]["func"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                handler,
                marker_path=str(marker),
                cancel_event=cancel_event,
            )
            _wait_for(marker.exists)
            cancel_event.set()
            assert future.result(timeout=5.0) == ""
    finally:
        manager.shutdown()

    child_pid = int(marker.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(child_pid)


def test_failed_real_stdio_startup_is_rolled_back_and_shutdown_is_clean():
    manager = MCPManager(
        startup_timeout_seconds=5.0,
        shutdown_timeout_seconds=10.0,
    )
    try:
        tools = manager.load_mcp_servers({
            "broken": {
                "enabled": True,
                "command": sys.executable,
                "args": ["-E", "-c", "raise SystemExit(23)"],
            }
        })
        assert tools == {}
        assert manager.adapters == []
    finally:
        manager.shutdown()

    assert not manager.is_running
