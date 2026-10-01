from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, AsyncIterator

from ...domain.schemas.attachment import GatewayAttachment, UrlContent
from ...domain.schemas.response import GatewayResponse


class GeneratedMediaCanonicalizationError(RuntimeError):
    """Terminal post-provider-success failure for CAS-F7-P1 canonicalization."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _GeneratedMediaRef:
    choice_position: int
    part_position: int
    source_kind: str
    attachment: GatewayAttachment | None


class GeneratedAssetCanonicalizer:
    """Canonicalize the bounded first F7-P1 non-stream generated-media slice.

    This component runs only after a provider attempt has succeeded. It never
    participates in provider selection/fallback/circuit-breaker accounting.
    """

    def __init__(
        self,
        *,
        asset_service: Any | None,
        max_bytes: int | None,
        strict_response_type: bool = True,
    ) -> None:
        self._asset_service = asset_service
        self._strict_response_type = bool(strict_response_type)
        self._max_bytes = (
            int(max_bytes)
            if max_bytes is not None and int(max_bytes) > 0
            else None
        )

    @classmethod
    def unavailable(cls) -> "GeneratedAssetCanonicalizer":
        """Install the response fence even when CAS persistence is unavailable."""

        return cls(
            asset_service=None,
            max_bytes=None,
            strict_response_type=False,
        )

    @staticmethod
    def _attachment_from_part(part: Any) -> GatewayAttachment | None:
        data = getattr(part, "data", None)
        if isinstance(data, GatewayAttachment):
            return data
        attachment = getattr(data, "attachment", None)
        if isinstance(attachment, GatewayAttachment):
            return attachment
        return None

    @classmethod
    def _scan_generated_media(
        cls,
        response: GatewayResponse,
    ) -> list[_GeneratedMediaRef]:
        refs: list[_GeneratedMediaRef] = []
        for choice_position, choice in enumerate(response.choices):
            content = getattr(choice.message, "content", None)
            if not isinstance(content, list):
                continue
            for part_position, part in enumerate(content):
                data = getattr(part, "data", None)
                if isinstance(data, UrlContent):
                    # Ordinary URL content is not generated-media provenance.
                    # Gemini generated fileData is preserved separately as a
                    # GatewayAttachment(source="provider") in the non-stream path.
                    continue

                attachment = cls._attachment_from_part(part)
                if attachment is None:
                    continue
                # Canonical assets are already durable identities and must not
                # be re-ingested as provider-generated transport.
                if attachment.source == "asset":
                    continue
                refs.append(
                    _GeneratedMediaRef(
                        choice_position=choice_position,
                        part_position=part_position,
                        source_kind=attachment.source,
                        attachment=attachment,
                    )
                )
        return refs

    def _decode_admitted_payload(self, attachment: GatewayAttachment) -> bytes:
        if attachment.source != "base64":
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_SOURCE_CLASS_CLOSED",
                (
                    "Generated fileData/URL/remote-handle transport is "
                    "excluded from the initial CAS-F7-P1 slice."
                ),
            )

        inline_representations = int(attachment.bytes_data is not None) + int(
            attachment.base64_data is not None
        )
        carries_remote_identity = (
            attachment.uri not in (None, "")
            or attachment.provider_file_id not in (None, "")
        )
        if inline_representations != 1 or carries_remote_identity:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_AMBIGUOUS_INLINE_TRANSPORT",
                (
                    "Generated inline media must carry exactly one inline byte "
                    "representation and no URI/provider-file identity."
                ),
            )

        if attachment.bytes_data is not None:
            payload = bytes(attachment.bytes_data)
        elif attachment.base64_data is not None:
            encoded_text = attachment.base64_data
            if not isinstance(encoded_text, str) or not encoded_text:
                raise GeneratedMediaCanonicalizationError(
                    "CAS_F7_INVALID_INLINE_BYTES",
                    "Generated inline media payload is empty or invalid.",
                )
            try:
                encoded = encoded_text.encode("ascii")
            except UnicodeEncodeError as exc:
                raise GeneratedMediaCanonicalizationError(
                    "CAS_F7_INVALID_INLINE_BYTES",
                    "Generated inline media payload is not valid base64.",
                ) from exc

            if self._max_bytes is not None:
                max_encoded = 4 * ((self._max_bytes + 2) // 3)
                if len(encoded) > max_encoded:
                    raise GeneratedMediaCanonicalizationError(
                        "CAS_F7_MEDIA_TOO_LARGE",
                        "Generated media exceeds the configured asset bound.",
                    )
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise GeneratedMediaCanonicalizationError(
                    "CAS_F7_INVALID_INLINE_BYTES",
                    "Generated inline media payload is not valid base64.",
                ) from exc
        else:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_INVALID_INLINE_BYTES",
                "Generated inline media has no complete byte payload.",
            )

        if self._max_bytes is not None and len(payload) > self._max_bytes:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_MEDIA_TOO_LARGE",
                "Generated media exceeds the configured asset bound.",
            )
        return payload

    @staticmethod
    async def _single_chunk(payload: bytes) -> AsyncIterator[bytes]:
        yield payload

    async def canonicalize(
        self,
        response: Any,
        *,
        owner_user_id: str | None,
    ) -> Any:
        if not isinstance(response, GatewayResponse):
            if not self._strict_response_type:
                return response
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_INVALID_GATEWAY_RESPONSE",
                "Provider success did not produce a GatewayResponse.",
            )

        refs = self._scan_generated_media(response)
        if not refs:
            return response

        if len(refs) > 1:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_MULTI_OBJECT_UNSUPPORTED",
                (
                    "Multiple generated-media objects are unsupported in the "
                    "initial CAS-F7-P1 slice."
                ),
            )

        ref = refs[0]
        if ref.choice_position != 0:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_NON_SELECTED_CHOICE_MEDIA",
                (
                    "Generated media outside consumer-selected choice 0 is "
                    "unsupported in the initial CAS-F7-P1 slice."
                ),
            )
        if not response.choices:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_MISSING_SELECTED_CHOICE",
                "Generated media requires response.choices[0].",
            )

        selected = response.choices[0]
        if getattr(selected, "index", 0) != 0:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_INVALID_SELECTED_CHOICE",
                "Generated media requires selected provider choice index 0.",
            )
        if getattr(selected.message, "role", None) != "assistant":
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_INVALID_MEDIA_ROLE",
                "Generated media is eligible only on an assistant response.",
            )
        if selected.message.tool_calls:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_NONTERMINAL_TOOL_CALL_MEDIA",
                (
                    "Generated media on a tool-call-bearing intermediate "
                    "response is unsupported."
                ),
            )

        attachment = ref.attachment
        if attachment is None or ref.source_kind != "base64":
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_SOURCE_CLASS_CLOSED",
                (
                    "Generated fileData/URL/remote-handle transport is "
                    "excluded from the initial CAS-F7-P1 slice."
                ),
            )

        if not owner_user_id:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_OWNER_REQUIRED",
                "Authenticated owner authority is required for generated media.",
            )
        if self._asset_service is None or self._max_bytes is None:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_PERSISTENCE_UNAVAILABLE",
                (
                    "Generated media cannot be returned while canonical asset "
                    "persistence is unavailable."
                ),
            )

        payload = self._decode_admitted_payload(attachment)
        filename = attachment.filename or "generated-media"
        mime_type = attachment.mime_type
        if not mime_type:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_MIME_REQUIRED",
                "Generated media requires an explicit MIME type.",
            )

        provider_response_id = getattr(
            getattr(response, "metadata", None),
            "provider_response_id",
            None,
        )
        provider_name = getattr(
            getattr(response, "metadata", None),
            "provider",
            None,
        )

        try:
            descriptor = await self._asset_service.ingest_stream(
                owner_user_id=owner_user_id,
                filename=filename,
                mime_type=mime_type,
                stream=self._single_chunk(payload),
                content_length=len(payload),
                max_bytes=self._max_bytes,
                origin_type="ASSISTANT",
                origin_id=provider_response_id,
                metadata={
                    "provider": provider_name,
                    "gateway_response_id": response.id,
                    "source_attachment_id": attachment.id,
                    "source_transport": "inline",
                },
            )
        except Exception as exc:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_INGEST_FAILED",
                "Generated media could not be canonicalized.",
            ) from exc

        if getattr(descriptor, "state", None) != "READY":
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_INGEST_NOT_READY",
                "Generated media ingest did not produce a READY asset.",
            )

        canonical_metadata = attachment.metadata.model_copy(
            update={"checksum_sha256": getattr(descriptor, "sha256", None)}
        )
        canonical_attachment = GatewayAttachment(
            id=attachment.id,
            asset_id=descriptor.asset_id,
            filename=descriptor.filename,
            mime_type=descriptor.mime_type,
            size=descriptor.size_bytes,
            extension=attachment.extension,
            uri=descriptor.uri,
            source="asset",
            metadata=canonical_metadata,
        )

        canonical_response = response.model_copy(deep=True)
        selected_copy = canonical_response.choices[0]
        content_copy = selected_copy.message.content
        if not isinstance(content_copy, list):
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_RESPONSE_SHAPE_CHANGED",
                "Generated media response shape changed during canonicalization.",
            )
        part_copy = content_copy[ref.part_position]
        data_copy = getattr(part_copy, "data", None)
        if isinstance(data_copy, GatewayAttachment):
            part_copy.data = canonical_attachment
        elif isinstance(
            getattr(data_copy, "attachment", None),
            GatewayAttachment,
        ):
            data_copy.attachment = canonical_attachment
        else:
            raise GeneratedMediaCanonicalizationError(
                "CAS_F7_RESPONSE_SHAPE_CHANGED",
                "Generated media attachment disappeared before substitution.",
            )

        # Provider raw payload may contain the transient base64/file identity.
        # It is non-authoritative provenance and must not escape after a
        # successful canonical substitution.
        canonical_response.metadata.raw_response = None
        return canonical_response
