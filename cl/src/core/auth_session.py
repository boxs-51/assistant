from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


class AuthSessionStore:
    """Durable store for the desktop client's user or guest session."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path else self.default_path()

    @staticmethod
    def default_path() -> Path:
        override = os.environ.get("ASSISTANT_CLIENT_SESSION_PATH")
        if override:
            return Path(override).expanduser()
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        return root / "AssistantClient" / "auth-session.json"

    def load(self) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        return data

    def save(self, session: dict[str, Any]) -> None:
        payload = {
            key: session.get(key)
            for key in (
                "access_token",
                "refresh_token",
                "principal_type",
                "user_id",
                "expires_at",
            )
            if session.get(key) is not None
        }
        if not payload.get("access_token"):
            raise ValueError("An access token is required to persist an auth session.")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temporary_path.chmod(0o600)
            except OSError:
                pass
            temporary_path.replace(self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
