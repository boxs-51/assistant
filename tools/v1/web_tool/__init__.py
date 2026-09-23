from __future__ import annotations

import asyncio
from typing import Any

from tools.v1._shared.contracts import failure_result

from .config import TOOL_METADATA, WEB_TOOL_NAME, WEB_TOOL_VERSION
from .core import WebTool
from .errors import WebToolError


def _canonical_action(action: str) -> str:
    return {
        "read": "scrape",
        "scrape_webpage": "scrape",
        "read_many": "scrape_many",
    }.get(action, action)


async def _async_execute(action: str, **kwargs: Any) -> dict[str, Any]:
    canonical = _canonical_action(action)
    try:
        tool = WebTool()
    except WebToolError as exc:
        return failure_result(
            tool=WEB_TOOL_NAME,
            action=canonical if isinstance(canonical, str) and canonical else "unknown",
            version=WEB_TOOL_VERSION,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        )

    result: dict[str, Any] | None = None
    operation_error: BaseException | None = None
    cleanup_error: WebToolError | None = None

    try:
        result = await tool.execute(action=action, **kwargs)
    except BaseException as exc:
        operation_error = exc

    try:
        await tool.close()
    except WebToolError as exc:
        cleanup_error = exc
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as exc:
        if operation_error is None:
            operation_error = exc

    if operation_error is not None:
        raise operation_error

    if cleanup_error is not None:
        return failure_result(
            tool=WEB_TOOL_NAME,
            action=canonical if isinstance(canonical, str) and canonical else "unknown",
            version=WEB_TOOL_VERSION,
            code=cleanup_error.code,
            message=cleanup_error.message,
            retryable=cleanup_error.retryable,
            details=cleanup_error.details,
        )

    assert result is not None
    return result


def run(action: str, **kwargs: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        return loop.create_task(_async_execute(action, **kwargs))

    return asyncio.run(_async_execute(action, **kwargs))


__all__ = ["WebTool", "run", "TOOL_METADATA"]
