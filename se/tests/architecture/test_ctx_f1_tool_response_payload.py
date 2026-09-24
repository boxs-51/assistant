import asyncio

import pytest

from se.src.context.tool_response_payload import (
    InMemoryToolResponsePayloadRepository,
    ToolResponsePayloadConflictError,
    canonical_payload_bytes,
    create_tool_response_payload,
    tool_response_content_digest,
)


def _payload(
    *,
    source_result_id: str = "result-1",
    invocation_id: str = "invocation-1",
    content=None,
):
    return create_tool_response_payload(
        source_result_id=source_result_id,
        invocation_id=invocation_id,
        execution_id="execution-1",
        tool_call_id=f"call-{invocation_id}",
        logical_capability_id="web.search",
        content={"z": 2, "a": 1} if content is None else content,
        source_commit_state="COMMITTED",
        owner_user_id="user-1",
        session_id="session-1",
    )


def test_ctx_f1_canonical_digest_is_key_order_independent():
    left = {"z": 2, "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "z": 2}

    assert canonical_payload_bytes(left) == canonical_payload_bytes(right)
    assert tool_response_content_digest(left) == tool_response_content_digest(right)


def test_ctx_f1_rejects_non_committed_source():
    with pytest.raises(ValueError, match="Only COMMITTED"):
        create_tool_response_payload(
            source_result_id="result-1",
            invocation_id="invocation-1",
            execution_id="execution-1",
            tool_call_id="call-1",
            logical_capability_id="web.search",
            content={"value": 1},
            source_commit_state="PROVISIONAL",
        )


def test_ctx_f1_same_result_same_content_has_same_identity():
    first = _payload()
    second = _payload(content={"a": 1, "z": 2})

    assert first.payload_id == second.payload_id
    assert first.content_digest == second.content_digest


def test_ctx_f1_different_invocations_with_same_bytes_have_distinct_identity():
    first = _payload(source_result_id="result-1", invocation_id="invocation-1")
    second = _payload(source_result_id="result-2", invocation_id="invocation-2")

    assert first.content_digest == second.content_digest
    assert first.payload_id != second.payload_id


@pytest.mark.asyncio
async def test_ctx_f1_repository_true_replay_converges():
    repo = InMemoryToolResponsePayloadRepository()
    first = _payload()
    replay = _payload(content={"a": 1, "z": 2})

    stored = await repo.put(first)
    same = await repo.put(replay)

    assert same is stored
    assert await repo.get(first.payload_id) is stored
    assert await repo.get_by_source_result("result-1") is stored


@pytest.mark.asyncio
async def test_ctx_f1_repository_rejects_same_source_with_changed_content():
    repo = InMemoryToolResponsePayloadRepository()
    await repo.put(_payload(content={"value": 1}))

    with pytest.raises(ToolResponsePayloadConflictError, match="source_result_id"):
        await repo.put(_payload(content={"value": 2}))


@pytest.mark.asyncio
async def test_ctx_f1_concurrent_true_replay_converges():
    repo = InMemoryToolResponsePayloadRepository()
    first = _payload()
    replay = _payload(content={"a": 1, "z": 2})

    stored_first, stored_second = await asyncio.gather(
        repo.put(first),
        repo.put(replay),
    )

    assert stored_first.payload_id == stored_second.payload_id
    assert await repo.get_by_source_result("result-1") is stored_first


def test_ctx_f1_payload_content_and_metadata_are_deeply_immutable():
    payload = create_tool_response_payload(
        source_result_id="result-immutable",
        invocation_id="invocation-immutable",
        execution_id="execution-1",
        tool_call_id="call-immutable",
        logical_capability_id="web.search",
        content={"items": [{"value": 1}]},
        source_commit_state="COMMITTED",
        metadata={"trace": {"step": 1}},
    )

    assert payload.content["items"][0]["value"] == 1
    with pytest.raises(TypeError):
        payload.content["items"][0]["value"] = 2
    with pytest.raises(TypeError):
        payload.metadata["trace"]["step"] = 2

    dumped = payload.model_dump(mode="json")
    assert dumped["content"] == {"items": [{"value": 1}]}
    assert dumped["metadata"] == {"trace": {"step": 1}}
