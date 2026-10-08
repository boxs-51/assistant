"""Contract-only regression evidence for APR-FC1; no production mutations."""
from __future__ import annotations

import ast
from pathlib import Path

CONTRACT = Path("docs/agent_platform/APR_FC1_FAST_CONTROL_FRESHNESS_DEADLINE_CONTRACT_5FAEAE02.md")
P0 = Path("docs/agent_platform/APR_P0_AGENT_DEFINITION_PROFILE_BINDING_CONTRACT_7C4C4D42.md")
X1 = Path("docs/agent_platform/APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md")
AE_SCHEMA = Path("se/src/domain/schemas/agent_execution.py")
AE_PERSISTENCE = Path("se/src/runtimes/agent/persistence.py")
DCS = Path("se/src/runtimes/agent/contracts/selection.py")
DCS_SELECTOR = Path("se/src/runtimes/agent/selection.py")
GAC = Path("cl/src/game_automation/actions/action.py")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def norm(path: Path) -> str:
    return " ".join(read(path).replace(chr(96), "").split())


def section(title: str) -> str:
    whole = norm(CONTRACT)
    marker = "## " + title
    assert marker in whole
    return whole.split(marker, 1)[1].split(" ## ", 1)[0]


def has_all(text: str, phrases: tuple[str, ...]) -> None:
    for phrase in phrases:
        assert phrase in text, f"missing frozen contract clause: {phrase}"


def fields(path: Path, cls: str) -> set[str]:
    for node in ast.parse(read(path)).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
            }
    raise AssertionError(f"{cls} missing from {path}")


def test_claim_exact_two_new_no_production_authority() -> None:
    has_all(norm(CONTRACT), (
        "stage = APR-FC1",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "independent PRE-CLAIM = PASS / RELEASED",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
        "production CLAIM = NONE",
        "runtime/API/client/schema/migration authority = NONE",
        "AE state/revision/CAS/HITL/fault/recovery authority = NONE",
        "DCS/SBX/Tools/UBQ/TBO/CTX/AIC/CAS/GAC authority = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "third path = PROHIBITED",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
        "main@5faeae02499d16fa71a04c77d6bfa0a5a4cf4ef2",
        CONTRACT.name, Path(__file__).name,
    ))
    assert "third path requires an independent PRE-CLAIM amendment" in section(
        "1. Frozen claim and authority"
    )


def test_ae_and_apr_identity_revision_authority_stays_canonical() -> None:
    has_all(section("2. Canonical input contracts and independent identities"), (
        "AgentExecution.owner_instance_id != agent_instance_id",
        "DecisionRevision != AgentExecution.revision",
        "DecisionRevision != AgentEventEnvelope.event_id",
        "ActionDeadline != GAC.ActionIntent.deadline_monotonic",
        "not introduced production classes",
    ))
    assert "AgentExecution.owner_instance_id != agent_instance_id" in norm(P0)
    assert "DecisionCommit != independent persistence authority" in norm(X1)
    assert {"execution_id", "revision", "owner_instance_id", "state"} <= fields(
        AE_SCHEMA, "AgentExecution"
    )
    has_all(read(AE_PERSISTENCE), ("expected_revision", "ExecutionConflictError"))


def test_observation_epoch_revision_age_and_clock_fail_closed() -> None:
    has_all(section("3. Observation identity and freshness"), (
        "source identity, source epoch, source revision, stable result identity",
        "revision is comparable only inside its explicitly declared source epoch",
        "Observed-at and received-at MUST remain distinct",
        "explicitly specified clock domain and a bounded max-age/TTL",
        "MUST fail closed or obtain a fresh trusted observation",
        "MUST NOT subtract unrelated monotonic timestamps",
        "MUST NOT dispatch a new side effect",
    ))


def test_deterministic_order_is_not_permission_to_bypass_ae_cas() -> None:
    key = ("source_execution_revision, semantic_priority, source_kind, "
           "source_identity, completion_or_result_identity")
    has_all(section("4. DecisionRevision and deterministic adoption"), (
        key,
        "frozen semantic precedence class",
        "Missing stable tie-break identity MUST fail closed/defer",
        "AE expected-revision/CAS",
        "If CAS loses, the adoption MUST fail closed",
        "does not require network roundtrips or an AE write on every frame",
    ))
    assert key in norm(X1)
    assert "sequencer-local order != AgentExecution.revision" in norm(X1)


def test_deadline_does_not_copy_client_monotonic_clock() -> None:
    has_all(section("5. ActionDeadline and expiry"), (
        "explicit owner and clock domain",
        "all must hold",
        "same owning domain",
        "strictly before the deadline",
        "zero/negative or unknown TTL is rejected",
        "MUST NOT import, overwrite, rename or reinterpret",
        "not copy a raw monotonic timestamp",
        "Expired/unknown-deadline decisions MUST NOT dispatch a new side effect",
    ))
    assert {"action_id", "automation_session_id", "binding_generation",
            "deadline_monotonic"} <= fields(GAC, "ActionIntent")


def test_preemption_order_and_external_side_effects() -> None:
    s = section("6. Preemption, cancellation and race precedence").lower()
    has_all(s, (
        "p0 trusted emergency stop",
        "p1 committed terminal ae state",
        "p2 newer incompatible source epoch/revision",
        "p3 expired actiondeadline",
        "p4 bounded eligible fast decision",
        "missing identity fails closed",
        "preemption rejects new adoption/dispatch",
        "ae-r6 reconciliation",
    ))


def test_preselected_working_set_no_full_dcs_each_tick() -> None:
    assert {"visible_capability_ids", "revision"} <= fields(DCS, "CapabilityWorkingSet")
    assert "CapabilitySelectionViolationError" in read(DCS_SELECTOR)
    has_all(section("7. Bounded preselected session capability set"), (
        "session-scoped preselected bounded capability subset",
        "MUST NOT execute full DCS group/lazy/rank/schema-token selection",
        "Subset never exceeds DCS-selected and still-authorized capabilities",
        "MUST NOT silently fall back to ALL-TOOLS",
        "CLIENT_LOCAL targets MUST NOT silently route to SERVER or SANDBOX",
        "Live UBQ/TBO admission",
    ))


def test_strategy_is_advisory_and_safety_controller_has_veto() -> None:
    has_all(section("8. Split fast policy from deep reasoning"), (
        "deep LLM/Agent reasoning runs off that critical path",
        "source session/epoch/revision",
        "confidence and validity/TTL",
        "Missing or incompatible provenance is rejected/deferred",
        "Stale StrategyHint MUST NOT directly dispatch ActionIntent",
        "local deterministic safety controller retains final veto",
        "deterministic/safety-certified controllers own the hard realtime loop",
    ))


def test_gac_namespace_and_ctx_memory_boundaries() -> None:
    assert "intentionally client-internal" in read(GAC)
    has_all(section("9. Client-local GAC, state and Memory fences"), (
        "cl/src/game_automation/**",
        "GAC world/frame revision != AgentExecution.revision",
        "GAC binding_generation != agent_instance_id",
        "GAC ActionIntent != conceptual APR action/adoption request",
        "FAST_CONTROL transient policy state != CTX durable Memory",
        "StrategyHint or observation != automatic CTX Memory promotion",
        "ResponseEmission != durable execution/memory/authorization authority",
        "No second durable replay log, checkpoint, Memory store",
    ))


def test_recovery_is_ae_owned_and_no_production_release() -> None:
    has_all(section("10. Recovery, failure and exit boundaries"), (
        "reject/defer and refresh",
        "AE canonical durable state and reconciliation are reloaded by AE",
        "not reconstructed from partial response emissions",
        "AE-R14 bilateral owner disposition",
        "SBX/DCS ownership disposition",
        "GAC approval for any client-local change",
    ))
    has_all(section("11. Focused architecture evidence / exit gate"), (
        "Only the claimed architecture test may be added",
        "exact-head Linux and Windows Architecture GREEN",
        "independent contract FINAL PASS",
        "independent FINAL = PENDING",
        "READY / FROZEN = NO",
        "merge authority = NONE",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
    ))
