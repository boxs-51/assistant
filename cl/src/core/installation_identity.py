from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional


class InstallationIdentityStore:
    """Durable client installation identity, independent from auth sessions."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path else self.default_path()

    @staticmethod
    def default_path() -> Path:
        override = os.environ.get("ASSISTANT_CLIENT_IDENTITY_PATH")
        if override:
            return Path(override).expanduser()
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        return root / "AssistantClient" / "installation.json"

    def load_or_create(self) -> str:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")).get("client_id")
            if isinstance(value, str) and value.strip():
                return value
        except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
            pass
        value = f"client-{uuid.uuid4().hex}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"client_id": value}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return value


__all__ = ["InstallationIdentityStore"]
