from __future__ import annotations

import asyncio
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.application.user_budget import (
    DualAccountingSettings,
    UserBudgetDualAccountingService,
)
from se.src.application.user_inference_quota import (
    InferenceQuotaSettings,
    NormalizedInferenceUsage,
    UserInferenceEstimateUnavailableError,
    UserInferenceQuotaConflictError,
    UserInferenceQuotaContextError,
    UserInferenceQuotaService,
    UserBudgetUnsupportedGovernedOperationError,
    derive_skill_inference_request_id,
)
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.user_budget import (
    UserBudgetPolicy,
    UserBudgetResourceKind,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.user_budget import UserBudgetRepository
from se.src.infrastructure.storage.repositories.user_data.users import UserRepository


ROOT = Path(__file__).resolve().parents[3]


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


def _seed_user(database: Path, user_id: str) -> None:
    raw = sqlite3.connect(database)
    try:
        raw.execute(
            "INSERT INTO users (id, email, password_hash, status) "
            "VALUES (?, ?, ?, 'active')",
            (user_id, f"{user_id}@example.test", "hash"),
        )
        raw.commit()
    finally:
        raw.close()


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.users = UserRepository(self.session)
        self.agents = AgentRepository(self.session)
        self.user_budgets = UserBudgetRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


async def _setup(
    tmp_path: Path,
    *,
    max_compute_units: Decimal | None = None,
    finite_governed_policy: bool = True,
):
    database = tmp_path / "ubq4-inference-quota.sqlite"
    env_key = "ASSISTANT_ALEMBIC_DATABASE_URL"
    previous_url = os.environ.get(env_key)
    os.environ[env_key] = f"sqlite+aiosqlite:///{database.as_posix()}"
    try:
        await asyncio.to_thread(command.upgrade, _config(database), "head")
    finally:
        if previous_url is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous_url

    await asyncio.to_thread(_seed_user, database, "user-ubq4")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)

    owner_authority = UserBudgetDualAccountingService(
        factory,
        DualAccountingSettings(enabled=False),
    )
    service = UserInferenceQuotaService(
        factory,
        owner_authority=owner_authority,
        settings=InferenceQuotaSettings(
            enabled=True,
            default_output_token_reservation=8,
        ),
    )
    policy = UserBudgetPolicy(
        policy_id="ubq4-policy",
        owner_user_id="user-ubq4",
        policy_version="ubq4-test-v1",
        window_duration_seconds=3600,
        max_compute_units=max_compute_units,
        max_inference_calls=(2 if finite_governed_policy else None),
        max_input_tokens=(100_000 if finite_governed_policy else None),
        max_output_tokens=(100_000 if finite_governed_policy else None),
        max_total_tokens=(200_000 if finite_governed_policy else None),
        max_tool_calls_total=None,
        default_per_tool_limit=None,
        tool_limits={},
        max_cost_usd=None,
    )
    async with factory() as uow:
        await uow.user_budgets.create_or_get_immutable_policy(policy)
        account = await uow.user_budgets.create_or_get_account("user-ubq4")
        await uow.user_budgets.select_next_policy(
            "user-ubq4",
            expected_revision=int(account.revision),
            next_policy_id=policy.policy_id,
        )
        await uow.commit()

    identity = Identity(
        user_id="user-ubq4",
        auth_type="jwt",
        scopes={"*"},
    )
    return engine, factory, service, identity


def _body(content: str = "hello") -> dict:
    return {
        "model": "mock-chat",
        "messages": [{"role": "user", "content": content}],
        "tools": [],
        "config": {"max_tokens": 4},
    }


def _context(service, identity, request_id="inf-ubq4"):
    return service.build_context(
        budget_identity=identity,
        logical_request_id=request_id,
        source_surface="TEST",
        session_id="session-ubq4",
        execution_id="exec-ubq4",
        iteration=1,
        agent_iteration_id="exec-ubq4:iteration:1",
    )


@pytest.mark.asyncio
async def test_ubq4_reserve_replay_and_known_settlement_are_exact_once(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        context = _context(service, identity)
        first = await service.reserve(
            context=context,
            body=_body(),
            streaming_mode=False,
        )
        replay = await service.reserve(
            context=context,
            body=_body(),
            streaming_mode=False,
        )
        assert first is not None and replay is not None
        assert first.replayed is False
        assert replay.replayed is True
        assert replay.inference_reservation_state == "RESERVED"
        assert replay.window_epoch == first.window_epoch
        assert dict(replay.reservation_keys) == dict(first.reservation_keys)

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq4", first.window_epoch
            )
            pending_input = await uow.user_budgets.sum_pending_reservation_amount(
                "user-ubq4",
                first.window_epoch,
                UserBudgetResourceKind.INPUT_TOKEN,
            )
            pending_output = await uow.user_budgets.sum_pending_reservation_amount(
                "user-ubq4",
                first.window_epoch,
                UserBudgetResourceKind.OUTPUT_TOKEN,
            )
            assert int(window.inference_reserved) == 1
            assert int(window.tokens_reserved) > 0
            assert pending_input > 0
            assert pending_output == 4

        usage = NormalizedInferenceUsage(
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            compute_units=None,
            cost_usd=None,
            normalization_identity=service.normalization_identity,
            provider="mock",
            model="mock-chat",
        )
        await service.settle_success(first, usage)
        await service.settle_success(first, usage)

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq4", first.window_epoch
            )
            assert int(window.inference_used) == 1
            assert int(window.inference_reserved) == 0
            assert int(window.input_tokens_used) == 2
            assert int(window.output_tokens_used) == 3
            assert int(window.total_tokens_used) == 5
            assert int(window.tokens_reserved) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq4_unknown_terminal_usage_is_not_silently_refunded(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        admission = await service.reserve(
            context=_context(service, identity, "inf-unknown"),
            body=_body(),
            streaming_mode=True,
        )
        usage = NormalizedInferenceUsage(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            compute_units=None,
            cost_usd=None,
            normalization_identity=service.normalization_identity,
            provider="openai",
            model="mock-chat",
        )
        await service.settle_success(admission, usage)

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq4", admission.window_epoch
            )
            assert int(window.inference_used) == 1
            assert int(window.inference_reserved) == 0
            assert int(window.total_tokens_used) == 0
            assert int(window.tokens_reserved) > 0
            total_key = admission.reservation_keys[
                UserBudgetResourceKind.TOTAL_TOKEN.value
            ]
            row = await uow.user_budgets.get_reservation(
                "user-ubq4", total_key
            )
            assert row.state == "RESERVED"
    finally:
        await engine.dispose()


def test_ubq4_transport_config_is_not_logical_semantic_authority() -> None:
    class _OwnerAuthority:
        pass

    service = UserInferenceQuotaService(
        lambda: None,
        owner_authority=_OwnerAuthority(),
        settings=InferenceQuotaSettings(enabled=True),
    )
    identity = Identity(user_id="user-ubq4", auth_type="jwt", scopes={"*"})
    context = service.build_context(
        budget_identity=identity,
        logical_request_id="inf-config",
        source_surface="TEST",
        session_id="session-ubq4",
    )
    left = _body()
    left["config"].update({"stream": False, "agent_activity_stream": False})
    right = _body()
    right["config"].update({"stream": True, "agent_activity_stream": True})

    assert service.logical_fingerprint(
        left,
        context,
        streaming_mode=True,
    ) == service.logical_fingerprint(
        right,
        context,
        streaming_mode=True,
    )
    assert service.logical_fingerprint(
        left,
        context,
        streaming_mode=False,
    ) != service.logical_fingerprint(
        left,
        context,
        streaming_mode=True,
    )


def test_ubq4_unfrozen_config_key_fails_closed() -> None:
    class _OwnerAuthority:
        pass

    service = UserInferenceQuotaService(
        lambda: None,
        owner_authority=_OwnerAuthority(),
        settings=InferenceQuotaSettings(enabled=True),
    )
    identity = Identity(user_id="user-ubq4", auth_type="jwt", scopes={"*"})
    context = service.build_context(
        budget_identity=identity,
        logical_request_id="inf-new-config",
        source_surface="TEST",
    )
    body = _body()
    body["config"]["unreviewed_semantic_option"] = 1
    with pytest.raises(UserInferenceQuotaContextError):
        service.logical_fingerprint(
            body,
            context,
            streaming_mode=False,
        )


@pytest.mark.asyncio
async def test_ubq4_binary_float_semantics_fail_before_quota_mutation(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        body = _body()
        body["config"]["temperature"] = 0.1
        with pytest.raises(UserInferenceQuotaContextError):
            await service.reserve(
                context=_context(service, identity, "inf-float"),
                body=body,
                streaming_mode=False,
            )
        async with factory() as uow:
            assert await uow.user_budgets.get_active_window("user-ubq4") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq4_same_request_id_changed_semantics_conflicts_before_charge(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        context = _context(service, identity, "inf-conflict")
        first = await service.reserve(
            context=context,
            body=_body("first"),
            streaming_mode=False,
        )
        with pytest.raises(UserInferenceQuotaConflictError):
            await service.reserve(
                context=context,
                body=_body("changed"),
                streaming_mode=False,
            )
        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq4", first.window_epoch
            )
            assert int(window.inference_reserved) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq4_embedding_fence_blocks_selected_finite_policy(
    tmp_path: Path,
) -> None:
    engine, _factory, service, identity = await _setup(tmp_path)
    try:
        with pytest.raises(UserBudgetUnsupportedGovernedOperationError):
            await service.require_embedding_allowed(
                budget_identity=identity,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq4_embedding_fence_allows_unlimited_governed_policy(
    tmp_path: Path,
) -> None:
    engine, _factory, service, identity = await _setup(
        tmp_path,
        finite_governed_policy=False,
    )
    try:
        await service.require_embedding_allowed(
            budget_identity=identity,
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq4_compatibility_owner_mismatch_fails_before_charge(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        context = service.build_context(
            budget_identity=identity,
            logical_request_id="inf-owner-mismatch",
            source_surface="TEST",
            owner_user_id="different-user",
            session_id="session-ubq4",
        )
        with pytest.raises(UserInferenceQuotaContextError):
            await service.reserve(
                context=context,
                body=_body(),
                streaming_mode=False,
            )
        async with factory() as uow:
            window = await uow.user_budgets.get_active_window("user-ubq4")
            assert window is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq4_finite_compute_limit_without_estimator_fails_closed(
    tmp_path: Path,
) -> None:
    engine, _factory, service, identity = await _setup(
        tmp_path,
        max_compute_units=Decimal("10"),
    )
    try:
        with pytest.raises(UserInferenceEstimateUnavailableError):
            await service.reserve(
                context=_context(service, identity, "inf-compute"),
                body=_body(),
                streaming_mode=False,
            )
    finally:
        await engine.dispose()


def test_ubq4_skill_inference_identity_is_invocation_local_and_stable() -> None:
    first = derive_skill_inference_request_id("inv-same")
    assert first == derive_skill_inference_request_id("inv-same")
    assert first != derive_skill_inference_request_id("inv-other")
    assert first.startswith("skillinf:v1:")
