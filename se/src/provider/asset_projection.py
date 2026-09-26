from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY = "_cas_f5_provider_projection"


def canonical_attachment_from_content_part(
    part: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Extract the canonical attachment shape frozen by F4.

    Canonical file content has two legal serialized forms:
    - wrapped document data: data.attachment
    - flat GatewayAttachment data for type=file
    Legacy provider-facing wrapped shapes remain readable for compatibility.
    """

    raw_type = part.get("type")
    part_type = raw_type.value if hasattr(raw_type, "value") else raw_type

    data = part.get("data")
    if isinstance(data, dict):
        if part_type == "file" and "attachment" not in data:
            return data
        attachment = data.get("attachment")
        if isinstance(attachment, dict):
            return attachment

    if isinstance(part_type, str):
        wrapped = part.get(part_type)
        if isinstance(wrapped, dict):
            nested = wrapped.get("attachment")
            if isinstance(nested, dict):
                return nested
            if part_type == "file":
                return wrapped
    return None


class ProviderAssetProjectionError(RuntimeError):
    """Fail-closed CAS-F5-D provider-attempt projection error."""


@dataclass(frozen=True, slots=True)
class ProviderAssetProjection:
    """Execution-local provider-native identity for one canonical asset."""

    provider_name: str
    provider_namespace: str
    provider_file_id: str | None
    provider_uri: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class ProviderAttemptProjection:
    """Transient provider-facing request copy produced for one exact attempt."""

    body: Dict[str, Any]
    engaged: bool
