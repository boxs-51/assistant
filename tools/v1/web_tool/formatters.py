from __future__ import annotations

from .errors import WebToolError


def _removed(*_args, **_kwargs):
    raise WebToolError(
        "INVALID_ARGUMENT",
        "T6 returns structured ToolResult and no longer formats terminal results as presentation strings",
    )


format_search_results = _removed
format_scrape_results = _removed
