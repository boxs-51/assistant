from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F4C_USER_WIDE_CROSS_SESSION_SEARCH_CONTRACT_BE2CA9ED.md"
)
F4B_CONTRACT = Path(
    "docs/context_future/CTX_F4B_FINITE_STRUCTURAL_SEARCH_DAA672F2.md"
)
PLANNING = Path("docs/context_future/CTX_USER_AGENT_MEMORY_SCOPE_ROADMAP.md")
SEARCH = Path("se/src/context/search.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def _function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_ctx_f4c_freezes_exact_zero_production_claim() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "stage = CTX-F4C",
        "development baseline = be2ca9ed5d0438a17f86fb8ab127b4dcfc95f7f7",
        "baseline Architecture #2255 / 37576549147 = GREEN/GREEN",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = CLOSED",
        "production CLAIM = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "se/src/** delta = ZERO",
        "repository/SQL delta = ZERO",
        "persisted-index delta = ZERO",
        "schema/migration delta = ZERO",
        "runtime/API/client delta = ZERO",
        "ContextBuilder/Working Set/ContextSnapshot delta = ZERO",
    ):
        assert phrase in contract

    assert "CTX_F4C_USER_WIDE_CROSS_SESSION_SEARCH_CONTRACT_BE2CA9ED.md" in contract
    assert "test_ctx_f4c_user_wide_cross_session_search_contract.py" in contract


def test_ctx_f4c_freezes_authority_separation() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "SOURCE AUTHORITY",
        "SEARCH PROJECTION",
        "READ / HYDRATION",
        "MODEL WORKING SET",
        "A search projection is derived evidence only",
        "never become source authority",
        "Canonical Session/Task/Branch/Transcript ownership and provenance remain authoritative",
        "re-check current canonical source ownership, provenance/version, and availability",
    ):
        assert phrase in contract


def test_ctx_f4c_freezes_trusted_owner_scope() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "trusted, authenticated owner authority resolved by the server",
        "caller-supplied owner_user_id",
        "Session ID",
        "Task ID",
        "Branch ID",
        "persisted index row",
        "model output",
        "client or connection metadata",
        "do not independently mint owner or source authority",
    ):
        assert phrase in contract


def test_ctx_f4c_preserves_f4b_finite_search_shape() -> None:
    source = _read(SEARCH)
    contract = _normalized(CONTRACT)
    f4b = _normalized(F4B_CONTRACT)
    function = _function(source, "search_context_sources")

    positional = [arg.arg for arg in function.args.args]
    keyword_only = [arg.arg for arg in function.args.kwonlyargs]

    assert positional == ["query"]
    assert keyword_only == ["sessions", "tasks", "branches"]
    assert "build_discovery_collection(" in source
    assert "Search finite already-loaded F3 evidence with literal structural matching" in source

    for phrase in (
        "finite already-loaded Session/Task/Branch evidence only",
        "build_discovery_collection(...)",
        "literal field-local substring matching",
        "deterministic global context_source_id ordering",
        "zero evidence => ContextAccessAuthorityError",
        "valid owner scope + no textual match => immutable empty result",
        "F4C does not rewrite, weaken, replace, or reinterpret F4B",
    ):
        assert phrase in contract

    assert "NO persisted search/index/loader" in f4b
    assert "NO ranking/scoring/fuzzy/vector/NL retrieval" in f4b


def test_ctx_f4c_keeps_persisted_search_implementation_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "repository/SQL loader",
        "persisted search/index",
        "search projection tables",
        "schema/migration",
        "background indexer",
        "search API/router/client endpoint",
        "ranking/scoring",
        "vector/embedding retrieval",
        "semantic/NL retrieval",
        "model Working Set injection",
        "ContextBuilder injection",
        "ContextSnapshot persistence/use",
        "Issue #15 must separately release every production slice",
    ):
        assert phrase in contract


def test_ctx_f4c_preserves_r11_and_cas_boundaries() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Issue #31/R11 retains transcript/checkpoint read-liveness, retention, and destructive-GC authority",
        "create a retention root",
        "extend Session/Task/Branch/Transcript lifetime",
        "infer read-liveness from the existence of a search projection",
        "fresh CTX/R11 bilateral audit before production PRE-CLAIM",
        "CAS #74 retains ASSET/FileAsset/FileBlob/ObjectStorage/provider lifecycle",
        "ASSET search",
        "asset hydration/dereference",
        "cached CAS-grant substitution",
        "current grant/readability re-proof",
        "A CTX search projection may never make a revoked CAS grant usable",
    ):
        assert phrase in contract


def test_ctx_f4c_freezes_aic_agent_visibility_boundary() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "trusted server-resolved agent_instance_id",
        "AgentDefinition name",
        "AgentExecution.owner_instance_id",
        "model output",
        "request/client metadata",
        "Session/Task/Branch metadata",
        "Agent-specific visibility is a separate authorization gate",
        "No AIC/APR production authority transfers to CTX-F4C",
    ):
        assert phrase in contract


def test_ctx_f4c_preserves_dcs_and_later_ctx_fences() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Issue #156 / DCS owns model-visible capability and Skill selection",
        "CapabilityWorkingSet",
        "ActiveSkillSet",
        "Tool ranking",
        "capability schema estimation",
        "DCS lazy expansion",
        "DCS acquires no CTX source, search, persistence, or retention authority",
        "F6 Personalization remains CLOSED",
        "F9 Working Set/ContextSnapshot remains CLOSED",
        "F10 CompactContext remains CLOSED",
    ):
        assert phrase in contract


def test_ctx_f4c_consumes_reserved_cross_session_roadmap_gap_without_opening_production() -> None:
    planning = _normalized(PLANNING)
    contract = _normalized(CONTRACT)

    assert "User-wide cross-session index/search with owner and Agent visibility filters" in planning
    assert "source records remain canonical" in planning
    assert "user-wide cross-session discovery/search" in contract
    assert "without implementing persistence, loading, indexing, ranking, read/hydration, or model-visible selection" in contract


def test_ctx_f4c_exit_gate_requires_exact_head_ci_and_independent_final() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "CTX-F4C contract FINAL requires",
        "exact changed paths remain 2 NEW / 2",
        "production/runtime/schema/migration/repository/index/API/client delta remains zero",
        "exact-head Linux Architecture is GREEN",
        "exact-head Windows Architecture is GREEN",
        "independent CTX-F4C contract FINAL is PASS",
        "unresolved blocking review threads = 0",
        "blocking P0/P1 = 0",
        "no MATERIAL current-main drift invalidates the freeze",
        "zero-production conditional merge exception",
    ):
        assert phrase in contract
