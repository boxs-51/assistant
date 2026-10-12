"""SBX3 B0 kernel cgroup v2 crash/restart evidence.

Strict positive requires SBX3_CGROUP_DELEGATED_ROOT pointing to an actual,
writable delegated cgroup2 mount. CI runs the positive in a privileged Linux
container; ordinary unsupported runners skip it without claiming certification.
Windows always tests explicit fail-closed rejection.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from se.src.runtimes.capability.native_tree_cgroup_b0 import (
    CgroupTreeIdentity,
    CgroupTreeMismatch,
    CgroupTreeNotEmpty,
    CgroupTreeUnavailable,
    LinuxCgroupTreeLedger,
)


class KernelOwnedTreeRecoveryTests(unittest.TestCase):
    def _identity(self, sandbox: str = "sbx-test") -> CgroupTreeIdentity:
        return CgroupTreeIdentity(
            owner_id="owner-a",
            execution_id="execution-a",
            sandbox_id=sandbox,
            generation=uuid.uuid4().hex,
        )

    def test_forged_cgroup_files_must_not_yield_kernel_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fake-cgroup"
            root.mkdir()
            for name in ("cgroup.events", "cgroup.procs", "cgroup.kill"):
                (root / name).write_text(
                    "populated 0\n" if name == "cgroup.events" else "",
                    encoding="ascii",
                )
            with self.assertRaises(CgroupTreeUnavailable):
                LinuxCgroupTreeLedger(root, Path(directory) / "ledger.sqlite")

    def test_non_linux_or_unsupported_root_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            with self.assertRaises(CgroupTreeUnavailable):
                LinuxCgroupTreeLedger(fake, fake / "state.sqlite")

    def test_bad_generation_is_not_a_tree_identity(self) -> None:
        with self.assertRaises(ValueError):
            CgroupTreeIdentity("owner-a", "execution-a", "sandbox-a", "../escape")

    def test_real_kernel_reattach_kill_and_generation_specific_empty(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest(
                "KERNEL_CGROUP_POSITIVE_NOT_RUN: requires explicitly delegated "
                "cgroup2; strict CI MUST set SBX3_CGROUP_DELEGATED_ROOT"
            )
        if platform.system() != "Linux":
            self.fail("strict kernel cgroup test must run on Linux")

        with tempfile.TemporaryDirectory(prefix="sbx3-os-tree-") as tmp:
            journal = Path(tmp) / "control" / "native-trees.sqlite"
            recorder = LinuxCgroupTreeLedger(Path(delegated), journal)
            identity = self._identity()
            cg = recorder.enroll(identity)
            spawned = Path(tmp) / "descendant.pid"
            nested = cg / "nested"
            nested.mkdir()
            worker_script = (
                "import os, pathlib, subprocess, sys\n"
                f"leaf = pathlib.Path({str(nested)!r})\n"
                "leaf.joinpath('cgroup.procs').write_text(str(os.getpid()),"
                "encoding='ascii')\n"
                "child = subprocess.Popen([sys.executable, '-c',"
                "'import time; time.sleep(90)'],"
                "start_new_session=True, stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
                f"pathlib.Path({str(spawned)!r}).write_text(str(child.pid))\n"
                "os._exit(77)\n"
            )
            # The trusted fixture moves itself into the kernel cgroup *before*
            # spawning an independent descendant, then crashes unrecoverably.
            exited = subprocess.run(
                [sys.executable, "-c", worker_script],
                check=False,
                timeout=20,
            )
            self.assertEqual(exited.returncode, 77)
            pid = int(spawned.read_text())
            self.assertGreater(pid, 1)
            # The process survives worker crash; recovering via worker's PID
            # or interpreting worker exit as tree empty would be unsafe.
            populated = dict(
                line.split(maxsplit=1)
                for line in (cg / "cgroup.events").read_text("ascii").splitlines()
            )
            self.assertEqual(populated.get("populated"), "1")

            # New manager instance reattaches via DB identity + kernel inode,
            # not a PID, in-memory lease or synthetic outcome.
            restored = LinuxCgroupTreeLedger(Path(delegated), journal)
            stale = CgroupTreeIdentity(
                identity.owner_id, identity.execution_id,
                identity.sandbox_id, uuid.uuid4().hex,
            )
            with self.assertRaises(CgroupTreeMismatch):
                restored.recover_and_verify_empty(stale)
            foreign = CgroupTreeIdentity(
                "other-owner", identity.execution_id,
                identity.sandbox_id, identity.generation,
            )
            with self.assertRaises(CgroupTreeMismatch):
                restored.recover_and_verify_empty(foreign)
            receipt = restored.recover_and_verify_empty(identity, timeout_seconds=15)
            self.assertEqual(receipt.status, "VERIFIED_EMPTY")
            self.assertFalse(receipt.production_usable)
            audit = restored.audit_committed_empty(identity)
            self.assertEqual(audit.observed, "EMPTY_OBSERVED_DIAGNOSTIC")
            self.assertFalse(audit.delete_permitted)
            self.assertEqual(audit.generation, identity.generation)
            with self.assertRaises(CgroupTreeMismatch):
                restored.audit_committed_empty(stale)
            with self.assertRaises(CgroupTreeMismatch):
                restored.audit_committed_empty(foreign)
            self.assertEqual(receipt.generation, identity.generation)
            self.assertEqual(receipt.sandbox_id, identity.sandbox_id)
            self.assertEqual(
                dict(
                    line.split(maxsplit=1)
                    for line in (cg / "cgroup.events").read_text("ascii").splitlines()
                ).get("populated"),
                "0",
            )
            self.assertEqual((cg / "cgroup.procs").read_text("ascii").strip(), "")
            with self.assertRaises(CgroupTreeMismatch):
                restored.recover_and_verify_empty(identity)
            nested.rmdir()
            cg.rmdir()

    def test_kernel_tree_repopulation_invalidates_committed_empty_audit(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-repopulate-") as tmp:
            journal = Path(tmp) / "state.sqlite"
            ledger = LinuxCgroupTreeLedger(Path(delegated), journal)
            identity = self._identity()
            cgroup = ledger.enroll(identity)
            receipt = ledger.recover_and_verify_empty(identity)
            self.assertFalse(receipt.production_usable)
            self.assertFalse(ledger.audit_committed_empty(identity).delete_permitted)
            ready = Path(tmp) / "entered"
            script = (
                "import os, pathlib, time\n"
                f"pathlib.Path({str(cgroup)!r}).joinpath('cgroup.procs')"
                ".write_text(str(os.getpid()), encoding='ascii')\n"
                f"pathlib.Path({str(ready)!r}).write_text('ok')\n"
                "time.sleep(60)\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        self.fail("kernel cgroup reattach fixture failed to start")
                    time.sleep(.05)
                self.assertTrue(ready.exists())
                with self.assertRaises(CgroupTreeNotEmpty):
                    ledger.audit_committed_empty(identity)
            finally:
                (cgroup / "cgroup.kill").write_text("1", encoding="ascii")
                child.wait(timeout=10)
            self.assertFalse(ledger.audit_committed_empty(identity).delete_permitted)
            cgroup.rmdir()

    def test_real_hard_crash_after_cgroup_create_before_journal_insert(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-untracked-cgroup-") as tmp:
            journal = Path(tmp) / "state.sqlite"
            identity = self._identity()
            expected = Path(delegated) / ("sbx3-" + identity.generation)
            worker_script = (
                "import os, pathlib\n"
                f"pathlib.Path({str(expected)!r}).mkdir(mode=0o700)\n"
                "os._exit(79)\n"
            )
            exited = subprocess.run(
                [sys.executable, "-c", worker_script], timeout=20,
                check=False,
            )
            self.assertEqual(exited.returncode, 79)
            restored = LinuxCgroupTreeLedger(Path(delegated), journal)
            unknown = restored.discover_unregistered_cgroups()
            self.assertIn(expected, unknown)
            with self.assertRaises(CgroupTreeMismatch):
                restored.recover_and_verify_empty(identity)
            with self.assertRaises(CgroupTreeMismatch):
                restored.enroll(identity)
            self.assertTrue(expected.is_dir())
            expected.rmdir()  # Explicit operator/test cleanup only.

    def test_real_crash_between_kernel_empty_and_receipt_commit(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-receipt-crash-") as tmp:
            journal = Path(tmp) / "state.sqlite"
            first = LinuxCgroupTreeLedger(Path(delegated), journal)
            identity = self._identity()
            cg = first.enroll(identity)
            observed = Path(tmp) / "kernel-empty-observed"
            script = (
                "import pathlib, time\n"
                "from se.src.runtimes.capability.native_tree_cgroup_b0 "
                "import LinuxCgroupTreeLedger, CgroupTreeIdentity\n"
                f"ledger = LinuxCgroupTreeLedger(pathlib.Path({delegated!r}), "
                f"pathlib.Path({str(journal)!r}))\n"
                f"identity = CgroupTreeIdentity({identity.owner_id!r}, "
                f"{identity.execution_id!r}, {identity.sandbox_id!r}, "
                f"{identity.generation!r})\n"
                "original = ledger._load\n"
                "counter = [0]\n"
                "def intercept(value):\n"
                "    answer = original(value)\n"
                "    counter[0] += 1\n"
                "    if counter[0] == 2:\n"
                f"        pathlib.Path({str(observed)!r}).write_text('kernel-empty')\n"
                "        time.sleep(90)\n"
                "    return answer\n"
                "ledger._load = intercept\n"
                "ledger.recover_and_verify_empty(identity)\n"
            )
            process = subprocess.Popen([sys.executable, "-c", script])
            try:
                deadline = time.monotonic() + 12
                while not observed.is_file() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("recovery process exited before kernel proof")
                    time.sleep(.05)
                self.assertTrue(observed.is_file(), "kernel verification seam not reached")
            finally:
                process.kill()
                process.wait(timeout=10)
            # Child crashed AFTER cgroup.kill but BEFORE durable state CAS.
            with sqlite3.connect(journal) as db:
                status = db.execute(
                    "SELECT state FROM native_cgroup_trees WHERE sandbox_id=?",
                    (identity.sandbox_id,),
                ).fetchone()[0]
            self.assertEqual(status, "ENROLLED")
            witness = LinuxCgroupTreeLedger(
                Path(delegated), journal
            ).recover_and_verify_empty(identity)
            self.assertEqual(witness.generation, identity.generation)
            self.assertFalse(witness.production_usable)
            cg.rmdir()

    def test_kernel_enrollment_refuses_generation_path_reuse(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-cgroup-reuse-") as tmp:
            ledger = LinuxCgroupTreeLedger(Path(delegated), Path(tmp) / "state.sqlite")
            identity = self._identity()
            cgroup = ledger.enroll(identity)
            with self.assertRaises(CgroupTreeMismatch):
                ledger.enroll(identity)
            cgroup.rmdir()


if __name__ == "__main__":
    unittest.main()
