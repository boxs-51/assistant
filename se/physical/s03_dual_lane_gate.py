"""S03 dual-lane CI proof: immutable physical IDs, source hashes and exact-run JUnit.

This verifier is a development witness only. Required check configuration and
trusted review/approval must be enforced outside PR-mutable workflow contents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PHYSICAL = frozenset([
    "se/tests/integration/test_cas_account_owner_fkoff_fence_real_schema.py::test_cas_retained_owner_fkoff_and_provider_link_physical_guard[CAS-only-postgresql17]",
    "se/tests/integration/test_cas_account_owner_fkoff_fence_real_schema.py::test_cas_retained_owner_fkoff_and_provider_link_physical_guard[CAS-and-UBQ-postgresql17]",
    "se/tests/integration/test_cross_owner_aic_cas_ubq_retention_real_schema.py::test_real_schema_retired_aic_cas_provider_ubq_delete_rekey_and_hardexit[False-postgresql]",
    "se/tests/integration/test_cross_owner_aic_cas_ubq_retention_real_schema.py::test_real_schema_retired_aic_cas_provider_ubq_delete_rekey_and_hardexit[True-postgresql]",
    "se/tests/integration/test_ctx_user_wide_owner_retention_real_schema.py::test_ctx_only_pg17_parent_child_two_pid_physical[child_first-DELETE]",
    "se/tests/integration/test_ctx_user_wide_owner_retention_real_schema.py::test_ctx_only_pg17_parent_child_two_pid_physical[child_first-REKEY]",
    "se/tests/integration/test_ctx_user_wide_owner_retention_real_schema.py::test_ctx_only_pg17_parent_child_two_pid_physical[parent_first-DELETE]",
    "se/tests/integration/test_ctx_user_wide_owner_retention_real_schema.py::test_ctx_only_pg17_parent_child_two_pid_physical[parent_first-REKEY]"
])
RECOVERY = "se/physical/s03_pg17_recovery.py::test_s03_pg17_template_copy_crash_restart_and_forward_only"
SOURCE_BLOBS = {
    "se/tests/integration/test_cas_account_owner_fkoff_fence_real_schema.py": "1060fa7ff6b16cad8cc8a1f1c70166c13f61f82a",
    "se/tests/integration/test_cross_owner_aic_cas_ubq_retention_real_schema.py": "f964cf81ebbf38e757242a89b2b547ffe471f127",
    "se/tests/integration/test_ctx_user_wide_owner_retention_real_schema.py": "aebd7eff67ed75ec8f6d11b497e1b19275e11e26",
    "se/physical/s03_pg17_recovery.py": "8b9371964a41bcce2225248869d3732d12f58188"
}
AE359_IGNORES = frozenset((
    "se/tests/integration/test_ae359_utc_naive_pg17_redfirst.py",
    "se/tests/integration/test_auth_otp_db_atomic_consume_pg17_redfirst.py",
    "se/tests/integration/test_ae359_post_ack_loss_physical.py",
))
# S04 challenge tests need actual Redis/PG17, so portable lanes must not
# collect-and-skip them. The strict S04 job runs their two variants.
S04_PHYSICAL_IGNORE = "se/tests/integration/test_alpha_s04_legacy_redis_otp_writer_redfirst.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _node(case: ET.Element) -> str:
    name = case.attrib.get("name", "")
    classname = case.attrib.get("classname", "")
    require(name.startswith("test_") and classname, "Unidentified JUnit testcase")
    return classname.replace(".", "/") + ".py::" + name


def read_junit(path: Path, *, allow_skips: bool, unrepresented_subtests: int = 0,
               expected_skips_sha256: str | None = None) -> set[str]:
    require(path.is_file(), f"Missing JUnit artifact: {path}")
    root = ET.parse(path).getroot()
    require(root.tag in ("testsuite", "testsuites"), "Unexpected JUnit root")
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    require(bool(suites), "No JUnit test suites")
    require(unrepresented_subtests >= 0, "Invalid subtest accounting")
    if unrepresented_subtests:
        require(allow_skips and len(suites) == 1, "Ambiguous subtest accounting")
    cases = [case for suite in suites for case in suite.findall("testcase")]
    names = [_node(case) for case in cases]
    require(len(names) == len(set(names)), "Duplicate JUnit testcase")
    require(bool(names), "Empty JUnit artifact")
    for suite in suites:
        for key in ("errors", "failures"):
            require(int(suite.attrib.get(key, "-1")) == 0, f"JUnit {key} not zero")
        expected_tests = len(suite.findall("testcase")) + unrepresented_subtests
        require(int(suite.attrib.get("tests", "-1")) == expected_tests, "JUnit case count mismatch")
        require(int(suite.attrib.get("skipped", "-1")) == sum(
            case.find("skipped") is not None for case in suite.findall("testcase")
        ), "JUnit skipped case count mismatch")
        if not allow_skips:
            require(int(suite.attrib.get("skipped", "-1")) == 0, "Physical skipped tests")
    for case in cases:
        require(case.find("failure") is None and case.find("error") is None, "A testcase failed")
        if not allow_skips:
            require(case.find("skipped") is None, "Physical testcase skipped/xfail")
        # JUnit identity fields may legitimately contain "xpass"/"xfail" in
        # test names; these strings are not an execution status. Keep vetoes on
        # all remaining attributes, result elements and same-run pytest summary.
        require(not any(
            "xfail" in str(value).lower() or "xpass" in str(value).lower()
            for key, value in case.attrib.items()
            if key not in {"name", "classname"}
        ), "Xfail/xpass status")
    if expected_skips_sha256 is not None:
        skipped_nodes = [_node(case) for case in cases if case.find("skipped") is not None]
        require(_node_ids_sha256(skipped_nodes) == expected_skips_sha256,
                "Portable skipped testcase identity drift")
    return set(names)


def verify_physical_output(path: Path) -> None:
    """JUnit cannot reveal a non-strict XPASS; terminal summary must veto it."""
    require(path.is_file(), f"Missing physical pytest terminal output: {path}")
    output = re.sub(r"\x1b\[[0-9;]*m", "", path.read_text(encoding="utf-8"))
    summaries = [line.strip() for line in output.splitlines()
                 if re.search(r"\b\d+\s+passed\b", line) and " in " in line]
    require(len(summaries) == 1, "Missing/ambiguous physical pytest summary")
    summary = summaries[0]
    require(re.search(r"^9 passed\b", summary) is not None, "Physical test count not exactly nine passed")
    require(re.search(r"\b[1-9]\d*\s+(?:xpassed|xfailed|skipped|failed|errors?)\b", summary)
            is None, f"Physical pytest abnormal outcome: {summary}")



# pytest-subtests contributes to testsuite@tests but does not serialize each
# passing subtest as a standalone <testcase>. Bind the difference to captured
# same-run pytest output instead of silently ignoring JUnit totals.
PORTABLE_SUBTEST_BASELINE = 164
PORTABLE_SKIP_BASELINE = 62
# Exact passing c47d580 JUnit artifact #12275336594: frozen 62 skipped node IDs.
# This belongs in an externally protected trust anchor for authoritative admission.
PORTABLE_SKIP_IDS_SHA256 = "35bad6c4444a0d8a958cad08de252dafb5e1882fd291aff91b08df76eab60c02"
# Linux artifact #12275116382 and Windows #12274251593 agree on these 85 IDs.
SQLITE_NODE_IDS_SHA256 = "78f96857dd55b8a1d40f2cd831cc145afa3f105965c6c472aaf14d0b7e6f0cb0"
SQLITE_EXPECTED_CASES = 85


def _node_ids_sha256(nodes: set[str] | list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(nodes)).encode("utf-8")).hexdigest()



def read_portable_summary(path: Path) -> tuple[int, int, int]:
    require(path.is_file(), f"Missing portable pytest output: {path}")
    output = re.sub(r"\x1b\[[0-9;]*m", "", path.read_text(encoding="utf-8"))
    summaries = [line.strip() for line in output.splitlines()
                 if re.search(r"^\d+ passed\b", line.strip()) and " in " in line]
    require(len(summaries) == 1, "Missing/ambiguous portable pytest summary")
    summary = summaries[0]
    require(re.search(r"\b[1-9]\d*\s+(?:failed|errors?|xpassed|xfailed|reruns?)\b", summary)
            is None, f"Portable pytest abnormal outcome: {summary}")
    passed = re.match(r"^(\d+) passed\b", summary)
    skipped = re.search(r"\b(\d+) skipped\b", summary)
    subtests = re.search(r"\b(\d+) subtests passed\b", summary)
    require(passed is not None and skipped is not None and subtests is not None,
            "Incomplete portable pytest accounting")
    values = int(passed.group(1)), int(skipped.group(1)), int(subtests.group(1))
    require(values[1] <= PORTABLE_SKIP_BASELINE, "New portable skipped tests")
    require(values[2] >= PORTABLE_SUBTEST_BASELINE, "Missing portable subtest coverage")
    return values


def verify_physical(path: Path) -> None:
    actual = read_junit(path, allow_skips=False)
    expected = PHYSICAL | {RECOVERY}
    require(actual == expected, f"Physical case drift: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")


def verify_sqlite(path: Path) -> None:
    actual = read_junit(path, allow_skips=False)
    require(len(actual) == SQLITE_EXPECTED_CASES, "SQLite physical case count drift")
    require(_node_ids_sha256(actual) == SQLITE_NODE_IDS_SHA256,
            "SQLite physical testcase identity drift")


def verify_portable(path: Path, output: Path | None = None) -> None:
    require(output is not None, "Missing portable pytest terminal witness")
    passed, skipped, subtests = read_portable_summary(output)
    actual = read_junit(path, allow_skips=True, unrepresented_subtests=subtests,
                        expected_skips_sha256=PORTABLE_SKIP_IDS_SHA256)
    require(len(actual) == passed + skipped, "Portable JUnit/terminal case count mismatch")
    require(len(actual) >= 4500, f"Portable suite unexpectedly small: {len(actual)}")
    require(not actual.intersection(PHYSICAL), "Portable suite reran PG17 without real PG17")
    require(RECOVERY not in actual, "Recovery executed in portable suite")


def verify_contract() -> None:
    arch = (ROOT / ".github/workflows/architecture-baseline.yml").read_text(encoding="utf-8")
    owner = (ROOT / ".github/workflows/alpha-s03-owner-real-schema.yml").read_text(encoding="utf-8")
    for name, value in (("Architecture", arch), ("S03", owner)):
        deselect = re.findall(r"--deselect=([^\s]+)", value)
        require(len(deselect) == len(PHYSICAL) and set(deselect) == PHYSICAL,
                f"{name}: not the exact eight physical reroutes")
        ignored = re.findall(r"--ignore=([^\s]+)", value)
        expected_ignores = AE359_IGNORES | {S04_PHYSICAL_IGNORE}
        require(len(ignored) == len(expected_ignores) and set(ignored) == expected_ignores,
                f"{name}: AE359 or S04 physical-only test routing changed")
    # Separate S04 run is mandatory evidence for the excluded physical module.
    # Verify the gate is unfiltered, exact-HEAD, strict and checks two JUnit
    # test identities with zero fail/error/skip. The S03 verifier does not
    # falsely claim an external workflow succeeded: GitHub must verify it.
    s04 = (ROOT / ".github/workflows/alpha-s04-otp-legacy-redis-pg17-redfirst.yml").read_text(encoding="utf-8")
    required_s04 = (
        "on:\\n  pull_request:", 
        'AE359_POSTACK_STRICT: "1"',
        "S04_EXPECTED_SHA:",
        'test "$(git rev-parse HEAD)" = "$S04_EXPECTED_SHA"',
        "se/tests/integration/test_alpha_s04_legacy_redis_otp_writer_redfirst.py",
        "--junitxml=alpha-s04-legacy-redis-physical.xml",
        '"test_s04_existing_legacy_redis_otp_must_survive_modern_issue_attempt": 2',
        'assert counts["skipped"] == 0',
        'assert counts["error"] == 0',
        'assert counts["failure"] == 0',
        "if-no-files-found: error",
    )
    # The event anchor above is a newline, not the literal two characters \\n.
    required_s04 = ("on:\n  pull_request:", *required_s04[1:])
    for witness in required_s04:
        require(witness in s04, f"S04 physical admission/evidence missing: {witness}")
    require("paths:" not in s04, "S04 physical workflow cannot be path-filtered")
    require("postgres:" not in arch.lower(), "Architecture now provisioned PG17")
    require("image: postgres:17-alpine" in owner, "Real PG17 removed")
    require("Attest exact HEAD and disposable PG17" in owner, "Physical HEAD attestation missing")
    require("s03-dual-lane-proof:" in owner and "if: always()" in owner, "Fail-closed gate missing")
    sqlite_step = owner.split("name: Real SQLite owner retention FK OFF ON and recursive OFF ON", 1)
    require(len(sqlite_step) == 2 and "-o xfail_strict=true" in sqlite_step[1].split("- uses: actions/upload-artifact", 1)[0],
            "SQLite physical strict XPASS gate missing")
    for artifact in ("s03-owner-sqlite-ubuntu-24.04-raw", "s03-owner-sqlite-windows-2022-raw"):
        require(owner.count("name: " + artifact) == 1 and artifact in owner,
                f"Missing same-run SQLite download: {artifact}")
    for name, sha in SOURCE_BLOBS.items():
        output = subprocess.check_output(["git", "hash-object", name], cwd=ROOT, text=True).strip()
        require(output == sha, f"Frozen security oracle modified: {name}")


def verify_run(physical: Path, portable: Path, physical_output: Path,
               portable_output: Path, physical_head: Path, portable_head: Path,
               expected_head: str, needs_json: str, *,
               sqlite_linux: Path | None = None, sqlite_windows: Path | None = None,
               sqlite_linux_head: Path | None = None,
               sqlite_windows_head: Path | None = None) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", expected_head) is not None, "Missing exact SHA")
    actual_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(actual_head == expected_head, "Verification checkout SHA mismatch")
    states = json.loads(needs_json)
    require(set(states) == {"sqlite-owner-matrix", "pg17-owner-matrix", "portable-architecture-linux"},
            "Required CI dependencies removed")
    require(all(value.get("result") == "success" for value in states.values()),
            "One or more required jobs absent/failed/skipped/cancelled")
    require(all(path is not None for path in
                (sqlite_linux, sqlite_windows, sqlite_linux_head, sqlite_windows_head)),
            "Missing required two-OS SQLite proof inputs")
    for attestation in (physical_head, portable_head, sqlite_linux_head, sqlite_windows_head):
        require(attestation.is_file() and attestation.read_text(encoding="utf-8-sig").strip() == expected_head,
                "Stale or missing job SHA attestation")
    verify_contract()
    verify_physical(physical)
    verify_physical_output(physical_output)
    verify_portable(portable, portable_output)
    verify_sqlite(sqlite_linux)
    verify_sqlite(sqlite_windows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--physical", type=Path, required=True)
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--physical-output", type=Path, required=True)
    parser.add_argument("--portable-output", type=Path, required=True)
    parser.add_argument("--physical-head", type=Path, required=True)
    parser.add_argument("--portable-head", type=Path, required=True)
    parser.add_argument("--sqlite-linux", type=Path, required=True)
    parser.add_argument("--sqlite-windows", type=Path, required=True)
    parser.add_argument("--sqlite-linux-head", type=Path, required=True)
    parser.add_argument("--sqlite-windows-head", type=Path, required=True)
    args = parser.parse_args()
    verify_run(args.physical, args.portable, args.physical_output,
               args.portable_output, args.physical_head, args.portable_head,
               os.environ["EXPECTED_HEAD"], os.environ["NEEDS_JSON"],
               sqlite_linux=args.sqlite_linux, sqlite_windows=args.sqlite_windows,
               sqlite_linux_head=args.sqlite_linux_head,
               sqlite_windows_head=args.sqlite_windows_head)
    print("S03_DUAL_LANE_PROOF PASS (exact-head, nine PG17, 85+85 SQLite, portable JUnit)")


if __name__ == "__main__":
    main()
