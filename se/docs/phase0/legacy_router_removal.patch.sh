#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(pwd)}"
cd "$REPO_ROOT"

fail() { echo "ERROR: $*" >&2; exit 1; }

[ -d .git ] || fail "Not a git repository: $REPO_ROOT"

TARGETS=(
  src/main.py
  src/application/container.py
  src/transport/gateway/dependencies.py
  src/services/admin_service.py
  src/provider/__init__.py
  src/transport/gateway/api/v1/admin.py
  src/transport/gateway/api/v1/health_router.py
  src/transport/gateway/api/v1/__init__.py
  src/transport/gateway/api/v1/auth_router.py
  src/transport/gateway/api/v1/agent_router.py
  src/transport/gateway/api/v1/tool_router.py
  src/transport/gateway/api/v1/events_router.py
  src/transport/gateway/api/v1/multi_agent_router.py
  src/transport/gateway/router/auth.py
  src/transport/gateway/router/agent.py
  src/transport/gateway/router/tool.py
  src/transport/gateway/router/events.py
  src/transport/gateway/router/admin.py
  src/transport/gateway/router/multi_agent.py
  src/transport/gateway/router/models.py
  src/transport/gateway/router/files.py
  src/transport/gateway/router/embeddings.py
  src/transport/gateway/router/health.py
  src/transport/gateway/router/chat.py
  tests/architecture/test_no_legacy_router.py
)

# Do not clobber pre-existing work in files this phase owns.
MODIFIED="$(git status --porcelain -- "${TARGETS[@]}" || true)"
if [ -n "$MODIFIED" ]; then
  echo "$MODIFIED"
  fail "One or more Phase Legacy Router Removal files already have local changes. Commit/stash them before applying this patch."
fi

mkdir -p src/transport/gateway/api/v1

# -----------------------------------------------------------------------------
# 1) Move legacy transport routers into api/v1 (preserving URL prefixes).
#    Relative imports are shifted one package deeper: router/* -> api/v1/*.
# -----------------------------------------------------------------------------
python - <<'PY'
from pathlib import Path

root = Path('.')
pairs = {
    'auth.py': 'auth_router.py',
    'agent.py': 'agent_router.py',
    'tool.py': 'tool_router.py',
    'events.py': 'events_router.py',
    'multi_agent.py': 'multi_agent_router.py',
}
base = root / 'src/transport/gateway/router'
out = root / 'src/transport/gateway/api/v1'

for old_name, new_name in pairs.items():
    src = base / old_name
    dst = out / new_name
    text = src.read_text(encoding='utf-8')
    # router package -> api.v1 is one level deeper relative to src.
    text = text.replace('from ....application', 'from .....application')
    text = text.replace('from ....domain', 'from .....domain')
    text = text.replace('from ....infrastructure', 'from .....infrastructure')
    text = text.replace('from ....agent', 'from .....agent')
    text = text.replace('from ....tool', 'from .....tool')
    text = text.replace('from ....services', 'from .....services')
    text = text.replace('from ....provider', 'from .....provider')
    text = text.replace('from ..authentication', 'from ...authentication')
    text = text.replace('from ..dependencies', 'from ...dependencies')
    dst.write_text(text, encoding='utf-8')
PY

# -----------------------------------------------------------------------------
# 2) api/v1 package exports.
# -----------------------------------------------------------------------------
cat > src/transport/gateway/api/v1/__init__.py <<'PY'
"""Versioned HTTP transport routers.

This package is the canonical transport surface. Routers under
``transport.gateway.router`` are legacy and removed by the Legacy Router Removal
migration.
"""

from . import (
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

__all__ = [
    "admin",
    "agent_router",
    "auth_router",
    "chat_router",
    "embeddings_router",
    "events_router",
    "files_router",
    "health_router",
    "models_router",
    "multi_agent_router",
    "tool_router",
]
PY

# -----------------------------------------------------------------------------
# 3) Replace v1 admin transport adapter and remove facade leakage from it.
# -----------------------------------------------------------------------------
cat > src/transport/gateway/api/v1/admin.py <<'PY'
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from .....application.container import ApplicationContainer
from .....services.admin_service import AdminService
from ...authentication.dependency import require_permission, verify_admin_ip
from ...dependencies import get_container

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[
        Depends(verify_admin_ip),
        Depends(require_permission("admin:write")),
    ],
)


def get_admin_service(
    container: ApplicationContainer = Depends(get_container),
) -> AdminService:
    return AdminService(container)


@router.post(
    "/reload/routing",
    summary="Hot-reload quy tắc định tuyến",
    description="Tải lại các rules từ YAML mà không cần khởi động lại Gateway.",
)
async def reload_routing_rules(
    admin_service: AdminService = Depends(get_admin_service),
):
    success = await admin_service.reload_routing_rules()
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reload routing rules. Check system logs for details.",
        )
    return {"status": "success", "message": "Routing rules reloaded successfully."}


@router.get(
    "/circuit-breakers/status",
    summary="Trạng thái Circuit Breakers",
    dependencies=[Depends(require_permission("admin:read"))],
)
async def get_circuit_breaker_statuses(
    admin_service: AdminService = Depends(get_admin_service),
):
    statuses = await admin_service.get_circuit_breaker_statuses()
    return JSONResponse(content=statuses)
PY

# -----------------------------------------------------------------------------
# 4) Health transport must use the application container, never app.state.*.
# -----------------------------------------------------------------------------
cat > src/transport/gateway/api/v1/health_router.py <<'PY'
import psutil
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import generate_latest

from .....application.container import ApplicationContainer
from ...dependencies import get_container

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Kubernetes liveness probe."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check(
    container: ApplicationContainer = Depends(get_container),
):
    """Kubernetes readiness probe backed by the application dependency graph."""
    try:
        redis_driver = container.storage.drivers.get("redis")
        if redis_driver:
            await redis_driver.ping()

        provider_runtime = container.provider_runtime
        if provider_runtime is None or not provider_runtime.providers:
            raise RuntimeError("Provider runtime is not initialized or has no providers.")

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Service Unavailable: {exc}",
        ) from exc

    return {"status": "ready"}


@router.get("/metrics")
def get_metrics():
    """Prometheus metrics scraper."""
    return StreamingResponse(generate_latest(), media_type="text/plain")


@router.get("/stats")
async def get_stats(
    container: ApplicationContainer = Depends(get_container),
):
    process = psutil.Process()
    config = container.config
    return {
        "gateway_name": config.gateway.name,
        "gateway_version": config.gateway.version,
        "cpu_usage_percent": process.cpu_percent(interval=0.1),
        "memory_usage_mb": process.memory_info().rss / (1024 * 1024),
    }
PY

# -----------------------------------------------------------------------------
# 5) Admin application service: ProviderRuntime is the authority.
# -----------------------------------------------------------------------------
cat > src/services/admin_service.py <<'PY'
from typing import Any, Dict

import structlog

from ..application.container import ApplicationContainer

logger = structlog.get_logger(__name__)


class AdminService:
    """Application service for provider/runtime administration."""

    def __init__(self, container: ApplicationContainer):
        provider_runtime = container.provider_runtime
        if provider_runtime is None:
            raise RuntimeError("ProviderRuntime is not initialized.")

        self.routing_policy = provider_runtime.routing_policy
        self.circuit_breaker_manager = container.circuit_breaker_manager

    async def reload_routing_rules(self) -> bool:
        if self.routing_policy is None:
            logger.error("Routing policy is not initialized")
            return False

        try:
            return await self.routing_policy.reload_rules()
        except Exception as exc:
            logger.exception("Error during routing rules hot-reload", error=str(exc))
            return False

    async def get_circuit_breaker_statuses(self) -> Dict[str, Any]:
        if self.circuit_breaker_manager is None:
            logger.error("Circuit Breaker Manager is not initialized")
            return {}

        return await self.circuit_breaker_manager.get_all_statuses()
PY

# -----------------------------------------------------------------------------
# 6) Remove legacy dependency from the gateway DI boundary.
# -----------------------------------------------------------------------------
python - <<'PY'
from pathlib import Path
p = Path('src/transport/gateway/dependencies.py')
s = p.read_text(encoding='utf-8')
block = '''\n\ndef get_legacy_model_router(request: Request):\n    return get_container(request).require("legacy_model_router")\n'''
if block not in s:
    raise SystemExit('Expected get_legacy_model_router block not found')
s = s.replace(block, '')
p.write_text(s, encoding='utf-8')
PY

# -----------------------------------------------------------------------------
# 7) Remove legacy_model_router from ApplicationContainer.
# -----------------------------------------------------------------------------
python - <<'PY'
from pathlib import Path
p = Path('src/application/container.py')
s = p.read_text(encoding='utf-8')
block = '''\n    legacy_model_router: Optional[Any] = None\n'''
if block not in s:
    raise SystemExit('Expected legacy_model_router field not found')
s = s.replace(block, '')
p.write_text(s, encoding='utf-8')
PY

# -----------------------------------------------------------------------------
# 8) Remove LegacyModelRouterFacade and its helper from provider package.
#    Keep deprecated ModelRouter for a separate external-compatibility cleanup.
# -----------------------------------------------------------------------------
python - <<'PY'
from pathlib import Path
import re
p = Path('src/provider/__init__.py')
s = p.read_text(encoding='utf-8')
start = s.find('\nclass LegacyModelRouterFacade:')
if start < 0:
    raise SystemExit('LegacyModelRouterFacade block not found')
s = s[:start].rstrip() + '\n'
# The remaining ModelRouter docstring must not advertise a facade that no longer exists.
s = s.replace(
    'New application code must use ProviderRuntime through\n    LegacyModelRouterFacade until the legacy transport is removed.',
    'New application code must use ProviderRuntime. This deprecated class remains\n    only as a temporary external-compatibility boundary.'
)
p.write_text(s, encoding='utf-8')
PY

# -----------------------------------------------------------------------------
# 9) Main bootstrap + router registration: api/v1 is now canonical.
# -----------------------------------------------------------------------------
python - <<'PY'
from pathlib import Path
p = Path('src/main.py')
s = p.read_text(encoding='utf-8')
old_imports = '''# Routers\nfrom .transport.gateway.router.auth import router as auth_router\nfrom .transport.gateway.router.agent import router as agent_router\nfrom .transport.gateway.router.tool import router as tool_router\nfrom .transport.gateway.router.events import router as events_router\nfrom .transport.gateway.router.admin import router as admin_router\nfrom .transport.gateway.router.multi_agent import router as multi_agent_router\n\nfrom .transport.gateway.api.v1 import (\n    chat_router, embeddings_router, \n    files_router, health_router,\n    models_router\n)\n\nfrom .provider import LegacyModelRouterFacade\n'''
new_imports = '''# Canonical versioned HTTP transport routers\nfrom .transport.gateway.api.v1 import (\n    admin as admin_router,\n    agent_router,\n    auth_router,\n    chat_router,\n    embeddings_router,\n    events_router,\n    files_router,\n    health_router,\n    models_router,\n    multi_agent_router,\n    tool_router,\n)\n'''
if old_imports not in s:
    raise SystemExit('Expected legacy router import block not found')
s = s.replace(old_imports, new_imports)

legacy_bind = '''    # Bind LegacyModelRouterFacade vào container\n    container.legacy_model_router = LegacyModelRouterFacade(container.provider_runtime)\n\n'''
if legacy_bind not in s:
    raise SystemExit('Expected legacy facade bootstrap block not found')
s = s.replace(legacy_bind, '')

old_routes = '''    # Route Registrations\n    app_instance.include_router(auth_router)\n    app_instance.include_router(files_router.router)\n    app_instance.include_router(models_router.router)\n    app_instance.include_router(chat_router.router)\n    app_instance.include_router(embeddings_router.router)\n    app_instance.include_router(admin_router)\n    app_instance.include_router(agent_router)\n    app_instance.include_router(tool_router)\n    app_instance.include_router(events_router)\n    app_instance.include_router(multi_agent_router)\n    app_instance.include_router(health_router.router)\n'''
new_routes = '''    # Route Registrations: api/v1 is the sole HTTP router surface.\n    app_instance.include_router(auth_router.router)\n    app_instance.include_router(files_router.router)\n    app_instance.include_router(models_router.router)\n    app_instance.include_router(chat_router.router)\n    app_instance.include_router(embeddings_router.router)\n    app_instance.include_router(admin_router.router)\n    app_instance.include_router(agent_router.router)\n    app_instance.include_router(tool_router.router)\n    app_instance.include_router(events_router.router)\n    app_instance.include_router(multi_agent_router.router)\n    app_instance.include_router(health_router.router)\n'''
if old_routes not in s:
    raise SystemExit('Expected legacy route registration block not found')
s = s.replace(old_routes, new_routes)
p.write_text(s, encoding='utf-8')
PY

# -----------------------------------------------------------------------------
# 10) Delete old transport router modules after all callers have moved.
# -----------------------------------------------------------------------------
git rm -f \
  src/transport/gateway/router/auth.py \
  src/transport/gateway/router/agent.py \
  src/transport/gateway/router/tool.py \
  src/transport/gateway/router/events.py \
  src/transport/gateway/router/admin.py \
  src/transport/gateway/router/multi_agent.py \
  src/transport/gateway/router/models.py \
  src/transport/gateway/router/files.py \
  src/transport/gateway/router/embeddings.py \
  src/transport/gateway/router/health.py \
  src/transport/gateway/router/chat.py

# -----------------------------------------------------------------------------
# 11) Architecture guard: source tree must have no facade references and no
#     imports from the deleted transport router package.
# -----------------------------------------------------------------------------
cat > tests/architecture/test_no_legacy_router.py <<'PY'
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _source_files():
    return [path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def test_legacy_model_router_facade_is_removed_from_source():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if "LegacyModelRouterFacade" in text or "legacy_model_router" in text:
            offenders.append(str(path))
    assert not offenders, f"Legacy router references remain: {offenders}"


def test_legacy_transport_router_imports_are_removed():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if "transport.gateway.router" in text:
            offenders.append(str(path))
    assert not offenders, f"Legacy router imports remain: {offenders}"


def test_canonical_v1_router_modules_exist():
    required = {
        "auth_router.py",
        "agent_router.py",
        "tool_router.py",
        "events_router.py",
        "multi_agent_router.py",
        "chat_router.py",
        "embeddings_router.py",
        "models_router.py",
        "files_router.py",
        "health_router.py",
        "admin.py",
    }
    actual = {path.name for path in (SRC_ROOT / "transport/gateway/api/v1").glob("*.py")}
    assert required <= actual
PY

# -----------------------------------------------------------------------------
# 12) Static checks. Optional pytest is attempted when the environment has the
#     project's test dependencies installed.
# -----------------------------------------------------------------------------
python -m compileall -q \
  src/transport/gateway/api/v1 \
  src/transport/gateway/dependencies.py \
  src/application/container.py \
  src/services/admin_service.py \
  src/provider/__init__.py \
  src/main.py

git diff --check

if command -v pytest >/dev/null 2>&1; then
  pytest -q tests/architecture/test_no_legacy_router.py || {
    echo "WARNING: architecture test failed; inspect the output before committing." >&2
    exit 2
  }
fi

echo
echo "Phase Legacy Router Removal applied successfully."
echo "Review with: git diff --stat && git diff"
