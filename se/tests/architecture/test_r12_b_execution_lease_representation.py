from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from se.src.domain.schemas.agent_execution import AgentExecution
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs/agent_execution_r12/"
    "R12_B_DURABLE_OWNER_LEASE_FENCE_REPRESENTATION.md"
)


def _execution(**updates) -> AgentExecution:
    values = {
        "execution_id": "exec-r12-b",
        "session_id": "session-r12-b",
        "agent_id": "agent-r12-b",
        "correlation_id": "corr-r12-b",
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    values.update(updates)
    return AgentExecution(**values)


def test_r12_b_domain_defaults_are_legacy_safe() -> None:
    execution = _execution()

    assert execution.owner_instance_id is None
    assert execution.lease_expires_at is None
    assert execution.lease_generation == 0


def test_r12_b_domain_accepts_valid_active_lease_representation() -> None:
    expiry = datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)
    execution = _execution(
        owner_instance_id="worker-incarnation-r12-b",
        lease_expires_at=expiry,
        lease_generation=7,
    )

    assert execution.owner_instance_id == "worker-incarnation-r12-b"
    assert execution.lease_expires_at == expiry
    assert execution.lease_generation == 7


@pytest.mark.parametrize(
    "updates",
    [
        {"owner_instance_id": "worker", "lease_generation": 1},
        {
            "lease_expires_at": datetime(
                2026, 9, 27, 1, 0, tzinfo=timezone.utc
            ),
            "lease_generation": 1,
        },
        {
            "owner_instance_id": "worker",
            "lease_expires_at": datetime(
                2026, 9, 27, 1, 0, tzinfo=timezone.utc
            ),
            "lease_generation": 0,
        },
        {"lease_generation": -1},
    ],
)
def test_r12_b_domain_rejects_invalid_lease_representation(updates) -> None:
    with pytest.raises(ValidationError):
        _execution(**updates)


def test_r12_b_sql_model_freezes_exact_fields_without_scanner_index() -> None:
    table = AgentExecutionRecord.__table__

    assert table.c.owner_instance_id.nullable is True
    assert table.c.lease_expires_at.nullable is True
    assert table.c.lease_generation.nullable is False
    assert table.c.lease_generation.server_default is not None

    assert table.c.owner_instance_id.index in (None, False)
    assert table.c.lease_expires_at.index in (None, False)
    assert table.c.lease_generation.index in (None, False)

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_agent_executions_lease_owner_expiry_pair" in checks
    assert "ck_agent_executions_lease_generation_nonnegative" in checks
    assert "ck_agent_executions_lease_owner_generation_positive" in checks


def test_r12_b_contract_keeps_runtime_authority_closed() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "22a_r12_execution_lease_fence" in text
    assert "21a_ctx_f5_memory_foundation" in text
    assert "R12-C" in text
    assert "R12-D" in text
    assert "R12-E" in text
    assert "R12-F" in text
    assert "R12-G" in text
    assert "R12-H" in text
    assert "R12-D owns stale-RUNNING query/scanner/index policy" in text
    assert "R12-B does not acquire, renew, release, scan, recover, dispatch" in text
    assert "parent-first integration" in text.lower()
