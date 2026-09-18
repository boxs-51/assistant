from __future__ import annotations

from dataclasses import dataclass

from ..runtimes.connection.contracts import ConnectionNotFoundError


@dataclass(frozen=True)
class ConnectionAffinityError(ValueError):
    code: str
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


def validate_connection_affinity(registry, connection_id: str, user_id: str):
    """Validate an explicit transport binding against authenticated ownership."""
    try:
        snapshot = registry.get(connection_id)
    except ConnectionNotFoundError as exc:
        raise ConnectionAffinityError(
            "CONNECTION_NOT_ACTIVE",
            f"Connection '{connection_id}' is not active.",
            409,
        ) from exc
    if not snapshot.is_usable:
        raise ConnectionAffinityError(
            "CONNECTION_NOT_ACTIVE",
            f"Connection '{connection_id}' is not active.",
            409,
        )
    if snapshot.user_id != user_id:
        raise ConnectionAffinityError(
            "CONNECTION_FORBIDDEN",
            "The active connection belongs to another authenticated principal.",
            403,
        )
    return snapshot


__all__ = ["ConnectionAffinityError", "validate_connection_affinity"]
