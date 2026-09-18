from __future__ import annotations

import asyncio
import inspect
import json
import threading
from typing import Any, Dict, Tuple

from ..hitl.hitl_manager import HITLManager
from ..hitl.risk_analyzer import RiskAnalyzer


class LocalExecutionDenied(PermissionError):
    pass


class LocalExecutionCancelled(RuntimeError):
    pass


class LocalCapabilityExecutor:
    """Canonical client-side policy, consent and callable execution boundary."""

    def __init__(self, registry, hitl: HITLManager | None = None) -> None:
        self.registry = registry
        self.hitl = hitl or HITLManager()
        self.risk = RiskAnalyzer("")

    def execute(self, capability_id, arguments, envelope, cancellation_event):
        target, metadata = self._resolve(capability_id)
        if target is None:
            raise LookupError(f"Capability '{capability_id}' is not executable on this client.")
        self._validate_input(metadata, arguments)
        if cancellation_event.is_set():
            raise LocalExecutionCancelled()
        risk_level, reason = self.risk.evaluate_action(
            {**metadata, "name": metadata.get("name", capability_id)},
            json.dumps(arguments, ensure_ascii=False, default=str),
        )
        provenance = {
            "execution_id": envelope.get("execution_id"),
            "invocation_id": envelope.get("invocation_id"),
            "trace_id": envelope.get("trace_id"),
            **((envelope.get("payload") or {}).get("context") or {}),
        }
        if not self.hitl.request_approval(
            capability_id,
            json.dumps(arguments, ensure_ascii=False, default=str),
            risk_level,
            f"{reason} | provenance={provenance}",
        ):
            raise LocalExecutionDenied(f"Local user denied capability '{capability_id}'.")
        if cancellation_event.is_set():
            raise LocalExecutionCancelled()
        result = self._call(target, arguments, envelope, cancellation_event)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        if cancellation_event.is_set():
            raise LocalExecutionCancelled()
        self._ensure_json_value(result)
        return result

    def _resolve(self, capability_id: str) -> Tuple[Any, Dict[str, Any]]:
        tool = self.registry.tools.get(capability_id)
        if tool is None:
            tool = self.registry.get_tool(capability_id)
        if isinstance(tool, dict):
            return tool.get("func"), dict(tool.get("metadata") or {})
        return (tool if callable(tool) else None), {}

    @staticmethod
    def _validate_input(metadata: Dict[str, Any], arguments: Dict[str, Any]) -> None:
        schema = metadata.get("parameters") or metadata.get("input_schema")
        if not schema:
            return
        try:
            import jsonschema
        except ImportError:
            required = schema.get("required", []) if isinstance(schema, dict) else []
            missing = [name for name in required if name not in arguments]
            if missing:
                raise ValueError(f"Missing required arguments: {', '.join(missing)}")
            return
        jsonschema.validate(arguments, schema)

    @staticmethod
    def _ensure_json_value(value: Any) -> None:
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError("Capability result is not JSON serializable.") from exc

    @staticmethod
    def _call(target, arguments, envelope, cancellation_event):
        try:
            parameters = inspect.signature(target).parameters
        except (TypeError, ValueError):
            return target(**arguments)
        kwargs = dict(arguments)
        accepts_kwargs = any(
            item.kind == inspect.Parameter.VAR_KEYWORD
            for item in parameters.values()
        )
        injected = {
            "invocation_id": envelope.get("invocation_id"),
            "connection_id": envelope.get("connection_id"),
            "session_id": envelope.get("session_id"),
            "cancel_event": cancellation_event,
        }
        for name, value in injected.items():
            if accepts_kwargs or name in parameters:
                kwargs[name] = value
        return target(**kwargs)


__all__ = ["LocalCapabilityExecutor", "LocalExecutionCancelled", "LocalExecutionDenied"]
