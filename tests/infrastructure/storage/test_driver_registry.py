import pytest

from src.infrastructure.storage.core.registry import DriverRegistry


class FakeDriver:
    def __init__(self, fail_connect=False):
        self.fail_connect = fail_connect
        self.connected = False
        self.disconnected = False

    async def connect(self):
        if self.fail_connect:
            raise RuntimeError("connect failed")
        self.connected = True

    async def disconnect(self):
        self.connected = False
        self.disconnected = True


@pytest.mark.asyncio
async def test_connect_all_rolls_back_previous_drivers():
    registry = DriverRegistry()

    first = FakeDriver()
    second = FakeDriver(fail_connect=True)

    registry.register("first", first)
    registry.register("second", second)

    with pytest.raises(RuntimeError, match="connect failed"):
        await registry.connect_all()

    assert first.connected is False
    assert first.disconnected is True


@pytest.mark.asyncio
async def test_disconnect_all_continues_after_failure():
    registry = DriverRegistry()

    class BrokenDriver(FakeDriver):
        async def disconnect(self):
            self.disconnected = True
            raise RuntimeError("disconnect failed")

    broken = BrokenDriver()
    healthy = FakeDriver()

    registry.register("broken", broken)
    registry.register("healthy", healthy)

    await registry.connect_all()
    await registry.disconnect_all()

    assert broken.disconnected is True
    assert healthy.disconnected is True