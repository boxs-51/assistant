from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable

from ...provider.core.asset_projection import ProviderAssetProjection
from ...provider.exceptions import ProviderError
from .hydration import (
    AssetHydrationError,
    HydrationResult,
    HydrationStatus,
)


class AssetProjectionError(ProviderError):
    code = "ASSET_PROJECTION_FAILED"
    failure_domain = "MESSAGE_ASSET"
    retryable = False


@dataclass(frozen=True, slots=True)
class ProjectedProviderRequest:
    body: Dict[str, Any]
    asset_count: int


class ProviderPinnedAssetProjector:
    """Project canonical asset parts into one exact provider attempt.

    Projection is copy-only. The source request remains canonical and
    provider-neutral, while the returned body can carry process-local
    ProviderAssetProjection objects for provider converters.
    """

    _ELIGIBLE = {
        HydrationStatus.REUSED,
        HydrationStatus.HYDRATED,
    }

    def __init__(self, hydration_service) -> None:
        self.hydration_service = hydration_service

    @staticmethod
    def _provider_authority(provider: Any) -> tuple[str, str]:
        provider_name = getattr(provider, "name", None)
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise AssetProjectionError(
                "Selected provider lacks stable server identity."
            )
        config = getattr(provider, "config", None)
        namespace = getattr(config, "file_binding_namespace", None)
        if not isinstance(namespace, str) or not namespace.strip():
            raise AssetProjectionError(
                "Selected provider lacks server-owned file binding namespace.",
                provider_name=provider_name,
            )
        return provider_name, namespace

    @staticmethod
    def _candidate_attachments(part: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        data = part.get("data")
        if isinstance(data, dict):
            nested = data.get("attachment")
            if isinstance(nested, dict):
                yield nested
            else:
                yield data

        flat = part.get("file")
        if isinstance(flat, dict):
            nested = flat.get("attachment")
            yield nested if isinstance(nested, dict) else flat

        for key in ("image", "audio", "video"):
            value = part.get(key)
            if isinstance(value, dict):
                nested = value.get("attachment")
                if isinstance(nested, dict):
                    yield nested

    @staticmethod
    def _canonical_attachment(part: Dict[str, Any]) -> Dict[str, Any] | None:
        for attachment in ProviderPinnedAssetProjector._candidate_attachments(part):
            asset_id = attachment.get("asset_id")
            source = attachment.get("source")
            uri = attachment.get("uri")
            claims_asset = bool(asset_id) or source == "asset" or (
                isinstance(uri, str) and uri.startswith("asset://")
            )
            if not claims_asset:
                continue
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise AssetProjectionError(
                    "Canonical asset projection requires non-blank asset_id."
                )
            if source != "asset" or uri != f"asset://{asset_id}":
                raise AssetProjectionError(
                    "Malformed canonical asset identity in provider request."
                )
            return attachment
        return None

    @staticmethod
    def _iter_asset_parts(body: Dict[str, Any]):
        messages = body.get("messages")
        if not isinstance(messages, list):
            return
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                attachment = ProviderPinnedAssetProjector._canonical_attachment(part)
                if attachment is not None:
                    if message.get("role") == "system":
                        raise AssetProjectionError(
                            "System messages cannot carry canonical asset projection authority."
                        )
                    yield part, attachment

    @staticmethod
    def _projection_for(
        *,
        result: HydrationResult,
        provider_name: str,
        provider_namespace: str,
    ) -> ProviderAssetProjection:
        if result.status not in ProviderPinnedAssetProjector._ELIGIBLE:
            raise AssetProjectionError(
                f"Hydration status {result.status.value} is not projection-eligible.",
                provider_name=provider_name,
            )
        provider_file_id = result.provider_file_id
        if not isinstance(provider_file_id, str) or not provider_file_id.strip():
            raise AssetProjectionError(
                "Projection-eligible hydration lacks stable provider_file_id.",
                provider_name=provider_name,
            )
        mime_type = result.mime_type
        if not isinstance(mime_type, str) or not mime_type.strip():
            raise AssetProjectionError(
                "Projection-eligible hydration lacks canonical mime type.",
                provider_name=provider_name,
            )

        if provider_name != "gemini":
            raise AssetProjectionError(
                "CAS-F5-D first production slice supports Gemini native projection only.",
                provider_name=provider_name,
            )
        provider_uri = result.provider_uri
        if not isinstance(provider_uri, str) or not provider_uri.strip():
            raise AssetProjectionError(
                "Gemini projection requires server-proven non-blank provider_uri.",
                provider_name=provider_name,
            )

        return ProviderAssetProjection(
            provider_name=provider_name,
            provider_namespace=provider_namespace,
            provider_file_id=provider_file_id,
            provider_uri=provider_uri,
            mime_type=mime_type,
        )

    async def project(
        self,
        *,
        body: Dict[str, Any],
        owner_user_id: str,
        provider: Any,
    ) -> ProjectedProviderRequest:
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise AssetProjectionError(
                "Trusted authenticated owner identity is required."
            )
        provider_name, provider_namespace = self._provider_authority(provider)
        request_copy = deepcopy(body)
        asset_count = 0

        try:
            asset_parts = list(self._iter_asset_parts(request_copy))
            for part, attachment in asset_parts:
                result = await self.hydration_service.hydrate(
                    owner_user_id=owner_user_id,
                    asset_id=attachment["asset_id"],
                    provider_name=provider_name,
                )
                part["_provider_asset_projection"] = self._projection_for(
                    result=result,
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                )
                asset_count += 1
        except AssetProjectionError:
            raise
        except AssetHydrationError as exc:
            raise AssetProjectionError(
                "Canonical asset hydration failed before provider inference.",
                provider_name=provider_name,
            ) from exc

        if asset_count == 0:
            raise AssetProjectionError(
                "Asset hook entered without a valid canonical asset part.",
                provider_name=provider_name,
            )

        return ProjectedProviderRequest(
            body=request_copy,
            asset_count=asset_count,
        )
