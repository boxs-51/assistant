"""APR-CU1 conceptual computer-interactive environment architecture evidence.

These read-only tests freeze contract and current source fences; they do not
exercise a real Agent computer-use runtime, OS UI or an approval workflow.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


DOC = Path("docs/agent_platform/APR_CU1_COMPUTER_INTERACTIVE_ENVIRONMENT_SESSION_CONTRACT_48715EDD.md")
ROADMAP = Path("docs/agent_platform/AGENT_SPECIALIZATION_ROADMAP.md")
P0 = Path("docs/agent_platform/APR_P0_AGENT_DEFINITION_PROFILE_BINDING_CONTRACT_7C4C4D42.md")
X1 = Path("docs/agent_platform/APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md")
DESKTOP = Path("tools/v1/desktop_tool.py")
WINDOW = Path("tools/v1/window_tool.py")
CLIENT_WS = Path("cl/src/core/realtime_client.py")
AE_SCHEMA = Path("se/src/domain/schemas/agent_execution.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(value: str) -> str:
    return " ".join(
        value.replace(chr(96), "")
        .replace("/**", "/__GLOBSTAR__")
        .replace("**", "")
        .replace("/__GLOBSTAR__", "/**")
        .split()
    )


def _section(name: str) -> str:
    source = _read(DOC)
    marker = "## " + name + "\n"
    assert marker in source
    return _flat(source.split(marker, 1)[1].split("\n## ", 1)[0])


def _has(source: str, *clauses: str) -> None:
    for clause in clauses:
        assert clause in source, f"missing APR-CU1 contract clause: {clause}"


def _records() -> dict[str, set[str]]:
    found = re.findall(
        r"(?m)^    (Environment(?:Session|Observation|Action|Result))\(([\s\S]*?)\)",
        _read(DOC),
    )
    return {
        name: {field.strip() for field in values.split(",")}
        for name, values in found
    }


def test_claim_is_two_add_only_and_no_production_authority() -> None:
    s = _section("1. Authority and exact claim")
    _has(
        s,
        "APR-CU1",
        "Policy #85 v2.5",
        "PASS/RELEASED",
        "CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "2 NEW / 2",
        "third path is PROHIBITED",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
        "tools/v1/** delta = ZERO",
        "Production PRE-CLAIM = HOLD / NOT RELEASED",
        "production CLAIM = NONE",
        "independent FINAL = PENDING",
        "merge authority = NONE",
        DOC.name,
        Path(__file__).name,
    )


def test_current_desktop_and_window_tools_do_not_infer_agent_runtime() -> None:
    d = _read(DESKTOP)
    w = _read(WINDOW)
    assert "MAX_SCREENSHOT_PNG_BYTES" in d
    assert "desktop.screenshot" in d
    assert "NON_IDEMPOTENT" in d
    assert "desktop.mouse_click" in d
    assert "window.find" in w
    assert "window.focus" in w
    assert "TOOL_METADATA" in d and "TOOL_METADATA" in w
    assert "## 9. APR-CU1 — Computer Use Runtime" in _read(ROADMAP)
    assert "No ambient host authority." in _read(ROADMAP)
    _has(
        _section("2. Canonical source inventory and non-equivalence"),
        "MAX_SCREENSHOT_PNG_BYTES",
        "NOT a durable environment identity",
        "NOT a deployed Agent computer-interactive session",
        "not durable AE state",
    )


def test_identity_and_placement_reject_generation_conflation() -> None:
    s = _section("3. Independent identities, placement and generation")
    _has(
        s,
        "owner_user_id",
        "agent_instance_id",
        "execution_id",
        "AgentExecution.revision",
        "owner_instance_id",
        "environment_session_id",
        "connection_id",
        "transport_generation",
        "CLIENT_LOCAL",
        "EPHEMERAL_SANDBOX",
        "target_epoch",
        "A reconnect MUST NOT mint a new Agent identity",
    )
    assert "AgentExecutionBinding" in _read(P0)
    tree = ast.parse(_read(AE_SCHEMA))
    agent = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentExecution"
    )
    fields = {
        node.target.id for node in agent.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert {"execution_id", "revision", "owner_instance_id"} <= fields


def test_conceptual_observation_action_and_result_bind_current_target() -> None:
    fields = _records()
    assert set(fields) == {
        "EnvironmentSession", "EnvironmentObservation",
        "EnvironmentAction", "EnvironmentResult",
    }
    common = {
        "owner_user_id", "agent_instance_id", "execution_id",
        "environment_session_id", "placement", "connection_id",
        "transport_generation", "target_id", "target_epoch",
    }
    for name, actual in fields.items():
        assert common <= actual, f"{name} lacks {sorted(common - actual)}"
    assert {"session_revision", "grant_id"} <= fields["EnvironmentSession"]
    assert {
        "observation_id", "observation_revision",
        "inline_payload_or_immutable_asset_ref", "content_hash",
        "byte_length", "expires_at",
    } <= fields["EnvironmentObservation"]
    assert {
        "action_id", "expected_observation_id",
        "expected_observation_revision", "approval_id",
        "arguments_ref", "idempotency_class",
    } <= fields["EnvironmentAction"]
    assert {"action_id", "result_status", "uncertain_external_effect"} <= fields["EnvironmentResult"]
    invocation_semantics = {
        "action_id", "invocation_id", "client_id", "principal_id",
        "tool_id", "capability_version", "request_fingerprint",
    }
    assert invocation_semantics <= fields["EnvironmentAction"]
    assert invocation_semantics <= fields["EnvironmentResult"]
    grant_snapshot = {"grant_id", "session_revision"}
    assert grant_snapshot <= fields["EnvironmentSession"]
    assert grant_snapshot <= fields["EnvironmentAction"]
    assert grant_snapshot <= fields["EnvironmentResult"]


def test_bounded_observation_and_argument_payloads_are_authorized() -> None:
    _has(
        _section("4. Conceptual environment-session records"),
        "immutable session-to-owner-to-target binding",
        "current generation",
        "exactly one bounded inline payload",
        "scoped immutable CAS/F7-T asset reference",
        "capture time",
        "untrusted observation DATA",
        "MUST fail closed",
        "arguments_ref",
        "not instructions or authorization",
    )


def test_action_adoption_requires_fresh_observation_target_and_hitl() -> None:
    _has(
        _section("5. Freshness, target selection and state-sensitive dispatch"),
        "expected_observation_id",
        "expected_observation_revision",
        "target_epoch",
        "transport_generation",
        "HITL approval_id",
        "unique action_id",
        "reused window handle",
        "sandbox-to-host fallback",
        "Never guess first window",
        "does not authorize a side effect by itself",
    )


def test_untrusted_ui_and_emergency_stop_never_mint_authority() -> None:
    _has(
        _section("6. Approval, untrusted UI and safety"),
        "HIGH-risk Tool HITL",
        "clipboard data are untrusted",
        "UI prompt injection as data",
        "never policy authority",
        "bypass consent",
        "P0 security revoke/emergency stop",
        "P1 AE terminal/HITL denial",
        "P2 owner interruption",
        "P3 deadline/session expiry",
        "P4 normal observation/action progress",
        "AE-R6 outcome reconciliation",
    )


def test_unknown_external_effect_does_not_replay_tool_dispatch() -> None:
    _has(
        _section("7. Bounded observation and state reconciliation"),
        "NON_IDEMPOTENT Tool",
        "Do not automatically replay mouse_click",
        "Re-observe the same authorized target",
        "Exactly-once Tool execution cannot be inferred",
        "Only AE expected-revision/CAS",
        "APR-X1 DecisionCommit",
        "sequencer-local order is NOT AgentExecution.revision",
        "cannot overwrite AE terminal state",
    )
    assert "DecisionCommit != independent persistence authority" in _read(X1)


def test_reconnect_and_usage_do_not_change_host_placement() -> None:
    _has(
        _section("8. Lifecycle, quota, placement and delegation"),
        "fresh authentication",
        "Old generations and old observation/action approvals fail closed",
        "UBQ",
        "TBO",
        "MUST NOT refill budgets",
        "CAS F7-T",
        "CLIENT_LOCAL",
        "EPHEMERAL_SANDBOX",
        "GAC #221",
        "CL-UI #242",
        "CTX #15",
        "AE #359",
    )
    assert "connection.register" in _read(CLIENT_WS)


def test_negative_vectors_fail_closed_before_tool_effects() -> None:
    _has(
        _section("9. Negative acceptance vectors and refusal semantics"),
        "different owner or agent_instance_id",
        "different target_epoch",
        "stale transport_generation",
        "foreground focus lost",
        "ambiguous selector",
        "CLIENT_LOCAL",
        "EPHEMERAL_SANDBOX",
        "prompt injection",
        "unsafe repeated click/type",
        "late observation/action result",
        "typed, observable refusal",
    )


def test_architecture_does_not_claim_real_os_or_ui_e2e_pass() -> None:
    _has(
        _section("10. Evidence boundaries and exit gates"),
        "MAY NOT claim real computer-use E2E",
        "OS isolation",
        "independent PRE-CLAIM",
        "bilateral CRT/SBX/Tools/AIC/CTX/CAS/UBQ/TBO/CL-UI/GAC approvals",
        "SQLite/PostgreSQL AE evidence",
        "exact-head Linux+Windows Architecture GREEN",
        "independent auditor PASS",
        "explicit user authorization",
        "no merge authority",
    )


def test_queued_action_revalidates_at_physical_effect_boundary() -> None:
    s = _section("5. Freshness, target selection and state-sensitive dispatch")
    _has(
        s,
        "physical CLIENT_LOCAL/Tool execution boundary",
        "immediately before the first external side effect",
        "MUST atomically revalidate",
        "window/browser/DOM/geometry state fingerprint",
        "foreground/focus target",
        "expected_observation_id + expected_observation_revision",
        "per-invocation HITL approval_id",
        "serialized with initiation of the physical side effect",
        "TOCTOU bug",
        "after dispatch but before execution",
        "FAIL CLOSED / REQUIRE NEW OBSERVATION",
        "neither implements OS locking nor grants new runtime",
    )
    negative = _section("9. Negative acceptance vectors and refusal semantics")
    _has(
        negative,
        "after dispatch while queued but before physical effect-boundary validation",
        "target_epoch or DOM/window/geometry fingerprint changes",
        "atomic execution-boundary fence or fail closed",
    )


def test_action_identity_is_canonical_ae_r6_invocation_not_parallel_ledger() -> None:
    s = _section("4. Conceptual environment-session records")
    _has(
        s,
        "one-to-one and immutably",
        "AE-R6 canonical invocation_id",
        "(client_id, principal_id, invocation_id)",
        "capability_version",
        "request_fingerprint",
        "different capability_version or request_fingerprint",
        "same action_id with different invocation_id",
        "same invocation_id with different capability_version/request_fingerprint",
        "MUST fail closed before physical dispatch",
        "NEVER mint a second invocation",
        "No new APR action ledger",
    )
    source = _read(Path("cl/src/core/client_invocation_ledger.py"))
    assert "PRIMARY KEY (client_id, principal_id, invocation_id)" in source
    assert "request_fingerprint" in source
    assert "capability_version" in source
    assert "invocation_id is already bound to different request semantics." in source
    _has(
        _section("7. Bounded observation and state reconciliation"),
        "resolve action_id to its immutable AE-R6 invocation_id",
        "Conflicting semantic replay is forbidden",
    )
    _has(
        _section("9. Negative acceptance vectors and refusal semantics"),
        "action_id mapped to a different invocation_id",
        "changed capability_version/request_fingerprint",
        "without a second NON_IDEMPOTENT effect",
    )


def test_late_result_reconciliation_never_reuses_expired_action_authority() -> None:
    records = _section("4. Conceptual environment-session records")
    _has(
        records,
        "NEW action dispatch, physical Tool effects and authority-bearing adoption",
        "current generation, target epoch, session/grant lifetime and original owner",
        "Previously dispatched, immutable EnvironmentResult evidence",
        "NON-AUTHORIZING RECONCILIATION PATH",
        "original",
        "action_id ↔ invocation_id",
        "current principal access to that invocation's evidence",
        "DO NOT demand that the historical generation, target epoch or grant is still current",
        "recording verified outcome evidence is NOT a new Tool permission",
        "AE expected-revision/CAS",
        "without reopening execution",
    )
    assert (
        "All observation/action/result projections MUST validate current generation"
        not in records
    )
    reconciliation = _section("7. Bounded observation and state reconciliation")
    _has(
        reconciliation,
        "late authenticated canonical invocation outcome MUST NOT be discarded solely because",
        "transport_generation, target_epoch, environment session or original grant",
        "original action itself changed/closed its target",
        "immutable, non-authorizing reconciliation observation",
        "If the target is no longer available",
        "Current AE terminal state cannot be rewritten or reopened",
        "outcome evidence acceptance",
        "authority-bearing state adoption",
    )
    lifecycle = _section("8. Lifecycle, quota, placement and delegation")
    _has(
        lifecycle,
        "fail closed for any NEW execution",
        "Historical authenticated Tool outcomes still enter the non-authorizing reconciliation path",
        "never reacquire Tool permission from the old result",
    )
    negatives = _section("9. Negative acceptance vectors and refusal semantics")
    _has(
        negatives,
        "late authenticated NON_IDEMPOTENT result arriving after reconnect",
        "action-closed target",
        "non-authorizing reconciliation evidence",
        "without another click/type",
        "AE terminal state regression",
        "forged or mismatched late result",
        "wrong original owner, invocation_id",
        "reject without granting execution",
    )
    ledger = _read(Path("cl/src/core/client_invocation_ledger.py"))
    assert "PRIMARY KEY (client_id, principal_id, invocation_id)" in ledger
    assert "request_fingerprint" in ledger
    assert "capability_version" in ledger


def test_action_snapshot_requires_exact_admitting_grant_and_late_result_retains_it() -> None:
    records = _section("4. Conceptual environment-session records")
    _has(
        records,
        "EnvironmentAction MUST snapshot immutable grant_id and admitted session_revision",
        "exact trusted EnvironmentSession grant",
        "Every EnvironmentResult MUST carry that same original grant_id",
        "without silently rebinding to a renewed grant",
        "replacement grant for the same target/session",
        "DISTINCT grant_id",
        "original grant_id and admitted session_revision",
        "distinct replacement grant_id",
        "cannot retroactively validate its old physical execution",
        "NON-AUTHORIZING",
    )
    execution = _section("5. Freshness, target selection and state-sensitive dispatch")
    _has(
        execution,
        "SAME immutable admitting grant_id and admitted session_revision",
        "MUST check exact grant identity/expected admitted session revision",
        "not merely existence of ANY active grant",
        "admitting grant expires/is revoked",
        "DIFFERENT grant_id becomes active",
        "reject the queued action before any physical side effect",
        "never elevate stale admission",
    )
    negatives = _section("9. Negative acceptance vectors and refusal semantics")
    _has(
        negatives,
        "grant_id G1",
        "new G2 becomes active",
        "SAME owner/session/target",
        "refuse physical effect despite active G2",
        "no silent rebinding",
        "authenticated late result for original admitted G1",
        "non-authorizing reconciliation evidence only",
        "NEVER authorize G1 effect replay",
    )


def test_late_tool_result_retains_original_canonical_tool_id() -> None:
    fields = _records()
    for name in ("EnvironmentAction", "EnvironmentResult"):
        assert "tool_id" in fields[name], f"{name} missing tool_id"
    records = _section("4. Conceptual environment-session records")
    _has(
        records,
        "EnvironmentResult.tool_id MUST preserve the originally admitted EnvironmentAction.tool_id",
        "canonical invocation record MUST corroborate the tool_id",
        "capability_version and request_fingerprint",
        "Reject a missing, substituted or inconsistent tool_id",
        "never infer it from the currently selected Tool",
    )
    recovery = _section("7. Bounded observation and state reconciliation")
    _has(
        recovery,
        "EnvironmentResult.tool_id",
        "EnvironmentAction.tool_id",
        "canonical invocation's immutable Tool identity",
        "without creating new execution authority",
    )
    negatives = _section("9. Negative acceptance vectors and refusal semantics")
    _has(
        negatives,
        "late Tool result missing tool_id",
        "different from the original Action or canonical invocation",
        "matching action_id/invocation_id",
        "reject forged Tool provenance",
        "never replay a NON_IDEMPOTENT effect",
    )
    ledger = _read(Path("cl/src/core/client_invocation_ledger.py"))
    assert "PRIMARY KEY (client_id, principal_id, invocation_id)" in ledger
    assert "capability_version" in ledger
    assert "request_fingerprint" in ledger
