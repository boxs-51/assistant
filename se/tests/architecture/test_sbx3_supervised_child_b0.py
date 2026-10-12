"""Native B0 OS-supervised pre-exec child: Linux kernel fault cuts.

The real tests MUST run with a delegated cgroup v2 mount. On unsupported
Windows hosts the supervisor must reject before any attempt to spawn.
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

from se.src.runtimes.capability.native_spawn_supervisor_b0 import (
    spawn_supervised_stopped_native_b0,
)
from se.src.runtimes.capability.native_tree_cgroup_b0 import (
    CgroupTreeIdentity,
    LinuxCgroupTreeLedger,
)
from se.src.runtimes.capability.native_tree_fence_b0 import (
    NativeSpawnFenceDenied,
    NativeTreeLifecycleFence,
)


def _identity() -> CgroupTreeIdentity:
    return CgroupTreeIdentity(
        "owner-native", "exec-native", "sbx-native", uuid.uuid4().hex,
    )


class SupervisedNativeKernelTests(unittest.TestCase):
    def _delegated(self) -> str:
        path = os.environ.get("SBX3_CGROUP_DELEGATED_ROOT")
        if not path:
            self.skipTest("real Linux cgroup2 delegation unavailable")
        if platform.system() != "Linux":
            self.fail("strict Linux kernel test may not run on non-Linux")
        return path

    def test_unsupported_or_relative_executable_fails_pre_spawn(self) -> None:
        if platform.system() != "Linux":
            with self.assertRaises(NativeSpawnFenceDenied):
                spawn_supervised_stopped_native_b0(
                    NativeTreeLifecycleFence(object()), _identity(),
                    sys.executable,
                )
            return
        with self.assertRaises(NativeSpawnFenceDenied):
            spawn_supervised_stopped_native_b0(
                NativeTreeLifecycleFence(object()), _identity(),
                "./untrusted-executable",
            )

    def test_real_preexec_program_executes_only_after_kernel_admission(self) -> None:
        delegated = self._delegated()
        with tempfile.TemporaryDirectory(prefix="sbx3-supervised-success-") as tmp:
            journal = Path(tmp) / "ledger.sqlite"
            sentinel = Path(tmp) / "ran-under-native-cgroup"
            identity = _identity()
            ledger = LinuxCgroupTreeLedger(delegated, journal)
            cgroup = ledger.enroll(identity)
            child = spawn_supervised_stopped_native_b0(
                NativeTreeLifecycleFence(ledger),
                identity,
                sys.executable,
                ("-I", "-c", (
                    "import pathlib,time; "
                    f"pathlib.Path({str(sentinel)!r}).write_text('ready'); "
                    "time.sleep(90)"
                )),
            )
            try:
                deadline = time.monotonic() + 10
                while not sentinel.is_file() and time.monotonic() < deadline:
                    self.assertIsNone(child.poll())
                    time.sleep(.02)
                self.assertTrue(sentinel.is_file())
                self.assertIn(
                    str(child.pid), (cgroup / "cgroup.procs").read_text("ascii").split()
                )
                witness = ledger.recover_and_verify_empty(identity)
                self.assertEqual(witness.status, "VERIFIED_EMPTY")
                self.assertFalse(witness.production_usable)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)
            cgroup.rmdir()

    def test_real_worker_crash_before_admission_kills_stopped_child(self) -> None:
        delegated = self._delegated()
        with tempfile.TemporaryDirectory(prefix="sbx3-supervised-crash-") as tmp:
            journal = Path(tmp) / "ledger.sqlite"
            child_pid_export = Path(tmp) / "stopped-child.pid"
            target_sentinel = Path(tmp) / "untrusted-program-ran"
            identity = _identity()
            initial = LinuxCgroupTreeLedger(delegated, journal)
            cgroup = initial.enroll(identity)
            target_code = (
                "import pathlib; pathlib.Path("
                + repr(str(target_sentinel))
                + ").write_text('should-not-run')"
            )
            worker = (
                "import os, pathlib, sys\n"
                "from se.src.runtimes.capability.native_tree_cgroup_b0 "
                "import LinuxCgroupTreeLedger, CgroupTreeIdentity\n"
                "from se.src.runtimes.capability.native_tree_fence_b0 "
                "import NativeTreeLifecycleFence\n"
                "from se.src.runtimes.capability.native_spawn_supervisor_b0 "
                "import spawn_supervised_stopped_native_b0\n"
                f"ledger=LinuxCgroupTreeLedger({delegated!r}, {str(journal)!r})\n"
                f"identity=CgroupTreeIdentity(*{(identity.owner_id, identity.execution_id, identity.sandbox_id, identity.generation)!r})\n"
                "def fault_cut(pid):\n"
                f"    pathlib.Path({str(child_pid_export)!r}).write_text(str(pid))\n"
                "    os._exit(91)\n"
                "spawn_supervised_stopped_native_b0(\n"
                "    NativeTreeLifecycleFence(ledger), identity, sys.executable,\n"
                f"    ('-I', '-c', {target_code!r}),\\n"
                "    _after_stop_for_test=fault_cut,\n"
                ")\n"
            )
            crashed = subprocess.run(
                [sys.executable, "-c", worker], check=False, timeout=20,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            self.assertEqual(
                crashed.returncode, 91,
                "trusted worker pre-admission hard cut failed: "
                + crashed.stderr.decode("utf-8", "replace")[-1200:],
            )
            pid = int(child_pid_export.read_text())
            deadline = time.monotonic() + 10
            stopped_or_running = None
            while time.monotonic() < deadline:
                state_path = Path("/proc") / str(pid) / "status"
                if not state_path.exists():
                    stopped_or_running = None
                    break
                state = next(
                    (line.split(":", 1)[1].strip() for line
                     in state_path.read_text().splitlines()
                     if line.startswith("State:")), "",
                )
                stopped_or_running = state[:1]
                if stopped_or_running in {"Z", "X", "x"}:
                    break
                time.sleep(.05)
            self.assertIn(stopped_or_running, {None, "Z", "X", "x"})
            self.assertFalse(target_sentinel.exists())
            # Worker crash rolled back OPEN writer lock. Restart may close
            # the generation and receive a kernel observation, NOT cleanup
            # authority. The killed pre-exec child cannot become untracked.
            restarted = LinuxCgroupTreeLedger(delegated, journal)
            witness = restarted.recover_and_verify_empty(identity)
            self.assertFalse(witness.production_usable)
            cgroup.rmdir()


if __name__ == "__main__":
    unittest.main()
