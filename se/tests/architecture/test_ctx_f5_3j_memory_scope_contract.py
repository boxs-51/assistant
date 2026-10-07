from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3J_MEMORY_SCOPE_CONTRACT_5B0A6C86.md"
)
PLANNING = Path("docs/context_future/CTX_USER_AGENT_MEMORY_SCOPE_ROADMAP.md")
MEMORY = Path("se/src/context/memory.py")
MEMORY_SQL = Path("se/src/infrastructure/storage/models/sql/memory.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def _section(path: Path, start: str, end: str) -> str:
    text = _normalized(path)
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _annotated_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }


def test_ctx_f5_3j_freezes_exact_zero_production_claim() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "stage = CTX-F5-3J",
        "development baseline = 5b0a6c868c62a50f6a78de1484a032cbcce346f8",
        "baseline Architecture #2216 / 37521737064 = GREEN/GREEN",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = CLOSED",
        "production CLAIM = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "se/src/** delta = ZERO",
        "schema/migration delta = ZERO",
        "repository/model delta = ZERO",
        "runtime/API/router delta = ZERO",
        "registry delta = ZERO",
    ):
        assert phrase in contract

    assert "CTX_F5_3J_MEMORY_SCOPE_CONTRACT_5B0A6C86.md" in contract
    assert "test_ctx_f5_3j_memory_scope_contract.py" in contract


def test_ctx_f5_3j_proves_current_memory_representations_are_owner_only() -> None:
    memory = _read(MEMORY)
    sql = _read(MEMORY_SQL)

    memory_record = _class(memory, "MemoryRecord")
    memory_row = _class(sql, "MemoryRecordRow")

    domain_fields = _annotated_names(memory_record)
    row_fields = _annotated_names(memory_row)

    assert "owner_user_id" in domain_fields
    assert "owner_user_id" in row_fields
    assert "agent_instance_id" not in domain_fields
    assert "agent_instance_id" not in row_fields


def test_ctx_f5_3j_freezes_scope_taxonomy_and_visibility_separation() -> None:
    contract = _normalized(CONTRACT)
    planning = _normalized(PLANNING)

    for phrase in (
        "USER_WIDE",
        "AGENT_PRIVATE",
        "(owner_user_id, agent_instance_id)",
        "Agent visibility remains a separate authorization and retrieval decision",
        "Storage classification is not read visibility",
        "persisted/global Memory listing/search",
        "user-wide cross-session index/search",
        "model Working Set injection",
        "ContextBuilder injection",
        "cross-Agent sharing",
    ):
        assert phrase in contract

    assert "USER_WIDE" in planning
    assert "AGENT_PRIVATE" in planning
    assert "(owner_user_id, agent_instance_id)" in planning


def test_ctx_f5_3j_freezes_trusted_agent_identity_dependency() -> None:
    identity = _section(
        CONTRACT,
        "## 4. Trusted Agent-instance identity dependency",
        "## 5. Legacy owner-only Memory rows",
    )

    assert "MUST NOT be granted or inferred from:" in identity
    for forbidden_source in (
        "model output",
        "request body",
        "source metadata",
        "Memory metadata",
        "Session, Task, or Branch metadata",
        "client identity or connection identity",
        "AgentDefinition.name",
        "AgentExecution.agent_id",
        "AgentExecution.owner_instance_id",
        "promotion caller input by itself",
    ):
        assert forbidden_source in identity

    for required_fence in (
        "server-resolved from separately canonical Agent registration/AIC authority",
        "AIC-0 durable Agent-instance identity is a HARD prerequisite",
        "APR does not transfer Agent identity implementation authority into CTX",
    ):
        assert required_fence in identity


def test_ctx_f5_3j_forbids_legacy_agent_assignment_by_inference() -> None:
    legacy = _section(
        CONTRACT,
        "## 5. Legacy owner-only Memory rows",
        "## 6. Promotion and source authority remain unchanged",
    )

    assert "Existing owner-only Memory rows MUST NOT:" in legacy
    for forbidden_behavior in (
        "be silently classified as AGENT_PRIVATE",
        "receive an inferred/default Agent instance",
        "be assigned to an Agent based on historical Session/Task/Branch metadata",
        "be relabeled by model output, request metadata, or a caller-provided scope",
    ):
        assert forbidden_behavior in legacy

    assert (
        "A separately released migration/classification policy is required before any "
        "schema migration, backfill, scope relabel, Agent-instance assignment, or "
        "destructive erasure behavior based on new scope."
    ) in legacy
    assert (
        "legacy rows remain governed by their current canonical owner/provenance "
        "semantics"
    ) in legacy
    assert "does not rewrite, migrate, backfill, relabel, or delete any row" in legacy


def test_ctx_f5_3j_preserves_promotion_and_p3_boundaries() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Caller-provided scope or Agent identity never creates promotion authority",
        "B1/B3/B4/H-B2/B5/B8 chain",
        "P3 = LANDED / CANONICAL / HEALTHY",
        "P3 source PR = #305@4160696ca7dc9a9172cd7db9bfa8e8afc90e5d87",
        "P3 canonical main = 3c1022fd57a4ab16efebb6a0487fe5e663e0a081",
        "P3 post-merge Architecture #2240 / 37570885508 = GREEN/GREEN",
        "P3 wave = #317 / IW-2026-10-07-06 / COMPLETE / CLOSED",
        "automatic promotion = CLOSED",
        "does not authorize a second P4 caller",
    ):
        assert phrase in contract


def test_ctx_f5_3j_preserves_external_authority_and_later_ctx_fences() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Issue #31/R11 retains transcript/checkpoint read-liveness, retention, and destructive-GC authority",
        "CAS #74 retains asset/object/provider lifecycle, grants, deletion, and GC",
        "Issue #156/DCS retains capability selection/routing/sandbox authority",
        "UBQ/TBO retain resource, quota, scheduling, and timeout policy",
        "AIC/APR retain their Agent identity/profile/runtime authority",
        "F6 Personalization remains CLOSED",
        "AGENT_PRIVATE production = HARD HOLD ON CANONICAL AIC-0 IDENTITY",
        "user-wide cross-session search = SEPARATE FUTURE STAGE / NOT CLAIMED",
        "F9 = CLOSED",
        "F10 = CLOSED",
    ):
        assert phrase in contract


def test_ctx_f5_3j_exit_gate_requires_fresh_exact_head_evidence() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "3J exit gate",
        "exact changed paths remain 2 NEW / 2",
        "production/runtime/schema/migration/repository/model/API/client delta remains zero",
        "MemoryRecordRow",
        "remain owner-only with no agent_instance_id",
        "exact-head Linux Architecture is GREEN",
        "exact-head Windows Architecture is GREEN",
        "independent CTX-F5-3J contract FINAL is PASS",
        "unresolved blocking review threads = 0",
        "blocking P0/P1 = 0",
        "no MATERIAL current-main drift invalidates the freeze",
        "standing conditional zero-production auto-merge exception",
    ):
        assert phrase in contract
