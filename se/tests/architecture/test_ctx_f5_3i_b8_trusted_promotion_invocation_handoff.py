from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B8_TRUSTED_PROMOTION_INVOCATION_HANDOFF_A8B707ED.md"
)
MANAGER = Path("se/src/infrastructure/storage/core/manager.py")
B5 = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_memory_promotion.py"
)
CONTAINER = Path("se/src/application/container.py")
MAIN = Path("se/src/main.py")
PRODUCTION_ROOT = Path("se/src")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function(cls: ast.ClassDef, name: str):
    for node in cls.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"function {name} not found")


def test_b8_contract_freezes_exact_zero_production_claim() -> None:
    contract = _read(CONTRACT)

    for phrase in (
        "stage = CTX-F5-3I-B8",
        "development baseline = a8b707ed4e05ddc1bac6bed98910d3ef4969feaf",
        "baseline Architecture #2113 / 37262785593 = GREEN/GREEN",
        "parent CTX-F5-3I-B7 = LANDED / CANONICAL / HEALTHY",
        "class = CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = CLOSED",
        "production CLAIM = NONE",
        "merge authority = NONE",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
        "production/runtime delta = ZERO",
        "schema/migration delta = ZERO",
        "ApplicationContainer/main wiring delta = ZERO",
    ):
        assert phrase in contract

    assert (
        "docs/context_future/"
        "CTX_F5_3I_B8_TRUSTED_PROMOTION_INVOCATION_HANDOFF_A8B707ED.md"
        in contract
    )
    assert (
        "se/tests/architecture/"
        "test_ctx_f5_3i_b8_trusted_promotion_invocation_handoff.py"
        in contract
    )


def test_b8_contract_preserves_exact_b5_input_authority() -> None:
    contract = _normalized(CONTRACT)
    source = _read(B5)
    promote = _function(
        _class(source, "DurableToolResponsePayloadMemoryPromotion"),
        "promote",
    )

    assert isinstance(promote, ast.AsyncFunctionDef)
    assert [arg.arg for arg in promote.args.args] == ["self"]
    assert [arg.arg for arg in promote.args.kwonlyargs] == [
        "source_ref",
        "owner_user_id",
    ]
    assert promote.args.vararg is None
    assert promote.args.kwarg is None

    for phrase in (
        "one canonical `ContextSourceRef`",
        "one authenticated/canonical `owner_user_id`",
        "content or content snapshot",
        "content digest",
        "`promotion_authority_id`",
        "`SourcePromotionProof`",
        "`PromotionReservation`",
        "Memory id",
        "caller-selected repository, SQL session, UnitOfWork, or transaction state",
        "MUST NOT treat `source_ref.owner_user_id` alone as authentication authority",
    ):
        assert phrase in contract


def test_b8_contract_binds_future_acquisition_to_b7_resolver_only() -> None:
    manager = _read(MANAGER)
    contract = _normalized(CONTRACT)

    assert "def get_tool_response_payload_memory_promotion(" in manager
    resolver = _function(
        _class(manager, "StorageEngine"),
        "get_tool_response_payload_memory_promotion",
    )

    assert isinstance(resolver, ast.FunctionDef)
    assert not any(isinstance(node, ast.Await) for node in ast.walk(resolver))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "promote"
        for node in ast.walk(resolver)
    )

    resolver_source = ast.get_source_segment(manager, resolver)
    assert resolver_source is not None
    assert 'self.drivers.is_available("sqlite")' in resolver_source
    assert "self.services.get(_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION)" in (
        resolver_source
    )
    assert "_is_service_generation_active" in resolver_source

    for phrase in (
        "B7 resolver is the only service acquisition seam",
        "storage.get_tool_response_payload_memory_promotion()",
        'StorageEngine.services["tool_response_payload_memory_promotion"]',
        "MUST NOT cache or retain that returned service",
        "resolve the current service immediately before the single promotion call",
        "caller-owned cached service reference",
    ):
        assert phrase in contract


def test_b8_evidence_proves_no_production_caller_exists() -> None:
    callers: list[str] = []
    stage_markers: list[str] = []

    for path in PRODUCTION_ROOT.rglob("*.py"):
        source = _read(path)
        if ".get_tool_response_payload_memory_promotion(" in source:
            callers.append(str(path))
        if "CTX_F5_3I_B8" in source or "CTX-F5-3I-B8" in source:
            stage_markers.append(str(path))

    assert callers == []
    assert stage_markers == []


def test_b8_contract_reuses_existing_container_storage_without_new_wiring() -> None:
    container = _read(CONTAINER)
    main = _read(MAIN)
    contract = _normalized(CONTRACT)

    assert "storage: Any" in container
    assert "storage=storage_engine" in main

    for phrase in (
        "Existing `ApplicationContainer.storage` is sufficient",
        "B8 does not add an ApplicationContainer field",
        "does not change `main.py` wiring",
        "parallel registry, service locator, or fallback promotion service",
    ):
        assert phrase in contract


def test_b8_contract_keeps_automatic_and_background_invocation_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Automatic promotion remains CLOSED",
        "tool completion",
        "capability completion",
        "AgentToolResult COMMITTED observation",
        "provider completion",
        "retry, recovery, or resume",
        "Session, Task, or Branch lifecycle",
        "application startup or shutdown",
        "event-bus publication",
        "ContextBuilder, retrieval, Working Set, or ContextSnapshot assembly",
        "detached tasks",
        "fire-and-forget promotion",
        "hidden queueing or scheduling authority",
    ):
        assert phrase in contract


def test_b8_contract_keeps_failure_repair_and_budget_authority_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Failure and cancellation authority remains lower-layer owned",
        "retry loops",
        "exception normalization",
        "source repair",
        "reservation repair",
        "Memory repair",
        "propagate lower-layer failure and cancellation semantics",
        "Scheduling placement",
        "Agent critical-path placement",
        "timeout ownership",
        "budget accounting",
        "then-current owning authorities",
    ):
        assert phrase in contract


def test_b8_contract_keeps_public_routing_cas_and_later_ctx_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "HTTP/FastAPI route or dependency",
        "client protocol",
        "tool or capability registration",
        "model-visible capability or schema",
        "`capability_id`",
        "quota admission",
        "DirectChat trigger",
        "AgentRuntime trigger",
        "Issue #156 routing or sandbox authority",
        "TOOL_RESPONSE_PAYLOAD content remains opaque committed source material",
        "does not dereference, hydrate, rewrite, strip, canonicalize",
        "Memory retrieval, search, or ranking",
        "ContextBuilder injection",
        "F6 Personalization",
        "F7 pins, scoring, or dedupe",
        "F9 Working Set or ContextSnapshot",
        "F10 CompactContext",
        "retention or destructive GC",
    ):
        assert phrase in contract


def test_b8_contract_requires_separate_future_production_preclaim() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Production PRE-CLAIM remains closed",
        "does not authorize any `se/src/**` edit",
        "separate independent production PRE-CLAIM",
        "exact production paths",
        "authenticated owner source",
        "scheduling/budget ownership",
        "production CLAIM is recorded separately",
        "production PRE-CLAIM = CLOSED",
        "production CLAIM = NONE",
        "F6 Personalization = CLOSED",
    ):
        assert phrase in contract
