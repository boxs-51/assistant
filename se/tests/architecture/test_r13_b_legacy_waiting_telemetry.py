from __future__ import annotations

import inspect

import pytest

from cl.src.core import gateway_client
from se.src import main as server_main
from se.src.infrastructure.storage.repositories.agent import AgentRepository


class _CaptureLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict]] = []
        self.warnings: list[tuple[str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.infos.append((event, dict(kwargs)))

    def warning(self, event: str, **kwargs) -> None:
        self.warnings.append((event, dict(kwargs)))


def test_r13b_client_observes_only_legacy_waiting_wire_labels(monkeypatch) -> None:
    logger = _CaptureLogger()
    monkeypatch.setattr(gateway_client, "logger", logger)

    legacy_payload = {
        "status": "WAITING_FOR_CONNECTION",
        "execution_id": "must-not-be-logged",
        "message": "must-not-be-logged",
    }
    normalized = gateway_client.normalize_waiting_payload(legacy_payload)

    assert normalized["status"] == "WAITING"
    assert normalized["wait_reason"] == "CONNECTION"
    assert logger.infos == [
        (
            "ae_r13_legacy_waiting_wire_consumed",
            {
                "legacy_status": "WAITING_FOR_CONNECTION",
                "normalized_wait_reason": "CONNECTION",
            },
        )
    ]

    logger.infos.clear()
    canonical = gateway_client.normalize_waiting_payload(
        {"status": "WAITING", "wait_reason": "CONNECTION"}
    )
    assert canonical["status"] == "WAITING"
    assert logger.infos == []


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _CountingSession:
    def __init__(self) -> None:
        self.values = iter((2, 3, 5))
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(
            str(statement.compile(compile_kwargs={"literal_binds": True}))
        )
        return _ScalarResult(next(self.values))


@pytest.mark.asyncio
async def test_r13b_repository_probe_is_read_only_and_role_specific() -> None:
    session = _CountingSession()
    repository = AgentRepository(session)

    counts = await repository.count_legacy_waiting_residue()

    assert counts == {
        "task_waiting_for_connection": 2,
        "execution_waiting_for_connection": 3,
        "execution_waiting_agent": 5,
    }
    assert len(session.statements) == 3

    sql = "\n".join(session.statements)
    assert "agent_tasks.status = 'WAITING_FOR_CONNECTION'" in sql
    assert "agent_executions.state = 'WAITING_FOR_CONNECTION'" in sql
    assert "agent_executions.state = 'WAITING_AGENT'" in sql

    source = inspect.getsource(AgentRepository.count_legacy_waiting_residue)
    for forbidden in ("update(", "delete(", ".flush(", "with_for_update"):
        assert forbidden not in source


class _Agents:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def count_legacy_waiting_residue(self):
        if self.fail:
            raise RuntimeError("probe unavailable")
        return {
            "task_waiting_for_connection": 7,
            "execution_waiting_for_connection": 11,
            "execution_waiting_agent": 13,
        }


class _Uow:
    def __init__(self, *, fail: bool = False) -> None:
        self.agents = _Agents(fail=fail)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_r13b_startup_probe_logs_bounded_counts_and_fails_open(
    monkeypatch,
) -> None:
    logger = _CaptureLogger()
    monkeypatch.setattr(server_main, "logger", logger)

    await server_main._observe_legacy_waiting_residue(lambda: _Uow())

    assert logger.infos == [
        (
            "ae_r13_legacy_waiting_residue",
            {
                "task_waiting_for_connection": 7,
                "execution_waiting_for_connection": 11,
                "execution_waiting_agent": 13,
            },
        )
    ]

    logger.infos.clear()
    await server_main._observe_legacy_waiting_residue(
        lambda: _Uow(fail=True)
    )

    assert logger.infos == []
    assert logger.warnings == [
        (
            "ae_r13_legacy_waiting_residue_probe_failed",
            {"error_type": "RuntimeError"},
        )
    ]
