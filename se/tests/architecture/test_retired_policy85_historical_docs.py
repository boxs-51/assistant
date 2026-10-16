"""Documentation-only guard against reviving retired repository Agent Policy #85.

Do not parse historical technical contracts as current governance. This oracle
does not modify or weaken any existing architecture/security assertion.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
NOTICE_START = "<!-- POLICY85_HISTORICAL_GOVERNANCE_20261016:START -->"
NOTICE_END = "<!-- POLICY85_HISTORICAL_GOVERNANCE_20261016:END -->"
ARCHIVE = ROOT / "docs/policy/DEVELOPER_AGENT_HARNESS_V3_2.md"


def test_historical_policy85_markdown_references_are_explicitly_disclaimed() -> None:
    matched = []
    for folder in (ROOT / "docs", ROOT / "tools/v1"):
        for path in sorted(folder.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            if "#85" not in text:
                continue
            if path == ARCHIVE:
                assert "ARCHIVED" in text and "not in effect" in text
                continue
            matched.append(path)
            assert text.startswith(NOTICE_START + "\n"), str(path.relative_to(ROOT))
            assert NOTICE_END in text, str(path.relative_to(ROOT))
            top = text.split(NOTICE_END, 1)[0]
            assert "RETIRED" in top and "NON-NORMATIVE" in top
            assert "P0/P1/HOLD" in top and "test expectations" in top

    assert matched, "No historical #85 Markdown reference was exercised"


def test_root_agent_instructions_do_not_reactivate_historical_governance() -> None:
    root_instructions = (ROOT / "AGENT.md").read_text(encoding="utf-8")
    assert "no active repository governance" in root_instructions
    assert "No historical version automatically applies as a fallback" in root_instructions
