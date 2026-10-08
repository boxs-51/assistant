from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Mapping

from ..contracts.context import CapabilityExecutionContext
from ..contracts.definition import CapabilityDefinition
from ..contracts.sandbox import SandboxProfile
from ..contracts.target import FallbackPolicy, ResourceScope
from ..sandbox import SandboxError, SandboxManager
from .base import BaseCapabilityDriver


SANDBOX_FILE_CAPABILITY_IDS = frozenset(
    {
        "file.read",
        "file.search",
        "file.write",
        "file.append",
        "file.replace",
        "glob.find",
    }
)

_SANDBOX_RESULT_PATH_KEYS = frozenset({"path", "root_dir"})


def _lease_relative_result_path(value: str, root: Path) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        return value

    root_resolved = root.resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SandboxError(
            "sandbox tool returned a path outside the active lease"
        ) from exc

    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise SandboxError(
            "sandbox tool returned a non-lease path"
        ) from exc
    rendered = relative.as_posix()
    return rendered if rendered else "."


def _externalize_sandbox_result_paths(
    value: Any,
    root: Path,
    *,
    field_name: str | None = None,
) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _externalize_sandbox_result_paths(
                item,
                root,
                field_name=str(key),
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _externalize_sandbox_result_paths(
                item,
                root,
                field_name=field_name,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _externalize_sandbox_result_paths(
                item,
                root,
                field_name=field_name,
            )
            for item in value
        )
    if (
        isinstance(value, str)
        and field_name is not None
        and (
            field_name in _SANDBOX_RESULT_PATH_KEYS
            or field_name.endswith("_paths")
        )
    ):
        return _lease_relative_result_path(value, root)
    return value


class SandboxPythonCapabilityDriver(BaseCapabilityDriver):
    """Context-aware Python driver for the exact SBX-2 file/glob surface."""

    def __init__(
        self,
        definition: CapabilityDefinition,
        handler: Callable[..., Any],
        sandbox_manager: SandboxManager,
        sandbox_profile: SandboxProfile,
    ) -> None:
        super().__init__(definition)
        if definition.capability_id not in SANDBOX_FILE_CAPABILITY_IDS:
            raise ValueError(
                "sandbox file driver is limited to the SBX-2 logical IDs"
            )
        self._handler = handler
        self._sandbox_manager = sandbox_manager
        self._sandbox_profile = sandbox_profile

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        target = context.target
        if (
            target is None
            or target.resource_scope is not ResourceScope.SANDBOX
            or target.resource_ref != context.execution_id
            or target.stable_client_id is not None
            or target.fallback_policy is not FallbackPolicy.NONE
        ):
            raise SandboxError(
                "SBX-2 server file/glob execution requires an exact "
                "SANDBOX target bound to this execution"
            )

        owner_user_id = str(
            getattr(context.identity, "user_id", "") or ""
        )
        if not owner_user_id:
            raise SandboxError(
                "SBX-2 sandbox execution requires an authenticated owner"
            )

        lease = self._sandbox_manager.acquire_for_execution(
            execution_id=context.execution_id,
            owner_user_id=owner_user_id,
            profile=self._sandbox_profile,
        )
        rewritten = dict(arguments)

        if self.definition.capability_id == "glob.find":
            root_dir = rewritten.get("root_dir", ".")
            rewritten["root_dir"] = str(
                self._sandbox_manager.resolve_path(lease, root_dir)
            )
        else:
            if "file_paths" not in rewritten:
                raise SandboxError(
                    "SBX-2 file capability requires file_paths"
                )
            raw_paths = rewritten["file_paths"]
            if isinstance(raw_paths, str):
                rewritten["file_paths"] = str(
                    self._sandbox_manager.resolve_path(lease, raw_paths)
                )
            elif isinstance(raw_paths, (list, tuple)):
                rewritten["file_paths"] = [
                    str(self._sandbox_manager.resolve_path(lease, item))
                    for item in raw_paths
                ]
            else:
                raise SandboxError(
                    "SBX-2 file_paths must be a string or sequence"
                )

        result = self._handler(**rewritten)
        if inspect.isawaitable(result):
            result = await result
        return _externalize_sandbox_result_paths(result, lease.root)


__all__ = [
    "SANDBOX_FILE_CAPABILITY_IDS",
    "SandboxPythonCapabilityDriver",
]
