from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from ...infrastructure.storage.interfaces.object import ObjectStorageDriver
from ...infrastructure.storage.repositories.assets import AssetRepository
from ...provider.core.interfaces.file import (
    FileProvider,
    ProviderUploadOutcome,
    ProviderUploadOutcomeKind,
)


class HydrationStatus(str, Enum):
    REUSED = "REUSED"
    HYDRATED = "HYDRATED"
    HYDRATION_IN_PROGRESS = "HYDRATION_IN_PROGRESS"
    HYDRATION_OUTCOME_UNKNOWN = "HYDRATION_OUTCOME_UNKNOWN"
    HYDRATION_FAILED_SAFE = "HYDRATION_FAILED_SAFE"
    HYDRATION_RACE_LOST = "HYDRATION_RACE_LOST"
    HYDRATION_FINGERPRINT_DRIFT = "HYDRATION_FINGERPRINT_DRIFT"
    HYDRATION_PERSISTENCE_CONFLICT = "HYDRATION_PERSISTENCE_CONFLICT"


@dataclass(frozen=True, slots=True)
class HydrationResult:
    status: HydrationStatus
    binding_id: Optional[str] = None
    provider_file_id: Optional[str] = None
    provider_uri: Optional[str] = None
    mime_type: Optional[str] = None


class AssetHydrationError(RuntimeError):
    pass


class AssetHydrationPreflightError(AssetHydrationError):
    pass


class AssetHydrationProviderError(AssetHydrationError):
    pass


@dataclass(frozen=True, slots=True)
class _CanonicalBlobSnapshot:
    blob_id: str
    sha256: str
    object_key: str
    size_bytes: int
    filename: str
    mime_type: str


class CanonicalAssetHydrationService:
    """
    Dormant CAS-F5-C hydration orchestrator.

    This service is intentionally not wired into WorkflowRuntime, DirectChat,
    Agent execution, or provider routing. Its caller must supply authenticated
    owner identity, canonical asset identity, and the exact server-selected
    provider name.
    """

    def __init__(
        self,
        *,
        uow_factory,
        object_store: ObjectStorageDriver,
        provider_registry,
        http_client: Any,
        timeout: Optional[float] = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.object_store = object_store
        self.provider_registry = provider_registry
        self.http_client = http_client
        self.timeout = timeout

    async def hydrate(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
        provider_name: str,
    ) -> HydrationResult:
        if not owner_user_id:
            raise AssetHydrationPreflightError(
                "owner_user_id is required"
            )
        if not asset_id:
            raise AssetHydrationPreflightError("asset_id is required")
        if not provider_name:
            raise AssetHydrationPreflightError(
                "provider_name is required"
            )

        provider, upload_outcome, provider_namespace = (
            self._resolve_exact_provider(provider_name)
        )

        initial = await self._read_canonical_snapshot(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
        )
        await self._verify_physical_object(initial)

        existing_result = await self._handle_existing_live_slot(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            provider_name=provider_name,
            provider_namespace=provider_namespace,
            snapshot=initial,
        )
        if existing_result is not None:
            return existing_result

        claim_id, claim_revision, claim_blob_id, claim_sha256, created = (
            await self._claim_processing(
                owner_user_id=owner_user_id,
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                snapshot=initial,
            )
        )
        if not created:
            return await self._result_after_claim_race(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                snapshot=initial,
            )

        # The PROCESSING claim was durably committed by _claim_processing().
        # Re-prove canonical authority and acquire the canonical stream before
        # entering the provider boundary. Proven local failure in this window
        # is SAFE_NO_REMOTE_COMMIT and must release the live slot. Fingerprint
        # drift is a separately frozen stop condition and intentionally keeps
        # the PROCESSING fence intact for lifecycle audit.
        try:
            current = await self._read_canonical_snapshot(
                owner_user_id=owner_user_id,
                asset_id=asset_id,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._release_safe_preprovider_failure(
                    asset_id=asset_id,
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                    claim_id=claim_id,
                    claim_revision=claim_revision,
                    reason="PRE_PROVIDER_CANCELLED",
                )
            )
            raise
        except Exception as exc:
            await self._release_safe_preprovider_failure(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
                reason=f"PRE_PROVIDER_{type(exc).__name__}",
            )
            raise

        if (
            current.blob_id != claim_blob_id
            or current.sha256 != claim_sha256
        ):
            return HydrationResult(
                status=HydrationStatus.HYDRATION_FINGERPRINT_DRIFT,
                binding_id=claim_id,
            )

        try:
            await self._verify_physical_object(current)
            stream = await self.object_store.open_stream(
                current.object_key
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._release_safe_preprovider_failure(
                    asset_id=asset_id,
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                    claim_id=claim_id,
                    claim_revision=claim_revision,
                    reason="PRE_PROVIDER_CANCELLED",
                )
            )
            raise
        except Exception as exc:
            await self._release_safe_preprovider_failure(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
                reason=f"PRE_PROVIDER_{type(exc).__name__}",
            )
            raise

        try:
            outcome = await upload_outcome(
                http_client=self.http_client,
                timeout=self.timeout,
                file_stream=stream,
                file_size=current.size_bytes,
                mime_type=current.mime_type,
                display_name=current.filename,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._persist_unknown_after_boundary_failure(
                    asset_id=asset_id,
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                    claim_id=claim_id,
                    claim_revision=claim_revision,
                    reason="BOUNDARY_CANCELLED",
                )
            )
            raise
        except Exception as exc:
            await self._persist_unknown_after_boundary_failure(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
                reason=type(exc).__name__,
            )
            raise AssetHydrationProviderError(
                "Provider upload outcome boundary failed unexpectedly."
            ) from exc

        if not isinstance(outcome, ProviderUploadOutcome):
            await self._persist_unknown_after_boundary_failure(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
                reason="INVALID_OUTCOME_TYPE",
            )
            raise AssetHydrationProviderError(
                "Provider upload boundary returned an invalid outcome."
            )

        if (
            outcome.kind
            is ProviderUploadOutcomeKind.SAFE_NO_REMOTE_COMMIT
        ):
            return await self._finalize_safe_failure(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
            )

        if (
            outcome.kind
            is ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN
        ):
            return await self._finalize_unknown(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
            )

        if (
            outcome.kind
            is not ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN
            or not outcome.provider_file_id
        ):
            return await self._finalize_unknown(
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
                claim_revision=claim_revision,
            )

        # Remote success is known. Never invoke the provider again in this
        # invocation, even if the canonical fingerprint or persistence fence
        # changed while the remote call was in flight.
        latest = await self._read_canonical_snapshot(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
        )
        if (
            latest.blob_id != claim_blob_id
            or latest.sha256 != claim_sha256
        ):
            return HydrationResult(
                status=HydrationStatus.HYDRATION_FINGERPRINT_DRIFT,
                binding_id=claim_id,
                provider_file_id=outcome.provider_file_id,
                provider_uri=outcome.provider_uri,
            )

        return await self._finalize_success(
            asset_id=asset_id,
            provider_name=provider_name,
            provider_namespace=provider_namespace,
            claim_id=claim_id,
            claim_revision=claim_revision,
            outcome=outcome,
            mime_type=latest.mime_type,
        )

    def _resolve_exact_provider(self, provider_name: str):
        provider = self.provider_registry.get_provider(provider_name)
        if provider is None:
            raise AssetHydrationPreflightError(
                f"Provider '{provider_name}' is not configured."
            )
        if getattr(provider, "name", provider_name) != provider_name:
            raise AssetHydrationPreflightError(
                "Provider registry returned a non-exact provider."
            )

        files = getattr(provider, "files", None)
        upload_outcome = getattr(files, "upload_file_outcome", None)
        if not callable(upload_outcome):
            raise AssetHydrationPreflightError(
                "Provider does not expose the CAS-F5 upload outcome boundary."
            )
        implementation = getattr(
            type(files), "upload_file_outcome", None
        )
        if implementation is FileProvider.upload_file_outcome:
            raise AssetHydrationPreflightError(
                "Provider has not implemented the CAS-F5 upload outcome boundary."
            )

        config = getattr(provider, "config", None)
        namespace = AssetRepository.require_provider_binding_namespace(
            getattr(config, "file_binding_namespace", None)
        )
        return provider, upload_outcome, namespace

    async def _read_canonical_snapshot(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
    ) -> _CanonicalBlobSnapshot:
        try:
            async with self.uow_factory() as uow:
                file_record, blob = await uow.assets.get_owned_ready_file(
                    file_id=asset_id,
                    owner_user_id=owner_user_id,
                )
                self._validate_blob_integrity(blob)
                return _CanonicalBlobSnapshot(
                    blob_id=blob.id,
                    sha256=blob.sha256,
                    object_key=blob.object_key,
                    size_bytes=int(blob.size_bytes),
                    filename=file_record.filename,
                    mime_type=file_record.mime_type,
                )
        except (KeyError, PermissionError, ValueError) as exc:
            raise AssetHydrationPreflightError(str(exc)) from exc

    @staticmethod
    def _validate_blob_integrity(blob) -> None:
        if blob.state != "READY":
            raise ValueError("Canonical blob is not READY.")
        if not isinstance(blob.object_key, str) or not blob.object_key:
            raise ValueError("Canonical READY blob lacks object_key.")
        if blob.size_bytes is None or int(blob.size_bytes) < 0:
            raise ValueError("Canonical READY blob lacks valid size.")
        sha256 = blob.sha256
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in sha256)
        ):
            raise ValueError("Canonical READY blob lacks valid sha256.")

    async def _verify_physical_object(
        self,
        snapshot: _CanonicalBlobSnapshot,
    ) -> None:
        try:
            stat = await self.object_store.stat(snapshot.object_key)
        except Exception as exc:
            raise AssetHydrationPreflightError(
                "Canonical object is unavailable."
            ) from exc
        if stat.size_bytes != snapshot.size_bytes:
            raise AssetHydrationPreflightError(
                "Canonical object size does not match READY blob metadata."
            )

    async def _handle_existing_live_slot(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        snapshot: _CanonicalBlobSnapshot,
    ) -> Optional[HydrationResult]:
        async with self.uow_factory() as uow:
            live = await uow.assets.get_live_provider_binding(
                asset_id,
                provider_name,
                provider_namespace=provider_namespace,
            )
            if live is None:
                return None

            result = self._result_for_live_binding(live, snapshot)
            if result is not None:
                return result

            # The only live state intentionally reaching this point is an
            # invalid/expired ACTIVE row. Release the live slot with CAS and
            # commit before any replacement PROCESSING claim is attempted.
            retired = await uow.assets.expire_active_provider_binding(
                live.id,
                expected_revision=live.revision,
            )
            if retired is None:
                await uow.rollback()
                return await self._reread_race_winner(
                    asset_id=asset_id,
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                    snapshot=snapshot,
                )
            await uow.commit()
            return None

    @staticmethod
    def _result_for_live_binding(
        binding,
        snapshot: _CanonicalBlobSnapshot,
    ) -> Optional[HydrationResult]:
        if binding.state == "PROCESSING":
            return HydrationResult(
                status=HydrationStatus.HYDRATION_IN_PROGRESS,
                binding_id=binding.id,
            )
        if binding.state == "UNKNOWN":
            return HydrationResult(
                status=HydrationStatus.HYDRATION_OUTCOME_UNKNOWN,
                binding_id=binding.id,
            )
        if binding.state == "ACTIVE":
            fingerprint_matches = (
                binding.provider_file_id
                and binding.source_blob_id == snapshot.blob_id
                and binding.source_sha256 == snapshot.sha256
            )
            if fingerprint_matches:
                expires_at = binding.expires_at
                valid_expiry = expires_at is None
                if expires_at is not None:
                    now = datetime.now(timezone.utc)
                    if expires_at.tzinfo is None:
                        now = now.replace(tzinfo=None)
                    valid_expiry = expires_at > now
                if valid_expiry:
                    return HydrationResult(
                        status=HydrationStatus.REUSED,
                        binding_id=binding.id,
                        provider_file_id=binding.provider_file_id,
                        provider_uri=binding.provider_uri,
                        mime_type=snapshot.mime_type,
                    )
            return None
        return HydrationResult(
            status=HydrationStatus.HYDRATION_RACE_LOST,
            binding_id=binding.id,
        )

    async def _reread_race_winner(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        snapshot: _CanonicalBlobSnapshot,
    ) -> HydrationResult:
        async with self.uow_factory() as uow:
            winner = await uow.assets.get_live_provider_binding(
                asset_id,
                provider_name,
                provider_namespace=provider_namespace,
            )
            if winner is None:
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_RACE_LOST
                )
            return (
                self._result_for_live_binding(winner, snapshot)
                or HydrationResult(
                    status=HydrationStatus.HYDRATION_RACE_LOST,
                    binding_id=winner.id,
                )
            )

    async def _claim_processing(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        snapshot: _CanonicalBlobSnapshot,
    ) -> tuple[str, int, str, str, bool]:
        async with self.uow_factory() as uow:
            claim, created = (
                await uow.assets.try_create_processing_provider_binding(
                    file_id=asset_id,
                    owner_user_id=owner_user_id,
                    provider_name=provider_name,
                    provider_namespace=provider_namespace,
                )
            )
            if not created:
                claim_snapshot = (
                    claim.id,
                    claim.revision,
                    claim.source_blob_id or snapshot.blob_id,
                    claim.source_sha256 or snapshot.sha256,
                    False,
                )
                await uow.rollback()
                return claim_snapshot

            claim_id = claim.id
            claim_revision = claim.revision
            claim_blob_id = claim.source_blob_id
            claim_sha256 = claim.source_sha256
            await uow.commit()
            return (
                claim_id,
                claim_revision,
                claim_blob_id,
                claim_sha256,
                True,
            )

    async def _result_after_claim_race(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        snapshot: _CanonicalBlobSnapshot,
    ) -> HydrationResult:
        return await self._reread_race_winner(
            asset_id=asset_id,
            provider_name=provider_name,
            provider_namespace=provider_namespace,
            snapshot=snapshot,
        )

    @staticmethod
    def _metadata_for(
        binding,
        marker: str,
        *,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        metadata = dict(binding.metadata_json or {})
        metadata["upload_outcome"] = marker
        if reason:
            metadata["upload_outcome_reason"] = reason
        return metadata

    async def _load_owned_claim(
        self,
        uow,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        claim_id: str,
    ):
        live = await uow.assets.get_live_provider_binding(
            asset_id,
            provider_name,
            provider_namespace=provider_namespace,
        )
        if live is None or live.id != claim_id:
            return None
        return live

    async def _finalize_safe_failure(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        claim_id: str,
        claim_revision: int,
        reason: Optional[str] = None,
    ) -> HydrationResult:
        async with self.uow_factory() as uow:
            live = await self._load_owned_claim(
                uow,
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
            )
            if live is None:
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT,
                    binding_id=claim_id,
                )
            updated = await uow.assets.compare_and_set_provider_binding(
                claim_id,
                expected_revision=claim_revision,
                expected_state="PROCESSING",
                expected_live_claim_token="LIVE",
                values={
                    "state": "ERROR",
                    "live_claim_token": None,
                    "metadata_json": self._metadata_for(
                        live,
                        ProviderUploadOutcomeKind.SAFE_NO_REMOTE_COMMIT.value,
                        reason=reason,
                    ),
                },
            )
            if updated is None:
                await uow.rollback()
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT,
                    binding_id=claim_id,
                )
            await uow.commit()
            return HydrationResult(
                status=HydrationStatus.HYDRATION_FAILED_SAFE,
                binding_id=updated.id,
            )

    async def _release_safe_preprovider_failure(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        claim_id: str,
        claim_revision: int,
        reason: str,
    ) -> HydrationResult:
        bounded_reason = reason[:128]
        return await self._finalize_safe_failure(
            asset_id=asset_id,
            provider_name=provider_name,
            provider_namespace=provider_namespace,
            claim_id=claim_id,
            claim_revision=claim_revision,
            reason=bounded_reason,
        )

    async def _finalize_unknown(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        claim_id: str,
        claim_revision: int,
        reason: Optional[str] = None,
    ) -> HydrationResult:
        async with self.uow_factory() as uow:
            live = await self._load_owned_claim(
                uow,
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
            )
            if live is None:
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT,
                    binding_id=claim_id,
                )
            updated = await uow.assets.compare_and_set_provider_binding(
                claim_id,
                expected_revision=claim_revision,
                expected_state="PROCESSING",
                expected_live_claim_token="LIVE",
                values={
                    "state": "UNKNOWN",
                    "live_claim_token": "LIVE",
                    "metadata_json": self._metadata_for(
                        live,
                        ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN.value,
                        reason=reason,
                    ),
                },
            )
            if updated is None:
                await uow.rollback()
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT,
                    binding_id=claim_id,
                )
            await uow.commit()
            return HydrationResult(
                status=HydrationStatus.HYDRATION_OUTCOME_UNKNOWN,
                binding_id=updated.id,
            )

    async def _persist_unknown_after_boundary_failure(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        claim_id: str,
        claim_revision: int,
        reason: str,
    ) -> None:
        await self._finalize_unknown(
            asset_id=asset_id,
            provider_name=provider_name,
            provider_namespace=provider_namespace,
            claim_id=claim_id,
            claim_revision=claim_revision,
            reason=reason,
        )

    async def _finalize_success(
        self,
        *,
        asset_id: str,
        provider_name: str,
        provider_namespace: str,
        claim_id: str,
        claim_revision: int,
        outcome: ProviderUploadOutcome,
        mime_type: str,
    ) -> HydrationResult:
        async with self.uow_factory() as uow:
            live = await self._load_owned_claim(
                uow,
                asset_id=asset_id,
                provider_name=provider_name,
                provider_namespace=provider_namespace,
                claim_id=claim_id,
            )
            if live is None:
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT,
                    binding_id=claim_id,
                    provider_file_id=outcome.provider_file_id,
                    provider_uri=outcome.provider_uri,
                )

            updated = await uow.assets.compare_and_set_provider_binding(
                claim_id,
                expected_revision=claim_revision,
                expected_state="PROCESSING",
                expected_live_claim_token="LIVE",
                values={
                    "state": "ACTIVE",
                    "provider_file_id": outcome.provider_file_id,
                    "provider_uri": outcome.provider_uri,
                    "live_claim_token": "LIVE",
                    "metadata_json": self._metadata_for(
                        live,
                        ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN.value,
                    ),
                },
            )
            if updated is None:
                await uow.rollback()
                return HydrationResult(
                    status=HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT,
                    binding_id=claim_id,
                    provider_file_id=outcome.provider_file_id,
                    provider_uri=outcome.provider_uri,
                )
            await uow.commit()
            return HydrationResult(
                status=HydrationStatus.HYDRATED,
                binding_id=updated.id,
                provider_file_id=updated.provider_file_id,
                provider_uri=updated.provider_uri,
                mime_type=mime_type,
            )
