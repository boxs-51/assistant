"""Fail-closed adversarial checks for the S04 physical JUnit gate.

Execute the exact embedded GitHub Actions Python verifier against disposable
synthetic XML inputs. This is a *harness* negative proof, not Redis/PG17 proof.
"""
from collections import Counter
from pathlib import Path
import subprocess
import sys
import textwrap
from xml.etree import ElementTree as ET

import pytest


RUN_MARKER = "      - name: Require both original-oracle variants in raw JUnit\n"
EXPECTED_CLASS = "se.tests.integration.test_alpha_s04_legacy_redis_otp_writer_redfirst"
STEM = "test_s04_existing_legacy_redis_otp_must_survive_modern_issue_attempt"
GOOD_NAMES = (f"{STEM}[registration]", f"{STEM}[password_reset]")


def _workflow_validator() -> str:
    root = Path(__file__).resolve().parents[3]
    body = (root / ".github/workflows/alpha-s04-otp-legacy-redis-pg17-redfirst.yml").read_text(
        encoding="utf-8"
    )
    assert body.count(RUN_MARKER) == 1, "HARNESS_ERROR: physical JUnit gate missing"
    suffix = body.split(RUN_MARKER, 1)[1]
    assert suffix.count("        run: |\n") >= 1
    block = suffix.split("        run: |\n", 1)[1].split("      - name:", 1)[0]
    script = textwrap.dedent(block)
    compile(script, "<S04_physical_JUnit_gate>", "exec")
    return script


def _write_junit(path: Path, names: tuple[str, ...], *, skipped: bool = False,
                 classname: str = EXPECTED_CLASS) -> None:
    suite = ET.Element("testsuite", {
        "tests": str(len(names)),
        "failures": "0",
        "errors": "0",
        "skipped": "1" if skipped else "0",
    })
    for index, name in enumerate(names):
        case = ET.SubElement(suite, "testcase", {
            "classname": classname,
            "name": name,
        })
        if skipped and index == 0:
            ET.SubElement(case, "skipped", {"message": "not run"})
    ET.ElementTree(suite).write(path, encoding="unicode")


def _execute_gate(tmp_path: Path, names: tuple[str, ...],
                  *, skipped: bool = False,
                  classname: str = EXPECTED_CLASS) -> subprocess.CompletedProcess[str]:
    _write_junit(tmp_path / "alpha-s04-legacy-redis-physical.xml",
                 names, skipped=skipped, classname=classname)
    return subprocess.run([sys.executable, "-c", _workflow_validator()],
                          cwd=tmp_path, text=True, capture_output=True, check=False)


def test_s04_junit_valid_two_variants_control(tmp_path: Path):
    receipt = _execute_gate(tmp_path, GOOD_NAMES)
    assert receipt.returncode == 0, receipt.stderr
    assert "S04_PHYSICAL_2_CASES" in receipt.stdout


@pytest.mark.parametrize("names", [
    (GOOD_NAMES[0], GOOD_NAMES[0]),
    (GOOD_NAMES[1], GOOD_NAMES[1]),
    (GOOD_NAMES[0],),
    (GOOD_NAMES[0], GOOD_NAMES[1], GOOD_NAMES[1]),
])
def test_s04_junit_missing_or_duplicated_variant_fails_closed(tmp_path: Path, names):
    receipt = _execute_gate(tmp_path, names)
    assert receipt.returncode != 0, "SEMANTIC_RED: invalid S04 variants passed"
    assert "HARNESS_ERROR" in receipt.stderr


def test_s04_junit_skipped_and_wrong_source_fail_closed(tmp_path: Path):
    skipped = _execute_gate(tmp_path, GOOD_NAMES, skipped=True)
    assert skipped.returncode != 0, "SEMANTIC_RED: skipped S04 case passed"
    wrong = _execute_gate(tmp_path, GOOD_NAMES,
                          classname="se.tests.integration.unrelated_test")
    assert wrong.returncode != 0, "SEMANTIC_RED: foreign testcase identity passed"
