from typing import Literal, Optional

from pydantic import Field, model_validator

from .base import GatewayBaseModel


class FileMetadata(GatewayBaseModel):
    """Metadata chi tiết cho một tệp đính kèm."""

    page_count: Optional[int] = None
    language: Optional[str] = None
    encoding: Optional[str] = None
    checksum_sha256: Optional[str] = Field(None, alias="sha256")
    created_at: Optional[int] = None
    modified_at: Optional[int] = None


class GatewayAttachment(GatewayBaseModel):
    """Provider-neutral attachment descriptor.

    asset_id is the only canonical Assistant-owned durable identity.
    Legacy id remains compatibility data and is never an asset id.
    """

    id: Optional[str] = None
    asset_id: Optional[str] = None
    filename: Optional[str] = None
    mime_type: str
    size: Optional[int] = None
    extension: Optional[str] = None
    uri: Optional[str] = None
    base64_data: Optional[str] = None
    bytes_data: Optional[bytes] = None
    provider_file_id: Optional[str] = None
    source: Literal[
        "local",
        "url",
        "base64",
        "provider",
        "memory",
        "asset",
    ] = "local"
    metadata: FileMetadata = Field(default_factory=FileMetadata)

    @model_validator(mode="before")
    @classmethod
    def _validate_canonical_asset_shape(cls, value):
        if not isinstance(value, dict):
            return value

        data = dict(value)
        asset_id = data.get("asset_id")
        source = data.get("source")

        if asset_id:
            if source not in (None, "asset", ""):
                raise ValueError(
                    "asset_id requires source='asset'; legacy/provider identities "
                    "must not be mixed with canonical assets."
                )
            expected_uri = f"asset://{asset_id}"
            uri = data.get("uri")
            if uri not in (None, "", expected_uri):
                raise ValueError(
                    "Canonical asset uri must match asset://<asset_id>."
                )
            for field in ("base64_data", "bytes_data", "provider_file_id"):
                if data.get(field) not in (None, ""):
                    raise ValueError(
                        f"Canonical asset attachment cannot carry {field}."
                    )
            data["source"] = "asset"
            data["uri"] = expected_uri
            return data

        if source == "asset":
            raise ValueError("source='asset' requires asset_id.")
        return data


class DocumentContent(GatewayBaseModel):
    attachment: GatewayAttachment
    page_range: Optional[str] = None
    extracted_text: Optional[str] = None


class ImageContent(GatewayBaseModel):
    attachment: GatewayAttachment
    detail: Literal["auto", "low", "high"] = "auto"


class AudioContent(GatewayBaseModel):
    attachment: GatewayAttachment
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    duration_seconds: Optional[float] = None


class VideoContent(GatewayBaseModel):
    attachment: GatewayAttachment
    duration_seconds: Optional[float] = None


class UrlContent(GatewayBaseModel):
    url: str
    crawl: bool = True
    max_depth: int = 0
    extract_main_content: bool = True
    title: Optional[str] = None
