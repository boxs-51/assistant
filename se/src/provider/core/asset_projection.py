from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderAssetProjection:
    """Execution-local provider-native identity for one canonical CAS asset.

    This object is intentionally a Python process object, not a Gateway schema.
    Client JSON cannot manufacture this authority. It exists only on a
    provider-facing request copy after authenticated hydration/reuse succeeds.
    """

    provider_name: str
    provider_namespace: str
    provider_file_id: str
    provider_uri: str | None
    mime_type: str
