"""CI boundary regression: physical AE359 owns its PG17/Redis fixtures.

The Architecture baseline remains PostgreSQL-independent for the separate UBQ2
parity/deployment oracle. All physical AE359 cases must instead run in unfiltered
PR workflows. GitHub required status checks are an external merge-admission gate,
not established by this repository-level contract test.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
CASES = {
    "se/tests/integration/test_ae359_utc_naive_pg17_redfirst.py": (
        ".github/workflows/ae359-utc-naive-pg17-redfirst.yml",
        "AE359_TZ_PG17_STRICT",
    ),
    "se/tests/integration/test_auth_otp_db_atomic_consume_pg17_redfirst.py": (
        ".github/workflows/ae359-otp-db-atomic-pg17.yml",
        "AE359_PG17_STRICT",
    ),
    "se/tests/integration/test_ae359_post_ack_loss_physical.py": (
        ".github/workflows/ae359-post-ack-loss-physical.yml",
        "AE359_POSTACK_STRICT",
    ),
}


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_architecture_baseline_keeps_ubq2_fixture_barrier_and_explicit_split() -> None:
    workflow = _read(".github/workflows/architecture-baseline.yml")
    # Do not loosen the existing UBQ2 no-PostgreSQL-in-Architecture oracle.
    assert "postgres:" not in workflow.lower()
    assert "AE359_TZ_PG17_URL" not in workflow
    assert "AE359_PG17_TEST_DATABASE_URL" not in workflow
    assert "AE359_POSTACK_PG_URL" not in workflow
    assert "python -m pytest -q" in workflow
    ignored = re.findall(r"--ignore=([^\s]+)", workflow)
    assert set(ignored) == set(CASES), (
        "The Architecture split must list exactly the three physical cases, "
        "not expand into unrelated test exclusions."
    )
    assert len(ignored) == len(CASES)


def test_every_physical_suite_runs_unfiltered_for_all_prs() -> None:
    for case, (workflow_path, strict_env) in CASES.items():
        workflow = _read(workflow_path)
        assert re.search(
            r"(?m)^on:\n  pull_request:\n  workflow_dispatch:", workflow
        ), f"{workflow_path} must run on every pull_request"
        assert not re.search(
            r"(?m)^\s+(?:paths|paths-ignore):", workflow
        ), f"{workflow_path} must not silently skip PRs by path"
        assert case in workflow, f"{case} lost its dedicated physical runner"
        assert strict_env in workflow, f"{workflow_path} lost strict admission"
        assert "AE359_EXPECTED_SHA:" in workflow
        assert "git rev-parse HEAD" in workflow
