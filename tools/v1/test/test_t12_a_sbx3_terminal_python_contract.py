"""Contract/architecture evidence only for Issue #371 TV1-T12-A.

These checks intentionally do not spawn host commands, execute Python from a
model invocation, or claim that SBX-3 OS isolation is implemented.
"""

from pathlib import Path

from tools.v1 import terminal_tool

from se.src.application.policy.authorization import AuthorizationService
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry


# Independent, committed terminal.run/launch output-schema snapshot.
# Do NOT derive the expected value from tool_result_schema or TOOL_METADATA.
_FROZEN_STRING = {"type": "string", "minLength": 1}
_FROZEN_META = {
    "type": "object",
    "properties": {
        "version": {"type": "string", "minLength": 1},
        "truncated": {"type": "boolean"},
        "warnings": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["version", "truncated", "warnings"],
    "additionalProperties": True,
}
_FROZEN_ERROR = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
        "message": {"type": "string", "minLength": 1},
        "retryable": {"type": "boolean"},
        "details": {"type": "object"},
    },
    "required": ["code", "message", "retryable", "details"],
    "additionalProperties": False,
}
# Complete public input snapshots are independent of current tool constants.
# Narrow numeric/type/schema changes must fail this contract gate.
FROZEN_TERMINAL_INPUT_SCHEMAS = {
    "terminal.run": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 32768},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
            "cwd": {"type": "string", "minLength": 1, "maxLength": 4096},
            "encoding": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "required": ["command"],
    },
    "terminal.launch": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 32768},
            "cwd": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "required": ["command"],
    },
}


FROZEN_TERMINAL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "tool", "action", "data", "error", "meta"],
    "properties": {
        "ok": {"type": "boolean"},
        "tool": _FROZEN_STRING,
        "action": _FROZEN_STRING,
        "data": {},
        "error": {},
        "meta": _FROZEN_META,
    },
    "additionalProperties": False,
    "oneOf": [
        {
            "properties": {
                "ok": {"const": True},
                "tool": _FROZEN_STRING,
                "action": _FROZEN_STRING,
                "data": {},
                "error": {"type": "null"},
                "meta": _FROZEN_META,
            },
        },
        {
            "properties": {
                "ok": {"const": False},
                "tool": _FROZEN_STRING,
                "action": _FROZEN_STRING,
                "data": {"type": "null"},
                "error": _FROZEN_ERROR,
                "meta": _FROZEN_META,
            },
        },
    ],
}


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT / "tools/v1/TV1_T12_SBX3_TERMINAL_PYTHON_CONTRACT_FREEZE.md"
)


def _doc() -> str:
    return " ".join(CONTRACT.read_text(encoding="utf-8").split())


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _requires(*fragments: str) -> None:
    document = _doc()
    for fragment in fragments:
        assert fragment in document, f"missing TV1-T12-A freeze: {fragment}"


def test_t12_a_exact_authority_and_no_implicit_production_release():
    _requires(
        "Issue #371 (TV1-T12)",
        "Issue #85 v2.5",
        "comment 6055282311",
        "comment 6060382557",
        "main@7332af469c074fb4331eee841f0ee121c7738f3e",
        "Linux GREEN + Windows GREEN",
        "**Production authority:** NONE",
        "**Production CLAIM:** NONE",
        "**Merge authority:** NONE",
        "Production/runtime/config/schema/migration delta is exactly ZERO.",
        "Production PRE-CLAIM = HOLD / NOT RELEASED.",
        "SBX-3 #164 production CLAIM = NONE.",
        "A third path, expanded semantics or new blocking P0/P1 requires HOLD",
    )


def test_t12_a_terminal_logical_ids_versions_and_public_schema_are_unchanged():
    metadata = terminal_tool.TOOL_METADATA
    assert metadata["manifest_version"] == "2.0"
    assert metadata["name"] == "terminal_tool"
    assert metadata["version"] == "2.0.0"
    assert metadata["expose_root"] is False

    exports = {entry["id"]: entry for entry in metadata["exports"]}
    assert set(exports) == {"terminal.run", "terminal.launch"}

    for name, export in exports.items():
        assert export["version"] == "1.0"
        assert export["name"] == name
        assert export["base_risk"] == "HIGH"
        assert export["effects"] == ["EXECUTE", "EXTERNAL_SIDE_EFFECT"]
        assert export["input_schema"] == FROZEN_TERMINAL_INPUT_SCHEMAS[name]
        assert export["output_schema"] == FROZEN_TERMINAL_OUTPUT_SCHEMA

    assert exports["terminal.run"]["bind"] == {"action": "run"}
    assert exports["terminal.launch"]["bind"] == {"action": "launch"}
    assert exports["terminal.run"]["idempotency"] == "UNKNOWN"
    assert exports["terminal.launch"]["idempotency"] == "NON_IDEMPOTENT"
    _requires(
        "terminal.run@1.0",
        "terminal.launch@1.0",
        "No schema accepts an environment",
        "process token, raw process handle, sandbox lease, sandbox root",
        "Runtime-only env and process-ownership control MUST NOT be model-visible",
    )


def test_t12_a_compatibility_preserves_limits_and_verified_cleanup():
    source = _source("tools/v1/terminal_tool.py")
    assert "RUN_TIMEOUT = IntLimitSpec(" in source
    assert "MAX_STDOUT_BYTES = 4 * 1024 * 1024" in source
    assert "MAX_STDERR_BYTES = 4 * 1024 * 1024" in source
    assert "MAX_TOTAL_OUTPUT_BYTES = 8 * 1024 * 1024" in source
    assert "def _capture_identity(" in source
    assert "def _platform_run_popen_kwargs(" in source
    assert "def _platform_launch_popen_kwargs(" in source
    assert "def _run_managed_process(" in source
    assert "def launch(" in source
    _requires(
        "hard timeout / bounded stdout-stderr / bounded",
        "aggregate output and verified process-tree termination behavior",
        "do not reinterpret UBQ-5 #147 timeout taxonomy",
        "UBQ-4 #146 compute charging",
    )


def test_t12_a_spawn_environment_seam_and_host_cwd_fail_closed():
    source = _source("tools/v1/terminal_tool.py")
    assert "subprocess.Popen(" in source
    assert "def _validate_cwd(cwd:" in source
    _requires(
        "P1-T12-ENVIRONMENT-HANDOFF-1",
        "host ambient os.environ is inherited",
        "host cwd",
        "Future production seam owner = Tools V1 / Issue #371",
        "No ambient host secrets; no global mutation of",
        "os.environ; no shell-specific env -i workaround",
        "mapping must be allowlisted from SandboxProfile",
        "server model-directed terminal default cwd MUST be",
        "verified sandbox root",
        "DENY / FAIL CLOSED",
    )


def test_t12_a_launch_never_leaks_unowned_process_authority():
    source = _source("tools/v1/terminal_tool.py")
    assert "getattr(subprocess, \"DETACHED_PROCESS\", 0)" in source
    assert '"start_new_session"' in source
    assert '"pid": process.pid' in source
    _requires(
        "P1-T12-LAUNCH-LEASE-OWNERSHIP-1",
        "PID-only termination is insufficient",
        "atomically hand off stable",
        "before returning started=true",
        "if registration fails, the entire launched",
        "terminated/verified or the operation fails closed",
        "must never be serialized into a public",
        "verify identity before tree termination",
        "never kill a reused/unrelated",
        "terminal.launch process lifetime <= SandboxLease lifetime",
        "no detached durable background-job authority",
    )


def test_t12_a_python_logical_ownership_and_network_default_none():
    _requires(
        "P1-T12-PYTHON-LOGICAL-OWNERSHIP-1",
        "python.run@1.0, owned by Tools V1 / Issue #371",
        "RESERVED FUTURE CAPABILITY",
        "No authority to add tools/v1/python_tool.py",
        "Issue #164 owns EPHEMERAL_SANDBOX execution",
        "No raw trusted-process SERVER",
        "fallback: FAIL CLOSED",
        "python.run default network mode = NONE",
        "Default environment excludes ambient secrets",
        "stdout/stderr/aggregate output",
        "generated-file containment",
    )



def test_t12_a_canonical_python_is_absent_and_not_registered_by_real_loader():
    # TV1-T12-A is a contract freeze: python.run MUST remain undiscoverable
    # until a separate Tools V1 + SBX-3 production registration guard exists.
    tools_dir = ROOT / "tools" / "v1"
    assert not (tools_dir / "python_tool.py").exists(), (
        "python.run physical module requires a separate production CLAIM "
        "and sandbox-only registration/landing gate"
    )

    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=CapabilityCatalog(),
    )
    tool_registry = ToolRegistry(runtime.registry)
    registered = register_local_tools(runtime, tool_registry, tools_dir)

    assert "terminal.run" in registered
    assert "terminal.launch" in registered
    assert "python.run" not in registered
    assert runtime.registry.get("python.run") is None
    assert runtime.registry.get_driver("python.run") is None
    assert tool_registry.get("python.run") is None
    assert not runtime.catalog.contains_definition("python.run")
    assert not runtime.catalog.contains_implementation("server:python.run")
    assert not runtime.catalog.contains_implementation("server:sandbox:python.run")
    assert runtime.driver_registry.get("server:python.run") is None
    assert runtime.driver_registry.get("server:sandbox:python.run") is None


def test_t12_a_loader_discovery_hazard_and_safe_atomic_landing_order():
    loader = _source("se/src/runtimes/capability/local_tool_loader.py")
    assert 'tools_dir.glob("*.py")' in loader
    assert 'getattr(module, "TOOL_METADATA", None)' in loader
    assert 'getattr(module, "run", None)' in loader
    assert "PythonCapabilityDriver(" in loader
    assert 'implementation_id = f"server:{capability_id}"' in loader
    _requires(
        "P1-T12-PYTHON-AUTO-DISCOVERY-ORDER-1",
        "server:python.run as trusted host Python",
        "no intermediate canonical",
        "sandbox-only registration/routing guard first",
        "first atomic exposure",
        "Missing driver/context/profile must deny registration or invocation",
        "never fall back to raw SERVER",
        "negative registration and invocation",
        "positive SANDBOX-only route on Linux and Windows",
    )


def test_t12_a_sandbox_lease_security_and_ephemeral_persistence():
    architecture = _source(
        "docs/capability_runtime/"
        "AGENT_ONLY_CAPABILITY_SANDBOX_CAS_CONTRACT_REFREEZE.md"
    )
    assert "EPHEMERAL_SANDBOX" in architecture
    assert "SandboxLease" in architecture
    assert "network.mode = NONE" in architecture
    assert "python.run" in architecture
    _requires(
        "execution-scoped SANDBOX target and current SandboxLease",
        "execution/lease-scoped",
        "sandbox:// ephemeral data",
        "separately authorized explicit persistence",
        "No implicit upload, CAS mutation or replay",
        "SandboxProfile policy data alone does not provide OS",
        "Isolation claims require real OS/process/e2e evidence",
    )


def test_t12_a_owner_split_and_gate_chain_do_not_grant_production():
    _requires(
        "Tools V1 #371 (future PRE-CLAIM, NOT RELEASED)",
        "SBX-3 #164 (future PRE-CLAIM, NOT RELEASED)",
        "Fresh Linux/Windows Architecture and independent T12-A FINAL",
        "re-audit #147 UBQ, AE-R14",
        "independent production PRE-CLAIM with exact pathset",
        "Production merges only with their applicable explicit wave authorization",
        "Do not edit terminal_tool.py, any terminal behavior test, python_tool.py",
        "local_tool_loader.py, any runtime/driver/AgentRuntime path",
        "Neither Linux nor Windows baseline CI is production acceptance for SBX-3.",
    )
