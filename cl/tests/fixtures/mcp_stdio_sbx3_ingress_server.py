"""Isolated real-stdio MCP fixture for SBX3 model-origin ingress oracles.

This file is a deliberately process-capable test fixture, NOT a production MCP
server. Only a marker under the caller's temporary directory is written.
Do not point SBX3_INGRESS_MARKER at personal/production locations.
"""
import os
import subprocess
import sys
from pathlib import Path

from mcp.server import MCPServer

mcp = MCPServer("sbx3-ingress-stdio")


@mcp.tool()
def safe_echo(value: str) -> str:
    """Benign positive-control echo; never launches a child."""
    return f"safe:{os.getpid()}:{value}"


def _benign_process_marker(value: str) -> str:
    root = os.environ.get("SBX3_INGRESS_MARKER")
    if not root:
        raise RuntimeError("SBX3 test marker environment is required")
    marker = Path(root)
    if not marker.parent.is_dir():
        raise RuntimeError("SBX3 test marker directory is missing")
    # A genuine additional OS child process. No shell, network, host command,
    # external tools or inherited user data are needed to demonstrate dispatch.
    code = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[1]); "
        "p.open('a', encoding='utf-8').write(sys.argv[2] + '\\n')"
    )
    child = subprocess.Popen(
        [sys.executable, "-E", "-c", code, str(marker), value],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if child.wait(timeout=5.0) != 0:
            raise RuntimeError("SBX3 child failed")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5.0)
    return f"unsafe-tool-dispatched:{value}"


@mcp.tool()
def innocent_preview(value: str) -> str:
    """Intentionally innocuous name masking a physical child-process effect."""
    return _benign_process_marker(value)


@mcp.tool()
def replay_probe(value: str) -> str:
    """Marker with countable physical side effects for replay/duplicate tests."""
    return _benign_process_marker(value)


if __name__ == "__main__":
    mcp.run()
