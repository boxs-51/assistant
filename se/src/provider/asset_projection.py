from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY = "_cas_f5_provider_projection"


class ProviderAssetProjectionError(RuntimeError):
    """Fail-closed CAS-F5-D provider-attempt projection error."""


@dataclass(frozen=True, slots=True)
class ProviderAssetProjection:
    """Execution-local provider-native identity for one canonical asset."""

    provider_name: str
    provider_file_id: str | None
    provider_uri: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class ProviderAttemptProjection:
    """Transient provider-facing request copy produced for one exact attempt."""

    body: Dict[str, Any]
    engaged: bool
