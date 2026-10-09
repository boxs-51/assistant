"""AE-R14-E-G0: fail-closed evidence-only architecture checks (NOT P0 exit)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs/agent_execution_r14/R14_E_FINAL_PRODUCTION_EXIT_EVIDENCE_9D9A6F20.md"
D_BINDER = ROOT / "se/tests/architecture/test_r14_d_composed_critical_p0_fault_matrix.py"
MAIN = "604a1abae68ea17a68b78fcad59be9865d0fe2c4"
LINUX_RUN = "37855824005"
SUITES = {
    "contract", "state-machine", "identity-lineage", "persistence",
    "agent-runtime", "capability-runtime", "continuation", "reconciliation",
    "branching", "TaskBudget", "provider-retry", "real-WebSocket",
    "multi-worker-race", "server-restart", "client-restart", "HITL",
    "fault-injection",
}
FAULT_ORDER = (
    "before remote dispatch", "after dispatch", "before side effect",
    "after side effect", "before result send", "after result send",
    "before SE commit", "after SE commit", "during resume CAS",
    "during ADOPT CAS", "during provider retry",
    "during checkpoint persistence", "during TaskBudget reservation",
)
NEGATIVE_GATES = {
    "EVIDENCE_CLASS": "GAP_LEDGER_ONLY",
    "LEDGER_STATUS": "GAP_LEDGER_FINAL_EXIT_HOLD_NOT_CERTIFIED",
    "REVIEWED_MAIN": MAIN,
    "CANONICAL_POLICY": "85_v2.5.2",
    "OWNER_CLAIM_REF": "359_6075119391",
    "INDEPENDENT_PRECLAIM_REF": "359_6075047756",
    "MAIN_ARCH_RUN": LINUX_RUN,
    "MAIN_ARCH_ATTEMPT": "2",
    "MAIN_ARCH_LINUX_JOB": "113583172394",
    "MAIN_ARCH_WINDOWS_CLIENT_JOB": "113583171064",
    "MAIN_ARCH_WINDOWS_SE_TOOLS_JOB": "113583173135",
    "WINDOWS_R6_D_ATTEMPT_1": "RED_UNWAIVED",
    "R9_H_409": "BLOCKED_P1_HOLD",
    "PR411": "DRAFT_INTENTIONALLY_RED_NEVER_MERGE_ALONE",
    "LEGACY_R7_L0_SQLITE_PLANNER": "NOT_RUN",
    "POSTGRESQL_TASK_CLAIM_PARITY": "NOT_RUN",
    "FULL_SERVER_SQL_RESTART_PLUS_AGENT_HITL_K1_K2": "GAP_NOT_RUN_AS_COMPOSED",
    "R14_E_FINAL_EXIT": "HOLD",
    "NO_P0_EXIT_CERTIFICATE": "True",
    "PRODUCTION_AUTHORITY": "NONE",
    "MERGE_AUTHORITY": "NONE",
    "R13_MIGRATE_FIRST": "PRESERVE",
    "R13_KEEP_CROSS_TRACK": "PRESERVE",
    "R13_KEEP_STABLE_ERROR": "PRESERVE",
    "R13_KEEP_HISTORICAL_MIGRATION": "PRESERVE",
    "R13_SUPERSEDED_ALREADY": "PRESERVE",
}
FIELDS = ("owner", "source_path", "test_selector", "evidence_type",
          "source_main_sha", "matching_job_or_NOT_RUN", "disposition", "limits")


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _metadata() -> dict[str, str]:
    block = _text().split("## Machine-readable negative gates\n", 1)[1]
    ini = block.split("```ini\n", 1)[1].split("\n```", 1)[0]
    pairs = [line.split("=", 1) for line in ini.splitlines() if line.strip()]
    assert all(len(pair) == 2 for pair in pairs)
    assert len(pairs) == len({key for key, _ in pairs}), "duplicate key can hide exit HOLD"
    return dict(pairs)


def _table(heading: str, key: str) -> list[dict[str, str]]:
    block = _text().split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in block.splitlines() if line.startswith("|")]
    assert len(lines) >= 3, "no data table for " + heading
    cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in lines]
    assert cells[0] == [key, *FIELDS]
    assert cells[1] == ["---"] * len(cells[0])
    assert all(len(row) == len(cells[0]) for row in cells[2:])
    rows = [dict(zip(cells[0], row, strict=True)) for row in cells[2:]]
    assert len(rows) == len({row[key] for row in rows})
    return rows


def _canonical() -> tuple[set[str], list[tuple[str, str, str, str, str]]]:
    """Read canonical R14-D declarations as syntax; never execute any E2E."""
    module = ast.parse(D_BINDER.read_text(encoding="utf-8"))
    constants: dict[str, str] = {}
    labels: set[str] = set()
    fault_rows: list[tuple[str, str, str, str, str]] = []
    for item in module.body:
        if not isinstance(item, ast.Assign) or len(item.targets) != 1:
            continue
        name = item.targets[0]
        if not isinstance(name, ast.Name):
            continue
        if isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
            constants[name.id] = item.value.value
        if name.id == "SUITE_DISPOSITION":
            labels = set(ast.literal_eval(item.value))
        if name.id == "FAULT_BOUNDARIES":
            assert isinstance(item.value, ast.Tuple)
            for call in item.value.elts:
                assert isinstance(call, ast.Call)
                assert isinstance(call.func, ast.Name) and call.func.id == "FaultBoundary"
                assert len(call.args) == 5
                path_node = call.args[2]
                path = (constants[path_node.id] if isinstance(path_node, ast.Name)
                        else ast.literal_eval(path_node))
                fault_rows.append((ast.literal_eval(call.args[0]),
                                   ast.literal_eval(call.args[1]),
                                   path,
                                   ast.literal_eval(call.args[3]),
                                   ast.literal_eval(call.args[4])))
    assert labels and fault_rows, "canonical R14-D source is absent"
    return labels, fault_rows


def _valid_locator(row: dict[str, str]) -> None:
    file_path = row["source_path"]
    selector = row["test_selector"]
    if file_path == "NOT_RUN":
        assert selector == "NOT_RUN"
        assert row["matching_job_or_NOT_RUN"] == "NOT_RUN"
        assert row["disposition"] in {"GAP", "NOT_RUN"}
        return
    source = Path(file_path)
    assert file_path.startswith("se/tests/") and ".." not in source.parts
    assert selector.startswith("test_") and selector.isidentifier()
    module = ast.parse((ROOT / source).read_text(encoding="utf-8"), filename=file_path)
    names = {node.name for node in module.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert selector in names, "missing test selector: " + file_path + "::" + selector


def test_r14_e_g0_negative_metadata_is_exact_and_unwaived() -> None:
    document = _text()
    assert "GAP LEDGER / FINAL EXIT HOLD / NOT CERTIFIED" in document.splitlines()[0]
    assert "> **NOT A PRODUCTION-EXIT CERTIFICATE.**" in document
    assert _metadata() == NEGATIVE_GATES
    for required in (
        "server-owned durable SQL process-crash/restart",
        "same SHA had a Windows-client R6-D 2-second result-wait RED",
        "R13 compatibility dispositions",
    ):
        assert required in document


def test_r14_e_g0_suite_rows_are_seventeen_inherited_or_blocked_not_certified() -> None:
    rows = _table("17 suite classes", "suite")
    canonical_names, _ = _canonical()
    assert len(rows) == 17
    assert canonical_names == SUITES == {row["suite"] for row in rows}
    for row in rows:
        assert row["source_main_sha"] == MAIN
        assert row["owner"] and row["limits"] and row["evidence_type"]
        assert row["disposition"] in {"INHERITED", "BLOCKED", "GAP", "NOT_RUN"}
        assert LINUX_RUN in row["matching_job_or_NOT_RUN"] or row["matching_job_or_NOT_RUN"] == "NOT_RUN"
        _valid_locator(row)
    by_name = {row["suite"]: row for row in rows}
    assert by_name["multi-worker-race"]["disposition"] == "BLOCKED"
    assert by_name["fault-injection"]["disposition"] == "BLOCKED"
    assert by_name["server-restart"]["disposition"] == "GAP"
    assert by_name["server-restart"]["test_selector"] == "NOT_RUN"
    assert by_name["reconciliation"]["disposition"] == "INHERITED"
    assert by_name["HITL"]["disposition"] == "INHERITED"
    assert not any(row["disposition"] == "PASS" for row in rows)


def test_r14_e_g0_fault_rows_exactly_match_d_and_preserve_adopt_block() -> None:
    rows = _table("13 fault boundaries", "boundary")
    _, canonical = _canonical()
    assert len(rows) == len(canonical) == 13
    assert tuple(row["boundary"] for row in rows) == FAULT_ORDER
    for row, (name, level, source, selector, d_status) in zip(rows, canonical, strict=True):
        assert (row["boundary"], row["evidence_type"], row["source_path"],
                row["test_selector"]) == (name, level, source, selector)
        assert row["source_main_sha"] == MAIN
        assert row["owner"] and row["limits"]
        assert LINUX_RUN in row["matching_job_or_NOT_RUN"]
        assert row["disposition"] == ("BLOCKED" if d_status == "BLOCKED_P1_409" else "INHERITED")
        _valid_locator(row)
    blocked = [row for row in rows if row["disposition"] == "BLOCKED"]
    assert len(blocked) == 1 and blocked[0]["boundary"] == "during ADOPT CAS"
    assert not any(row["disposition"] == "PASS" for row in rows)
