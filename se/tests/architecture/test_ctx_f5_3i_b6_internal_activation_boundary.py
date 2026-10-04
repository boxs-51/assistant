from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3I_B6_INTERNAL_ACTIVATION_BOUNDARY_522B543E.md"
)
MANAGER = Path("se/src/infrastructure/storage/core/manager.py")
SQLITE = Path("se/src/infrastructure/storage/drivers/sqlite/driver.py")
CONTAINER = Path("se/src/application/container.py")
MAIN = Path("se/src/main.py")
SOURCE_AUTHORITY = Path(
    "se/src/infrastructure/storage/services/tool_response_payload_source_authority.py"
)
ISSUER = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_issuer.py"
)
RECOVERY = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_recovery.py"
)
ORCHESTRATION = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_promotion_orchestration.py"
)
ADMISSION = Path(
    "se/src/infrastructure/storage/services/memory_promotion_admission.py"
)
B5 = Path(
    "se/src/infrastructure/storage/services/tool_response_payload_memory_promotion.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def test_b6_contract_freezes_exact_zero_production_status_and_scope() -> None:
    contract = _read(CONTRACT)

    for phrase in (
        "stage = CTX-F5-3I-B6",
        "class = CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION",
        "exact development baseline = 522b543e66f309085729e57a2248258b7c4f2599",
        "baseline Architecture #2005 / 37194583170 = GREEN/GREEN",
        "parent CTX-F5-3I-B5 = LANDED / CANONICAL / HEALTHY",
        "contract PRE-CLAIM = PASS / RELEASED",
        "contract CLAIM = TAKEN",
        "production PRE-CLAIM = CLOSED",
        "production CLAIM = NONE",
        "runtime/container/API/model-callable authority = NONE",
        "F6 Personalization authority = NONE",
        "production/runtime/schema/migration delta = ZERO",
        "merge authority = NONE",
        "se/src/** delta = ZERO",
        "client delta = ZERO",
        "schema/migration delta = ZERO",
        "runtime/container/API delta = ZERO",
    ):
        assert phrase in contract

    assert (
        "docs/context_future/"
        "CTX_F5_3I_B6_INTERNAL_ACTIVATION_BOUNDARY_522B543E.md"
        in contract
    )
    assert (
        "se/tests/architecture/test_ctx_f5_3i_b6_internal_activation_boundary.py"
        in contract
    )


def test_b6_evidence_proves_storage_engine_is_the_narrow_activation_seam() -> None:
    manager = _read(MANAGER)
    container = _read(CONTAINER)
    main = _read(MAIN)

    assert "self.services = {}" in manager
    assert "await self.drivers.connect_all()" in manager
    assert "self._initialize_services()" in manager
    assert (
        manager.index("await self.drivers.connect_all()")
        < manager.index("self._initialize_services()")
    )
    assert "self.services.clear()" in manager
    assert 'self.drivers.is_available("sqlite")' in manager

    # B5 is still dormant on the contract baseline.
    for source in (manager, container, main):
        assert "DurableToolResponsePayloadMemoryPromotion" not in source

    assert "storage: Any" in container
    assert "container = ApplicationContainer(" in main
    assert "storage=storage_engine" in main


def test_b6_evidence_proves_public_sqlite_session_context_is_canonical() -> None:
    sqlite = _read(SQLITE)

    assert "class SQLiteDriver(DatabaseDriver):" in sqlite
    assert "@asynccontextmanager" in sqlite
    assert "async def get_session(" in sqlite
    assert "async with self._session_factory() as session:" in sqlite
    assert "yield session" in sqlite

    contract = _normalized(CONTRACT)
    for phrase in (
        "Future activation reuses public `SQLiteDriver.get_session`",
        "create another SQLAlchemy engine",
        "call `async_sessionmaker`",
        "reinterpret `uow_factory` as the B1/B4/H-B2 session-context contract",
    ):
        assert phrase in contract


def test_b6_evidence_proves_complete_canonical_construction_chain_exists() -> None:
    source_authority = _read(SOURCE_AUTHORITY)
    issuer = _read(ISSUER)
    recovery = _read(RECOVERY)
    orchestration = _read(ORCHESTRATION)
    admission = _read(ADMISSION)
    b5 = _read(B5)

    assert "class DurableToolResponsePayloadSourceAuthority(" in source_authority
    assert "class DurablePromotionReservationIssuer(" in issuer
    assert "class DurablePromotionReservationRecovery:" in recovery
    assert "class DurableToolResponsePayloadPromotionOrchestration:" in orchestration
    assert "class DurableMemoryPromotionAdmission:" in admission
    assert "class DurableToolResponsePayloadMemoryPromotion:" in b5

    assert "reservation_recovery: DurablePromotionReservationRecovery | None = None" in (
        orchestration
    )
    assert "DurablePromotionReservationRecoveryHandoff(" in orchestration
    assert "reservation_recovery," in orchestration

    assert "handoff = await self._orchestration.reserve(" in b5
    assert "return await self._admission.admit(" in b5
    assert b5.index("await self._orchestration.reserve(") < b5.index(
        "await self._admission.admit("
    )


def test_b6_contract_requires_b4_recovery_in_any_future_activation() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "B4 recovery is mandatory",
        "a B6 activation must pass a real",
        "`DurablePromotionReservationRecovery`",
        "reservation_recovery=None",
        "is not an equivalent production activation and is prohibited",
        "trusted non-minting durable-winner recovery",
    ):
        assert phrase in contract


def test_b6_contract_keeps_trigger_api_routing_quota_and_retrieval_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "No implicit or automatic promotion",
        "Automatic promotion remains CLOSED",
        "No public or model-callable surface",
        "tool/capability registration",
        "model-visible capability schemas",
        "No new UBQ or routing identity",
        "`capability_id`",
        "`invocation_id`",
        "CAS boundary remains opaque",
        "are not dereferenced, hydrated, rewritten, stripped, or canonicalized",
        "Retrieval and later CTX stages remain closed",
        "ContextBuilder injection",
        "F6 Personalization",
        "F7 Pins/scoring/dedupe",
        "F9 Working Set / ContextSnapshot",
        "F10 CompactContext",
    ):
        assert phrase in contract


def test_b6_contract_preserves_b5_input_and_lower_layer_failure_authority() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Caller authority remains B5",
        "source_ref = canonical ContextSourceRef",
        "owner_user_id = authenticated/canonical owner",
        "content or content snapshot",
        "promotion_authority_id",
        "PromotionReservation",
        "SourcePromotionProof",
        "Failure and cancellation authority remains lower-layer owned",
        "retry loop",
        "exception normalization",
        "source repair",
        "reservation state repair",
        "Memory repair",
    ):
        assert phrase in contract


def test_b6_contract_does_not_release_production_activation() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Future production PRE-CLAIM — explicitly not released",
        "This contract does not release production implementation",
        "se/src/infrastructure/storage/core/manager.py",
        "a separate independent production PRE-CLAIM",
        "It does not activate Memory promotion",
        "change ApplicationContainer",
        "grant F6 authority",
    ):
        assert phrase in contract

def test_b6_contract_freezes_generation_bound_retained_reference_lifetime() -> None:
    manager = _read(MANAGER)
    sqlite = _read(SQLITE)
    contract = _normalized(CONTRACT)

    # Current source shape explains why registry cleanup alone is not revocation.
    assert "self.services.clear()" in manager
    assert "await self.drivers.disconnect_all()" in manager
    assert "await self._engine.dispose()" in sqlite
    assert "async def get_session(" in sqlite
    assert "async with self._session_factory() as session:" in sqlite

    for phrase in (
        "Retained-reference lifetime fence — follow-up amendment",
        "`StorageEngine.services.clear()` is registry cleanup only",
        "not retained-reference revocation",
        "generation-bound revocation token, lease, guard, or equivalent stale-reference fence",
        "generation N disconnect revokes generation N",
        "every later service_N invocation fails closed",
        "before any new SQLiteDriver.get_session / SQL acquisition",
        "service_N remains permanently stale",
        "A simple reusable `_started` boolean is insufficient",
        "distinguish generations",
        "evaluated before the first lower-layer operation that could acquire a SQL session",
        "Production implementation remains CLOSED",
        "separate independent production PRE-CLAIM",
    ):
        assert phrase in contract


def test_b6_lifetime_follow_up_keeps_non_authorities_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "This follow-up remains CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION",
        "grants no caller",
        "automatic promotion",
        "public/model-callable API",
        "capability",
        "UBQ",
        "routing",
        "CAS",
        "retrieval",
        "F6 authority",
    ):
        assert phrase in contract

