"""Versioned HTTP transport routers.

This package is the canonical transport surface. Legacy routers under
the old router package are removed by the Legacy Router Removal migration.
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
