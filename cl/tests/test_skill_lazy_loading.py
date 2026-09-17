from pathlib import Path

import pytest

from cl.src.loader.skills_loader import SkillManager, SkillNotFoundError


def test_skill_discovery_reads_metadata_without_loading_body(monkeypatch):
    original_read_text = Path.read_text
    body_reads = []

    def tracked_read_text(path, *args, **kwargs):
        body_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    manager = SkillManager("skills")
    discovered = manager.load_skills()

    assert body_reads == []
    assert discovered["code-review-workflow"]["content"] is None
    assert discovered["code-review-workflow"]["loaded"] is False
    assert "kiểm tra" in discovered["code-review-workflow"]["description"]

    loaded = manager.load_skill("code-review-workflow")
    assert len(body_reads) == 1
    assert "CODE REVIEW" in loaded["content"]
    assert loaded["loaded"] is True


def test_skill_cache_unload_and_missing_contract():
    manager = SkillManager("skills")
    manager.load_skills()

    first = manager.load_skill("deploy-app")
    second = manager.load_skill("deploy-app")
    assert first["content"] == second["content"]
    assert manager.unload_skill("deploy-app") is True
    assert manager.get_skill("deploy-app")["content"] is None

    with pytest.raises(SkillNotFoundError):
        manager.load_skill("missing")
