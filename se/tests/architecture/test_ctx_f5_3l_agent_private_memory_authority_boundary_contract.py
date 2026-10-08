from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs/context_future"
    / "CTX_F5_3L_AGENT_PRIVATE_MEMORY_AUTHORITY_BOUNDARY_CONTRACT_7332AF46.md"
)
AIC = (
    ROOT
    / "docs/agent_interconnect"
    / "AIC_0_AGENT_INSTANCE_IDENTITY_CONTRACT_B8A1989E.md"
)
CTX_ROADMAP = ROOT / "docs/context_future/CTX_USER_AGENT_MEMORY_SCOPE_ROADMAP.md"
MEMORY = ROOT / "se/src/context/memory.py"
MEMORY_ROW = ROOT / "se/src/infrastructure/storage/models/sql/memory.py"
ADMISSION = ROOT / "se/src/infrastructure/storage/services/memory_promotion_admission.py"
AGENT_EXECUTION = ROOT / "se/src/domain/schemas/agent_execution.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic(path: Path) -> str:
    return " ".join(_read(path).replace("`", "").replace("**", "").split())


def _class(source: str, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _annotated_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def test_ctx_f5_3l_contract_is_exact_two_new_paths_without_production_claim() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "stage = CTX-F5-3L-C0",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
        "production CLAIM = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "third path = PROHIBITED / replacement PRE-CLAIM",
        "schema/migration delta = ZERO",
        "Memory model/repository/admission delta = ZERO",
        "runtime/API/router delta = ZERO",
        "ContextBuilder/Working Set/ContextSnapshot delta = ZERO",
        "registry delta = ZERO",
        "main@7332af469c074fb4331eee841f0ee121c7738f3e",
        "#6061005795",
        "#6061036214",
    ):
        assert phrase in contract

    assert "se/src/** delta = ZERO" in _read(CONTRACT)
    assert "cl/** delta = ZERO" in _read(CONTRACT)
    assert "CTX_F5_3L_AGENT_PRIVATE_MEMORY_AUTHORITY_BOUNDARY_CONTRACT_7332AF46.md" in contract
    assert "test_ctx_f5_3l_agent_private_memory_authority_boundary_contract.py" in contract


def test_ctx_f5_3l_consumes_aic_identity_contract_without_runtime_identity() -> None:
    contract = _semantic(CONTRACT)
    aic = _semantic(AIC)
    execution_fields = _annotated_names(_class(_read(AGENT_EXECUTION), "AgentExecution"))
    for phrase in (
        "(owner_user_id, agent_instance_id)",
        "AgentDefinition.name",
        "AgentExecution.agent_id",
        "AgentExecution.owner_instance_id",
        "AIC-0-P1",
        "ACTIVE",
        "SUSPENDED",
        "DELETED",
        "two users",
        "same user",
        "fail closed",
        "HARD EXTERNAL PREREQUISITE",
    ):
        assert phrase in contract
    assert "(owner_user_id, agent_instance_id)" in aic
    assert "AgentExecution.owner_instance_id" in aic
    assert "agent_id" in execution_fields
    assert "owner_instance_id" in execution_fields
    assert "agent_instance_id" not in execution_fields
    assert "agent_instance_id" in _read(CTX_ROADMAP)


def test_ctx_f5_3l_proves_current_memory_storage_excludes_agent_private() -> None:
    contract = _semantic(CONTRACT)
    memory = _read(MEMORY)
    sql = _read(MEMORY_ROW)
    memory_fields = _annotated_names(_class(memory, "MemoryRecord"))
    row_fields = _annotated_names(_class(sql, "MemoryRecordRow"))
    for fields in (memory_fields, row_fields):
        assert "owner_user_id" in fields
        assert "memory_scope" in fields
        assert "agent_instance_id" not in fields
    assert 'MEMORY_SCOPE_USER_WIDE = "USER_WIDE"' in memory
    assert 'memory_scope must be USER_WIDE when present' in memory
    assert "memory_scope IS NULL OR memory_scope = 'USER_WIDE'" in sql

    for phrase in (
        "Legacy NULL remains unclassified",
        "AGENT_PRIVATE is NOT accepted",
        "No schema default, backfill, relabel",
        "No third file",
    ):
        if phrase == "No third file":
            assert "NOT a third file" in contract
        else:
            assert phrase in contract


def test_ctx_f5_3l_preserves_v1_memory_identity_material() -> None:
    memory = _read(MEMORY)
    tree = ast.parse(memory)
    memory_id_fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "memory_id"
    )
    material = next(
        node for node in ast.walk(memory_id_fn)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "material" for target in node.targets)
    )
    assert isinstance(material.value, ast.Dict)
    keys = [
        k.value for k in material.value.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]
    assert keys == [
        "owner_user_id",
        "promotion_authority_id",
        "source_context_source_id",
        "content_digest",
        "memory_schema_version",
    ]
    assert 'MEMORY_IDENTITY_DOMAIN = "ctx-memory-v1"' in memory
    contract = _semantic(CONTRACT)
    assert "immutable replay material" in contract
    assert "no schema default, backfill" in contract.lower()


def test_ctx_f5_3l_preserves_first_winner_and_consumed_replay() -> None:
    admission = _read(ADMISSION)
    contract = _semantic(CONTRACT)
    cls = _class(admission, "DurableMemoryPromotionAdmission")
    admit = next(
        node for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "admit"
    )
    assert [x.arg for x in admit.args.kwonlyargs] == ["reservation", "content"]
    assert "memory_scope=MEMORY_SCOPE_USER_WIDE" in admission
    assert "memory_scope=existing.memory_scope" in admission
    assert "memory_records_replay_equivalent(" in admission
    assert "DurablePromotionReservationState.REVOKED" in admission
    assert "DurablePromotionReservationState.ISSUED" in admission
    assert "DurablePromotionReservationState.CONSUMED" in admission
    assert "agent_instance_id" not in admission

    for phrase in (
        "ISSUED + unexpected existing winner remains a conflict",
        "REVOKED remains fail closed",
        "memory_records_replay_equivalent",
        "Missing/corrupt/mismatched winners must fail closed",
        "Replay may not relabel NULL/USER_WIDE",
    ):
        assert phrase in contract


def test_ctx_f5_3l_explicit_cross_track_fences_and_future_release_gates() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "source participation/grant-proof",
        "same template/two users",
        "same user/two instances",
        "revoked source/grant",
        "AIC / Agent registration",
        "APR",
        "AE / #107 / #31 R11",
        "CAS / #74",
        "#156 / DCS / CRT / SBX / Tools",
        "UBQ / #141 and TBO",
        "CTX F6–F12",
        "schema/Alembic migration",
        "ContextBuilder",
        "Working Set",
        "ContextSnapshot",
        "separate docs authority",
        "independent contract FINAL PASS",
        "fresh exact-head Linux+Windows Architecture GREEN",
    ):
        assert phrase in contract
