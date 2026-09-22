from __future__ import annotations

import json


_SAMPLE_LIMIT = 8192
_OCTET = "application/octet-stream"

_ALIASES = {
    "image/jpg": "image/jpeg",
    "application/x-json": "application/json",
}

_ZIP_CONTAINER_TYPES = {
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def normalize_mime_type(value: str | None) -> str:
    raw = (value or "").split(";", 1)[0].strip().lower()
    if not raw:
        return _OCTET
    return _ALIASES.get(raw, raw)


def detect_mime_type(sample: bytes) -> str:
    data = bytes(sample[:_SAMPLE_LIMIT])
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "application/ogg"
    if data.startswith(b"ID3"):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip"

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _OCTET

    if "\x00" in text:
        return _OCTET

    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            return "application/json"

    if data:
        control = sum(
            1
            for byte in data
            if byte < 32 and byte not in {9, 10, 13}
        )
        if control / len(data) > 0.02:
            return _OCTET
    return "text/plain"


def mime_types_compatible(declared: str, detected: str) -> bool:
    declared = normalize_mime_type(declared)
    detected = normalize_mime_type(detected)
    if declared == detected:
        return True
    if declared == _OCTET or detected == _OCTET:
        return True
    if declared in _ZIP_CONTAINER_TYPES and detected == "application/zip":
        return True
    if declared == "text/plain" and detected == "application/json":
        return True
    return False


def canonical_mime_type(declared: str, detected: str) -> str:
    declared = normalize_mime_type(declared)
    detected = normalize_mime_type(detected)
    if declared == _OCTET and detected != _OCTET:
        return detected
    return declared


__all__ = [
    "canonical_mime_type",
    "detect_mime_type",
    "mime_types_compatible",
    "normalize_mime_type",
]
