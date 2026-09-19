from __future__ import annotations

import uuid
from collections.abc import Callable


class AgentExecutionIdFactory:
    """Canonical allocator for AgentExecution identities.

    The factory owns identity allocation only. AgentRuntime remains the
    authority that creates and transitions the durable AgentExecution row.
    """

    def __init__(
        self,
        *,
        prefix: str = "exec_",
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if not prefix:
            raise ValueError("Agent execution ID prefix must not be empty.")
        self._prefix = prefix
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex)

    def new_id(self) -> str:
        token = str(self._token_factory()).strip()
        if not token:
            raise ValueError("Agent execution ID token must not be empty.")
        return f"{self._prefix}{token}"


__all__ = ["AgentExecutionIdFactory"]
