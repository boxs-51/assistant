"""RED-first S04 physical handoff: negative routing/proof-contract guards.

Portable Linux has no strict disposable Redis/PG17. Two S04 physical tests
must never be reported as additional skips: they run under their independently
required physical workflow instead. Preserve all S03 legacy skip identities.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from se.physical import s03_dual_lane_gate as contract

S04 = "se/tests/integration/test_alpha_s04_legacy_redis_otp_writer_redfirst.py"
LANES = (
    ".github/workflows/architecture-baseline.yml",
    ".github/workflows/alpha-s03-owner-real-schema.yml",
)


def test_s04_strict_physical_handoff_and_s03_frozen_oracles():
    assert contract.PORTABLE_SKIP_BASELINE == 62
    assert contract.PORTABLE_SUBTEST_BASELINE == 164
    assert contract.PORTABLE_SKIP_IDS_SHA256 == (
        "35bad6c4444a0d8a958cad08de252dafb5e1882fd291aff91b08df76eab60c02"
    )
    assert len(contract.PHYSICAL) == 8
    assert contract.S04_PHYSICAL_IGNORE == S04
    contract.verify_contract()
    for filename in LANES:
        source = (contract.ROOT / filename).read_text(encoding="utf-8")
        ignored = re.findall(r"--ignore=([^\\s]+)", source)
        assert len(ignored) == 4
        assert set(ignored) == contract.AE359_IGNORES | {S04}
        assert len(re.findall(r"--deselect=([^\\s]+)", source)) == 8


@pytest.mark.parametrize("lane", LANES)
def test_missing_s04_physical_handoff_must_fail_closed(monkeypatch, lane):
    target = contract.ROOT / lane
    original = Path.read_text

    def tampered_read(self, *args, **kwargs):
        raw = original(self, *args, **kwargs)
        if self == target:
            assert raw.count("--ignore=" + S04) == 1
            return raw.replace("--ignore=" + S04, "--ignore=S04_MISSING_HARNESS", 1)
        return raw

    monkeypatch.setattr(Path, "read_text", tampered_read)
    with pytest.raises(AssertionError, match="physical-only test routing changed"):
        contract.verify_contract()


def test_missing_s04_exact_sha_physical_witness_must_fail_closed(monkeypatch):
    target = contract.ROOT / ".github/workflows/alpha-s04-otp-legacy-redis-pg17-redfirst.yml"
    original = Path.read_text

    def tampered_read(self, *args, **kwargs):
        raw = original(self, *args, **kwargs)
        if self == target:
            return raw.replace('S04_EXPECTED_SHA:', "REMOVED_EXPECTED_SHA:", 1)
        return raw

    monkeypatch.setattr(Path, "read_text", tampered_read)
    with pytest.raises(AssertionError, match="S04 physical admission/evidence missing"):
        contract.verify_contract()
