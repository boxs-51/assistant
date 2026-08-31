import pytest

from src.kernel.base import LifecycleState
from src.kernel.kernel import RuntimeKernel


class FakeRuntime:
    class _Manifest:
        id = "fake_runtime"

    def __init__(self):
        self.manifest = self._Manifest()
        self.state = LifecycleState.RUNNING
        self.stop_calls = 0
        self.initialize_calls = 0
        self.start_calls = 0
        self.dispose_calls = 0
        self.initialized_context = None

    async def stop(self):
        self.stop_calls = 1
        self.state = LifecycleState.STOPPED

    async def initialize(self, context):
        self.initialize_calls = 1
        self.initialized_context = context
        self.state = LifecycleState.INITIALIZED

    async def start(self):
        self.start_calls = 1
        self.state = LifecycleState.STARTED

    async def dispose(self):
        self.dispose_calls = 1
        self.state = LifecycleState.DISPOSED


class FakeRegistry:
    def __init__(self, runtime):
        self.runtime = runtime

    def get(self, runtime_id):
        return self.runtime if runtime_id == "fake_runtime" else None


@pytest.mark.asyncio
async def test_runtime_kernel_recovery_awaits_async_initialize_and_does_not_dispose():
    runtime = FakeRuntime()
    context = object()

    kernel = RuntimeKernel.__new__(RuntimeKernel)
    kernel.registry = FakeRegistry(runtime)
    kernel.context = context

    await kernel.recover_runtime("fake_runtime")

    assert runtime.stop_calls == 1
    assert runtime.initialize_calls == 1
    assert runtime.start_calls == 1
    assert runtime.dispose_calls == 0
    assert runtime.initialized_context is context
    assert runtime.state is LifecycleState.RUNNING