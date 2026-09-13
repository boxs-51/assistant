import asyncio
import inspect
from typing import Any
from .core import WebTool
from .config import TOOL_METADATA

async def _async_execute(action: str, **kwargs) -> Any:
    """Khởi tạo và dọn dẹp WebTool hoàn toàn trong cùng 1 Event Loop."""
    async with WebTool() as tool:
        result = await tool.execute(action=action, **kwargs)
    # Delay 50ms để Windows ProactorEventLoop kịp dọn dẹp subprocess pipes
    await asyncio.sleep(0.05)
    return result

def run(action: str, **kwargs) -> Any:
    """Hàm wrapper đồng bộ an toàn cho Windows."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        return loop.create_task(_async_execute(action, **kwargs))
    else:
        # Tạo loop thủ công để dọn dẹp triệt để Proactor pipes trên Windows
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(_async_execute(action, **kwargs))
        finally:
            # Xả các async generators và đóng transports chưa giải phóng
            new_loop.run_until_complete(new_loop.shutdown_asyncgens())
            new_loop.run_until_complete(new_loop.shutdown_default_executor())
            new_loop.close()

__all__ = ["WebTool", "run", "TOOL_METADATA"]