from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs" / "agent_interconnect" / "AIC_0_AGENT_INSTANCE_IDENTITY_CONTRACT_B8A1989E.md"
AIC_ROADMAP = ROOT / "docs" / "agent_interconnect" / "AIC_ROADMAP.md"
NAMESPACE = ROOT / "docs" / "ROADMAP_NAMESPACE_REGISTRY.md"
AGENT_REGISTRY = ROOT / "se" / "src" / "agent" / "registry.py"
AGENT_ROUTER = ROOT / "se" / "src" / "transport" / "gateway" / "api" / "v1" / "agent_router.py"
RESOLVER = ROOT / "se" / "src" / "runtimes" / "agent" / "resolver.py"
AGENT_EXECUTION = ROOT / "se" / "src" / "domain" / "schemas" / "agent_execution.py"
CTX_ROADMAP = ROOT / "docs" / "context_future" / "CTX_USER_AGENT_MEMORY_SCOPE_ROADMAP.md"
AAT_ROADMAP = ROOT / "docs" / "agent_automation" / "AAT_ROADMAP.md"
CAS_ROADMAP = ROOT / "docs" / "central_asset" / "CAS_AGENT_ASSET_GRANTS_ROADMAP.md"
APR_CONTRACT = ROOT / "docs" / "agent_platform" / "AGENT_PLATFORM_RUNTIME_CONTRACT.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic(path: Path) -> str:
    return " ".join(_read(path).replace("**", "").replace(chr(96), "").split())


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _annotated_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def test_aic0_r0_pins_exact_zero_production_claim() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "stage = AIC-0-R0",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production CLAIM = NONE",
        "schema/migration authority = NONE",
        "API/runtime migration authority = NONE",
        "merge authority = NONE",
        "APR-P0 authority = HOLD",
        "exact changed paths = 2 NEW / 2",
        "third path = PROHIBITED",
        "Development baseline: main@b8a1989eb03c7ea15ecd6eb17e3fc90bd7450570",
        "Baseline Architecture: #2323 / 37649368535 = GREEN/GREEN",
    ):
        assert phrase in contract
    assert "AIC_0_AGENT_INSTANCE_IDENTITY_CONTRACT_B8A1989E.md" in contract
    assert "test_aic0_agent_instance_identity_contract.py" in contract


def test_aic0_r0_freezes_identity_distinctions_and_ae_lease_boundary() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "(owner_user_id, agent_instance_id)",
        "AgentDefinition.name",
        "current definition-selection agent_id",
        "AgentExecution.agent_id",
        "AgentExecution.owner_instance_id",
        "execution_id",
        "session_id",
        "task_id",
        "branch_id",
        "client_id",
        "connection_id",
        "runtime-session identity",
        "MUST NOT be reinterpreted as agent_instance_id",
        "definition_version",
        "lifecycle_state",
    ):
        assert phrase in contract
    execution = _class(_read(AGENT_EXECUTION), "AgentExecution")
    fields = _annotated_names(execution)
    assert "agent_id" in fields
    assert "owner_instance_id" in fields
    assert "agent_instance_id" not in fields


def test_aic0_r0_proves_current_definition_name_surfaces() -> None:
    registry = _read(AGENT_REGISTRY)
    router = _read(AGENT_ROUTER)
    resolver = _read(RESOLVER)
    assert "self._agents: Dict[str, AgentDefinition] = {}" in registry
    assert "self._agents[definition.name] = definition" in registry
    assert '"/{agent_name}"' in router
    assert "container.agent_registry.get(agent_name)" in router
    assert "agent_id: str | None" in resolver
    assert 'routing.get("default_agent_id")' in resolver
    assert "self._agent_registry.get(selection.agent_id)" in resolver
    for source in (registry, router, resolver):
        assert "agent_instance_id" not in source


def test_aic0_r0_freezes_migration_classification_without_silent_reinterpretation() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "AgentRegistry",
        "DEFINITION_ONLY",
        "/v1/agents list/get/register",
        "DEFINITION_COMPATIBILITY",
        "AgentResolver.agent_id",
        "DEFINITION_SELECTOR",
        "default_agent_id",
        "AgentExecution.agent_id",
        "EXECUTION_DEFINITION_REFERENCE",
        "AgentExecution.owner_instance_id",
        "AE_LEASE_OWNER",
        "MUST NOT be silently reinterpreted as durable instance identity",
        "AIC-0-P1 must perform a fresh exact-main audit",
    ):
        assert phrase in contract


def test_aic0_r0_freezes_owner_isolation_and_lifecycle_contract() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "server derives owner_user_id from authenticated authority",
        "Same-template isolation is mandatory",
        "two users selecting the same Agent definition receive distinct Agent instances",
        "one user may have two distinct instances of the same definition",
        "ACTIVE",
        "SUSPENDED",
        "DELETED",
        "DELETED is not reusable identity",
        "cross-user substitution",
    ):
        assert phrase in contract


def test_aic0_r0_preserves_reserved_production_state() -> None:
    contract = _semantic(CONTRACT)
    roadmap = _read(AIC_ROADMAP)
    namespace = _read(NAMESPACE)
    assert "AIC-0-R0 contract/evidence work only" in contract
    assert "R0 does not open production" in contract
    assert "AIC production and AIC-1+ remain RESERVED / NOT OPEN" in contract
    assert "`AIC-*` remains `RESERVED / NOT OPEN`" in roadmap
    assert "**Current state:** `RESERVED / NOT OPEN`" in namespace


def test_aic0_r0_preserves_cross_track_authority_fences() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "APR MUST NOT mint, persist, migrate, or own Agent instances",
        "AGENT_PRIVATE storage/read authority remains CLOSED",
        "R0 does not create automation records or trigger authority",
        "R0 does not create or mutate Agent grants or assets",
        "owner_instance_id remains AE authority",
        "R0 grants no capability or sandbox authority",
    ):
        assert phrase in contract
    assert "(owner_user_id, agent_instance_id)" in _read(CTX_ROADMAP)
    assert "(owner_user_id, agent_instance_id)" in _read(AAT_ROADMAP)
    assert "agent_instance_id" in _read(CAS_ROADMAP)
    assert "(owner_user_id, agent_instance_id)" in _read(APR_CONTRACT)


def test_aic0_r0_exit_gate_requires_exact_head_evidence_and_fresh_final() -> None:
    contract = _semantic(CONTRACT)
    for phrase in (
        "exact changed paths remain 2 NEW / 2",
        "production/runtime/schema/migration/API/client delta remains ZERO",
        "exact-head Linux Architecture is GREEN",
        "exact-head Windows Architecture is GREEN",
        "independent AIC-0-R0 contract FINAL is PASS",
        "unresolved blocking review threads = 0",
        "blocking P0/P1/P2 = 0/0/0",
        "no MATERIAL current-main drift invalidates the identity freeze",
        "Landing R0 does not itself authorize AIC-0-P1 or APR-P0 production work",
    ):
        assert phrase in contract
