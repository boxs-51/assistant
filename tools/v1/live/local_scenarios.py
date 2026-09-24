from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psutil

from tools.v1 import file_tool, find_by_glob, terminal_tool
from tools.v1.live.harness import (
    LiveCategory,
    LiveHarnessDisabled,
    ProcessIdentity,
    Scenario,
    ScenarioContext,
    ScenarioRunner,
    ScenarioStep,
    create_live_run_config,
    write_evidence,
)


LOCAL_MARKER = "tools-v1-live-local-ok"
LOCAL_FILE_NAME = "local-tools-v1.txt"
LOCAL_LAUNCH_SECONDS = 30


class LiveProcessIdentityError(RuntimeError):
    pass


def _python_command(code: str) -> str:
    args = [sys.executable, "-c", code]
    return (
        subprocess.list2cmdline(args)
        if os.name == "nt"
        else shlex.join(args)
    )


def _same_process(process: psutil.Process, identity: ProcessIdentity) -> bool:
    try:
        token = float(identity.token)
        return (
            process.pid == identity.pid
            and abs(process.create_time() - token) < 0.001
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (
        TypeError,
        ValueError,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
    ):
        return False


class PsutilProcessController:
    """Real LOCAL cleanup controller fenced by pid + create-time identity."""

    def __init__(self) -> None:
        self._descendants: dict[
            tuple[int, float],
            dict[int, ProcessIdentity],
        ] = {}

    @staticmethod
    def _key(identity: ProcessIdentity) -> tuple[int, float]:
        try:
            return identity.pid, float(identity.token)
        except (TypeError, ValueError) as exc:
            raise LiveProcessIdentityError(
                "process identity token must be a create-time number"
            ) from exc

    def _exact_process(
        self,
        identity: ProcessIdentity,
    ) -> psutil.Process | None:
        try:
            process = psutil.Process(identity.pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied as exc:
            raise LiveProcessIdentityError(
                "cannot verify process identity due to access denial"
            ) from exc

        try:
            if not _same_process(process, identity):
                return None
        except psutil.AccessDenied as exc:
            raise LiveProcessIdentityError(
                "cannot verify process identity due to access denial"
            ) from exc
        return process

    def _remember_descendants(
        self,
        identity: ProcessIdentity,
        root: psutil.Process,
    ) -> dict[int, ProcessIdentity]:
        key = self._key(identity)
        remembered = self._descendants.setdefault(key, {})
        try:
            children = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return remembered
        except psutil.AccessDenied as exc:
            raise LiveProcessIdentityError(
                "cannot inspect owned process descendants"
            ) from exc

        for child in children:
            try:
                child_identity = ProcessIdentity(
                    pid=child.pid,
                    token=child.create_time(),
                )
            except (
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
            ):
                continue
            except psutil.AccessDenied as exc:
                raise LiveProcessIdentityError(
                    "cannot capture owned descendant identity"
                ) from exc
            remembered.setdefault(child.pid, child_identity)
        return remembered

    def _live_descendants(
        self,
        identity: ProcessIdentity,
    ) -> list[tuple[ProcessIdentity, psutil.Process]]:
        live: list[tuple[ProcessIdentity, psutil.Process]] = []
        for child_identity in self._descendants.get(
            self._key(identity),
            {},
        ).values():
            child = self._exact_process(child_identity)
            if child is not None:
                live.append((child_identity, child))
        return live

    def capture(self, pid: int) -> ProcessIdentity | None:
        if type(pid) is not int or pid <= 0:
            raise LiveProcessIdentityError(
                "process pid must be a positive integer"
            )
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            identity = ProcessIdentity(
                pid=pid,
                token=process.create_time(),
            )
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied as exc:
            raise LiveProcessIdentityError(
                "cannot capture process identity due to access denial"
            ) from exc

        self._remember_descendants(identity, process)
        return identity

    def is_alive(self, identity: ProcessIdentity) -> bool:
        root = self._exact_process(identity)
        if root is not None:
            self._remember_descendants(identity, root)
            return True
        return bool(self._live_descendants(identity))

    def terminate(self, identity: ProcessIdentity) -> None:
        root = self._exact_process(identity)
        if root is None:
            # Exact root is gone/reused. Only previously captured exact
            # descendants remain eligible for cleanup.
            descendants = self._live_descendants(identity)
        else:
            remembered = self._remember_descendants(identity, root)
            descendants = []
            for child_identity in remembered.values():
                child = self._exact_process(child_identity)
                if child is not None:
                    descendants.append((child_identity, child))

        for _, child in reversed(descendants):
            child.terminate()
        if root is not None:
            root.terminate()

    def wait(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
    ) -> bool:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.is_alive(identity):
                return True
            time.sleep(0.05)
        return not self.is_alive(identity)

    def kill(self, identity: ProcessIdentity) -> None:
        root = self._exact_process(identity)
        if root is not None:
            self._remember_descendants(identity, root)

        descendants = self._live_descendants(identity)
        for _, child in reversed(descendants):
            child.kill()

        root = self._exact_process(identity)
        if root is not None:
            root.kill()


def _validate_terminal_run(result: Mapping[str, Any]) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and data.get("exit_code") == 0
        and LOCAL_MARKER in str(data.get("stdout", ""))
    )


def _validate_file_write(result: Mapping[str, Any]) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and data.get("changed") is True
        and data.get("created") is True
    )


def _validate_file_read(result: Mapping[str, Any]) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and LOCAL_MARKER in str(data.get("content", ""))
        and data.get("eof") is True
    )


def _validate_glob(result: Mapping[str, Any]) -> bool:
    data = result.get("data")
    if not isinstance(data, Mapping):
        return False
    matches = data.get("matches")
    if not isinstance(matches, list):
        return False
    return any(
        isinstance(item, Mapping)
        and Path(str(item.get("path", ""))).name == LOCAL_FILE_NAME
        for item in matches
    )


def _validate_launch(result: Mapping[str, Any]) -> bool:
    data = result.get("data")
    return (
        isinstance(data, Mapping)
        and type(data.get("pid")) is int
        and data["pid"] > 0
        and data.get("started") is True
    )


def build_local_scenario(
    *,
    file_run=file_tool.run,
    glob_run=find_by_glob.run,
    terminal_run=terminal_tool.run,
) -> Scenario:
    def terminal_probe(context: ScenarioContext) -> Mapping[str, Any]:
        return terminal_run(
            action="run",
            command=_python_command(f"print({LOCAL_MARKER!r})"),
            timeout=15,
            cwd=str(context.config.artifact_directory),
            encoding="utf-8",
        )

    def file_write(context: ScenarioContext) -> Mapping[str, Any]:
        target = context.config.artifact_directory / LOCAL_FILE_NAME
        context.state["local_file"] = target
        return file_run(
            action="write",
            file_paths=str(target),
            content=f"{LOCAL_MARKER}\n",
            mode="w",
            encoding="utf-8",
        )

    def file_read(context: ScenarioContext) -> Mapping[str, Any]:
        target = context.state["local_file"]
        return file_run(
            action="read",
            file_paths=str(target),
            encoding="utf-8",
            max_chars=4096,
        )

    def glob_find(context: ScenarioContext) -> Mapping[str, Any]:
        return glob_run(
            pattern="*.txt",
            root_dir=str(context.config.artifact_directory),
            recursive=False,
            max_results=20,
        )

    def terminal_launch(context: ScenarioContext) -> Mapping[str, Any]:
        result = terminal_run(
            action="launch",
            command=_python_command(
                f"import time; time.sleep({LOCAL_LAUNCH_SECONDS})"
            ),
            cwd=str(context.config.artifact_directory),
        )
        if result.get("ok") is True:
            context.processes.register_launch_result(result)
        return result

    return Scenario(
        id="local-file-glob-terminal",
        category=LiveCategory.LOCAL,
        steps=(
            ScenarioStep(
                id="terminal-run",
                tool="terminal_tool",
                action="run",
                summary="Run a bounded Python probe in the scenario artifact directory.",
                execute=terminal_probe,
                validate=_validate_terminal_run,
            ),
            ScenarioStep(
                id="file-write",
                tool="file_tool",
                action="write",
                summary="Create one scenario-owned text file.",
                execute=file_write,
                validate=_validate_file_write,
            ),
            ScenarioStep(
                id="file-read",
                tool="file_tool",
                action="read",
                summary="Read the scenario-owned text file through structured data.",
                execute=file_read,
                validate=_validate_file_read,
            ),
            ScenarioStep(
                id="glob-find",
                tool="find_by_glob",
                action="find",
                summary="Discover the scenario-owned text file by structured glob matches.",
                execute=glob_find,
                validate=_validate_glob,
            ),
            ScenarioStep(
                id="terminal-launch",
                tool="terminal_tool",
                action="launch",
                summary="Launch a bounded sleeper and prove owned-process cleanup.",
                execute=terminal_launch,
                validate=_validate_launch,
            ),
        ),
    )


def run_local_live(
    *,
    env: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
    process_controller: Any | None = None,
) -> tuple[dict[str, Any], Path]:
    effective_env = os.environ if env is None else env
    root = (
        Path(__file__).resolve().parents[3]
        if repo_root is None
        else Path(repo_root)
    )

    # create_live_run_config checks the literal master gate before any
    # artifact allocation. Controller construction happens only afterwards.
    config = create_live_run_config(
        category=LiveCategory.LOCAL,
        repo_root=root,
        env=effective_env,
    )
    controller = (
        PsutilProcessController()
        if process_controller is None
        else process_controller
    )
    evidence = ScenarioRunner(
        config,
        process_controller=controller,
    ).run((build_local_scenario(),))
    evidence_path = write_evidence(
        evidence,
        config.artifact_directory,
    )
    return evidence, evidence_path


def main() -> int:
    try:
        evidence, path = run_local_live()
    except LiveHarnessDisabled as exc:
        print(f"SKIP: {exc}")
        return 2
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(
        f"{evidence['status']}: LOCAL evidence written to {path}"
    )
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
