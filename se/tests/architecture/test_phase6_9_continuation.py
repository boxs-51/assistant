from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace

import pytest

from se.src.application.container import ApplicationContainer
from se.src.runtimes.agent import contracts as agent_contracts
from se.src.runtimes.agent.contracts.result import AgentExecutionResult
from se.src.runtimes.agent.legacy_materialization import (
    LegacyCheckpointMaterializationError,
    parse_legacy_checkpoint_source,
    sanitize_legacy_transcript,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.workflow.runtime import WorkflowRuntime
from se.src.transport.gateway.api.v1 import events_router


def _legacy_execution(*, client_id="client-1", owner="user-1"):
    checkpoint_id = "legacy-cp-1"
    return SimpleNamespace(
        id="exec-1",
        session_id="session-1",
        state="WAITING",
        wait_reason="CONNECTION",
        revision=7,
        context_state={
            "continuation": {
                "current_checkpoint_id": checkpoint_id,
                "checkpoints": {
                    checkpoint_id: {
                        "checkpoint_id": checkpoint_id,
                        "execution_id": "exec-1",
                        "session_id": "session-1",
                        "reason": "WAITING_FOR_CONNECTION",
                        "state": "WAITING",
                        "wait_reason": "CONNECTION",
                        "parent_checkpoint_id": None,
                        "origin_connection_id": "conn-k1",
                        "current_connection_id": None,
                        "pending_invocation_id": "inv-1",
                        "pending_tool_call_id": "call-1",
                        "pending_capability_id": "tool.echo",
                        "iteration": 2,
                        "transcript": [
                            {"role": "user", "content": "go"},
                            {
                                "role": "tool",
                                "tool_call_id": "call-1",
                                "content": {
                                    "error_code": "REMOTE_OUTCOME_UNKNOWN"
                                },
                            },
                        ],
                        "metadata": {
                            "owner_user_id": owner,
                            "origin_client_id": client_id,
                            "server_continuation_available": True,
                        },
                    }
                },
                "branches": {},
            }
        },
    )


def test_r7_i_legacy_parser_is_read_only_identity_gate():
    execution = _legacy_execution()
    source = parse_legacy_checkpoint_source(
        execution,
        requested_checkpoint_id="legacy-cp-1",
        target_user_id="user-1",
        target_client_id="client-1",
    )
    assert source.execution_id == "exec-1"
    assert source.checkpoint_id == "legacy-cp-1"
    assert source.wait_reason == "CONNECTION"
    assert source.origin_client_id == "client-1"
    assert source.legacy_source_key.startswith("phase6.9:")


def test_r7_i_legacy_parser_rejects_foreign_client():
    with pytest.raises(
        LegacyCheckpointMaterializationError,
        match="stable client",
    ) as exc:
        parse_legacy_checkpoint_source(
            _legacy_execution(client_id="client-a"),
            target_user_id="user-1",
            target_client_id="client-b",
        )
    assert exc.value.code == "FOREIGN_CLIENT"


def test_r7_i_sanitizes_current_batch_tool_outcomes():
    source = parse_legacy_checkpoint_source(_legacy_execution())
    transcript = sanitize_legacy_transcript(
        source.transcript,
        active_tool_call_ids=("call-1",),
    )
    assert transcript == ({"role": "user", "content": "go"},)


def test_r7_i_removes_phase6_9_runtime_authority_surface():
    signature = inspect.signature(AgentRuntime.__init__)
    assert "continuation_service" not in signature.parameters
    assert "continuation_service" not in ApplicationContainer.__annotations__
    assert "continuation_state" not in AgentExecutionResult.model_fields

    for legacy_name in (
        "CheckpointReason",
        "ContinuationBranch",
        "ContinuationState",
        "ExecutionCheckpoint",
    ):
        assert not hasattr(agent_contracts, legacy_name)

    source = inspect.getsource(events_router._resume_execution)
    assert "confirm_merge" not in source
    assert "service.reconnect" not in source
    assert "continuation_service" not in source

    workflow_source = inspect.getsource(WorkflowRuntime._execute_agent)
    assert "continuation_service" not in workflow_source
    assert "current_checkpoint(" not in workflow_source

    assert not hasattr(DurableAgentStore, "save_continuation_state")
    assert not hasattr(DurableAgentStore, "load_continuation_state")

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("se.src.runtimes.agent.continuation")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("se.src.runtimes.agent.contracts.continuation")
