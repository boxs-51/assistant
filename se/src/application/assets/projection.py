from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterator

from ...infrastructure.storage.repositories.assets import AssetRepository
from ...provider.asset_projection import (
    ProviderAssetProjection,
    ProviderAssetProjectionError,
    ProviderAttemptProjection,
    TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY,
)
from .hydration import HydrationStatus


class CanonicalAssetProviderProjectionHook:
    """Dormant CAS-F5-D hook for exact-provider hydration/projection.

    The hook never selects a provider and never mutates the canonical request.
    It may only be invoked after provider eligibility has already passed.
    """

    _PROJECTABLE = frozenset(
        (HydrationStatus.REUSED, HydrationStatus.HYDRATED)
    )

    def __init__(self, hydration_service) -> None:
        self.hydration_service = hydration_service

    @classmethod
    def contains_canonical_assets(cls, body: Dict[str, Any]) -> bool:
        return any(cls._iter_asset_attachments(body))

    @classmethod
    def _iter_asset_attachments(
        cls,
        body: Dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                attachment = cls._attachment_from_part(part)
                if not isinstance(attachment, dict):
                    continue
                asset_id = attachment.get("asset_id")
                if isinstance(asset_id, str) and asset_id.strip():
                    yield attachment

    @staticmethod
    def _attachment_from_part(part: dict[str, Any]):
        data = part.get("data")
        if isinstance(data, dict):
            attachment = data.get("attachment")
            if isinstance(attachment, dict):
                return attachment

        part_type = part.get("type")
        if isinstance(part_type, str):
            wrapped = part.get(part_type)
            if isinstance(wrapped, dict):
                nested = wrapped.get("attachment")
                if isinstance(nested, dict):
                    return nested
                if part_type == "file":
                    return wrapped
        return None

    def _require_exact_selected_provider(
        self,
        provider: Any,
    ) -> tuple[str, str]:
        provider_name = str(getattr(provider, "name", "") or "").strip()
        if not provider_name:
            raise ProviderAssetProjectionError(
                "CAS-F5-D requires an exact server-selected provider."
            )

        registry = getattr(self.hydration_service, "provider_registry", None)
        get_provider = getattr(registry, "get_provider", None)
        if not callable(get_provider) or get_provider(provider_name) is not provider:
            raise ProviderAssetProjectionError(
                "CAS-F5-D selected provider does not match hydration registry authority."
            )

        config = getattr(provider, "config", None)
        try:
            provider_namespace = (
                AssetRepository.require_provider_binding_namespace(
                    getattr(config, "file_binding_namespace", None)
                )
            )
        except ValueError as exc:
            raise ProviderAssetProjectionError(
                "CAS-F5-D selected provider has no server binding namespace."
            ) from exc
        return provider_name, provider_namespace

    async def project_attempt(
        self,
        *,
        provider: Any,
        body: Dict[str, Any],
        owner_user_id: str | None,
    ) -> ProviderAttemptProjection:
        if not self.contains_canonical_assets(body):
            return ProviderAttemptProjection(body=body, engaged=False)

        owner = str(owner_user_id or "").strip()
        if not owner:
            raise ProviderAssetProjectionError(
                "CAS-F5-D canonical asset projection requires trusted owner identity."
            )

        provider_name, provider_namespace = (
            self._require_exact_selected_provider(provider)
        )
        if provider_name != "gemini":
            raise ProviderAssetProjectionError(
                f"CAS-F5-D provider projection is not implemented for '{provider_name}'."
            )

        projected = deepcopy(body)
        attachments = list(self._iter_asset_attachments(projected))
        if not attachments:
            raise ProviderAssetProjectionError(
                "CAS-F5-D asset-bearing attempt lost canonical asset identity."
            )

        for attachment in attachments:
            if attachment.get("source") not in (None, "asset"):
                raise ProviderAssetProjectionError(
                    "CAS-F5-D canonical asset has conflicting source authority."
                )
            asset_id = str(attachment.get("asset_id") or "").strip()
            try:
                result = await self.hydration_service.hydrate(
                    owner_user_id=owner,
                    asset_id=asset_id,
                    provider_name=provider_name,
                )
            except BaseException:
                raise

            if result.status not in self._PROJECTABLE:
                raise ProviderAssetProjectionError(
                    "CAS-F5-D hydration result is not projection-eligible: "
                    f"{result.status.value}."
                )

            provider_uri = str(result.provider_uri or "").strip()
            mime_type = str(getattr(result, "mime_type", None) or "").strip()
            if not provider_uri:
                raise ProviderAssetProjectionError(
                    "CAS-F5-D Gemini projection requires server-proven provider_uri."
                )
            if not mime_type:
                raise ProviderAssetProjectionError(
                    "CAS-F5-D projection requires server-proven canonical mime_type."
                )

            attachment[TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY] = (
                ProviderAssetProjection(
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                    provider_file_id=result.provider_file_id,
                    provider_uri=provider_uri,
                    mime_type=mime_type,
                )
            )

        return ProviderAttemptProjection(body=projected, engaged=True)
