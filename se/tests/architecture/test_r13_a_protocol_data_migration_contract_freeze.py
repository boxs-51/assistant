from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_r13a_roadmap_defines_compatibility_cleanup_and_evidence_gate() -> None:
    roadmap = _text("docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md")

    assert "# Phase R13 — Protocol and Data Migration Cleanup" in roadmap
    assert "Remove temporary compatibility only after rollout confidence." in roadmap
    assert "old `WAITING_FOR_CONNECTION` wire form" in roadmap
    assert "old continuation JSON" in roadmap
    assert "remaining SE/CL schema drift" in roadmap
    assert "Compatibility removal is backed by migration tests/telemetry." in roadmap


def test_r13a_freezes_legacy_waiting_role_split_without_removing_it() -> None:
    schema = _text("se/src/domain/schemas/agent_execution.py")
    gateway = _text("cl/src/core/gateway_client.py")
    runtime = _text("se/src/runtimes/agent/runtime.py")
    workflow = _text("se/src/runtimes/workflow/runtime.py")

    # R13-D2 retires dead Python enum source aliases while preserving the
    # raw legacy-input compatibility boundary frozen by R13-A.
    assert 'WAITING_AGENT = "WAITING"' not in schema
    assert 'WAITING_FOR_CONNECTION = "WAITING"' not in schema
    assert "_LEGACY_WAITING_STATES" in schema
    assert '"WAITING_FOR_CONNECTION": AgentExecutionWaitReason.CONNECTION' in schema

    assert "_LEGACY_WAIT_REASONS" in gateway
    assert '"WAITING_FOR_CONNECTION": "CONNECTION"' in gateway
    assert '"WAITING_AGENT": "AGENT"' in gateway

    # R13 must distinguish obsolete state/wire compatibility from stable
    # runtime/result error vocabulary.
    assert 'error_code="WAITING_FOR_CONNECTION"' in runtime
    assert 'result.error_code == "WAITING_FOR_CONNECTION"' in workflow


def test_r13a_freezes_legacy_continuation_as_read_only_migration_input() -> None:
    materialization = _text("se/src/runtimes/agent/legacy_materialization.py")
    persistence = _text("se/src/runtimes/agent/persistence.py")

    assert 'context_state.get("continuation")' in materialization
    assert '{"WAITING", "WAITING_FOR_CONNECTION"}' in materialization

    assert "LEGACY_CONTINUATION_READ_ONLY" in persistence
    assert "new AgentExecution rows cannot write legacy continuation JSON" in persistence
    assert "context_state['continuation'] cannot be written after R7-I" in persistence
    assert "Historical Phase 6.9 continuation JSON remains inert data." in persistence


def test_r13a_preserves_historical_migration_and_cross_track_timeout_compatibility() -> None:
    migration = _text(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "8a_agent_execution_waiting_cas.py"
    )
    se_schema = _text("se/src/domain/schemas/agent_execution.py")
    cl_schema = _text("cl/src/schemas/agent_execution.py")

    assert "WHERE status = 'WAITING_FOR_CONNECTION'" in migration
    assert "SET state = 'WAITING', wait_reason = 'CONNECTION'" in migration
    assert "WHERE state = 'WAITING_FOR_CONNECTION'" in migration

    for source in (se_schema, cl_schema):
        assert '"execution_timeout_seconds"' in source
        assert '"timeout_seconds"' in source
        assert '"provider_call_timeout_seconds"' in source
        assert '"inference_timeout_seconds"' in source
        assert '"tool_call_timeout_seconds"' in source
        assert '"tool_timeout_seconds"' in source


def test_r13a_textcontent_is_not_a_production_class() -> None:
    production_roots = (ROOT / "se" / "src", ROOT / "cl" / "src")
    offenders: list[str] = []

    for production_root in production_roots:
        for path in production_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if re.search(r"\bclass\s+TextContent\b", source):
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
