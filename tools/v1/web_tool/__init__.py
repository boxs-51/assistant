import asyncio
from typing import Any
from .core import WebTool
from .config import TOOL_METADATA

async def _async_execute(action: str, **kwargs) -> Any:
    """Execute one WebTool invocation with one explicit async lifecycle owner."""
    async with WebTool() as tool:
        return await tool.execute(action=action, **kwargs)

def run(action: str, **kwargs) -> Any:
    """Sync-compatible entrypoint without timing-based transport cleanup."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        return loop.create_task(_async_execute(action, **kwargs))

    # asyncio.run owns the loop, async-generator shutdown, and default executor.
    # Playwright shutdown itself is awaited by WebTool.__aexit__ on that same loop.
    return asyncio.run(_async_execute(action, **kwargs))

__all__ = ["WebTool", "run", "TOOL_METADATA"]