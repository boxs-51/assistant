"""Contract and adversarial tests for S03 dual-lane regression gating."""
import xml.etree.ElementTree as ET

import pytest

from se.physical.s03_dual_lane_gate import (
    PHYSICAL, RECOVERY, verify_contract, verify_physical, verify_portable,
    read_junit, require,
)


def _write(tmp_path, cases, *, skipped=False):
    path = tmp_path / "proof.xml"
    root = ET.Element("testsuite", {
        "tests": str(len(cases)), "failures": "0",
        "errors": "0", "skipped": str(int(skipped)),
    })
    for node in cases:
        path_name, name = node.split("::", 1)
        attrs = {"classname": path_name[:-3].replace("/", "."), "name": name}
        case = ET.SubElement(root, "testcase", attrs)
        if skipped and node == cases[0]:
            ET.SubElement(case, "skipped", {"message": "xfail"})
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def test_dual_lane_contract_requires_exact_8_and_frozen_oracles():
    verify_contract()


def test_dual_lane_all_nine_physical_required(tmp_path):
    cases = sorted(PHYSICAL | {RECOVERY})
    verify_physical(_write(tmp_path, cases))
    for changed in (
        cases[1:],
        cases[:-1],
        cases + [cases[0]],
        cases + ["se/tests/integration/fake.py::test_fake"],
    ):
        with pytest.raises(AssertionError):
            verify_physical(_write(tmp_path, changed))


def test_dual_lane_physical_rejects_skips_and_corruption(tmp_path):
    cases = sorted(PHYSICAL | {RECOVERY})
    with pytest.raises(AssertionError):
        verify_physical(_write(tmp_path, cases, skipped=True))
    corrupt = tmp_path / "corrupt.xml"
    corrupt.write_text("<testsuite", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        verify_physical(corrupt)


def test_dual_lane_portable_cannot_claim_eight_physical_cases(tmp_path):
    cases = sorted(PHYSICAL | {RECOVERY})
    with pytest.raises(AssertionError):
        verify_portable(_write(tmp_path, cases))
    assert len(PHYSICAL) == 8 and len(cases) == 9
