"""S03 dual-lane CI proof: immutable physical IDs, source hashes and exact-run JUnit.

This verifier is a development witness only. Required check configuration and
trusted review/approval must be enforced outside PR-mutable workflow contents.
"""
from __future__ import annotations

import argparse
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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _node(case: ET.Element) -> str:
    name = case.attrib.get("name", "")
    classname = case.attrib.get("classname", "")
    require(name.startswith("test_") and classname, "Unidentified JUnit testcase")
    return classname.replace(".", "/") + ".py::" + name


def read_junit(path: Path, *, allow_skips: bool, unrepresented_subtests: int = 0) -> set[str]:
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
        require(not any("xfail" in str(v).lower() or "xpass" in str(v).lower() for v in case.attrib.values()), "Xfail/xpass status")
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


def verify_portable(path: Path, output: Path | None = None) -> None:
    require(output is not None, "Missing portable pytest terminal witness")
    passed, skipped, subtests = read_portable_summary(output)
    actual = read_junit(path, allow_skips=True, unrepresented_subtests=subtests)
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
        require(len(ignored) == 3 and set(ignored) == AE359_IGNORES,
                f"{name}: AE359 boundary changed")
    require("postgres:" not in arch.lower(), "Architecture now provisioned PG17")
    require("image: postgres:17-alpine" in owner, "Real PG17 removed")
    require("Attest exact HEAD and disposable PG17" in owner, "Physical HEAD attestation missing")
    require("s03-dual-lane-proof:" in owner and "if: always()" in owner, "Fail-closed gate missing")
    for name, sha in SOURCE_BLOBS.items():
        output = subprocess.check_output(["git", "hash-object", name], cwd=ROOT, text=True).strip()
        require(output == sha, f"Frozen security oracle modified: {name}")


def verify_run(physical: Path, portable: Path, physical_output: Path,
               portable_output: Path, physical_head: Path, portable_head: Path,
               expected_head: str, needs_json: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", expected_head) is not None, "Missing exact SHA")
    actual_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(actual_head == expected_head, "Verification checkout SHA mismatch")
    states = json.loads(needs_json)
    require(set(states) == {"sqlite-owner-matrix", "pg17-owner-matrix", "portable-architecture-linux"},
            "Required CI dependencies removed")
    require(all(value.get("result") == "success" for value in states.values()),
            "One or more required jobs absent/failed/skipped/cancelled")
    for attestation in (physical_head, portable_head):
        require(attestation.is_file() and attestation.read_text().strip() == expected_head,
                "Stale or missing job SHA attestation")
    verify_contract()
    verify_physical(physical)
    verify_physical_output(physical_output)
    verify_portable(portable, portable_output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--physical", type=Path, required=True)
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--physical-output", type=Path, required=True)
    parser.add_argument("--portable-output", type=Path, required=True)
    parser.add_argument("--physical-head", type=Path, required=True)
    parser.add_argument("--portable-head", type=Path, required=True)
    args = parser.parse_args()
    verify_run(args.physical, args.portable, args.physical_output,
               args.portable_output, args.physical_head, args.portable_head,
               os.environ["EXPECTED_HEAD"], os.environ["NEEDS_JSON"])
    print("S03_DUAL_LANE_PROOF PASS (exact-head, nine physical oracles, portable JUnit)")


if __name__ == "__main__":
    main()
