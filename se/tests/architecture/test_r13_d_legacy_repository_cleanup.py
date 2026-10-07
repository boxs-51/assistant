from __future__ import annotations

import inspect

from se.src.infrastructure.storage.repositories.agent import AgentRepository


def test_r13_d1_dead_legacy_repository_helpers_are_retired() -> None:
    source = inspect.getsource(AgentRepository)

    assert "async def list_legacy_waiting_executions_for_owner(" not in source
    assert "async def bind_legacy_checkpoint_pointer(" not in source

    assert hasattr(AgentRepository, "count_legacy_waiting_residue")
    assert hasattr(AgentRepository, "list_waiting_executions_for_client")


def test_r13_d1_preserves_r13_b_residue_observation_boundary() -> None:
    source = inspect.getsource(AgentRepository.count_legacy_waiting_residue)

    assert 'AgentTaskRecord.status == "WAITING_FOR_CONNECTION"' in source
    assert 'AgentExecutionRecord.state == "WAITING_FOR_CONNECTION"' in source
    assert 'AgentExecutionRecord.state == "WAITING_AGENT"' in source

    for forbidden in ("update(", "delete(", ".flush(", "with_for_update"):
        assert forbidden not in source
