from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3I_A_PROMOTION_SOURCE_ELIGIBILITY_AUTHORITY_HANDOFF.md"
)
SOURCE_IDENTITY = Path("se/src/context/source_identity.py")
SOURCE_ADAPTERS = Path("se/src/context/source_adapters.py")
PROMOTION = Path("se/src/context/memory_promotion.py")


def _normalized_contract() -> str:
    return " ".join(CONTRACT.read_text(encoding="utf-8").split()).replace(chr(96), "")


def _enum_values(source: str, class_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            values: set[str] = set()
            for child in node.body:
                if (
                    isinstance(child, ast.Assign)
                    and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)
                    and isinstance(child.value, ast.Constant)
                    and isinstance(child.value.value, str)
                ):
                    values.add(child.value.value)
            return values
    raise AssertionError(f"class {class_name} not found")


def _source_kind_set(source: str, assignment_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == assignment_name
            and isinstance(node.value, ast.Set)
        ):
            result: set[str] = set()
            for element in node.value.elts:
                assert isinstance(element, ast.Attribute)
                assert isinstance(element.value, ast.Name)
                assert element.value.id == "ContextSourceKind"
                result.add(element.attr)
            return result
    raise AssertionError(f"assignment {assignment_name} not found")


def test_ctx_f5_3i_a_freezes_exact_source_vocabulary_and_versioning() -> None:
    source = SOURCE_IDENTITY.read_text(encoding="utf-8")

    assert _enum_values(source, "ContextSourceKind") == {
        "SESSION",
        "TASK",
        "BRANCH",
        "AGENT_TRANSCRIPT",
        "ASSET",
        "TOOL_RESPONSE_PAYLOAD",
    }
    assert _source_kind_set(source, "_VERSIONED_SOURCE_KINDS") == {
        "TASK",
        "BRANCH",
        "AGENT_TRANSCRIPT",
    }
    assert _source_kind_set(source, "_UNVERSIONED_SOURCE_KINDS") == {
        "SESSION",
        "ASSET",
        "TOOL_RESPONSE_PAYLOAD",
    }

    contract = _normalized_contract()
    for phrase in (
        "There is no ContextSourceKind.MEMORY",
        "versioned source identities: TASK BRANCH AGENT_TRANSCRIPT",
        "unversioned source identities: SESSION ASSET TOOL_RESPONSE_PAYLOAD",
        "concrete supported promotion source kinds = NONE",
    ):
        assert phrase in contract


def test_ctx_f5_3i_a_freezes_existing_projection_surface_without_activation() -> None:
    adapters = SOURCE_ADAPTERS.read_text(encoding="utf-8")
    contract = _normalized_contract()

    for function_name in (
        "project_session_source",
        "project_task_source",
        "project_branch_source",
        "project_agent_transcript_source",
        "project_asset_source",
        "project_tool_response_payload_source",
    ):
        assert f"def {function_name}(" in adapters

    for phrase in (
        "SESSION — HOLD / not eligible for activation in I-A",
        "TASK — candidate only / not released",
        "BRANCH — candidate only / not released",
        "AGENT_TRANSCRIPT — candidate only / not released",
        "ASSET — candidate only / not released",
        "TOOL_RESPONSE_PAYLOAD — candidate only / not released",
        "I-A activates no source kind",
    ):
        assert phrase in contract


def test_ctx_f5_3i_a_keeps_source_reproof_owned_by_source_authority() -> None:
    promotion = PROMOTION.read_text(encoding="utf-8")
    contract = _normalized_contract()

    assert "class SourcePromotionAuthorityPort(Protocol):" in promotion
    assert "async def reprove_for_memory_promotion(" in promotion
    assert "source_ref: ContextSourceRef" in promotion
    assert "owner_user_id: str" in promotion
    assert "-> SourcePromotionProof" in promotion

    for phrase in (
        "The source owner, not CTX, defines what authority_state_token proves",
        "A timestamp alone is not deterministic freshness authority",
        "Source-owner handoff required before any I-B production PRE-CLAIM",
        "I-A does not implement that port",
    ):
        assert phrase in contract


def test_ctx_f5_3i_a_freezes_cross_track_ownership_and_source_specific_holds() -> None:
    contract = _normalized_contract()

    for phrase in (
        "ASSET CTX identity is intentionally stable across FileAsset revision",
        "FileAsset revision is projected metadata and is not part of CTX ASSET identity",
        "CAS Issue #74 retains FileAsset/FileBlob/ObjectStorage/provider/lifecycle/GC authority",
        "R11's landed checkpoint/transcript persistence and retention/GC boundaries remain canonical evidence",
        "R12 is the active successor for recovery/continuation semantics",
        "UBQ owns renewable logical tool-call quota semantics",
        "I-A creates no capability, target, route, fingerprint or sandbox behavior",
    ):
        assert phrase in contract


def test_ctx_f5_3i_a_freezes_i_b_entry_gate_and_closed_authority() -> None:
    contract = _normalized_contract()

    for phrase in (
        "one exact source kind is selected",
        "that source owner has an explicit authority handoff contract",
        "independent PRE-CLAIM release is recorded",
        "No I-B production branch may be created by implication from I-A",
        "source-specific loader or re-proof adapter",
        "runtime/container promotion wiring",
        "HTTP/public/model-callable promotion",
        "ContextSourceKind.MEMORY",
        "ContextBuilder or model Working Set Memory visibility",
        "non-SQLite admission authority",
        "No se/src/** file changes",
        "zero production/runtime/schema/migration delta",
    ):
        assert phrase in contract
