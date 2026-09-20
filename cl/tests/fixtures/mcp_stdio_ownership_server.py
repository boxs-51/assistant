import asyncio
import os
from pathlib import Path

from mcp.server import MCPServer


mcp = MCPServer("client-ownership-test")


@mcp.tool()
def echo(value: str) -> str:
    """Return the server pid together with the provided value."""
    return f"{os.getpid()}:{value}"


@mcp.tool()
async def wait_for_cancel(marker_path: str) -> str:
    """Create a marker, then stay active until the client cancels the request."""
    Path(marker_path).write_text(str(os.getpid()), encoding="utf-8")
    await asyncio.sleep(60)
    return "completed"


if __name__ == "__main__":
    mcp.run()
