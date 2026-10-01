from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.application.user_budget import (
    DualAccountingSettings,
    ToolQuotaSettings,
    UserBudgetDualAccountingService,
    UserToolCapabilityQuotaExceededError,
    UserToolQuotaExceededError,
    UserToolQuotaService,
)
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.user_budget import UserBudgetPolicy
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
    total_limit: int = 3,
    default_per_tool_limit: int = 2,
    tool_limits: dict[str, int] | None = None,
):
    database = tmp_path / "ubq3-tool-quota.sqlite"
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

    await asyncio.to_thread(_seed_user, database, "user-ubq3")
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
    service = UserToolQuotaService(
        factory,
        owner_authority=owner_authority,
        settings=ToolQuotaSettings(enabled=True),
    )
    policy = UserBudgetPolicy(
        policy_id="ubq3-policy",
        owner_user_id="user-ubq3",
        policy_version="ubq3-test-v1",
        window_duration_seconds=3600,
        max_compute_units=None,
        max_inference_calls=None,
        max_input_tokens=None,
        max_output_tokens=None,
        max_total_tokens=None,
        max_tool_calls_total=total_limit,
        default_per_tool_limit=default_per_tool_limit,
        tool_limits=dict(tool_limits or {}),
        max_cost_usd=None,
    )
    async with factory() as uow:
        await uow.user_budgets.create_or_get_immutable_policy(policy)
        account = await uow.user_budgets.create_or_get_account("user-ubq3")
        await uow.user_budgets.select_next_policy(
            "user-ubq3",
            expected_revision=int(account.revision),
            next_policy_id=policy.policy_id,
        )
        await uow.commit()

    identity = Identity(
        user_id="user-ubq3",
        auth_type="jwt",
        scopes={"*"},
    )
    return engine, factory, service, identity


async def _reserve(
    service: UserToolQuotaService,
    identity: Identity,
    *,
    invocation_id: str,
    capability_id: str = "tool.echo",
):
    return await service.reserve_tool_call(
        identity=identity,
        invocation_id=invocation_id,
        capability_id=capability_id,
        request_fingerprint=f"fp:{invocation_id}:{capability_id}",
        arguments={"value": invocation_id},
        session_id="session-ubq3",
    )


@pytest.mark.asyncio
async def test_ubq3_replay_before_rollover_reserves_and_settles_once(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        first = await _reserve(service, identity, invocation_id="inv-1")
        replay = await _reserve(service, identity, invocation_id="inv-1")
        assert first is not None and replay is not None
        assert first.reservation_id == replay.reservation_id
        assert first.window_epoch == replay.window_epoch
        assert replay.reservation_state == "RESERVED"

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq3", int(first.window_epoch)
            )
            usage = await uow.user_budgets.get_tool_usage(
                "user-ubq3", int(first.window_epoch), "tool.echo"
            )
            assert window is not None and usage is not None
            assert int(window.tool_calls_used) == 0
            assert int(window.tool_calls_reserved) == 1
            assert int(usage.used_calls) == 0
            assert int(usage.reserved_calls) == 1

        settled = await service.settle_tool_call(first)
        assert settled is not None
        assert settled.reservation_state == "SETTLED"
        settled_replay = await service.settle_tool_call(first)
        assert settled_replay is not None
        assert settled_replay.reservation_state == "SETTLED"

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq3", int(first.window_epoch)
            )
            usage = await uow.user_budgets.get_tool_usage(
                "user-ubq3", int(first.window_epoch), "tool.echo"
            )
            assert int(window.tool_calls_used) == 1
            assert int(window.tool_calls_reserved) == 0
            assert int(usage.used_calls) == 1
            assert int(usage.reserved_calls) == 0

        after_settle = await _reserve(
            service, identity, invocation_id="inv-1"
        )
        assert after_settle is not None
        assert after_settle.reservation_state == "SETTLED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq3_release_returns_reserved_capacity_exactly_once(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        admission = await _reserve(
            service, identity, invocation_id="inv-release"
        )
        assert admission is not None
        released = await service.release_tool_call(admission)
        assert released is not None
        assert released.reservation_state == "RELEASED"

        replay = await service.release_tool_call(admission)
        assert replay is not None
        assert replay.reservation_state == "RELEASED"

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq3", int(admission.window_epoch)
            )
            usage = await uow.user_budgets.get_tool_usage(
                "user-ubq3",
                int(admission.window_epoch),
                "tool.echo",
            )
            assert int(window.tool_calls_used) == 0
            assert int(window.tool_calls_reserved) == 0
            assert int(usage.used_calls) == 0
            assert int(usage.reserved_calls) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq3_per_capability_exhaustion_is_more_specific(
    tmp_path: Path,
) -> None:
    engine, _, service, identity = await _setup(
        tmp_path,
        total_limit=1,
        default_per_tool_limit=5,
        tool_limits={"tool.echo": 1},
    )
    try:
        first = await _reserve(service, identity, invocation_id="inv-cap-1")
        await service.settle_tool_call(first)
        with pytest.raises(UserToolCapabilityQuotaExceededError):
            await _reserve(service, identity, invocation_id="inv-cap-2")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq3_total_exhaustion_across_capabilities(
    tmp_path: Path,
) -> None:
    engine, _, service, identity = await _setup(
        tmp_path,
        total_limit=1,
        default_per_tool_limit=5,
    )
    try:
        first = await _reserve(
            service,
            identity,
            invocation_id="inv-total-1",
            capability_id="tool.a",
        )
        await service.settle_tool_call(first)
        with pytest.raises(UserToolQuotaExceededError):
            await _reserve(
                service,
                identity,
                invocation_id="inv-total-2",
                capability_id="tool.b",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq3_concurrent_same_invocation_creates_one_reservation(
    tmp_path: Path,
) -> None:
    engine, factory, service, identity = await _setup(tmp_path)
    try:
        left, right = await asyncio.gather(
            _reserve(service, identity, invocation_id="inv-race"),
            _reserve(service, identity, invocation_id="inv-race"),
        )
        assert left is not None and right is not None
        assert left.reservation_id == right.reservation_id
        assert left.window_epoch == right.window_epoch

        async with factory() as uow:
            window = await uow.user_budgets.get_window(
                "user-ubq3", int(left.window_epoch)
            )
            usage = await uow.user_budgets.get_tool_usage(
                "user-ubq3", int(left.window_epoch), "tool.echo"
            )
            assert int(window.tool_calls_reserved) == 1
            assert int(usage.reserved_calls) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq3_disabled_mode_performs_no_budget_mutation(
    tmp_path: Path,
) -> None:
    engine, factory, _, identity = await _setup(tmp_path)
    disabled = UserToolQuotaService(
        factory,
        owner_authority=UserBudgetDualAccountingService(
            factory,
            DualAccountingSettings(enabled=False),
        ),
        settings=ToolQuotaSettings(enabled=False),
    )
    try:
        admission = await _reserve(
            disabled,
            identity,
            invocation_id="inv-disabled",
        )
        assert admission is None
        async with factory() as uow:
            assert (
                await uow.user_budgets.get_active_window("user-ubq3")
                is None
            )
    finally:
        await engine.dispose()
