"""Compatibility entry point for manifest-backed server support discovery."""
from pathlib import Path

from .local_support_loader import LocalSupportLoader


def register_builtin_support(
    container,
    *,
    skills_dir: Path | None = None,
    agents_dir: Path | None = None,
) -> dict[str, list[str]]:
    root = Path(__file__).resolve().parents[4]
    loader = LocalSupportLoader(
        container,
        skills_dir or root / "skills" / "v1",
        agents_dir or root / "agents" / "v1",
    )
    result = loader.discover()
    container.support_loader = loader
    container.agent_registry.set_loader(loader.load_agent)
    return result


__all__ = ["LocalSupportLoader", "register_builtin_support"]
