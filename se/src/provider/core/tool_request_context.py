from __future__ import annotations

from typing import Any, Mapping

from .tool_contract import ProviderToolNameMap, build_provider_tool_name_map


def _name_from_tool_call(call: Any) -> str | None:
    if not isinstance(call, Mapping):
        return None
    function = call.get("function")
    if isinstance(function, Mapping):
        name = function.get("name")
        return name if isinstance(name, str) and name else None

    name = call.get("name")
    return name if isinstance(name, str) and name else None


def collect_request_tool_names(request: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect unique logical tool names referenced by one provider request.

    Names are gathered from declarations and continuation history so a single
    request-scoped bijection covers the entire declaration -> call -> result
    round trip.  The returned order is deterministic and preserves first use.
    """

    ordered: list[str] = []
    seen: set[str] = set()

    def add(name: Any) -> None:
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            ordered.append(name)

    for tool in request.get("tools") or []:
        if isinstance(tool, Mapping):
            function = tool.get("function")
            if isinstance(function, Mapping):
                add(function.get("name"))
            else:
                add(tool.get("name"))
        else:
            add(getattr(tool, "name", None))

    for message in request.get("messages") or []:
        if not isinstance(message, Mapping):
            continue

        role = message.get("role")
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    add(_name_from_tool_call(call))
            elif isinstance(tool_calls, Mapping):
                add(_name_from_tool_call(tool_calls))

            function_call = message.get("function_call")
            if isinstance(function_call, Mapping):
                add(function_call.get("name"))

        if role in {"tool", "tool_result", "function"}:
            add(message.get("name"))
            add(message.get("tool_name"))

        for key in ("tool_result", "function_response"):
            value = message.get(key)
            if isinstance(value, Mapping):
                add(value.get("name"))
                add(value.get("tool_name"))

        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                raw_type = part.get("type")
                part_type = getattr(raw_type, "value", raw_type)
                if part_type in {"tool_result", "function_response"}:
                    add(part.get("name"))
                    add(part.get("tool_name"))

    return tuple(ordered)


def build_request_tool_name_map(
    provider: str,
    request: Mapping[str, Any],
) -> ProviderToolNameMap:
    """Build one immutable provider-name bijection for one outbound request."""

    return build_provider_tool_name_map(provider, collect_request_tool_names(request))
