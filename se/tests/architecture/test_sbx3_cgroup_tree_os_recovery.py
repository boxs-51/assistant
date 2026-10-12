"""SBX3 B0 kernel cgroup v2 crash/restart evidence.

Strict positive requires SBX3_CGROUP_DELEGATED_ROOT pointing to an actual,
writable delegated cgroup2 mount. CI runs the positive in a privileged Linux
container; ordinary unsupported runners skip it without claiming certification.
Windows always tests explicit fail-closed rejection.
"""

from __future__ import annotations

import os
import platform
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
            worker_script = (
                "import os, pathlib, subprocess, sys\n"
                f"leaf = pathlib.Path({str(cg)!r})\n"
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
