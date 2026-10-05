import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from se.src.infrastructure.storage.core.manager import StorageEngine


@pytest.mark.asyncio
async def test_b8_p1_handoff_resolves_once_and_forwards_exact_inputs() -> None:
    engine = StorageEngine.__new__(StorageEngine)
    service = Mock()
    result = object()
    service.promote = AsyncMock(return_value=result)
    resolver = Mock(return_value=service)
    engine.get_tool_response_payload_memory_promotion = resolver

    source_ref = object()
    owner_user_id = "user-b8-p1"

    actual = await engine.promote_tool_response_payload_memory(
        source_ref=source_ref,
        owner_user_id=owner_user_id,
    )

    assert actual is result
    resolver.assert_called_once_with()
    service.promote.assert_awaited_once_with(
        source_ref=source_ref,
        owner_user_id=owner_user_id,
    )


@pytest.mark.asyncio
async def test_b8_p1_handoff_resolves_fresh_service_each_invocation() -> None:
    engine = StorageEngine.__new__(StorageEngine)
    first = Mock()
    second = Mock()
    first_result = object()
    second_result = object()
    first.promote = AsyncMock(return_value=first_result)
    second.promote = AsyncMock(return_value=second_result)
    resolver = Mock(side_effect=[first, second])
    engine.get_tool_response_payload_memory_promotion = resolver

    source_ref = object()

    first_actual = await engine.promote_tool_response_payload_memory(
        source_ref=source_ref,
        owner_user_id="user-1",
    )
    second_actual = await engine.promote_tool_response_payload_memory(
        source_ref=source_ref,
        owner_user_id="user-2",
    )

    assert first_actual is first_result
    assert second_actual is second_result
    assert resolver.call_count == 2
    first.promote.assert_awaited_once_with(
        source_ref=source_ref,
        owner_user_id="user-1",
    )
    second.promote.assert_awaited_once_with(
        source_ref=source_ref,
        owner_user_id="user-2",
    )


@pytest.mark.asyncio
async def test_b8_p1_handoff_propagates_resolver_failure_unchanged() -> None:
    engine = StorageEngine.__new__(StorageEngine)
    failure = RuntimeError("resolver failed")
    engine.get_tool_response_payload_memory_promotion = Mock(
        side_effect=failure
    )

    with pytest.raises(RuntimeError) as caught:
        await engine.promote_tool_response_payload_memory(
            source_ref=object(),
            owner_user_id="user-failure",
        )

    assert caught.value is failure


@pytest.mark.asyncio
async def test_b8_p1_handoff_propagates_lower_layer_failure_unchanged() -> None:
    engine = StorageEngine.__new__(StorageEngine)
    service = Mock()
    failure = ValueError("promotion failed")
    service.promote = AsyncMock(side_effect=failure)
    engine.get_tool_response_payload_memory_promotion = Mock(
        return_value=service
    )

    with pytest.raises(ValueError) as caught:
        await engine.promote_tool_response_payload_memory(
            source_ref=object(),
            owner_user_id="user-failure",
        )

    assert caught.value is failure


@pytest.mark.asyncio
async def test_b8_p1_handoff_propagates_cancellation_unchanged() -> None:
    engine = StorageEngine.__new__(StorageEngine)
    service = Mock()
    service.promote = AsyncMock(side_effect=asyncio.CancelledError())
    engine.get_tool_response_payload_memory_promotion = Mock(
        return_value=service
    )

    with pytest.raises(asyncio.CancelledError):
        await engine.promote_tool_response_payload_memory(
            source_ref=object(),
            owner_user_id="user-cancel",
        )
