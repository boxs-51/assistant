from __future__ import annotations

import re
from pathlib import Path

from cl.src.schemas.agent_execution import (
    AgentExecutionLimits as ClientAgentExecutionLimits,
)
from se.src.domain.schemas.agent_execution import (
    AgentExecutionLimits as ServerAgentExecutionLimits,
)


ROOT = Path(__file__).resolve().parents[3]
LIMIT_CLASSES = (ServerAgentExecutionLimits, ClientAgentExecutionLimits)


def test_ubq6d_keeps_max_cost_wire_and_adds_read_only_legacy_label() -> None:
    for limits_cls in LIMIT_CLASSES:
        defaults = limits_cls()
        assert defaults.max_cost is None
        assert defaults.legacy_max_cost is None
        assert defaults.max_tool_calls == 16
        assert "legacy_max_cost" not in limits_cls.model_fields

        explicit = limits_cls.model_validate(
            {
                "max_cost": 12.5,
                "max_tool_calls": 7,
            }
        )
        assert explicit.max_cost == 12.5
        assert explicit.legacy_max_cost == 12.5
        assert explicit.max_tool_calls == 7

        wire = explicit.model_dump(mode="json")
        assert wire["max_cost"] == 12.5
        assert wire["max_tool_calls"] == 7
        assert "legacy_max_cost" not in wire

        schema = limits_cls.model_json_schema()["properties"]
        assert "max_cost" in schema
        assert "legacy_max_cost" not in schema

        ignored_alias = limits_cls.model_validate({"legacy_max_cost": 99.0})
        assert ignored_alias.max_cost is None
        assert ignored_alias.legacy_max_cost is None
        assert "legacy_max_cost" not in ignored_alias.model_dump(mode="json")


def test_ubq6d_server_client_payloads_remain_equivalent() -> None:
    payload = {
        "max_iterations": 3,
        "max_tool_calls": 5,
        "max_parallel_agents": 2,
        "max_parallel_tools": 2,
        "timeout_seconds": 41.0,
        "iteration_timeout_seconds": 13.0,
        "inference_timeout_seconds": 11.0,
        "tool_timeout_seconds": 7.0,
        "task_timeout_seconds": 301.0,
        "max_retry_attempts": 2,
        "max_cost": 4.25,
    }

    server = ServerAgentExecutionLimits.model_validate(payload)
    client = ClientAgentExecutionLimits.model_validate(payload)

    assert server.model_dump(mode="json") == client.model_dump(mode="json")
    assert server.legacy_max_cost == client.legacy_max_cost == 4.25


def test_ubq6d_preserves_ubq5e_timeout_dual_read_and_legacy_wire() -> None:
    canonical = {
        "execution_timeout_seconds": 61.0,
        "provider_call_timeout_seconds": 16.0,
        "tool_call_timeout_seconds": 11.0,
        "iteration_timeout_seconds": 21.0,
        "task_timeout_seconds": 301.0,
    }

    for limits_cls in LIMIT_CLASSES:
        limits = limits_cls.model_validate(canonical)
        assert limits.execution_timeout_seconds == 61.0
        assert limits.provider_call_timeout_seconds == 16.0
        assert limits.tool_call_timeout_seconds == 11.0

        wire = limits.model_dump(mode="json")
        assert wire["timeout_seconds"] == 61.0
        assert wire["inference_timeout_seconds"] == 16.0
        assert wire["tool_timeout_seconds"] == 11.0
        assert wire["iteration_timeout_seconds"] == 21.0
        assert wire["task_timeout_seconds"] == 301.0
        assert "execution_timeout_seconds" not in wire
        assert "provider_call_timeout_seconds" not in wire
        assert "tool_call_timeout_seconds" not in wire


def test_ubq6d_has_no_direct_production_max_cost_consumer() -> None:
    consumer_pattern = re.compile(r"\.max_cost\b")
    excluded = {
        (ROOT / "se/src/domain/schemas/agent_execution.py").resolve(),
        (ROOT / "cl/src/schemas/agent_execution.py").resolve(),
    }
    consumers: list[str] = []

    for production_root in (ROOT / "se/src", ROOT / "cl/src"):
        for source_path in production_root.rglob("*.py"):
            if source_path.resolve() in excluded:
                continue
            if consumer_pattern.search(source_path.read_text(encoding="utf-8")):
                consumers.append(source_path.relative_to(ROOT).as_posix())

    assert consumers == []


def test_ubq6d_accessor_hunks_are_semantic_labels_only() -> None:
    for relative in (
        "se/src/domain/schemas/agent_execution.py",
        "cl/src/schemas/agent_execution.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert source.count("max_cost: Optional[float] = None") == 1

        marker = "def legacy_max_cost(self) -> Optional[float]:"
        marker_start = source.index(marker)
        property_start = source.rfind("    @property", 0, marker_start)
        return_end = source.index("        return self.max_cost", marker_start) + len(
            "        return self.max_cost"
        )
        accessor = source[property_start:return_end]

        assert "return self.max_cost" in accessor
        for forbidden in (
            "UserBudgetPolicy",
            "TaskBudget",
            "ProviderCallBudget",
            "quota",
            "timeout",
            "usd",
            "currency",
        ):
            assert forbidden not in accessor.lower()
