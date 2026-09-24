from __future__ import annotations

import json
import platform
import re
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


MASTER_GATE_ENV = "RUN_TOOLS_V1_LIVE"
NETWORK_GATE_ENV = "RUN_TOOLS_V1_LIVE_NETWORK"
GUI_GATE_ENV = "RUN_TOOLS_V1_LIVE_GUI"
ARTIFACT_ROOT_ENV = "TOOLS_V1_LIVE_ARTIFACT_ROOT"
EVIDENCE_SCHEMA = "tools.v1.live.evidence/1"

_REDACTED = "[REDACTED]"
_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "proxy_password",
)
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LiveCategory(str, Enum):
    LOCAL = "LOCAL"
    NETWORK = "NETWORK"
    GUI = "GUI"


class LiveHarnessDisabled(RuntimeError):
    pass


class LiveHarnessConfigError(ValueError):
    pass


class ToolResultContractError(ValueError):
    pass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    token: object


class ProcessController(Protocol):
    def capture(self, pid: int) -> ProcessIdentity | None:
        """Capture stable process identity, or None when the process already exited."""

    def is_alive(self, identity: ProcessIdentity) -> bool:
        ...

    def terminate(self, identity: ProcessIdentity) -> None:
        ...

    def wait(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
    ) -> bool:
        """Return True when this exact process identity is confirmed no longer alive."""

    def kill(self, identity: ProcessIdentity) -> None:
        ...


@dataclass(frozen=True)
class LiveRunConfig:
    category: LiveCategory
    run_id: str
    artifact_directory: Path
    enabled_gates: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioStep:
    id: str
    tool: str
    action: str
    summary: str
    execute: Callable[["ScenarioContext"], Mapping[str, Any]]
    validate: Callable[[Mapping[str, Any]], bool | None] | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    category: LiveCategory
    steps: Sequence[ScenarioStep]
    setup: Callable[["ScenarioContext"], None] | None = None
    teardown: Callable[["ScenarioContext"], None] | None = None


@dataclass
class ScenarioContext:
    config: LiveRunConfig
    processes: "OwnedProcessRegistry"
    state: dict[str, Any] = field(default_factory=dict)


def _required_gates(category: LiveCategory) -> tuple[str, ...]:
    if category is LiveCategory.LOCAL:
        return (MASTER_GATE_ENV,)
    if category is LiveCategory.NETWORK:
        return (MASTER_GATE_ENV, NETWORK_GATE_ENV)
    if category is LiveCategory.GUI:
        return (MASTER_GATE_ENV, GUI_GATE_ENV)
    raise LiveHarnessConfigError(f"unsupported live category: {category!r}")


def require_live_enabled(
    category: LiveCategory,
    env: Mapping[str, str],
) -> tuple[str, ...]:
    required = _required_gates(category)
    missing = [name for name in required if env.get(name) != "1"]
    if missing:
        joined = ", ".join(missing)
        raise LiveHarnessDisabled(
            f"live execution is disabled; literal value '1' required for: {joined}"
        )
    return required


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise LiveHarnessConfigError(
            "run_id must be 1-128 characters using only letters, digits, '.', '_' or '-'"
        )
    return run_id


def _resolved(path: Path) -> Path:
    return path.resolve(strict=False)


def _is_same_or_descendant(path: Path, root: Path) -> bool:
    path = _resolved(path)
    root = _resolved(root)
    return path == root or root in path.parents


def create_live_run_config(
    *,
    category: LiveCategory,
    repo_root: str | Path,
    env: Mapping[str, str],
    run_id: str | None = None,
    temp_directory_factory: Callable[[str], str] | None = None,
) -> LiveRunConfig:
    # Gate first: disabled execution must not allocate artifacts.
    enabled_gates = require_live_enabled(category, env)

    repo = _resolved(Path(repo_root).expanduser())
    resolved_run_id = _validate_run_id(run_id or uuid.uuid4().hex)
    configured_root = env.get(ARTIFACT_ROOT_ENV)

    if configured_root:
        expanded_root = Path(configured_root).expanduser()
        if not expanded_root.is_absolute():
            raise LiveHarnessConfigError(
                f"{ARTIFACT_ROOT_ENV} must expand to an absolute path"
            )
        artifact_root = _resolved(expanded_root)
        if _is_same_or_descendant(artifact_root, repo):
            raise LiveHarnessConfigError(
                f"{ARTIFACT_ROOT_ENV} must not resolve inside the repository"
            )
        run_directory = _resolved(
            artifact_root / f"tools-v1-live-{resolved_run_id}"
        )
        if _is_same_or_descendant(run_directory, repo):
            raise LiveHarnessConfigError(
                "resolved live run directory must not be inside the repository"
            )
        artifact_root.mkdir(parents=True, exist_ok=True)
        run_directory.mkdir(parents=False, exist_ok=False)
    else:
        if temp_directory_factory is None:
            temp_root = _resolved(Path(tempfile.gettempdir()))
            if _is_same_or_descendant(temp_root, repo):
                raise LiveHarnessConfigError(
                    "system temporary directory must not resolve inside the repository"
                )

            def _default_factory(prefix: str) -> str:
                return tempfile.mkdtemp(prefix=prefix, dir=str(temp_root))

            factory = _default_factory
        else:
            factory = temp_directory_factory

        run_directory = _resolved(
            Path(factory(f"tools-v1-live-{resolved_run_id}-"))
        )
        if _is_same_or_descendant(run_directory, repo):
            raise LiveHarnessConfigError(
                "temporary live run directory must not resolve inside the repository"
            )
        if not run_directory.exists() or not run_directory.is_dir():
            raise LiveHarnessConfigError(
                "temp_directory_factory must return an existing directory"
            )

    return LiveRunConfig(
        category=category,
        run_id=resolved_run_id,
        artifact_directory=run_directory,
        enabled_gates=enabled_gates,
    )


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None:
        lowered = key.lower()
        if any(part in lowered for part in _SECRET_KEY_PARTS):
            return _REDACTED

    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return f"<{type(value).__name__}>"


def validate_tool_result(result: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise ToolResultContractError("ToolResult must be a mapping")

    required = {"ok", "tool", "action", "data", "error", "meta"}
    missing = sorted(required.difference(result))
    if missing:
        raise ToolResultContractError(
            f"ToolResult is missing required keys: {', '.join(missing)}"
        )

    if type(result["ok"]) is not bool:
        raise ToolResultContractError("ToolResult.ok must be boolean")
    if not isinstance(result["tool"], str) or not result["tool"]:
        raise ToolResultContractError("ToolResult.tool must be a non-empty string")
    if not isinstance(result["action"], str) or not result["action"]:
        raise ToolResultContractError("ToolResult.action must be a non-empty string")
    if not isinstance(result["meta"], Mapping):
        raise ToolResultContractError("ToolResult.meta must be a mapping")

    if result["ok"] is True:
        if result["error"] is not None:
            raise ToolResultContractError(
                "successful ToolResult.error must be null"
            )
    else:
        error = result["error"]
        if not isinstance(error, Mapping):
            raise ToolResultContractError(
                "failed ToolResult.error must be a mapping"
            )
        for key in ("code", "message", "retryable", "details"):
            if key not in error:
                raise ToolResultContractError(
                    f"failed ToolResult.error is missing {key}"
                )
        if not isinstance(error["code"], str) or not error["code"]:
            raise ToolResultContractError(
                "ToolResult.error.code must be a non-empty string"
            )
        if not isinstance(error["message"], str) or not error["message"]:
            raise ToolResultContractError(
                "ToolResult.error.message must be a non-empty string"
            )
        if type(error["retryable"]) is not bool:
            raise ToolResultContractError(
                "ToolResult.error.retryable must be boolean"
            )
        if not isinstance(error["details"], Mapping):
            raise ToolResultContractError(
                "ToolResult.error.details must be a mapping"
            )

    return result


def project_tool_error(result: Mapping[str, Any]) -> dict[str, Any] | None:
    validated = validate_tool_result(result)
    if validated["ok"] is True:
        return None

    error = validated["error"]
    assert isinstance(error, Mapping)
    return {
        "code": error["code"],
        # Evidence must never persist arbitrary tool/provider error text because
        # the canonical ToolResult contract does not guarantee message secrecy.
        "message": "Tool invocation failed.",
        "retryable": error["retryable"],
        "details": _redact_value(error["details"]),
    }


def _synthetic_error(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retryable": False,
        "details": _redact_value(dict(details or {})),
    }


class OwnedProcessRegistry:
    def __init__(
        self,
        controller: ProcessController | None,
        *,
        graceful_timeout_seconds: float = 2.0,
        kill_timeout_seconds: float = 2.0,
    ) -> None:
        if graceful_timeout_seconds <= 0 or kill_timeout_seconds <= 0:
            raise LiveHarnessConfigError("cleanup timeouts must be positive")
        self._controller = controller
        self._graceful_timeout_seconds = graceful_timeout_seconds
        self._kill_timeout_seconds = kill_timeout_seconds
        self._owned: dict[int, ProcessIdentity] = {}
        self._already_exited_pids: set[int] = set()

    @property
    def owned_pids(self) -> tuple[int, ...]:
        return tuple(
            sorted(set(self._owned).union(self._already_exited_pids))
        )

    def register_launch_result(self, result: Mapping[str, Any]) -> int:
        validated = validate_tool_result(result)
        if validated["ok"] is not True:
            raise ToolResultContractError(
                "cannot register ownership from a failed launch ToolResult"
            )
        if validated["tool"] != "terminal_tool":
            raise ToolResultContractError(
                "owned process registration requires tool='terminal_tool'"
            )
        if validated["action"] != "launch":
            raise ToolResultContractError(
                "owned process registration requires action='launch'"
            )
        data = validated["data"]
        if not isinstance(data, Mapping):
            raise ToolResultContractError(
                "launch ToolResult.data must be a mapping"
            )
        pid = data.get("pid")
        if type(pid) is not int or pid <= 0:
            raise ToolResultContractError(
                "launch ToolResult.data.pid must be a positive integer"
            )
        if data.get("started") is not True:
            raise ToolResultContractError(
                "launch ToolResult.data.started must be true"
            )
        if self._controller is None:
            raise LiveHarnessConfigError(
                "a process controller is required before registering launch ownership"
            )

        identity = self._controller.capture(pid)
        if identity is None:
            self._already_exited_pids.add(pid)
            return pid
        if identity.pid != pid:
            raise LiveHarnessConfigError(
                "captured process identity pid does not match launch result pid"
            )

        existing = self._owned.get(pid)
        if existing is not None and existing != identity:
            raise LiveHarnessConfigError(
                "pid was already registered with a different process identity"
            )
        self._owned[pid] = identity
        return pid

    def cleanup(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "owned_pids": list(self.owned_pids),
            "terminated_pids": sorted(self._already_exited_pids),
            "still_alive_pids": [],
            "errors": [],
        }
        if not self._owned:
            return report

        assert self._controller is not None
        for pid in sorted(self._owned):
            identity = self._owned[pid]
            try:
                if not self._controller.is_alive(identity):
                    report["terminated_pids"].append(pid)
                    continue

                self._controller.terminate(identity)
                if self._controller.wait(
                    identity,
                    self._graceful_timeout_seconds,
                ):
                    report["terminated_pids"].append(pid)
                    continue

                self._controller.kill(identity)
                if self._controller.wait(
                    identity,
                    self._kill_timeout_seconds,
                ):
                    report["terminated_pids"].append(pid)
                    continue

                if self._controller.is_alive(identity):
                    report["still_alive_pids"].append(pid)
                else:
                    report["terminated_pids"].append(pid)
            except Exception as exc:
                report["errors"].append(
                    _synthetic_error(
                        "LIVE_PROCESS_CLEANUP_FAILED",
                        "owned process cleanup raised an exception",
                        details={
                            "pid": pid,
                            "exception_type": type(exc).__name__,
                        },
                    )
                )
                try:
                    if self._controller.is_alive(identity):
                        report["still_alive_pids"].append(pid)
                except Exception:
                    if pid not in report["still_alive_pids"]:
                        report["still_alive_pids"].append(pid)

        report["terminated_pids"] = sorted(
            set(report["terminated_pids"])
        )
        report["still_alive_pids"] = sorted(
            set(report["still_alive_pids"])
        )
        return report


def _timestamp(
    clock: Callable[[], datetime],
) -> str:
    value = clock()
    if value.tzinfo is None:
        raise LiveHarnessConfigError("live evidence clock must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _environment_evidence(config: LiveRunConfig) -> dict[str, Any]:
    return {
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "artifact_directory": str(config.artifact_directory),
        "enabled_gates": list(config.enabled_gates),
    }


def _step_evidence(
    step: ScenarioStep,
    context: ScenarioContext,
) -> dict[str, Any]:
    base = {
        "id": step.id,
        "status": "PASS",
        "tool": step.tool,
        "action": step.action,
        "summary": step.summary,
        "error": None,
    }

    try:
        result = step.execute(context)
    except Exception as exc:
        base["status"] = "FAIL"
        base["error"] = _synthetic_error(
            "LIVE_STEP_EXCEPTION",
            "live scenario step raised an exception",
            details={"exception_type": type(exc).__name__},
        )
        return base

    try:
        validated = validate_tool_result(result)
    except ToolResultContractError as exc:
        base["status"] = "FAIL"
        base["error"] = _synthetic_error(
            "LIVE_TOOL_RESULT_INVALID",
            str(exc),
        )
        return base

    if validated["ok"] is False:
        base["status"] = "FAIL"
        base["error"] = project_tool_error(validated)
        return base

    if step.validate is not None:
        try:
            outcome = step.validate(validated)
            if outcome is False:
                raise AssertionError("step validator returned False")
        except Exception as exc:
            base["status"] = "FAIL"
            base["error"] = _synthetic_error(
                "LIVE_SCENARIO_ASSERTION_FAILED",
                "live scenario step validation failed",
                details={"exception_type": type(exc).__name__},
            )
    return base


class ScenarioRunner:
    def __init__(
        self,
        config: LiveRunConfig,
        *,
        process_controller: ProcessController | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._process_controller = process_controller
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, scenarios: Sequence[Scenario]) -> dict[str, Any]:
        for scenario in scenarios:
            if scenario.category is not self._config.category:
                raise LiveHarnessConfigError(
                    "scenario category must match the enabled live run category"
                )

        started_at = _timestamp(self._clock)
        scenario_evidence: list[dict[str, Any]] = []
        aggregate_cleanup = {
            "owned_pids": [],
            "terminated_pids": [],
            "still_alive_pids": [],
            "errors": [],
        }

        for scenario in scenarios:
            processes = OwnedProcessRegistry(self._process_controller)
            context = ScenarioContext(
                config=self._config,
                processes=processes,
            )
            scenario_result = {
                "id": scenario.id,
                "category": scenario.category.value,
                "status": "PASS",
                "steps": [],
                "error": None,
            }

            setup_ok = True
            try:
                if scenario.setup is not None:
                    scenario.setup(context)
            except Exception as exc:
                setup_ok = False
                scenario_result["status"] = "FAIL"
                scenario_result["error"] = _synthetic_error(
                    "LIVE_SCENARIO_SETUP_FAILED",
                    "live scenario setup raised an exception",
                    details={"exception_type": type(exc).__name__},
                )

            try:
                if setup_ok:
                    for step in scenario.steps:
                        evidence = _step_evidence(step, context)
                        scenario_result["steps"].append(evidence)
                        if evidence["status"] == "FAIL":
                            scenario_result["status"] = "FAIL"
                            scenario_result["error"] = evidence["error"]
                            break
            finally:
                try:
                    if scenario.teardown is not None:
                        scenario.teardown(context)
                except Exception as exc:
                    scenario_result["status"] = "FAIL"
                    scenario_result["error"] = _synthetic_error(
                        "LIVE_SCENARIO_TEARDOWN_FAILED",
                        "live scenario teardown raised an exception",
                        details={"exception_type": type(exc).__name__},
                    )

                cleanup = processes.cleanup()
                for key in (
                    "owned_pids",
                    "terminated_pids",
                    "still_alive_pids",
                    "errors",
                ):
                    aggregate_cleanup[key].extend(cleanup[key])

                if cleanup["still_alive_pids"] or cleanup["errors"]:
                    scenario_result["status"] = "FAIL"
                    if scenario_result["error"] is None:
                        scenario_result["error"] = _synthetic_error(
                            "LIVE_SCENARIO_CLEANUP_FAILED",
                            "live scenario cleanup did not complete cleanly",
                            details={
                                "still_alive_pids": cleanup["still_alive_pids"],
                            },
                        )

            scenario_evidence.append(scenario_result)

        for key in ("owned_pids", "terminated_pids", "still_alive_pids"):
            aggregate_cleanup[key] = sorted(set(aggregate_cleanup[key]))

        overall_status = (
            "FAIL"
            if any(item["status"] == "FAIL" for item in scenario_evidence)
            or aggregate_cleanup["still_alive_pids"]
            or aggregate_cleanup["errors"]
            else "PASS"
        )

        return {
            "schema": EVIDENCE_SCHEMA,
            "run_id": self._config.run_id,
            "category": self._config.category.value,
            "started_at": started_at,
            "finished_at": _timestamp(self._clock),
            "status": overall_status,
            "environment": _environment_evidence(self._config),
            "scenarios": scenario_evidence,
            "cleanup": aggregate_cleanup,
        }


def write_evidence(
    evidence: Mapping[str, Any],
    artifact_directory: str | Path,
    *,
    filename: str = "evidence.json",
) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise LiveHarnessConfigError(
            "evidence filename must be a simple non-empty file name"
        )

    directory = _resolved(Path(artifact_directory))
    if not directory.exists() or not directory.is_dir():
        raise LiveHarnessConfigError(
            "artifact directory must already exist"
        )

    target = directory / filename
    payload = json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    target.write_text(payload + "\n", encoding="utf-8")
    return target
