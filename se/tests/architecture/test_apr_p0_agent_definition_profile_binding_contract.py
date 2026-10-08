from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/agent_platform/"
    "APR_P0_AGENT_DEFINITION_PROFILE_BINDING_CONTRACT_7C4C4D42.md"
)
PLATFORM = Path("docs/agent_platform/AGENT_PLATFORM_RUNTIME_CONTRACT.md")
AIC = Path(
    "docs/agent_interconnect/"
    "AIC_0_AGENT_INSTANCE_IDENTITY_CONTRACT_B8A1989E.md"
)
DCS_PARENT = Path(
    "docs/capability_runtime/"
    "AGENT_ONLY_CAPABILITY_SANDBOX_CAS_CONTRACT_REFREEZE.md"
)
AGENT_SCHEMA = Path("se/src/domain/schemas/agent.py")
EXECUTION_SCHEMA = Path("se/src/domain/schemas/agent_execution.py")
EXECUTION_CONTEXT = Path("se/src/runtimes/agent/contracts/context.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).replace("`", "").split())


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


def test_apr_p0_freezes_exact_zero_production_claim() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "stage = APR-P0",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = CLOSED / NONE",
        "production CLAIM = NONE",
        "schema/migration authority = NONE",
        "runtime/API/client authority = NONE",
        "AgentInstance persistence authority = NONE",
        "APR-X1 authority = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "third path = PROHIBITED",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
        "schema/migration delta = ZERO",
        "runtime/API/router/registry/persistence delta = ZERO",
    ):
        assert phrase in contract

    assert "APR_P0_AGENT_DEFINITION_PROFILE_BINDING_CONTRACT_7C4C4D42.md" in contract
    assert "test_apr_p0_agent_definition_profile_binding_contract.py" in contract


def test_apr_p0_proves_current_definition_is_legacy_blueprint_only() -> None:
    source = _read(AGENT_SCHEMA)
    definition = _class(source, "AgentDefinition")
    fields = _annotated_names(definition)

    for field in (
        "name",
        "goal",
        "instruction",
        "tools",
        "skills",
        "workflow_definition",
        "memory_config",
    ):
        assert field in fields

    for future_field in (
        "definition_id",
        "definition_version",
        "agent_instance_id",
        "profile_id",
        "profile_version",
        "supported_runtime_profiles",
        "default_runtime_profile",
    ):
        assert future_field not in fields

    assert "class AgentDefinitionV2" not in source
    assert "class AgentProfile" not in source
    assert "class AgentExecutionBinding" not in source


def test_apr_p0_proves_current_execution_identity_boundaries() -> None:
    execution_source = _read(EXECUTION_SCHEMA)
    context_source = _read(EXECUTION_CONTEXT)

    execution = _class(execution_source, "AgentExecution")
    context = _class(context_source, "AgentExecutionContext")

    execution_fields = _annotated_names(execution)
    context_fields = _annotated_names(context)

    assert "execution_id" in execution_fields
    assert "agent_id" in execution_fields
    assert "owner_instance_id" in execution_fields
    assert "agent_instance_id" not in execution_fields
    assert "profile_id" not in execution_fields
    assert "active_runtime_profile" not in execution_fields

    assert "execution_id" in context_fields
    assert "agent_id" in context_fields
    assert "agent" in context_fields
    assert "agent_instance_id" not in context_fields
    assert "profile_id" not in context_fields
    assert "active_runtime_profile" not in context_fields


def test_apr_p0_freezes_definition_profile_and_binding_shapes() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "AgentDefinitionV2",
        "definition_id",
        "definition_version",
        "maximum_capability_envelope_or_policy_ref",
        "skill_policy_or_envelope_ref",
        "supported_runtime_profiles",
        "AgentProfile",
        "profile_id",
        "profile_version",
        "capability_ceiling_or_ref",
        "skill_preference_or_ref",
        "AgentExecutionBinding",
        "execution_id",
        "owner_user_id?",
        "agent_instance_id?",
        "active_runtime_profile",
        "runtime_session_id?",
        "interaction_channel?",
    ):
        assert phrase in contract


def test_apr_p0_freezes_runtime_profile_vocabulary() -> None:
    contract = _normalized(CONTRACT)
    platform = _normalized(PLATFORM)

    for profile in (
        "STANDARD",
        "REALTIME",
        "FAST_CONTROL",
        "COMPUTER_INTERACTIVE",
        "BACKGROUND",
        "MEDIA_ORCHESTRATED",
    ):
        assert profile in contract
        assert profile in platform


def test_apr_p0_profile_is_not_authorization_and_dcs_remains_owner() -> None:
    contract = _normalized(CONTRACT)
    dcs_parent = _normalized(DCS_PARENT)

    for phrase in (
        "AgentProfile is reusable/versioned specialization configuration",
        "not an authorization grant",
        "An AgentProfile MUST NOT widen its parent Agent definition envelope",
        "AgentProfile cannot write directly to CapabilityWorkingSet",
        "DCS remains the sole owner of per-iteration model-visible capability selection",
        "AgentDefinition.tools != InferenceRequest.tools",
    ):
        assert phrase in contract

    assert "AgentDefinition.tools != InferenceRequest.tools" in dcs_parent
    assert "CapabilityWorkingSet" in contract


def test_apr_p0_preserves_aic_and_ae_identity_authority() -> None:
    contract = _normalized(CONTRACT)
    aic = _normalized(AIC)

    for phrase in (
        "(owner_user_id, agent_instance_id)",
        "AgentDefinition.name != agent_instance_id",
        "AgentExecution.agent_id != agent_instance_id",
        "AgentExecution.owner_instance_id != agent_instance_id",
        "APR MUST NOT mint, persist, register, suspend, delete, reassign, or otherwise own agent_instance_id",
        "owner_user_id in this binding is trusted/server-derived authority",
        "owner_user_id and agent_instance_id are co-present or both absent",
        "A bare agent_instance_id is insufficient to establish or restore Agent-instance ownership",
        "APR-P0 MUST NOT mint or infer either identity component",
        "Absence remains absence until separately canonical AIC/registration authority resolves a trusted (owner_user_id, agent_instance_id) binding",
        "Persisting this binding into AgentExecution, checkpoints, continuation records, or storage requires a fresh AE/APR production PRE-CLAIM",
    ):
        assert phrase in contract

    for phrase in (
        "(owner_user_id, agent_instance_id)",
        "AgentExecution.owner_instance_id",
        "MUST NOT be reinterpreted as agent_instance_id",
    ):
        assert phrase in aic


def test_apr_p0_freezes_reproducibility_and_runtime_session_boundary() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "A mutable later edit to a definition/profile MUST NOT silently rewrite an existing execution's meaning",
        "retry, recovery, reconnect, checkpoint restore or continuation",
        "runtime_session_id is optional execution-attached runtime/environment identity",
        "Runtime-session lifecycle remains owned by the applicable future specialized runtime stage",
    ):
        assert phrase in contract

    for non_identity in (
        "Agent identity",
        "Memory owner identity",
        "AE lease owner identity",
        "user identity",
        "capability authorization",
        "durable asset identity",
    ):
        assert non_identity in contract


def test_apr_p0_preserves_cross_track_and_future_stage_fences() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "AIC",
        "AE",
        "#156 / DCS / CRT / SBX",
        "SKV2",
        "CTX",
        "AAT",
        "CAS",
        "UBQ / TBO",
        "Tools / PTC / providers",
        "GAC #221",
        "APR-X1 execution lanes / event sequencer",
        "APR-FC1 FAST_CONTROL runtime",
        "APR-RT1 realtime runtime",
        "APR-CU1 computer-interactive runtime",
        "APR-MD1 media/job runtime integration",
    ):
        assert phrase in contract


def test_apr_p0_exit_gate_requires_fresh_exact_head_evidence() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "exact changed paths remain 2 NEW / 2",
        "third path remains absent",
        "production/runtime/schema/migration/API/client delta remains zero",
        "all six runtime-profile names are present",
        "profile-is-not-grant and no-envelope-widening semantics are explicit",
        "AgentDefinition.tools != InferenceRequest.tools",
        "AgentExecution.owner_instance_id != agent_instance_id",
        "trusted owner_user_id is co-bound with agent_instance_id",
        "exact-head Linux Architecture is GREEN",
        "exact-head Windows Architecture is GREEN",
        "independent APR-P0 contract FINAL is PASS",
        "unresolved blocking review threads = 0",
        "blocking P0/P1/P2 = 0/0/0",
        "no MATERIAL current-main drift invalidates the freeze",
    ):
        assert phrase in contract
