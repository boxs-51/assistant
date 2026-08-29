#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
cd "$ROOT"

fail(){ echo "ERROR: $*" >&2; exit 1; }
[ -d .git ] || fail "Not a git repository: $ROOT"

echo "== Phase Offline Mock Provider v2 =="

echo "Target: DI-pure runtime + strict mock-only discovery + deterministic faults + full offline v1 E2E"

TARGETS=(
  src/provider/mock/provider.py
  src/provider/mock/scenarios.py
  src/provider/mock/state.py
  src/provider/mock/errors.py
  src/provider/mock/__init__.py
  src/provider/discovery.py
  src/provider/policies/routing_policy.py
  src/provider/executor.py
  src/provider/policies/retry.py
  src/provider/handlers/base.py
  src/provider/handlers/chat_handler.py
  src/provider/handlers/embedding_handler.py
  src/provider/handlers/model_handler.py
  src/provider/handlers/file_handler.py
  src/runtimes/provider/runtime.py
  src/transport/gateway/api/v1/chat_router.py
  tests/providers/test_mock_faults.py
  tests/providers/test_mock_runtime_flow.py
  tests/e2e/test_v1_offline.py
  docs/phase0/PHASE0_OFFLINE_MOCK_PROVIDER_STATUS.md
)
MODIFIED="$(git status --porcelain -- "${TARGETS[@]}" || true)"
[ -z "$MODIFIED" ] || { echo "$MODIFIED"; fail "Phase-owned files have local changes. Commit/stash them first."; }

mkdir -p src/provider/mock tests/providers tests/e2e docs/phase0

# -----------------------------------------------------------------------------
# 1) Clean DI primitives: RetryPolicy no longer needs global configuration when
#    the caller supplies max_retries; ProviderExecutor propagates explicit DI.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path

p = Path("src/provider/policies/retry.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
'''    def __init__(self, max_retries: int | None = None):
        """
        Khởi tạo RetryPolicy.
        :param max_retries: Số lần thử lại tối đa. Nếu là None, sẽ lấy từ cấu hình.
        """
        self.max_retries = max_retries if max_retries is not None else settings.provider.retry
''',
'''    def __init__(self, max_retries: int | None = None, config=None):
        """Create a retry policy from explicit DI.

        ``config`` is retained only for compatibility with the application
        bootstrap. Unit/offline tests should pass ``max_retries`` directly.
        """
        if max_retries is not None:
            self.max_retries = max_retries
        elif config is not None:
            self.max_retries = config.provider.retry
        else:
            # Transitional compatibility for legacy callers. New runtime code
            # must inject config/max_retries explicitly.
            self.max_retries = settings.provider.retry
''')
p.write_text(s, encoding="utf-8")

p = Path("src/provider/executor.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
'''    def __init__(self, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận các manager/policy từ bên ngoài
        self.breaker_manager = circuit_breaker_manager
        self.retry_policy = RetryPolicy()
''',
'''    def __init__(
        self,
        circuit_breaker_manager: CircuitBreakerManager,
        retry_policy: RetryPolicy | None = None,
        *,
        max_retries: int | None = None,
        config=None,
    ):
        """Create an executor from explicit resilience dependencies."""
        self.breaker_manager = circuit_breaker_manager
        self.retry_policy = retry_policy or RetryPolicy(
            max_retries=max_retries,
            config=config,
        )
''')
p.write_text(s, encoding="utf-8")
PY

# -----------------------------------------------------------------------------
# 2) RoutingPolicy receives priority/rules path through DI.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("src/provider/policies/routing_policy.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
'''    def __init__(self, providers: Dict[str, BaseProvider]):
        self.providers = providers
        self._default_chain: List[BaseProvider] = []
        self._rules: List[Dict] = []
        self._reload_lock = asyncio.Lock() # Lock để đảm bảo an toàn khi reload
        self._initialize()
''',
'''    def __init__(
        self,
        providers: Dict[str, BaseProvider],
        *,
        priority: List[str] | None = None,
        rules_path: str | None = None,
        config=None,
    ):
        self.providers = providers
        self._default_chain: List[BaseProvider] = []
        self._rules: List[Dict] = []
        self._reload_lock = asyncio.Lock()
        self.priority = list(
            priority if priority is not None
            else getattr(getattr(config, "provider", None), "priority", None)
            or settings.provider.priority
        )
        self.rules_path = (
            rules_path
            if rules_path is not None
            else getattr(getattr(config, "provider", None), "routing_rules_path", None)
            or settings.provider.routing_rules_path
        )
        self.mock_enabled = bool(
            getattr(getattr(config, "provider", None), "mock_enabled", False)
        )
        self._initialize()
''')
s = s.replace(
'''        self._default_chain = [self.providers[name] for name in settings.provider.priority if name in self.providers]
''',
'''        self._default_chain = [
            self.providers[name]
            for name in self.priority
            if name in self.providers
        ]
''')
s = s.replace('if not os.path.exists(settings.provider.routing_rules_path):', 'if not os.path.exists(self.rules_path):')
s = s.replace('path=settings.provider.routing_rules_path', 'path=self.rules_path')
s = s.replace('with open(settings.provider.routing_rules_path, \'r\', encoding=\'utf-8\') as f:', 'with open(self.rules_path, \'r\', encoding=\'utf-8\') as f:')
p.write_text(s, encoding="utf-8")
PY

# -----------------------------------------------------------------------------
# 3) Strict offline mock-only discovery. In mock-only mode no real provider is
#    even instantiated (therefore no Ollama model cache/network probing).
# -----------------------------------------------------------------------------
cat > src/provider/discovery.py <<'PY'
import structlog

from .factory import ProviderFactory
from .registry import ProviderRegistry

logger = structlog.get_logger(__name__)


class ProviderDiscovery:
    """Discover providers without instantiating disabled/offline providers."""

    def __init__(self, registry: ProviderRegistry, config=None):
        self.registry = registry
        self.config = config

    def run(self):
        logger.info("Starting provider discovery...")

        provider_cfg = getattr(self.config, "provider", None)
        if provider_cfg is not None:
            priority = list(getattr(provider_cfg, "priority", []) or [])
            mock_enabled = bool(getattr(provider_cfg, "mock_enabled", False))

            if mock_enabled and priority == ["mock"]:
                provider_instance = ProviderFactory.create_provider("mock")
                if provider_instance:
                    self.registry.register(provider_instance)
                logger.info("Strict offline mock-only discovery enabled")
                return

        for name, provider_class in ProviderFactory._provider_classes.items():
            if not provider_class.is_configured():
                logger.info("Provider skipped because it is not configured", provider=name)
                continue
            provider_instance = ProviderFactory.create_provider(name)
            if provider_instance:
                self.registry.register(provider_instance)
PY

# -----------------------------------------------------------------------------
# 4) Handler DI: timeout comes from the runtime/application context instead of
#    the global settings singleton.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path

p = Path("src/provider/handlers/base.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
'''        circuit_breaker_manager: CircuitBreakerManager
    ):
        self.providers = providers
        self.routing_policy = routing_policy
        self.executor = executor
        self.circuit_breaker_manager = circuit_breaker_manager
''',
'''        circuit_breaker_manager: CircuitBreakerManager,
        timeout: float | None = None,
    ):
        self.providers = providers
        self.routing_policy = routing_policy
        self.executor = executor
        self.circuit_breaker_manager = circuit_breaker_manager
        self.timeout = 60.0 if timeout is None else float(timeout)
''')
p.write_text(s, encoding="utf-8")

for name in ["chat_handler.py", "embedding_handler.py"]:
    p = Path("src/provider/handlers") / name
    s = p.read_text(encoding="utf-8").replace(
        "settings.provider.timeout", "self.timeout"
    )
    p.write_text(s, encoding="utf-8")

p = Path("src/provider/handlers/model_handler.py")
s = p.read_text(encoding="utf-8").replace(
    "settings.provider.timeout", "self.timeout"
)
p.write_text(s, encoding="utf-8")

p = Path("src/provider/handlers/file_handler.py")
s = p.read_text(encoding="utf-8").replace(
    "timeout = settings.provider.timeout", "timeout = self.timeout"
)
p.write_text(s, encoding="utf-8")
PY

# -----------------------------------------------------------------------------
# 5) ProviderRuntime now propagates RuntimeContext.config into discovery,
#    routing, executor, handlers. This removes hidden config dependency from the
#    actual runtime execution path.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("src/runtimes/provider/runtime.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "discovery = ProviderDiscovery(registry=self.provider_registry)",
    "discovery = ProviderDiscovery(registry=self.provider_registry, config=context.config)",
)
s = s.replace(
    "self.routing_policy = RoutingPolicy(providers=self.providers)",
    "self.routing_policy = RoutingPolicy(providers=self.providers, config=context.config)",
)
s = s.replace(
    "self.executor = ProviderExecutor(self.circuit_breaker_manager)",
    "self.executor = ProviderExecutor(self.circuit_breaker_manager, config=context.config)",
)
s = s.replace(
'''            "circuit_breaker_manager": self.circuit_breaker_manager,
''',
'''            "circuit_breaker_manager": self.circuit_breaker_manager,
            "timeout": context.config.provider.timeout,
''')
p.write_text(s, encoding="utf-8")
PY

# -----------------------------------------------------------------------------
# 6) Fix the canonical chat transport event. The provider runtime subscribes to
#    provider.chat.execute; there is no production subscriber that translates
#    transport.event.request_received into that command.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("src/transport/gateway/api/v1/chat_router.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    'event_name="transport.event.request_received",',
    'event_name="provider.chat.execute",',
)
p.write_text(s, encoding="utf-8")
PY

# -----------------------------------------------------------------------------
# 7) Replace mock scenario/fault contracts with explicit semantics:
#    fail_next=N => exactly the next N matching calls fail; None => persistent.
#    fail_after_chunks=N => emit N chunks then fail on the following chunk.
# -----------------------------------------------------------------------------
cat > src/provider/mock/scenarios.py <<'PY'
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockScenario:
    name: str = "success"
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str = "mock fault"
    error_status_code: int | None = None
    error_code: str | None = None
    fail_operations: set[str] = field(default_factory=set)
    # None means persistent when error_type is set; N means exactly N failures.
    fail_next: int | None = None
    # N means emit N complete chunks, then fail before chunk N+1.
    fail_after_chunks: int | None = None
    stream_chunk_size: int = 1
    fixed_chat_response: str | None = None

    def clone(self) -> "MockScenario":
        return MockScenario(
            name=self.name,
            latency_ms=self.latency_ms,
            error_type=self.error_type,
            error_message=self.error_message,
            error_status_code=self.error_status_code,
            error_code=self.error_code,
            fail_operations=set(self.fail_operations),
            fail_next=self.fail_next,
            fail_after_chunks=self.fail_after_chunks,
            stream_chunk_size=self.stream_chunk_size,
            fixed_chat_response=self.fixed_chat_response,
        )
PY

# -----------------------------------------------------------------------------
# 8) Patch MockProvider semantics in-place.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("src/provider/mock/provider.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
'''        self.provider._before("chat_stream")
''',
'''        self.provider._before("chat_stream")
''', 1)
old = '''    def _raise_fault(self, operation):
        s=self.scenario
        if not s.error_type or (s.fail_operations and operation not in s.fail_operations): return
        if s.fail_next > 0: s.fail_next -= 1; return
        raise build_mock_error(provider_name=self.name,error_type=s.error_type,message=s.error_message,status_code=s.error_status_code,error_code=s.error_code)
    def _before(self, operation):
        self.state.count(operation); self._raise_fault(operation)
        if self.scenario.latency_ms: time.sleep(self.scenario.latency_ms/1000)
'''
new = '''    def _raise_fault(self, operation: str) -> None:
        scenario = self.scenario
        if not scenario.error_type:
            return
        if scenario.fail_after_chunks is not None and operation == "chat_stream":
            # Stream faults are injected at the exact chunk boundary instead.
            return
        if scenario.fail_operations and operation not in scenario.fail_operations:
            return

        if scenario.fail_next is not None:
            if scenario.fail_next <= 0:
                return
            scenario.fail_next -= 1

        raise build_mock_error(
            provider_name=self.name,
            error_type=scenario.error_type,
            message=scenario.error_message,
            status_code=scenario.error_status_code,
            error_code=scenario.error_code,
        )

    async def _before_async(self, operation: str) -> None:
        self.state.count(operation)
        self._raise_fault(operation)
        if self.scenario.latency_ms:
            await asyncio.sleep(self.scenario.latency_ms / 1000)

    def _before(self, operation: str) -> None:
        self.state.count(operation)
        self._raise_fault(operation)
'''
if old not in s:
    raise SystemExit("MockProvider fault block not found")
s = s.replace(old, new)
# Make stream use async timing only and count/fault once.
s = s.replace(
'''            if self.provider.scenario.latency_ms:
                await asyncio.sleep(self.provider.scenario.latency_ms / 1000)
''',
'''            if self.provider.scenario.latency_ms:
                await asyncio.sleep(self.provider.scenario.latency_ms / 1000)
''')
p.write_text(s, encoding="utf-8")
PY

# -----------------------------------------------------------------------------
# 9) Provider tests for strict discovery, DI and exact fault semantics.
# -----------------------------------------------------------------------------
cat > tests/providers/test_mock_faults.py <<'PY'
import pytest

from src.provider.mock import MockProvider, MockScenario
from src.provider.exceptions import ProviderRateLimitError, ProviderUnavailableError


@pytest.mark.asyncio
async def test_persistent_rate_limit_fault():
    p = MockProvider(
        scenario=MockScenario(
            name="rate-limit",
            error_type="rate_limit",
            fail_operations={"chat"},
        )
    )
    with pytest.raises(ProviderRateLimitError):
        await p.chat.chat(body={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}]})


@pytest.mark.asyncio
async def test_fail_next_means_exactly_next_n_calls():
    p = MockProvider(
        scenario=MockScenario(
            name="fail-once",
            error_type="rate_limit",
            fail_operations={"chat"},
            fail_next=1,
        )
    )
    with pytest.raises(ProviderRateLimitError):
        await p.chat.chat(body={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}]})

    response = await p.chat.chat(body={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}]})
    assert response.provider == "mock"


@pytest.mark.asyncio
async def test_stream_fault_after_first_chunk():
    p = MockProvider(
        scenario=MockScenario(
            name="stream-fail",
            error_type="service_unavailable",
            fail_after_chunks=1,
        )
    )
    chunks = []
    with pytest.raises(ProviderUnavailableError):
        async for chunk in p.chat.chat_stream(
            body={"model": "mock-chat", "messages": [{"role": "user", "content": "one two three"}]}
        ):
            chunks.append(chunk)
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_stream_fault_before_first_chunk_when_zero():
    p = MockProvider(
        scenario=MockScenario(
            name="stream-fail-zero",
            error_type="service_unavailable",
            fail_after_chunks=0,
        )
    )
    with pytest.raises(ProviderUnavailableError):
        async for _ in p.chat.chat_stream(
            body={"model": "mock-chat", "messages": [{"role": "user", "content": "hello"}]}
        ):
            pass
PY

cat > tests/providers/test_mock_runtime_flow.py <<'PY'
import httpx
import pytest

from src.circuit_breaker import CircuitBreakerManager
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.discovery import ProviderDiscovery
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.registry import ProviderRegistry
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler


@pytest.fixture
def offline_config():
    return ConfigSchema(
        provider=ProviderSettings(
            priority=["mock"],
            mock_enabled=True,
            timeout=5,
            retry=0,
        )
    )


@pytest.mark.asyncio
async def test_full_provider_handler_path_is_fully_offline(offline_config):
    registry = ProviderRegistry()
    ProviderDiscovery(registry, config=offline_config).run()
    providers = registry.list_all_providers()
    assert list(providers) == ["mock"]

    breaker = CircuitBreakerManager()
    executor = ProviderExecutor(breaker, max_retries=0)
    routing = RoutingPolicy(
        providers,
        config=offline_config,
    )
    client = httpx.AsyncClient()
    kwargs = dict(
        providers=providers,
        routing_policy=routing,
        executor=executor,
        circuit_breaker_manager=breaker,
        timeout=offline_config.provider.timeout,
    )
    try:
        chat = await ChatExecutionHandler(**kwargs).execute_with_fallback(
            client,
            {"model": "mock-chat", "messages": [{"role": "user", "content": "phase0"}]},
        )
        assert chat.provider == "mock"

        embeddings = await EmbeddingExecutionHandler(**kwargs).execute(
            client,
            {"model": "mock-embedding", "input": ["phase0"]},
        )
        assert embeddings["data"][0]["embedding"]

        models = await ModelOperationHandler(**kwargs).execute("mock", None, client)
        assert "mock-chat" in {item.id for item in models.data}

        uploaded = await FileOperationHandler(**kwargs).execute(
            {
                "action": "upload",
                "provider_name": "mock",
                "file_bytes": b"e2e",
                "file_size": 3,
                "mime_type": "text/plain",
                "display_name": "e2e.txt",
            },
            client,
        )
        assert uploaded["display_name"] == "e2e.txt"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_offline_config_does_not_instantiate_ollama(offline_config):
    registry = ProviderRegistry()
    ProviderDiscovery(registry, config=offline_config).run()
    assert list(registry.list_all_providers()) == ["mock"]
PY

# -----------------------------------------------------------------------------
# 10) Full offline API v1 E2E. Provider endpoints use the canonical event bus
#     path, while auth/admin/agent/tool/multi-agent use local deterministic fakes.
#     WebSocket coverage uses TestClient instead of httpx.AsyncClient.
# -----------------------------------------------------------------------------
cat > tests/e2e/test_v1_offline.py <<'PY'
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.schemas.event import BaseEvent
from src.domain.schemas.identity import Identity
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.mock import MockProvider
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler
from src.circuit_breaker import CircuitBreakerManager
from src.transport.gateway.api.v1 import (
    admin,
    agent_router,
    auth_router,
    chat_router,
    embeddings_router,
    events_router,
    files_router,
    health_router,
    models_router,
    multi_agent_router,
    tool_router,
)
from src.transport.gateway.api.v1.auth_router import get_auth_facade, get_api_key_service
from src.transport.gateway.authentication.dependency import get_current_identity, verify_admin_ip
from src.transport.gateway.dependencies import get_container


class InlineEventBus:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    def unsubscribe(self, name, handler):
        handlers = self.handlers.get(name, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: BaseEvent):
        handlers = list(self.handlers.get(event.event_name, []))
        for handler in handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        return None


class FakeWS:
    def __init__(self):
        self.connected = []

    async def connect(self, websocket):
        await websocket.accept()
        self.connected.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.connected:
            self.connected.remove(websocket)

    async def subscribe(self, websocket, event):
        return None

    async def unsubscribe(self, websocket, event):
        return None

    async def shutdown(self):
        self.connected.clear()


class FakeAuthFacade:
    async def initiate_registration(self, data):
        return {"status": "pending", "email": str(data.email)}

    async def confirm_registration(self, email, otp):
        from src.domain.schemas.auth import TokenSchema
        return TokenSchema(access_token="offline", refresh_token="offline-refresh")

    async def login(self, data):
        from src.domain.schemas.auth import TokenSchema
        return TokenSchema(access_token="offline", refresh_token="offline-refresh")

    async def refresh_access_token(self, token):
        from src.domain.schemas.auth import AccessTokenSchema
        return AccessTokenSchema(access_token="offline")

    async def logout(self, token):
        return None

    async def handle_oauth_callback(self, provider, user):
        from src.domain.schemas.auth import TokenSchema
        return TokenSchema(access_token="offline-oauth", refresh_token="offline-refresh")

    async def get_current_user_info(self, identity):
        from src.domain.schemas.auth import UserMeSchema
        return UserMeSchema(
            id=identity.user_id or "offline-user",
            email="offline@example.com",
            roles=["member"],
        )


class FakeAPIKeys:
    async def create_api_key(self, data, identity):
        return {
            "id": "mock-key",
            "full_key": "mock-api-key",
            "prefix": "mock",
            "created_at": "1970-01-01T00:00:00Z",
        }

    async def list_api_keys(self, identity):
        return []

    async def revoke_api_key(self, key_id, identity):
        return True


class FakeOAuthClient:
    async def authorize_redirect(self, request, redirect_uri):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(str(redirect_uri))

    async def authorize_access_token(self, request):
        return {"access_token": "offline"}

    async def userinfo(self, token):
        return {"email": "offline@example.com", "sub": "offline-user", "name": "Offline"}

    async def get(self, *args, **kwargs):
        return httpx.Response(200, json=[])


class FakeOAuth:
    _clients = {"mock": object()}

    def create_client(self, provider):
        return FakeOAuthClient()


class FakeCoordinator:
    def __init__(self):
        self.sessions = {}
        self.messages = {}
        self.tasks = {}
        self.executor = None

    async def create_session_async(self, identity, agent_ids):
        sid = f"mock-session-{len(self.sessions)+1}"
        self.sessions[sid] = {
            "session_id": sid,
            "owner_user_id": identity.user_id,
            "agent_ids": list(agent_ids),
            "status": "ACTIVE",
        }
        return self.sessions[sid]

    def add_agent(self, sid, aid, identity):
        self.sessions[sid]["agent_ids"].append(aid)
        return self.sessions[sid]

    def list_messages(self, sid, identity):
        return self.messages.get(sid, [])

    async def send_message_async(self, **kw):
        item = {
            "message_id": f"mock-message-{len(self.messages.get(kw['session_id'], []))+1}",
            **kw,
        }
        self.messages.setdefault(kw["session_id"], []).append(item)
        return item

    async def create_task_async(self, **kw):
        tid = f"mock-task-{len(self.tasks)+1}"
        item = {"task_id": tid, "status": "CREATED", **kw}
        self.tasks[tid] = item
        return item

    def get_task(self, tid, identity):
        return self.tasks[tid]

    def cancel_task(self, tid, identity):
        self.tasks[tid]["status"] = "CANCELLED"
        return self.tasks[tid]

    def close_session(self, sid, identity):
        self.sessions[sid]["status"] = "CLOSED"
        return self.sessions[sid]

    async def execute_task(self, tid, identity, executor):
        return await executor(self.tasks[tid])

    def get_execution(self, eid, identity):
        return {"execution_id": eid, "status": "completed"}


class OfflineContainer(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def require(self, key):
        value = getattr(self, key, None)
        if value is None:
            raise RuntimeError(f"Offline container dependency not found: {key}")
        return value


@pytest.fixture
def offline_app():
    app = FastAPI()
    identity = Identity(
        auth_type="jwt",
        user_id="offline-user",
        permissions=["admin:read", "admin:write"],
        scopes={"profile", "email"},
    )

    config = ConfigSchema(
        provider=ProviderSettings(priority=["mock"], mock_enabled=True, timeout=5, retry=0)
    )
    bus = InlineEventBus()
    ws = FakeWS()
    provider = MockProvider(seed="v1-offline")
    breakers = CircuitBreakerManager()
    executor = ProviderExecutor(breakers, max_retries=0)
    providers = {"mock": provider}
    routing = RoutingPolicy(providers, config=config)
    handler_kwargs = dict(
        providers=providers,
        routing_policy=routing,
        executor=executor,
        circuit_breaker_manager=breakers,
        timeout=config.provider.timeout,
    )

    # Use the same handler contracts as ProviderRuntime without requiring the
    # production storage/bootstrap stack.
    runtime = SimpleNamespace(
        providers=providers,
        routing_policy=routing,
        circuit_breaker_manager=breakers,
        chat_handler=ChatExecutionHandler(**handler_kwargs),
        embedding_handler=EmbeddingExecutionHandler(**handler_kwargs),
        model_handler=ModelOperationHandler(**handler_kwargs),
        file_handler=FileOperationHandler(**handler_kwargs),
        _http_client=httpx.AsyncClient(),
        event_bus=bus,
    )

    async def _handle_chat(event):
        try:
            body = event.payload.get("request_body", {})
            if body.get("config", {}).get("stream"):
                async for chunk in runtime.chat_handler.stream_with_fallback(runtime._http_client, body):
                    await bus.publish(BaseEvent(
                        event_name="provider.stream.chunk_emitted",
                        session_id=event.session_id,
                        payload={"chunk": chunk.model_dump(), "sse": chunk.to_sse()},
                    ))
                await bus.publish(BaseEvent(
                    event_name="provider.stream.completed",
                    session_id=event.session_id,
                    payload={},
                ))
            else:
                response = await runtime.chat_handler.execute_with_fallback(runtime._http_client, body)
                await bus.publish(BaseEvent(
                    event_name="provider.chat.responded",
                    session_id=event.session_id,
                    payload={"response": response.model_dump()},
                ))
        except Exception as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 503},
            ))

    async def _handle_embeddings(event):
        try:
            result = await runtime.embedding_handler.execute(
                runtime._http_client, event.payload.get("request_body", {})
            )
            await bus.publish(BaseEvent(
                event_name="provider.embeddings.responded",
                session_id=event.session_id,
                payload={"response": result},
            ))
        except Exception as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 503},
            ))

    async def _handle_models(event):
        try:
            result = await runtime.model_handler.execute(
                event.payload.get("provider_name"),
                event.payload.get("model_id"),
                runtime._http_client,
            )
            await bus.publish(BaseEvent(
                event_name="provider.model.responded",
                session_id=event.session_id,
                payload={"result": result},
            ))
        except KeyError as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 404},
            ))

    async def _handle_files(event):
        try:
            result = await runtime.file_handler.execute(
                event.payload, runtime._http_client
            )
            await bus.publish(BaseEvent(
                event_name="provider.file.responded",
                session_id=event.session_id,
                payload={"result": result},
            ))
        except KeyError as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 404},
            ))
        except Exception as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 500},
            ))

    bus.subscribe("provider.chat.execute", _handle_chat)
    bus.subscribe("provider.embeddings.execute", _handle_embeddings)
    bus.subscribe("provider.model.execute", _handle_models)
    bus.subscribe("provider.file.execute", _handle_files)

    agent_store = {}
    tool_store = {}
    agent_registry = SimpleNamespace(
        register=lambda x: agent_store.__setitem__(x.name, x),
        get=lambda x: agent_store.get(x),
    )
    tool_registry = SimpleNamespace(
        register=lambda x: tool_store.__setitem__(x.name, x),
        get=lambda x: tool_store.get(x),
    )

    container = SimpleNamespace(
        config=config,
        storage=SimpleNamespace(drivers={}, repositories={}),
        http_client=runtime._http_client,
        eventing_manager=SimpleNamespace(bus=bus, ws_manager=ws),
        provider_runtime=runtime,
        circuit_breaker_manager=breakers,
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        multi_agent_coordinator=FakeCoordinator(),
        oauth=FakeOAuth(),
    )

    for router in [
        auth_router.router,
        files_router.router,
        models_router.router,
        chat_router.router,
        embeddings_router.router,
        admin.router,
        agent_router.router,
        tool_router.router,
        events_router.router,
        multi_agent_router.router,
        health_router.router,
    ]:
        app.include_router(router)

    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[verify_admin_ip] = lambda: None
    app.dependency_overrides[get_auth_facade] = lambda: FakeAuthFacade()
    app.dependency_overrides[get_api_key_service] = lambda: FakeAPIKeys()

    yield app
    asyncio.get_event_loop().run_until_complete(runtime._http_client.aclose())


@pytest.mark.asyncio
async def test_v1_provider_apis_are_offline(offline_app):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-chat",
                "provider": "mock",
                "messages": [{"role": "user", "content": "hello"}],
                "config": {"stream": False},
            },
        )
        assert chat.status_code == 200, chat.text
        assert chat.json()["provider"] == "mock"

        embeddings = await client.post(
            "/v1/embeddings",
            json={"model": "mock-embedding", "provider": "mock", "input": ["hello"]},
        )
        assert embeddings.status_code == 200, embeddings.text

        models = await client.get("/v1/models/", params={"provider_name": "mock"})
        assert models.status_code == 200, models.text

        detail = await client.get(
            "/v1/models/mock-chat", params={"provider_name": "mock"}
        )
        assert detail.status_code == 200, detail.text

        upload = await client.post(
            "/v1/files/",
            params={"provider_name": "mock", "display_name": "x.txt"},
            files={"file": ("x.txt", b"hello", "text/plain")},
        )
        assert upload.status_code in {200, 201}, upload.text
        file_id = upload.json()["name"]

        metadata = await client.get(
            f"/v1/files/{file_id}",
            params={"provider_name": "mock", "action": "metadata"},
        )
        assert metadata.status_code == 200, metadata.text

        downloaded = await client.get(
            f"/v1/files/{file_id}",
            params={"provider_name": "mock", "action": "download"},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b"hello"

        deleted = await client.delete(
            f"/v1/files/{file_id}", params={"provider_name": "mock"}
        )
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_v1_streaming_chat_is_offline(offline_app):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "mock-chat",
                "provider": "mock",
                "messages": [{"role": "user", "content": "one two"}],
                "config": {"stream": True},
            },
        ) as response:
            body = await response.aread()
            assert response.status_code == 200, body
            text = body.decode()
            assert "mock:one" in text
            assert "mock:two" in text
            assert "[DONE]" in text


@pytest.mark.asyncio
async def test_v1_auth_api_is_offline(offline_app):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.post("/v1/auth/register/initiate", json={"email":"offline@example.com","password":"secret123","name":"Offline"})).status_code == 200
        assert (await client.post("/v1/auth/register/verify", json={"email":"offline@example.com","otp":"123456"})).status_code == 200
        assert (await client.post("/v1/auth/login", json={"email":"offline@example.com","password":"secret123"})).status_code == 200
        assert (await client.post("/v1/auth/refresh", json={"refresh_token":"offline-refresh"})).status_code == 200
        assert (await client.post("/v1/auth/logout", json={"refresh_token":"offline-refresh"})).status_code == 204
        assert (await client.get("/v1/auth/me")).status_code == 200
        assert (await client.post("/v1/auth/api-keys", json={"name":"x"})).status_code == 201
        assert (await client.get("/v1/auth/api-keys")).status_code == 200
        assert (await client.delete("/v1/auth/api-keys/mock-key")).status_code == 204
        assert (await client.post("/v1/auth/oauth/mock", json={"provider":"mock","provider_user_id":"offline-user","email":"offline@example.com"})).status_code == 200


@pytest.mark.asyncio
async def test_v1_agent_tool_admin_health_multi_agent(offline_app):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        tool = {"name":"offline.tool","description":"offline","parameters":{"type":"object","properties":{}}}
        assert (await client.post("/v1/tools/", json=tool)).status_code == 201
        agent = {"name":"offline-agent","goal":"test","instruction":"test","tools":["offline.tool"]}
        assert (await client.post("/v1/agents/", json=agent)).status_code == 201
        session = await client.post("/v1/multi-agent/sessions", json={"agent_ids":[]})
        assert session.status_code == 201
        sid = session.json()["session_id"]
        assert (await client.post(f"/v1/multi-agent/sessions/{sid}/agents", json={"agent_id":"offline-agent"})).status_code == 200
        assert (await client.get(f"/v1/multi-agent/sessions/{sid}/messages")).status_code == 200
        assert (await client.post("/v1/multi-agent/messages", json={"session_id":sid,"sender_id":"offline-user","payload":{"x":1}})).status_code == 201
        task = await client.post("/v1/multi-agent/tasks", json={"session_id":sid,"assigned_agent_id":"offline-agent","input":{"prompt":"hi"}})
        assert task.status_code == 201
        tid = task.json()["task_id"]
        assert (await client.get(f"/v1/multi-agent/tasks/{tid}")).status_code == 200
        assert (await client.post(f"/v1/multi-agent/tasks/{tid}/cancel")).status_code == 200
        assert (await client.post(f"/v1/multi-agent/sessions/{sid}/close")).status_code == 200
        assert (await client.get("/v1/multi-agent/executions/ex1")).status_code == 200
        assert (await client.post("/v1/admin/reload/routing")).status_code == 200
        assert (await client.get("/v1/admin/circuit-breakers/status")).status_code == 200
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200
        assert (await client.get("/metrics")).status_code == 200
        assert (await client.get("/stats")).status_code == 200


def test_v1_events_websocket_offline(offline_app):
    with TestClient(offline_app) as client:
        with client.websocket_connect("/v1/events/ws") as ws:
            ws.send_json({"action":"subscribe","event_name":"mock.event"})
            assert ws.receive_json()["status"] == "success"
            ws.send_json({"action":"unsubscribe","event_name":"mock.event"})
            assert ws.receive_json()["status"] == "success"
PY

# -----------------------------------------------------------------------------
# 11) Status document updated to describe v2 guarantees.
# -----------------------------------------------------------------------------
cat > docs/phase0/PHASE0_OFFLINE_MOCK_PROVIDER_STATUS.md <<'MD'
# Phase 0 — Offline Mock Provider v2

## Goal

Provide a deterministic, configurable, zero-network provider/test harness that
can exercise the canonical `/v1` HTTP transport and ProviderRuntime without
OpenAI/Gemini/Anthropic API keys, Ollama, a local model server, or outbound
provider HTTP calls.

## v2 guarantees

- Mock-only discovery never instantiates real providers.
- `ProviderRuntime` passes `RuntimeContext.config` into discovery, routing,
  retry and execution handlers.
- `RoutingPolicy`, `ProviderExecutor` and handler timeout can be constructed in
  tests without first loading the global `ConfigurationRegistry`.
- `fail_next=N` fails exactly N matching operations and then succeeds.
- `fail_after_chunks=N` emits exactly N stream chunks before failing.
- Mock identifiers and state are deterministic from the configured seed.
- `MockProvider.send()`/`send_stream()` remain explicit network-I/O guards.
- Canonical chat transport emits `provider.chat.execute`, matching the
  ProviderRuntime command subscription.
- HTTP E2E exercises chat, streaming, embeddings, models, files, auth, agents,
  tools, multi-agent, admin, health and WebSocket events using local fakes only
  where those application subsystems are unrelated to external AI providers.

## Verification

```bash
pytest -q \
  tests/providers/test_mock_provider.py \
  tests/providers/test_mock_capabilities.py \
  tests/providers/test_mock_faults.py \
  tests/providers/test_mock_runtime_flow.py \
  tests/e2e/test_v1_offline.py
```
MD

# -----------------------------------------------------------------------------
# 12) Static checks.
# -----------------------------------------------------------------------------
./.venv/Scripts/python.exe -m compileall -q \
  src/provider/mock \
  src/provider/discovery.py \
  src/provider/policies/routing_policy.py \
  src/provider/executor.py \
  src/provider/policies/retry.py \
  src/provider/handlers \
  src/runtimes/provider/runtime.py \
  src/transport/gateway/api/v1/chat_router.py \
  tests/providers \
  tests/e2e/test_v1_offline.py

git diff --check

echo
echo "Phase Offline Mock Provider v2 applied."
echo
echo "Recommended verification:"
echo "  pytest -q tests/providers/test_mock_provider.py tests/providers/test_mock_capabilities.py tests/providers/test_mock_faults.py tests/providers/test_mock_runtime_flow.py"
echo "  pytest -q tests/e2e/test_v1_offline.py"
