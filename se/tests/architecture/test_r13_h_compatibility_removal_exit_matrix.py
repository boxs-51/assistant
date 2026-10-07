from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MATRIX = (
    ROOT
    / "docs"
    / "agent_execution_r13"
    / "R13_H_COMPATIBILITY_REMOVAL_EXIT_MATRIX_B51F32ED.md"
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_r13h_matrix_uses_the_complete_terminal_taxonomy() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    for classification in (
        "REMOVE",
        "MIGRATE_FIRST",
        "KEEP_STABLE_ERROR",
        "KEEP_HISTORICAL_MIGRATION",
        "KEEP_CROSS_TRACK",
        "SUPERSEDED_ALREADY",
    ):
        assert classification in matrix

    assert "No deferred item is counted as complete removal." in matrix
    assert "Production and AE-R14 authority remain NONE." in matrix


def test_r13h_keeps_timeout_compatibility_cross_track() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    se_schema = _text("se/src/domain/schemas/agent_execution.py")
    cl_schema = _text("cl/src/schemas/agent_execution.py")

    assert "`AgentExecutionLimits` timeout dual-read compatibility | KEEP_CROSS_TRACK" in matrix
    assert "Issue #147 / UBQ-5E/T-4" in matrix

    for source in (se_schema, cl_schema):
        for canonical, legacy in (
            ("execution_timeout_seconds", "timeout_seconds"),
            ("provider_call_timeout_seconds", "inference_timeout_seconds"),
            ("tool_call_timeout_seconds", "tool_timeout_seconds"),
        ):
            assert f'"{canonical}"' in source
            assert f'"{legacy}"' in source


def test_r13h_separates_removed_textcontent_from_live_payload_migration() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    se_message = _text("se/src/domain/schemas/message.py")
    cl_message = _text("cl/src/schemas/message.py")

    production_roots = (ROOT / "se" / "src", ROOT / "cl" / "src")
    offenders: list[str] = []
    for production_root in production_roots:
        for path in production_root.rglob("*.py"):
            if re.search(r"\bclass\s+TextContent\b", path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
    assert "Production `TextContent` class/symbol | SUPERSEDED_ALREADY" in matrix
    assert "Legacy text/thinking `data` payload normalization" in matrix
    assert "| MIGRATE_FIRST |" in matrix

    for source in (se_message, cl_message):
        assert "def _normalize_content_part" in source
        assert 'part_type in {"text", "thinking"}' in source
        assert 'legacy = migrated.get("data")' in source


def test_r13h_preserves_stable_waiting_errors_and_migration_history() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    runtime = _text("se/src/runtimes/agent/runtime.py")
    workflow = _text("se/src/runtimes/workflow/runtime.py")
    migration = _text(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "8a_agent_execution_waiting_cas.py"
    )

    assert "KEEP_STABLE_ERROR" in matrix
    assert "KEEP_HISTORICAL_MIGRATION" in matrix
    assert 'error_code="WAITING_FOR_CONNECTION"' in runtime
    assert 'result.error_code == "WAITING_FOR_CONNECTION"' in workflow
    assert "WHERE status = 'WAITING_FOR_CONNECTION'" in migration
    assert "SET state = 'WAITING', wait_reason = 'CONNECTION'" in migration


def test_r13h_pins_bounded_removals_without_reopening_external_authority() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    se_execution = _text("se/src/domain/schemas/agent_execution.py")
    repository = _text("se/src/infrastructure/storage/repositories/agent.py")
    resume = _text("se/src/runtimes/agent/contracts/resume.py")

    assert "_LEGACY_WAITING_STATES" not in se_execution
    assert 'WAITING_AGENT = "WAITING"' not in se_execution
    assert 'WAITING_FOR_CONNECTION = "WAITING"' not in se_execution
    assert "list_legacy_waiting_executions_for_owner" not in repository
    assert "bind_legacy_checkpoint_pointer" not in repository
    assert '"CONNECTION_RECONNECT"' not in resume

    for owner in (
        "AE-R6",
        "AE-R12",
        "CTX #15",
        "CAS #74",
        "#156",
        "APR #278",
        "UBQ/TBO",
    ):
        assert owner in matrix

    assert "No production, runtime, client, schema, migration, configuration, API" in matrix
