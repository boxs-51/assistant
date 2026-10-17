"""Nonwired AIC P1A architecture fences against AE/CTX/CAS/UBQ authority drift."""

from pathlib import Path
import importlib
import inspect

from se.src.application.agent_instance import AgentInstanceService
from se.src.domain.schemas.agent_instance import AgentInstanceLifecycle
from se.src.infrastructure.storage.models.sql.agent.agent_instance import (
    AgentInstanceLiveRecord,
    AgentInstanceReservationRecord,
)
from se.src.infrastructure.storage.repositories.agent_instance import AgentInstanceRepository

ROOT = Path(__file__).resolve().parents[3]


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_aic0_p1_nonwired_identity_is_not_execution_worker_lease():
    assert AgentInstanceLifecycle.ACTIVE.value == "ACTIVE"
    assert AgentInstanceLifecycle.SUSPENDED.value == "SUSPENDED"
    assert AgentInstanceLifecycle.DELETED.value == "DELETED"
    from se.src.domain.schemas.agent_execution import AgentExecution
    assert "owner_instance_id" in AgentExecution.model_fields
    assert "agent_instance_id" not in AgentExecution.model_fields
    assert "AgentExecution" not in inspect.getsource(AgentInstanceRepository)
    assert "AgentExecution" not in inspect.getsource(AgentInstanceService)


def test_aic0_p1_retired_marker_has_no_owner_or_authority():
    marker = AgentInstanceReservationRecord.__table__
    live = AgentInstanceLiveRecord.__table__
    assert "owner_user_id" not in marker.c
    assert "definition_id" not in marker.c
    assert "owner_user_id" in live.c
    assert "definition_id" in live.c
    assert "agent_instance_id" in marker.primary_key.columns.keys()


def test_aic0_p1_one_caller_session_and_no_commit_or_runtime_activator():
    repository = inspect.getsource(AgentInstanceRepository)
    assert "AsyncSession(" not in repository
    assert ".commit(" not in repository
    assert "BEGIN IMMEDIATE" in repository
    assert "with_for_update(read=True)" in repository
    uow = _source("se/src/infrastructure/storage/core/unit_of_work.py")
    assert uow.count("self.agent_instances = AgentInstanceRepository(self.session)") == 1
    assert "agent_instances: AgentInstanceRepository" in uow
    facade = inspect.getsource(AgentInstanceService)
    assert 'identity.auth_type != "jwt"' in facade
    for relative in (
        "se/src/transport/gateway/api/v1/agent_router.py",
        "se/src/runtimes/agent/resolver.py",
        "se/src/runtimes/agent/persistence.py",
        "se/src/infrastructure/storage/repositories/user_budget.py",
    ):
        text = _source(relative)
        assert "AgentInstanceService" not in text
        assert "AgentInstanceRepository" not in text


def test_aic0_p1_owner_delete_guard_and_immutable_predecessors():
    migration = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions.31a_aic0_agent_instance_identity"
    )
    assert migration.down_revision == "30a_ctx_f5_user_wide_memory_scope"
    assert "trg_aic_user_delete" in "\n".join(migration._SQLITE_GUARDS)
    assert "trg_aic_live_insert" in "\n".join(migration._SQLITE_GUARDS)
    assert "RETIRING" in "\n".join(migration._PG_FUNCTIONS)
    assert "DROP TRIGGER IF EXISTS" in inspect.getsource(migration.downgrade)
    assert "COUNT(*) FROM aic_instance_reservations" in inspect.getsource(migration.downgrade)
