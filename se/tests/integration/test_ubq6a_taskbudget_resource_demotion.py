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
    UserBudgetPolicyAuthorityConflictError,
)
from se.src.application.user_inference_quota import (
    InferenceQuotaSettings,
    UserInferenceQuotaService,
)
from se.src.application.user_tool_quota import (
    ToolQuotaSettings,
    UserToolQuotaService,
)
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.domain.schemas.user_budget import UserBudgetPolicy
from se.src.infrastructure.storage.models.sql.agent import AgentTaskRecord
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.user_budget import UserBudgetRepository
from se.src.infrastructure.storage.repositories.user_data.users import UserRepository
from se.src.runtimes.agent.task_budget import (
    TaskBudgetExceededError,
    TaskBudgetService,
)


ROOT = Path(__file__).resolve().parents[3]


class _TaskUow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
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


class _QuotaUow:
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


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=4,
        max_active_executions=2,
        max_active_branches=2,
        max_parallel_agents=1,
        max_total_tool_calls=1,
        max_total_inference_calls=1,
        max_total_tokens=1,
        max_total_cost_usd=Decimal("1"),
        max_delegation_depth=2,
    )


async def _task_setup(
    tmp_path: Path,
    *,
    name: str,
    tool_enabled: bool,
    inference_enabled: bool,
):
    database = tmp_path / f"{name}.sqlite"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _TaskUow(sessions)
    service = TaskBudgetService(
        factory,
        user_tool_quota_enabled=tool_enabled,
        user_inference_quota_enabled=inference_enabled,
    )
    return engine, sessions, factory, service


async def _add_task(sessions, task_id: str) -> None:
    async with sessions() as session:
        session.add(
            AgentTaskRecord(
                id=task_id,
                session_id=f"session-{task_id}",
                created_by="user-ubq6a",
                assigned_agent_id="agent-ubq6a",
                status="CREATED",
                input={},
            )
        )
        await session.commit()


def _tool_call(call_id: str) -> dict:
    return {
        "tool_call_id": call_id,
        "capability_id": "tool.echo",
        "arguments": {"call_id": call_id},
    }


@pytest.mark.asyncio
async def test_ubq6a_feature_off_preserves_finite_taskbudget_guards(
    tmp_path: Path,
) -> None:
    engine, sessions, _factory, service = await _task_setup(
        tmp_path,
        name="feature-off",
        tool_enabled=False,
        inference_enabled=False,
    )
    try:
        await _add_task(sessions, "task-off")
        await service.ensure_budget(
            "task-off",
            _limits(),
            TaskBudgetPolicy(version="ubq6a-off"),
        )

        await service.reserve_tool_call_batch(
            "task-off",
            [_tool_call("tool-1")],
        )
        with pytest.raises(TaskBudgetExceededError):
            await service.reserve_tool_call_batch(
                "task-off",
                [_tool_call("tool-2")],
            )

        await service.reserve_inference("task-off", request_id="inf-1")
        await service.account_usage(
            "task-off",
            usage_key="usage-1",
            tokens=1,
            cost_usd="1",
        )
        with pytest.raises(TaskBudgetExceededError):
            await service.reserve_inference("task-off", request_id="inf-2")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq6a_canonical_modes_saturate_compatibility_counters_and_replay(
    tmp_path: Path,
) -> None:
    engine, sessions, factory, service = await _task_setup(
        tmp_path,
        name="canonical-on",
        tool_enabled=True,
        inference_enabled=True,
    )
    try:
        await _add_task(sessions, "task-on")
        initial = await service.ensure_budget(
            "task-on",
            _limits(),
            TaskBudgetPolicy(version="ubq6a-on"),
        )
        fingerprint = initial.policy_fingerprint

        await service.reserve_tool_call_batch(
            "task-on",
            [_tool_call("tool-1")],
        )
        second_tool = await service.reserve_tool_call_batch(
            "task-on",
            [_tool_call("tool-2")],
        )
        replay_tool = await service.reserve_tool_call_batch(
            "task-on",
            [_tool_call("tool-2")],
        )
        assert second_tool.used_tool_calls == 1
        assert replay_tool.used_tool_calls == 1

        await service.reserve_inference("task-on", request_id="inf-1")
        await service.account_usage(
            "task-on",
            usage_key="usage-1",
            tokens=1,
            cost_usd="1",
        )
        second_inference = await service.reserve_inference(
            "task-on",
            request_id="inf-2",
        )
        replay_inference = await service.reserve_inference(
            "task-on",
            request_id="inf-2",
        )
        assert second_inference.used_inference_calls == 1
        assert replay_inference.used_inference_calls == 1

        current = await service.get_budget("task-on")
        assert current.policy_fingerprint == fingerprint
        assert current.used_tokens == 1
        assert current.used_cost_usd == Decimal("1.00000000")

        legacy = TaskBudgetService(
            factory,
            user_tool_quota_enabled=False,
            user_inference_quota_enabled=False,
        )
        with pytest.raises(TaskBudgetExceededError):
            await legacy.reserve_tool_call_batch(
                "task-on",
                [_tool_call("tool-3")],
            )
        with pytest.raises(TaskBudgetExceededError):
            await legacy.reserve_inference("task-on", request_id="inf-3")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_enabled", "inference_enabled", "tool_allows_second", "inf_allows_second"),
    (
        (True, False, True, False),
        (False, True, False, True),
    ),
)
async def test_ubq6a_tool_and_inference_demotion_switches_are_independent(
    tmp_path: Path,
    tool_enabled: bool,
    inference_enabled: bool,
    tool_allows_second: bool,
    inf_allows_second: bool,
) -> None:
    engine, sessions, _factory, service = await _task_setup(
        tmp_path,
        name=f"independent-{int(tool_enabled)}-{int(inference_enabled)}",
        tool_enabled=tool_enabled,
        inference_enabled=inference_enabled,
    )
    try:
        task_id = f"task-{int(tool_enabled)}-{int(inference_enabled)}"
        await _add_task(sessions, task_id)
        await service.ensure_budget(task_id, _limits())

        await service.reserve_tool_call_batch(
            task_id,
            [_tool_call("tool-1")],
        )
        if tool_allows_second:
            second = await service.reserve_tool_call_batch(
                task_id,
                [_tool_call("tool-2")],
            )
            assert second.used_tool_calls == 1
        else:
            with pytest.raises(TaskBudgetExceededError):
                await service.reserve_tool_call_batch(
                    task_id,
                    [_tool_call("tool-2")],
                )

        await service.reserve_inference(task_id, request_id="inf-1")
        if inf_allows_second:
            second_inf = await service.reserve_inference(
                task_id,
                request_id="inf-2",
            )
            assert second_inf.used_inference_calls == 1
        else:
            with pytest.raises(TaskBudgetExceededError):
                await service.reserve_inference(task_id, request_id="inf-2")
    finally:
        await engine.dispose()


def _alembic_config(database: Path) -> Config:
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


async def _quota_setup(tmp_path: Path, name: str, user_id: str):
    database = tmp_path / f"{name}.sqlite"
    env_key = "ASSISTANT_ALEMBIC_DATABASE_URL"
    previous_url = os.environ.get(env_key)
    os.environ[env_key] = f"sqlite+aiosqlite:///{database.as_posix()}"
    try:
        await asyncio.to_thread(
            command.upgrade,
            _alembic_config(database),
            "head",
        )
    finally:
        if previous_url is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous_url

    await asyncio.to_thread(_seed_user, database, user_id)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _QuotaUow(sessions)
    owner = UserBudgetDualAccountingService(
        factory,
        DualAccountingSettings(enabled=True),
    )
    identity = Identity(user_id=user_id, auth_type="jwt", scopes={"*"})
    return engine, factory, owner, identity


def _inference_body() -> dict:
    return {
        "model": "mock-chat",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [],
        "config": {"max_tokens": 4},
    }


def _inference_context(
    service: UserInferenceQuotaService,
    identity: Identity,
    request_id: str,
):
    return service.build_context(
        budget_identity=identity,
        logical_request_id=request_id,
        source_surface="UBQ6A_TEST",
        session_id="session-ubq6a",
        execution_id="exec-ubq6a",
        iteration=1,
        agent_iteration_id="exec-ubq6a:iteration:1",
    )


@pytest.mark.asyncio
async def test_ubq6a_recognized_shadow_policy_fails_new_quota_admission_closed(
    tmp_path: Path,
) -> None:
    engine, factory, owner, identity = await _quota_setup(
        tmp_path,
        "shadow-policy",
        "user-shadow",
    )
    try:
        async with factory() as uow:
            await owner.ensure_shadow_account_in_uow(uow, "user-shadow")
            await uow.commit()

        tool = UserToolQuotaService(
            factory,
            owner_authority=owner,
            settings=ToolQuotaSettings(enabled=True),
        )
        with pytest.raises(UserBudgetPolicyAuthorityConflictError):
            await tool.reserve_tool_call(
                identity=identity,
                invocation_id="inv-shadow",
                capability_id="tool.echo",
                request_fingerprint="fp-shadow",
                arguments={"value": 1},
            )

        inference = UserInferenceQuotaService(
            factory,
            owner_authority=owner,
            settings=InferenceQuotaSettings(
                enabled=True,
                default_output_token_reservation=4,
            ),
        )
        with pytest.raises(UserBudgetPolicyAuthorityConflictError):
            await inference.reserve(
                context=_inference_context(
                    inference,
                    identity,
                    "inf-shadow",
                ),
                body=_inference_body(),
                streaming_mode=False,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq6a_explicit_nonshadow_unbounded_policy_is_canonical_authority(
    tmp_path: Path,
) -> None:
    engine, factory, owner, identity = await _quota_setup(
        tmp_path,
        "explicit-policy",
        "user-explicit",
    )
    try:
        policy = UserBudgetPolicy(
            policy_id="ubq6a-explicit-unbounded",
            owner_user_id="user-explicit",
            policy_version="ubq6a-explicit-v1",
            window_duration_seconds=3600,
            max_compute_units=None,
            max_inference_calls=None,
            max_input_tokens=None,
            max_output_tokens=None,
            max_total_tokens=None,
            max_tool_calls_total=None,
            default_per_tool_limit=None,
            tool_limits={},
            max_cost_usd=None,
        )
        async with factory() as uow:
            await uow.user_budgets.create_or_get_immutable_policy(policy)
            account = await uow.user_budgets.create_or_get_account(
                "user-explicit"
            )
            await uow.user_budgets.select_next_policy(
                "user-explicit",
                expected_revision=int(account.revision),
                next_policy_id=policy.policy_id,
            )
            await uow.commit()

        tool = UserToolQuotaService(
            factory,
            owner_authority=owner,
            settings=ToolQuotaSettings(enabled=True),
        )
        tool_admission = await tool.reserve_tool_call(
            identity=identity,
            invocation_id="inv-explicit",
            capability_id="tool.echo",
            request_fingerprint="fp-explicit",
            arguments={"value": 1},
        )
        assert tool_admission is not None

        inference = UserInferenceQuotaService(
            factory,
            owner_authority=owner,
            settings=InferenceQuotaSettings(
                enabled=True,
                default_output_token_reservation=4,
            ),
        )
        inference_admission = await inference.reserve(
            context=_inference_context(
                inference,
                identity,
                "inf-explicit",
            ),
            body=_inference_body(),
            streaming_mode=False,
        )
        assert inference_admission is not None
    finally:
        await engine.dispose()
