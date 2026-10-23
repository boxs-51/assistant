"""S04 P1: strict Redis ACL/Sentinel tests must not contaminate portable suites.

ADD-only routing guards. Preserve frozen S03 ignore/deselect and security oracle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = "se/tests/integration/test_alpha_s04_acl_restart_sentinel_physical.py"
FILTER = "--ignore-glob=" + MODULE
LANES = (
    ".github/workflows/architecture-baseline.yml",
    ".github/workflows/alpha-s03-owner-real-schema.yml",
)
PHYSICAL = (
    (".github/workflows/alpha-s04-redis-acl-restart-physical.yml",
     "S04_ACL_RESTART_STRICT", "test_s04_acl_restart_writer_epoch_persistence"),
    (".github/workflows/alpha-s04-redis-sentinel-acl-physical.yml",
     "S04_SENTINEL_STRICT", "test_s04_sentinel_automatic_promotion_writer_acl"),
)


def _validate_routing(source: str) -> None:
    assert source.count(FILTER) == 1, (
        "S04_HARNESS_ERROR: missing/duplicate Redis ACL physical handoff")
    # Existing immutable S03 routing remains four exact --ignore and 8 deselect.
    assert source.count("--ignore=") == 4, (
        "S04_HARNESS_ERROR: frozen S03 physical routing changed")
    assert source.count("--deselect=") == 8, (
        "S04_HARNESS_ERROR: frozen S03 PG17 identity count changed")


@pytest.mark.parametrize("lane", LANES)
def test_s04_acl_sentinel_physical_excluded_only_from_portable(lane):
    source = (ROOT / lane).read_text(encoding="utf-8")
    _validate_routing(source)


@pytest.mark.parametrize("lane", LANES)
def test_s04_portable_routing_removal_negative_control(lane):
    source = (ROOT / lane).read_text(encoding="utf-8")
    _validate_routing(source)
    with pytest.raises(AssertionError, match="physical handoff"):
        _validate_routing(source.replace(FILTER, "", 1))


@pytest.mark.parametrize("lane,marker,selector", PHYSICAL)
def test_s04_acl_sentinel_unfiltered_strict_lane_preserved(lane, marker, selector):
    source = (ROOT / lane).read_text(encoding="utf-8")
    assert MODULE in source
    assert FILTER not in source
    assert marker + ': "1"' in source
    assert selector in source
    assert '-k ' + selector in source
    assert "test \"$(git rev-parse HEAD)\" = \"$S04_EXPECTED_HEAD\"" in source
    assert "junitxml=" in source
    assert '"failures": "0"' in source
    assert '"skipped": "0"' in source
    assert "if-no-files-found: error" in source
