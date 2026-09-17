from __future__ import annotations

import re
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional


class SkillNotFoundError(LookupError):
    pass


class SkillManager:
    """Discover skill metadata eagerly and load instruction bodies on demand."""

    _FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$")

    def __init__(self, skills_dir: str):
        self.skills_dir = Path(skills_dir).resolve()
        self._skills: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()

    @staticmethod
    def _skill_name(path: Path, metadata: Dict[str, str], root: Path) -> str:
        name = metadata.get("name", "").strip()
        if name:
            return name
        return path.parent.name if path.parent != root else path.stem

    def _read_frontmatter(self, path: Path) -> Dict[str, str]:
        """Read only the YAML-like header; the instruction body remains unloaded."""
        metadata: Dict[str, str] = {}
        with path.open("r", encoding="utf-8") as stream:
            first = stream.readline()
            if first.strip() != "---":
                candidates = [first]
                for _ in range(31):
                    line = stream.readline()
                    if not line:
                        break
                    candidates.append(line)
            else:
                candidates = []
                for _ in range(128):
                    line = stream.readline()
                    if not line or line.strip() == "---":
                        break
                    candidates.append(line)

        for line in candidates:
            match = self._FIELD_RE.match(line.strip())
            if match:
                metadata[match.group(1).lower()] = match.group(2).strip(" \"'")
        return metadata

    def load_skills(self) -> Dict[str, Dict[str, Any]]:
        """Discover available skills without loading their full instructions."""
        discovered: Dict[str, Dict[str, Any]] = {}
        if not self.skills_dir.exists():
            with self._lock:
                self._skills = {}
            return {}

        paths = sorted(self.skills_dir.glob("*.md")) + sorted(self.skills_dir.glob("*/*.md"))
        for path in paths:
            resolved = path.resolve()
            if self.skills_dir not in resolved.parents:
                continue
            try:
                metadata = self._read_frontmatter(resolved)
                name = self._skill_name(resolved, metadata, self.skills_dir)
                if not name:
                    continue
                stat = resolved.stat()
                discovered[name] = {
                    "name": name,
                    "description": metadata.get("description", name),
                    "version": metadata.get("version", "1.0"),
                    "base_risk": metadata.get("base_risk", "MEDIUM").upper(),
                    "path": str(resolved),
                    "loaded": False,
                    "content": None,
                    "mtime_ns": stat.st_mtime_ns,
                }
            except (OSError, UnicodeError):
                continue

        with self._lock:
            self._skills = discovered
            return {name: dict(item) for name, item in discovered.items()}

    def load_skill(self, name: str, *, force: bool = False) -> Dict[str, Any]:
        """Load and cache one skill body when it is explicitly activated."""
        with self._lock:
            skill = self._skills.get(name)
            if skill is None:
                raise SkillNotFoundError(f"Skill '{name}' was not discovered.")
            path = Path(skill["path"]).resolve()

            if self.skills_dir not in path.parents:
                raise PermissionError("Skill path escapes the configured skills directory.")

            current_mtime = path.stat().st_mtime_ns
            if skill.get("loaded") and not force and skill.get("mtime_ns") == current_mtime:
                return dict(skill)

            content = path.read_text(encoding="utf-8")
            skill = {
                **skill,
                "content": content,
                "loaded": True,
                "mtime_ns": current_mtime,
            }
            self._skills[name] = skill
            return dict(skill)

    def get_skill(self, name: str, *, load: bool = False) -> Optional[Dict[str, Any]]:
        if load:
            return self.load_skill(name)
        with self._lock:
            skill = self._skills.get(name)
            return dict(skill) if skill is not None else None

    def unload_skill(self, name: str) -> bool:
        with self._lock:
            skill = self._skills.get(name)
            if skill is None:
                return False
            self._skills[name] = {**skill, "content": None, "loaded": False}
            return True
