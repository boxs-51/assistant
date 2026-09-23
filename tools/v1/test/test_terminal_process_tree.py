import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

import tools.v1.terminal_tool as terminal_module
from tools.v1.terminal_tool import TerminalTool


def shell_command(*args: str) -> str:
    values = [str(arg) for arg in args]
    if os.name == "nt":
        return subprocess.list2cmdline(values)
    return shlex.join(values)


def wait_pid_gone(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)


@unittest.skipIf(os.name == "nt", "Linux/POSIX process-group gate is authoritative for T3")
class TestTerminalRealProcessTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tool = TerminalTool(default_timeout=5)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def _read_pid(self, path: Path) -> int:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return int(path.read_text(encoding="utf-8").strip())
            time.sleep(0.02)
        self.fail(f"PID file was not created: {path}")

    def test_timeout_kills_parent_and_descendant(self):
        child_pid_path = self.root / "child.pid"
        parent_pid_path = self.root / "parent.pid"

        child = self._write(
            "child.py",
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(60)\n",
        )
        parent = self._write(
            "parent.py",
            "import os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "subprocess.Popen([sys.executable, sys.argv[2], sys.argv[3]])\n"
            "time.sleep(60)\n",
        )

        command = shell_command(
            sys.executable,
            str(parent),
            str(parent_pid_path),
            str(child),
            str(child_pid_path),
        )
        started = time.monotonic()
        result = self.tool.run(command, timeout=1, cwd=str(self.root))
        elapsed = time.monotonic() - started

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "TERMINAL_TIMEOUT")
        self.assertLess(elapsed, 8.0)

        parent_pid = self._read_pid(parent_pid_path)
        child_pid = self._read_pid(child_pid_path)
        self.assertTrue(wait_pid_gone(parent_pid), f"parent {parent_pid} survived")
        self.assertTrue(wait_pid_gone(child_pid), f"child {child_pid} survived")

    def test_output_limit_kills_real_producer(self):
        producer_pid_path = self.root / "producer.pid"
        producer = self._write(
            "producer.py",
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "chunk = b'x' * 4096\n"
            "while True:\n"
            "    sys.stdout.buffer.write(chunk)\n"
            "    sys.stdout.buffer.flush()\n"
            "    time.sleep(0.001)\n",
        )

        command = shell_command(sys.executable, str(producer), str(producer_pid_path))
        with patch.object(terminal_module, "MAX_STDOUT_BYTES", 16 * 1024), patch.object(
            terminal_module,
            "MAX_TOTAL_OUTPUT_BYTES",
            32 * 1024,
        ):
            result = self.tool.run(command, timeout=5, cwd=str(self.root))

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "TERMINAL_OUTPUT_LIMIT")
        producer_pid = self._read_pid(producer_pid_path)
        self.assertTrue(
            wait_pid_gone(producer_pid),
            f"output producer {producer_pid} survived",
        )

    def test_normal_run_cleans_background_descendant(self):
        child_pid_path = self.root / "background-child.pid"

        child = self._write(
            "background_child.py",
            "import time\n"
            "time.sleep(60)\n",
        )
        parent = self._write(
            "spawn_background.py",
            "import subprocess, sys\n"
            "from pathlib import Path\n"
            "with open(sys.argv[2], 'wb') as devnull:\n"
            "    child = subprocess.Popen(\n"
            "        [sys.executable, sys.argv[1]],\n"
            "        stdin=devnull,\n"
            "        stdout=devnull,\n"
            "        stderr=devnull,\n"
            "    )\n"
            "Path(sys.argv[3]).write_text(str(child.pid), encoding='utf-8')\n",
        )
        devnull_path = self.root / "devnull.bin"

        command = shell_command(
            sys.executable,
            str(parent),
            str(child),
            str(devnull_path),
            str(child_pid_path),
        )
        result = self.tool.run(command, timeout=5, cwd=str(self.root))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["exit_code"], 0)

        child_pid = self._read_pid(child_pid_path)
        self.assertTrue(
            wait_pid_gone(child_pid),
            f"background child {child_pid} survived managed run",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
