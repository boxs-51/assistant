"""SBX3 B0 kernel cgroup v2 crash/restart evidence.

Strict positive requires SBX3_CGROUP_DELEGATED_ROOT pointing to an actual,
writable delegated cgroup2 mount. CI runs the positive in a privileged Linux
container; ordinary unsupported runners skip it without claiming certification.
Windows always tests explicit fail-closed rejection.
"""

from __future__ import annotations

import os
import platform
import signal
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
from se.src.runtimes.capability.native_tree_fence_b0 import (
    NativeSpawnFenceDenied,
    NativeTreeLifecycleFence,
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
                stderr=subprocess.DEVNULL,
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

    def test_real_separate_process_spawn_lock_blocks_cleanup(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-crossproc-fence-") as tmp:
            root = Path(tmp)
            journal = root / "native.sqlite"
            identity = self._identity()
            ledger = LinuxCgroupTreeLedger(Path(delegated), journal)
            group = ledger.enroll(identity)
            ready = root / "spawn-lock-held"
            release = root / "allow-spawn-release"
            closing = root / "closing-committed"
            raw_identity = (
                identity.owner_id, identity.execution_id,
                identity.sandbox_id, identity.generation,
            )
            admission = (
                "import os, pathlib, time\n"
                "from se.src.runtimes.capability.native_tree_cgroup_b0 "
                "import LinuxCgroupTreeLedger, CgroupTreeIdentity\n"
                "from se.src.runtimes.capability.native_tree_fence_b0 "
                "import NativeTreeLifecycleFence\n"
                f"ledger=LinuxCgroupTreeLedger({delegated!r}, {str(journal)!r})\n"
                f"identity=CgroupTreeIdentity(*{raw_identity!r})\n"
                "with NativeTreeLifecycleFence(ledger).admit_spawn(identity):\n"
                f"    pathlib.Path({str(ready)!r}).write_text('OPEN')\n"
                f"    while not pathlib.Path({str(release)!r}).exists():\n"
                "        time.sleep(.02)\n"
            )
            cleanup = (
                "import pathlib\n"
                "from se.src.runtimes.capability.native_tree_cgroup_b0 "
                "import LinuxCgroupTreeLedger, CgroupTreeIdentity\n"
                "from se.src.runtimes.capability.native_tree_fence_b0 "
                "import NativeTreeLifecycleFence\n"
                f"ledger=LinuxCgroupTreeLedger({delegated!r}, {str(journal)!r})\n"
                f"identity=CgroupTreeIdentity(*{raw_identity!r})\n"
                "NativeTreeLifecycleFence(ledger).begin_cleanup(identity)\n"
                f"pathlib.Path({str(closing)!r}).write_text('CLOSING')\n"
            )
            admission_worker = subprocess.Popen([sys.executable, "-c", admission])
            cleanup_worker = None
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    if admission_worker.poll() is not None:
                        self.fail("spawn window exited before acquiring SQL writer")
                    time.sleep(.02)
                self.assertTrue(ready.exists(), "cross-process admission never began")
                cleanup_worker = subprocess.Popen(
                    [sys.executable, "-c", cleanup],
                )
                time.sleep(.3)
                self.assertFalse(
                    closing.exists(),
                    "cleanup incorrectly passed a live cross-process spawn writer",
                )
                self.assertIsNone(cleanup_worker.poll())
                release.write_text("release")
                self.assertEqual(admission_worker.wait(timeout=15), 0)
                self.assertEqual(cleanup_worker.wait(timeout=15), 0)
                self.assertTrue(closing.exists())
                with self.assertRaises(NativeSpawnFenceDenied):
                    with NativeTreeLifecycleFence(ledger).admit_spawn(identity):
                        self.fail("no spawn admission after durable CLOSING")
                self.assertFalse(
                    ledger.recover_and_verify_empty(identity).production_usable
                )
            finally:
                if admission_worker.poll() is None:
                    admission_worker.kill()
                    admission_worker.wait(timeout=10)
                if cleanup_worker is not None and cleanup_worker.poll() is None:
                    cleanup_worker.kill()
                    cleanup_worker.wait(timeout=10)
                release.touch(exist_ok=True)
            group.rmdir()

    def test_real_preexec_stopped_child_is_enrolled_before_resume(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-stopped-child-") as tmp:
            root = Path(tmp)
            ledger = LinuxCgroupTreeLedger(
                Path(delegated), root / "native.sqlite"
            )
            identity = self._identity()
            group = ledger.enroll(identity)
            marker = root / "ran-after-stop"
            # Trusted trampoline stops itself BEFORE any side-effect code.
            trampoline = (
                "import os, signal, pathlib, time\n"
                "os.kill(os.getpid(), signal.SIGSTOP)\n"
                f"pathlib.Path({str(marker)!r}).write_text('started')\n"
                "time.sleep(90)\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", trampoline],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                seen_pid, status = os.waitpid(child.pid, os.WUNTRACED)
                self.assertEqual(seen_pid, child.pid)
                self.assertTrue(os.WIFSTOPPED(status))
                self.assertFalse(marker.exists())
                with NativeTreeLifecycleFence(ledger).admit_spawn(identity) as permit:
                    permit.admit_stopped_process(child.pid)
                    self.assertTrue(permit.admitted)
                    self.assertIn(
                        str(child.pid),
                        (group / "cgroup.procs").read_text("ascii").split(),
                    )
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
                witness = ledger.recover_and_verify_empty(identity)
                self.assertFalse(witness.production_usable)
                self.assertFalse(ledger.audit_committed_empty(identity).delete_permitted)
                with self.assertRaises(NativeSpawnFenceDenied):
                    with NativeTreeLifecycleFence(ledger).admit_spawn(identity):
                        self.fail("closed tree cannot be reopened")
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)
            group.rmdir()

    def test_worker_crash_after_durable_closing_does_not_reopen_spawn(self) -> None:
        delegated = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not delegated:
            self.skipTest("KERNEL_CGROUP_POSITIVE_NOT_RUN: no delegated root")
        with tempfile.TemporaryDirectory(prefix="sbx3-fence-restart-") as tmp:
            journal = Path(tmp) / "native.sqlite"
            ledger = LinuxCgroupTreeLedger(Path(delegated), journal)
            identity = self._identity()
            group = ledger.enroll(identity)
            worker = (
                "import os\n"
                "from se.src.runtimes.capability.native_tree_cgroup_b0 "
                "import LinuxCgroupTreeLedger, CgroupTreeIdentity\n"
                "from se.src.runtimes.capability.native_tree_fence_b0 "
                "import NativeTreeLifecycleFence\n"
                f"ledger=LinuxCgroupTreeLedger({delegated!r}, {str(journal)!r})\n"
                f"identity=CgroupTreeIdentity(*{(identity.owner_id, identity.execution_id, identity.sandbox_id, identity.generation)!r})\n"
                "NativeTreeLifecycleFence(ledger).begin_cleanup(identity)\n"
                "os._exit(87)\n"
            )
            crash = subprocess.run(
                [sys.executable, "-c", worker], timeout=20,
                check=False,
            )
            self.assertEqual(crash.returncode, 87)
            restarted = LinuxCgroupTreeLedger(Path(delegated), journal)
            with self.assertRaises(NativeSpawnFenceDenied):
                with NativeTreeLifecycleFence(restarted).admit_spawn(identity):
                    self.fail("crashed closing phase must not open")
            witness = restarted.recover_and_verify_empty(identity)
            self.assertEqual(witness.generation, identity.generation)
            group.rmdir()

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
