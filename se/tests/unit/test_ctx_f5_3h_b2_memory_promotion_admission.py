from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from se.src.context.memory import (
    create_memory_record,
    memory_content_digest,
)
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionProofScope,
    PromotionAdmissionConsumedMemoryMismatchError,
    PromotionAdmissionConsumedMemoryMissingError,
    PromotionAdmissionIntentConflictError,
    PromotionAdmissionMemoryReplayConflictError,
    PromotionAdmissionPersistenceFailureError,
    PromotionAdmissionReservationNotIssuedError,
    PromotionAdmissionReservationRevokedError,
    PromotionAdmissionTransactionUnavailableError,
    PromotionReservation,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRecord,
    DurablePromotionReservationState,
    PromotionReservationPersistenceUnavailableError,
)
import se.src.infrastructure.storage.services.memory_promotion_admission as admission_module
from se.src.infrastructure.storage.services.memory_promotion_admission import (
    DurableMemoryPromotionAdmission,
)


NOW = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)


def _intent(content, *, suffix: str = "one", metadata=None) -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=f"session-{suffix}",
        owner_user_id="user-ctx-f5-3h-b2",
        session_id=f"session-{suffix}",
        source_created_at=NOW,
        source_state="active",
        metadata={"source": "canonical"},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source,
        proof_receipt_id=f"receipt-{suffix}",
        authority_state_token=f"token-{suffix}",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return MemoryPromotionIntent(
        owner_user_id=source.owner_user_id,
        source_ref_snapshot=source,
        source_proof=proof,
        content_digest=memory_content_digest(content),
        metadata={"kind": "admission"} if metadata is None else metadata,
        memory_schema_version=1,
    )


def _durable(
    *,
    authority_id: str,
    intent: MemoryPromotionIntent,
    state: DurablePromotionReservationState,
) -> DurablePromotionReservationRecord:
    return DurablePromotionReservationRecord(
        promotion_authority_id=authority_id,
        intent=intent,
        intent_digest="d" * 64,
        source_context_source_id=intent.source_ref_snapshot.context_source_id,
        proof_receipt_id=intent.source_proof.proof_receipt_id,
        authority_state_token=intent.source_proof.authority_state_token,
        proof_scope=intent.source_proof.scope,
        state=state,
    )


def _reservation(authority_id: str, intent: MemoryPromotionIntent) -> PromotionReservation:
    return PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )


def _memory(record: DurablePromotionReservationRecord, content, *, metadata=None):
    return create_memory_record(
        source_ref=record.intent.source_ref_snapshot,
        promotion_authority_id=record.promotion_authority_id,
        content=content,
        metadata=record.intent.metadata if metadata is None else metadata,
        memory_schema_version=record.intent.memory_schema_version,
    )


class _Session:
    pass


class _SessionContext:
    def __init__(self, session, *, on_enter=None):
        self.session = session
        self.on_enter = on_enter

    async def __aenter__(self):
        if self.on_enter is not None:
            self.on_enter()
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, *, on_enter=None):
        self.session = _Session()
        self.on_enter = on_enter
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return _SessionContext(self.session, on_enter=self.on_enter)


class _ReservationRepository:
    def __init__(self, record=None, *, get_failure=None, consume_failure=None):
        self.record = record
        self.get_failure = get_failure
        self.consume_failure = consume_failure
        self.get_calls = []
        self.consume_calls = []

    async def get(self, authority_id):
        self.get_calls.append(authority_id)
        if self.get_failure is not None:
            raise self.get_failure
        return self.record

    async def mark_consumed(self, authority_id):
        self.consume_calls.append(authority_id)
        if self.consume_failure is not None:
            raise self.consume_failure
        return self.record


class _MemoryRepository:
    def __init__(self, *, existing=None, put_failure=None):
        self.existing = existing
        self.put_failure = put_failure
        self.get_calls = []
        self.put_calls = []

    async def get_by_promotion_authority(self, authority_id):
        self.get_calls.append(authority_id)
        return self.existing

    async def put(self, record):
        self.put_calls.append(record)
        if self.put_failure is not None:
            raise self.put_failure
        self.existing = record
        return record


class _TransactionContext:
    def __init__(
        self,
        memory_repository,
        *,
        enter_failure=None,
        exit_failure=None,
    ):
        self.memory_repository = memory_repository
        self.enter_failure = enter_failure
        self.exit_failure = exit_failure

    async def __aenter__(self):
        if self.enter_failure is not None:
            raise self.enter_failure
        return self.memory_repository

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None and self.exit_failure is not None:
            raise self.exit_failure
        return False


def _install(
    monkeypatch,
    *,
    reservation_repository,
    memory_repository,
    tx_enter_failure=None,
    tx_exit_failure=None,
):
    created_sessions = []

    def reservation_factory(session):
        created_sessions.append(session)
        return reservation_repository

    def transaction_factory(session):
        return _TransactionContext(
            memory_repository,
            enter_failure=tx_enter_failure,
            exit_failure=tx_exit_failure,
        )

    monkeypatch.setattr(
        admission_module,
        "DurablePromotionReservationRepository",
        reservation_factory,
    )
    monkeypatch.setattr(
        admission_module,
        "sqlite_memory_admission_transaction",
        transaction_factory,
    )
    return created_sessions


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_detaches_payload_before_first_session_await(monkeypatch):
    caller_content = {"fact": ["alpha"]}
    intent = _intent(caller_content)
    durable = _durable(
        authority_id="authority-detached",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    reservation_repo = _ReservationRepository(durable)
    memory_repo = _MemoryRepository()

    def mutate_after_preflight():
        caller_content["fact"][0] = "mutated"

    sessions = _SessionFactory(on_enter=mutate_after_preflight)
    created_sessions = _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=memory_repo,
    )
    service = DurableMemoryPromotionAdmission(sessions)

    winner = await service.admit(
        reservation=_reservation(durable.promotion_authority_id, intent),
        content=caller_content,
    )

    assert caller_content == {"fact": ["mutated"]}
    assert winner.content["fact"] == ("alpha",)
    assert memory_repo.put_calls == [winner]
    assert reservation_repo.consume_calls == ["authority-detached"]
    assert sessions.calls == 1
    assert created_sessions == [sessions.session]


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_invalid_payload_fails_before_session_creation():
    content = {"fact": "alpha"}
    intent = _intent(content)
    sessions = _SessionFactory()
    service = DurableMemoryPromotionAdmission(sessions)

    with pytest.raises(PromotionAdmissionIntentConflictError):
        await service.admit(
            reservation=_reservation("authority-invalid", intent),
            content={"bad": (1, 2)},
        )

    assert sessions.calls == 0


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_cyclic_payload_fails_before_session_creation():
    valid_content = {"fact": "alpha"}
    intent = _intent(valid_content)

    cyclic_list = []
    cyclic_list.append(cyclic_list)
    cyclic_dict = {}
    cyclic_dict["self"] = cyclic_dict

    for payload in (cyclic_list, cyclic_dict):
        sessions = _SessionFactory()
        service = DurableMemoryPromotionAdmission(sessions)

        with pytest.raises(PromotionAdmissionIntentConflictError):
            await service.admit(
                reservation=_reservation("authority-cyclic", intent),
                content=payload,
            )

        assert sessions.calls == 0


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_missing_and_revoked_fail_without_memory_write(monkeypatch):
    content = {"fact": "alpha"}
    intent = _intent(content)

    for authority_id, durable, error_type in (
        (
            "authority-missing",
            None,
            PromotionAdmissionReservationNotIssuedError,
        ),
        (
            "authority-revoked",
            _durable(
                authority_id="authority-revoked",
                intent=intent,
                state=DurablePromotionReservationState.REVOKED,
            ),
            PromotionAdmissionReservationRevokedError,
        ),
    ):
        reservation_repo = _ReservationRepository(durable)
        memory_repo = _MemoryRepository()
        _install(
            monkeypatch,
            reservation_repository=reservation_repo,
            memory_repository=memory_repo,
        )
        service = DurableMemoryPromotionAdmission(_SessionFactory())

        with pytest.raises(error_type):
            await service.admit(
                reservation=_reservation(authority_id, intent),
                content=content,
            )

        assert memory_repo.put_calls == []
        assert reservation_repo.consume_calls == []


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_issued_existing_memory_fails_without_healing(monkeypatch):
    content = {"fact": "alpha"}
    intent = _intent(content)
    durable = _durable(
        authority_id="authority-issued-existing",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    existing = _memory(durable, content)
    reservation_repo = _ReservationRepository(durable)
    memory_repo = _MemoryRepository(existing=existing)
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=memory_repo,
    )

    with pytest.raises(PromotionAdmissionMemoryReplayConflictError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation(durable.promotion_authority_id, intent),
            content=content,
        )

    assert memory_repo.put_calls == []
    assert reservation_repo.consume_calls == []


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_consumed_replay_is_read_only_and_exact(monkeypatch):
    content = {"fact": "alpha"}
    intent = _intent(content)
    durable = _durable(
        authority_id="authority-consumed",
        intent=intent,
        state=DurablePromotionReservationState.CONSUMED,
    )
    existing = _memory(durable, content)
    reservation_repo = _ReservationRepository(durable)
    memory_repo = _MemoryRepository(existing=existing)
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=memory_repo,
    )

    returned = await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
        reservation=_reservation(durable.promotion_authority_id, intent),
        content=content,
    )

    assert returned is existing
    assert memory_repo.put_calls == []
    assert reservation_repo.consume_calls == []


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_consumed_missing_and_mismatch_fail_read_only(monkeypatch):
    content = {"fact": "alpha"}
    intent = _intent(content)

    for authority_id, existing, error_type in (
        (
            "authority-consumed-missing",
            None,
            PromotionAdmissionConsumedMemoryMissingError,
        ),
        (
            "authority-consumed-mismatch",
            "mismatch",
            PromotionAdmissionConsumedMemoryMismatchError,
        ),
    ):
        durable = _durable(
            authority_id=authority_id,
            intent=intent,
            state=DurablePromotionReservationState.CONSUMED,
        )
        existing_record = (
            _memory(durable, content, metadata={"kind": "different"})
            if existing == "mismatch"
            else None
        )
        reservation_repo = _ReservationRepository(durable)
        memory_repo = _MemoryRepository(existing=existing_record)
        _install(
            monkeypatch,
            reservation_repository=reservation_repo,
            memory_repository=memory_repo,
        )

        with pytest.raises(error_type):
            await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
                reservation=_reservation(authority_id, intent),
                content=content,
            )

        assert memory_repo.put_calls == []
        assert reservation_repo.consume_calls == []


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_durable_intent_and_payload_conflicts_fail_closed(monkeypatch):
    content = {"fact": "alpha"}
    caller_intent = _intent(content, suffix="caller")
    durable_intent = _intent(content, suffix="durable")
    durable = _durable(
        authority_id="authority-intent-conflict",
        intent=durable_intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    reservation_repo = _ReservationRepository(durable)
    memory_repo = _MemoryRepository()
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=memory_repo,
    )

    with pytest.raises(PromotionAdmissionIntentConflictError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation(durable.promotion_authority_id, caller_intent),
            content=content,
        )

    same_intent = _intent(content, suffix="digest")
    digest_durable = _durable(
        authority_id="authority-digest-conflict",
        intent=same_intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    reservation_repo.record = digest_durable

    with pytest.raises(PromotionAdmissionIntentConflictError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation(
                digest_durable.promotion_authority_id,
                same_intent,
            ),
            content={"fact": "beta"},
        )


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_pre_yield_sqlalchemy_failure_maps_transaction_unavailable(
    monkeypatch,
):
    content = {"fact": "alpha"}
    intent = _intent(content)
    durable = _durable(
        authority_id="authority-pre-yield",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    _install(
        monkeypatch,
        reservation_repository=_ReservationRepository(durable),
        memory_repository=_MemoryRepository(),
        tx_enter_failure=SQLAlchemyError("BEGIN failed"),
    )

    with pytest.raises(PromotionAdmissionTransactionUnavailableError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation(durable.promotion_authority_id, intent),
            content=content,
        )


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_post_yield_commit_failure_maps_persistence_failure(
    monkeypatch,
):
    content = {"fact": "alpha"}
    intent = _intent(content)
    durable = _durable(
        authority_id="authority-commit-failure",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    _install(
        monkeypatch,
        reservation_repository=_ReservationRepository(durable),
        memory_repository=_MemoryRepository(),
        tx_exit_failure=SQLAlchemyError("commit failed"),
    )

    with pytest.raises(PromotionAdmissionPersistenceFailureError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation(durable.promotion_authority_id, intent),
            content=content,
        )


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_reservation_persistence_failure_maps_after_entry(
    monkeypatch,
):
    content = {"fact": "alpha"}
    intent = _intent(content)
    failure = PromotionReservationPersistenceUnavailableError("read failed")
    reservation_repo = _ReservationRepository(get_failure=failure)
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=_MemoryRepository(),
    )

    with pytest.raises(PromotionAdmissionPersistenceFailureError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation("authority-read-failure", intent),
            content=content,
        )


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_cancellation_propagates_unchanged(monkeypatch):
    content = {"fact": "alpha"}
    intent = _intent(content)
    reservation_repo = _ReservationRepository(
        get_failure=asyncio.CancelledError()
    )
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=_MemoryRepository(),
    )

    with pytest.raises(asyncio.CancelledError):
        await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
            reservation=_reservation("authority-cancel", intent),
            content=content,
        )


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_first_admission_thaws_nested_durable_metadata(monkeypatch):
    content = {"fact": "nested-metadata"}
    nested_metadata = {
        "labels": {"tier": ["gold", "verified"]},
        "flags": [True, {"source": "durable"}],
    }
    intent = _intent(content, suffix="nested-first", metadata=nested_metadata)
    durable = _durable(
        authority_id="authority-nested-first",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    reservation_repo = _ReservationRepository(durable)
    memory_repo = _MemoryRepository()
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=memory_repo,
    )

    winner = await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
        reservation=_reservation(durable.promotion_authority_id, intent),
        content=content,
    )

    assert winner.metadata["labels"]["tier"] == ("gold", "verified")
    assert winner.metadata["flags"][1]["source"] == "durable"
    assert memory_repo.put_calls == [winner]
    assert reservation_repo.consume_calls == [durable.promotion_authority_id]


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_consumed_replay_thaws_nested_durable_metadata(monkeypatch):
    content = {"fact": "nested-replay"}
    nested_metadata = {
        "labels": {"tier": ["gold", "verified"]},
        "flags": [True, {"source": "durable"}],
    }
    intent = _intent(content, suffix="nested-replay", metadata=nested_metadata)
    durable = _durable(
        authority_id="authority-nested-replay",
        intent=intent,
        state=DurablePromotionReservationState.CONSUMED,
    )
    existing = create_memory_record(
        source_ref=durable.intent.source_ref_snapshot,
        promotion_authority_id=durable.promotion_authority_id,
        content=content,
        metadata=nested_metadata,
        memory_schema_version=durable.intent.memory_schema_version,
    )
    reservation_repo = _ReservationRepository(durable)
    memory_repo = _MemoryRepository(existing=existing)
    _install(
        monkeypatch,
        reservation_repository=reservation_repo,
        memory_repository=memory_repo,
    )

    replay = await DurableMemoryPromotionAdmission(_SessionFactory()).admit(
        reservation=_reservation(durable.promotion_authority_id, intent),
        content={"fact": "nested-replay"},
    )

    assert replay.memory_id == existing.memory_id
    assert replay.metadata["labels"]["tier"] == ("gold", "verified")
    assert memory_repo.put_calls == []
    assert reservation_repo.consume_calls == []
