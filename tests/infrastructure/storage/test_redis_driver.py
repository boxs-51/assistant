import pytest
from unittest.mock import AsyncMock, patch

from src.infrastructure.storage.drivers.redis.driver import RedisDriver


@pytest.mark.asyncio
async def test_connect_success():
    client = AsyncMock()
    client.ping.return_value = True

    with patch(
        "src.infrastructure.storage.drivers.redis.driver.redis.from_url",
        return_value=client,
    ):
        driver = RedisDriver({
            "url": "redis://localhost:6379/0",
        })

        await driver.connect()

        assert driver.is_connected is True
        client.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_failure_closes_client_and_raises():
    client = AsyncMock()
    client.ping.side_effect = RuntimeError("redis unavailable")

    with patch(
        "src.infrastructure.storage.drivers.redis.driver.redis.from_url",
        return_value=client,
    ):
        driver = RedisDriver({
            "url": "redis://localhost:6379/0",
        })

        with pytest.raises(RuntimeError, match="redis unavailable"):
            await driver.connect()

        assert driver.is_connected is False
        assert driver._client is None
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_is_idempotent():
    client = AsyncMock()
    client.ping.return_value = True

    with patch(
        "src.infrastructure.storage.drivers.redis.driver.redis.from_url",
        return_value=client,
    ):
        driver = RedisDriver({
            "url": "redis://localhost:6379/0",
        })

        await driver.connect()
        await driver.disconnect()
        await driver.disconnect()

        assert driver.is_connected is False
        assert driver._client is None
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_uses_cache_driver():
    client = AsyncMock()
    client.ping.return_value = True
    client.get.return_value = "value"

    with patch(
        "src.infrastructure.storage.drivers.redis.driver.redis.from_url",
        return_value=client,
    ):
        driver = RedisDriver({
            "url": "redis://localhost:6379/0",
        })
        await driver.connect()
        result = await driver.get("key")

        assert result == "value"
        client.get.assert_awaited_once_with("key")


@pytest.mark.asyncio
async def test_execute_script_falls_back_to_eval_on_noscript():
    client = AsyncMock()
    client.ping.return_value = True

    noscript = __import__(
        "redis.exceptions",
        fromlist=["NoScriptError"],
    ).NoScriptError("NOSCRIPT")

    client.evalsha.side_effect = noscript
    client.eval.return_value = [1, 99, 0]

    with patch(
        "src.infrastructure.storage.drivers.redis.driver.redis.from_url",
        return_value=client,
    ):
        driver = RedisDriver({
            "url": "redis://localhost:6379/0",
        })

        await driver.connect()

        result = await driver.execute_script(
            "return {1, 99, 0}",
            ["rate:key"],
            [1, 2, 3],
        )

        assert result == [1, 99, 0]
        client.evalsha.assert_awaited_once()
        client.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_operation_before_connect_fails_fast():
    driver = RedisDriver({
        "url": "redis://localhost:6379/0",
    })

    with pytest.raises(RuntimeError, match="not connected"):
        await driver.get("key")