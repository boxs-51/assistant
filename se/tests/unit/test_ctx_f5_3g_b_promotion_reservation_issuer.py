from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionIntentIntegrityError,
    MemoryPromotionProofScope,
    PromotionReservationIssuer,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRecord,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
    PromotionReservationPersistenceUnavailableError,
    PromotionReservationRevokedError,
)
import se.src.infrastructure.storage.services.promotion_reservation_issuer as issuer_module
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)


NOW = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)


def _intent(*, suffix: str = "one") -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=f"session-{suffix}",
        owner_user_id="user-ctx-f5-3g-b",
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
        content_digest=f"content-{suffix}",
        metadata={"kind": "issuer"},
        memory_schema_version=1,
    )


def _record(
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


class _FakeSession:
    def __init__(self, *, commit_failure: BaseException | None = None) -> None:
        self.commit_failure = commit_failure
        self.commit_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_failure is not None:
            raise self.commit_failure


class _SessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.entered = False
        self.exited = False
        self.exit_exc_type = None

    async def __aenter__(self):
        self.entered = True
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        self.exit_exc_type = exc_type
        return False


class _SessionFactory:
    def __init__(self, *sessions: _FakeSession) -> None:
        self.sessions = list(sessions)
        self.calls = 0
        self.contexts: list[_SessionContext] = []

    def __call__(self):
        session = self.sessions[self.calls]
        self.calls += 1
        context = _SessionContext(session)
        self.contexts.append(context)
        return context


class _Repository:
    def __init__(
        self,
        *,
        winner: DurablePromotionReservationRecord | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.winner = winner
        self.failure = failure
        self.calls: list[tuple[str, MemoryPromotionIntent]] = []

    async def insert_or_converge_issued_candidate(
        self,
        *,
        promotion_authority_id: str,
        intent: MemoryPromotionIntent,
    ):
        self.calls.append((promotion_authority_id, intent))
        if self.failure is not None:
            raise self.failure
        assert self.winner is not None
        return self.winner


def _install_repository(monkeypatch, repository: _Repository):
    created_sessions = []

    def _factory(session):
        created_sessions.append(session)
        return repository

    monkeypatch.setattr(
        issuer_module,
        "DurablePromotionReservationRepository",
        _factory,
    )
    return created_sessions


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_implements_protocol_and_returns_durable_winner(
    monkeypatch,
):
    intent = _intent()
    winner = _record(
        authority_id="authority-canonical",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    repository = _Repository(winner=winner)
    created_sessions = _install_repository(monkeypatch, repository)
    session = _FakeSession()
    sessions = _SessionFactory(session)
    id_calls = []

    def _candidate_id():
        id_calls.append("called")
        return "authority-candidate"

    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=_candidate_id,
    )

    assert isinstance(issuer, PromotionReservationIssuer)
    reservation = await issuer.reserve(intent=intent)

    assert reservation.promotion_authority_id == "authority-canonical"
    assert reservation.intent == intent
    assert repository.calls == [("authority-candidate", intent)]
    assert id_calls == ["called"]
    assert sessions.calls == 1
    assert created_sessions == [session]
    assert session.commit_calls == 1
    assert sessions.contexts[0].entered
    assert sessions.contexts[0].exited


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_validates_intent_before_id_or_session_creation():
    session = _FakeSession()
    sessions = _SessionFactory(session)
    id_calls = []

    def _candidate_id():
        id_calls.append("called")
        return "authority-candidate"

    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=_candidate_id,
    )

    with pytest.raises(MemoryPromotionIntentIntegrityError):
        await issuer.reserve(intent=object())  # type: ignore[arg-type]

    assert id_calls == []
    assert sessions.calls == 0
    assert session.commit_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        (
            DurablePromotionReservationState.CONSUMED,
            PromotionReservationAlreadyConsumedError,
        ),
        (
            DurablePromotionReservationState.REVOKED,
            PromotionReservationRevokedError,
        ),
    ],
)
async def test_ctx_f5_3g_b_terminal_winner_fails_without_commit_or_substitution(
    monkeypatch,
    state,
    error_type,
):
    intent = _intent(suffix=state.value.lower())
    winner = _record(
        authority_id=f"authority-{state.value.lower()}",
        intent=intent,
        state=state,
    )
    repository = _Repository(winner=winner)
    _install_repository(monkeypatch, repository)
    session = _FakeSession()
    sessions = _SessionFactory(session)
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=lambda: "authority-new-candidate",
    )

    with pytest.raises(error_type):
        await issuer.reserve(intent=intent)

    assert repository.calls == [("authority-new-candidate", intent)]
    assert session.commit_calls == 0
    assert winner.state is state


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_repository_cancellation_propagates_without_retry(
    monkeypatch,
):
    intent = _intent()
    repository = _Repository(failure=asyncio.CancelledError())
    _install_repository(monkeypatch, repository)
    session = _FakeSession()
    sessions = _SessionFactory(session)
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=lambda: "authority-cancel",
    )

    with pytest.raises(asyncio.CancelledError):
        await issuer.reserve(intent=intent)

    assert repository.calls == [("authority-cancel", intent)]
    assert sessions.calls == 1
    assert session.commit_calls == 0
    assert sessions.contexts[0].exited


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_commit_failure_has_no_false_success(monkeypatch):
    intent = _intent()
    winner = _record(
        authority_id="authority-commit",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    repository = _Repository(winner=winner)
    _install_repository(monkeypatch, repository)
    session = _FakeSession(commit_failure=SQLAlchemyError("commit failed"))
    sessions = _SessionFactory(session)
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=lambda: "authority-commit",
    )

    with pytest.raises(PromotionReservationPersistenceUnavailableError):
        await issuer.reserve(intent=intent)

    assert len(repository.calls) == 1
    assert session.commit_calls == 1
    assert sessions.contexts[0].exited


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_each_invocation_owns_exactly_one_session(monkeypatch):
    intent = _intent()
    winner = _record(
        authority_id="authority-stable",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    repository = _Repository(winner=winner)
    created_sessions = _install_repository(monkeypatch, repository)
    session_one = _FakeSession()
    session_two = _FakeSession()
    sessions = _SessionFactory(session_one, session_two)
    candidate_ids = iter(("candidate-one", "candidate-two"))
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=lambda: next(candidate_ids),
    )

    first = await issuer.reserve(intent=intent)
    second = await issuer.reserve(intent=intent)

    assert first.promotion_authority_id == second.promotion_authority_id
    assert sessions.calls == 2
    assert created_sessions == [session_one, session_two]
    assert session_one.commit_calls == 1
    assert session_two.commit_calls == 1
    assert repository.calls == [
        ("candidate-one", intent),
        ("candidate-two", intent),
    ]


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_persistence_failure_propagates_without_commit(
    monkeypatch,
):
    intent = _intent(suffix="persistence-failure")
    failure = PromotionReservationPersistenceUnavailableError("unavailable")
    repository = _Repository(failure=failure)
    _install_repository(monkeypatch, repository)
    session = _FakeSession()
    sessions = _SessionFactory(session)
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=lambda: "authority-persistence-failure",
    )

    with pytest.raises(PromotionReservationPersistenceUnavailableError):
        await issuer.reserve(intent=intent)

    assert len(repository.calls) == 1
    assert session.commit_calls == 0
    assert sessions.contexts[0].exited


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_commit_cancellation_propagates_without_false_success(
    monkeypatch,
):
    intent = _intent(suffix="commit-cancel")
    winner = _record(
        authority_id="authority-commit-cancel",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    repository = _Repository(winner=winner)
    _install_repository(monkeypatch, repository)
    session = _FakeSession(commit_failure=asyncio.CancelledError())
    sessions = _SessionFactory(session)
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=lambda: "authority-commit-cancel",
    )

    with pytest.raises(asyncio.CancelledError):
        await issuer.reserve(intent=intent)

    assert len(repository.calls) == 1
    assert session.commit_calls == 1
    assert sessions.contexts[0].exited
