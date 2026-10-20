"""Contract and adversarial tests for S03 dual-lane regression gating."""
import xml.etree.ElementTree as ET

import pytest

from se.physical.s03_dual_lane_gate import (
    PHYSICAL, RECOVERY, verify_contract, verify_physical, verify_portable,
    read_junit, require, verify_physical_output, verify_sqlite,
    _node_ids_sha256, SQLITE_EXPECTED_CASES,
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



def test_dual_lane_rejects_false_junit_green_from_nonstrict_xpass(tmp_path):
    """Reproduced with pytest 9.0.2: XPASS has tests=1, failures=0, skipped=0."""
    output = tmp_path / "terminal.txt"
    output.write_text("8 passed, 1 xpassed, 64 deselected in 1.23s\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        verify_physical_output(output)
    output.write_text("9 passed, 64 deselected, 2 warnings in 3.14s\n", encoding="utf-8")
    verify_physical_output(output)
    output.write_text("9 passed, 1 xfailed, 64 deselected in 3.14s\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        verify_physical_output(output)
    output.write_text("9 passed, 1 skipped in 3.14s\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        verify_physical_output(output)


def test_portable_junit_counts_real_pytest_subtests_without_weakening_total(tmp_path):
    """pytest reported 4459 PASS + 62 SKIP + 164 passing subtests at frozen RED."""
    cases = [f"se/tests/integration/synthetic.py::test_regular_{n}" for n in range(4521)]
    path = _write(tmp_path, cases)
    tree = ET.parse(path)
    suite = tree.getroot()
    suite.set("tests", "4685")  # 4521 serialized cases + 164 non-serialized subtests
    suite.set("skipped", "62")
    for case in suite.findall("testcase")[:62]:
        ET.SubElement(case, "skipped", {"message": "platform-specific"})
    tree.write(path, encoding="utf-8")
    output = tmp_path / "portable.log"
    output.write_text(
        "4459 passed, 62 skipped, 8 deselected, 243 warnings, "
        "164 subtests passed in 695.26s (0:11:35)\n", encoding="utf-8"
    )
    verify_portable(path, output)

    for wrong_count in ("4521", "4684", "4686"):
        suite.set("tests", wrong_count)
        tree.write(path, encoding="utf-8")
        with pytest.raises(AssertionError, match="JUnit case count mismatch"):
            verify_portable(path, output)
    suite.set("tests", "4685")
    tree.write(path, encoding="utf-8")
    for counterfeit in (
        "4459 passed, 62 skipped, 163 subtests passed in 2.1s",
        "4459 passed, 62 skipped, 165 subtests passed in 2.1s",
        "4459 passed, 63 skipped, 164 subtests passed in 2.1s",
        "4459 passed, 62 skipped, 1 xpassed, 164 subtests passed in 2.1s",
        "4459 passed, 62 skipped, 1 xfailed, 164 subtests passed in 2.1s",
    ):
        output.write_text(counterfeit + "\n", encoding="utf-8")
        with pytest.raises(AssertionError):
            verify_portable(path, output)
    output.unlink()
    with pytest.raises(AssertionError, match="Missing portable pytest output"):
        verify_portable(path, output)



def test_junit_xpass_word_in_identity_is_not_a_result(tmp_path):
    """Regression for real S03 composite #39437899106 (false XPASS)."""
    nodes = [
        "se/tests/integration/xfail_filename.py::test_xpass_in_name_is_normal",
        "se/tests/integration/normal.py::test_other_normal",
    ]
    path = _write(tmp_path, nodes)
    assert read_junit(path, allow_skips=False) == set(nodes)

    tree = ET.parse(path)
    case = tree.getroot().find("testcase")
    for status_key, status_value in (
        ("status", "xpassed"),
        ("result", "xfailed"),
        ("outcome", "XPASS"),
    ):
        case.set(status_key, status_value)
        tree.write(path, encoding="utf-8")
        with pytest.raises(AssertionError, match="Xfail/xpass status"):
            read_junit(path, allow_skips=False)
        del case.attrib[status_key]

def test_s03_portable_skip_swap_rejected_even_if_totals_equal(tmp_path):
    nodes = [
        "se/tests/architecture/test_a.py::test_existing_skip",
        "se/tests/architecture/test_b.py::test_secure_oracle",
        "se/tests/architecture/test_c.py::test_other",
    ]
    path = _write(tmp_path, nodes)
    tree = ET.parse(path)
    suite = tree.getroot()
    suite.set("skipped", "1")
    cases = suite.findall("testcase")
    ET.SubElement(cases[0], "skipped", {"message": "accepted historic skip"})
    tree.write(path, encoding="utf-8")
    original_digest = _node_ids_sha256({nodes[0]})
    assert read_junit(path, allow_skips=True, expected_skips_sha256=original_digest) == set(nodes)

    cases[0].remove(cases[0].find("skipped"))
    ET.SubElement(cases[1], "skipped", {"message": "security oracle silently skipped"})
    tree.write(path, encoding="utf-8")
    with pytest.raises(AssertionError, match="skipped testcase identity drift"):
        read_junit(path, allow_skips=True, expected_skips_sha256=original_digest)


def test_s03_sqlite_identity_and_real_skips_are_fail_closed(tmp_path, monkeypatch):
    import se.physical.s03_dual_lane_gate as gate

    nodes = [
        "se/tests/integration/test_owner.py::test_fkoff_negative",
        "se/tests/integration/test_owner.py::test_recursiveoff_negative",
        "se/tests/integration/test_owner.py::test_recovery_negative",
    ]
    monkeypatch.setattr(gate, "SQLITE_EXPECTED_CASES", len(nodes))
    monkeypatch.setattr(gate, "SQLITE_NODE_IDS_SHA256", _node_ids_sha256(set(nodes)))
    path = _write(tmp_path, nodes)
    verify_sqlite(path)
    tree = ET.parse(path)
    suite = tree.getroot()
    suite.findall("testcase")[0].set("name", "test_replaced_fake")
    tree.write(path, encoding="utf-8")
    with pytest.raises(AssertionError, match="SQLite physical testcase identity drift"):
        verify_sqlite(path)

    suite.findall("testcase")[0].set("name", "test_fkoff_negative")
    suite.set("skipped", "1")
    ET.SubElement(suite.findall("testcase")[1], "skipped", {"message": "unsafe bypass"})
    tree.write(path, encoding="utf-8")
    with pytest.raises(AssertionError, match="JUnit skipped"):
        verify_sqlite(path)

    suite.set("skipped", "0")
    suite.findall("testcase")[1].remove(suite.findall("testcase")[1].find("skipped"))
    tree.write(path, encoding="utf-8")
    verify_sqlite(path)
