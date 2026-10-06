from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "tools/v1/TV1_T11_F7T_FIRST_PRODUCER_CONTRACT_FREEZE.md"
F7T_CONTRACT = "F7T_INLINE_" + "BASE64_V1"
F7T_MEDIA_KEY = "$" + "f7t_media"
F7T_PROJECTION_KEY = F7T_MEDIA_KEY + "_projection"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _semantic(text: str) -> str:
    return " ".join(text.split())


def test_tv1_t11_a_identity_authority_and_zero_production_freeze():
    document = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "# TV1-T11-A — First F7-T Producer Contract Freeze",
        "capability_id = desktop.screenshot",
        "capability_version = 1.0",
        "future physical package version = 2.1.0",
        "source contract = " + F7T_CONTRACT,
        "placement = CLIENT only",
        "fallback = NONE",
        "**Production authority:** NONE",
        "**Production CLAIM:** NONE",
        "**Merge authority:** NONE",
        "Production/runtime/config/schema/migration delta is exactly ZERO.",
        "No concrete CAS enrollment entry is authorized by this document.",
    ):
        assert phrase in document


def test_tv1_t11_a_privacy_risk_and_hitl_are_fail_closed():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    executor = _read("cl/src/core/local_capability_executor.py")
    hitl = _read("cl/src/hitl/hitl_manager.py")

    for phrase in (
        "desktop.screenshot.base_risk = HIGH",
        "desktop.screenshot.effects = [READ, PRIVILEGED]",
        "approval = PER INVOCATION",
        "missing approval callback = DENY / FAIL CLOSED",
        "required_scopes = []",
        "required_permissions = []",
        "danger_patterns = []",
        "capture before the local HIGH-risk approval decision",
    ):
        assert phrase in document

    approval_index = executor.index("self.hitl.request_approval(")
    call_index = executor.index("result = self._call(")
    assert approval_index < call_index
    assert 'if risk_level in ["LOW", "MEDIUM"]:' in hitl
    assert "if not self.approval_callback:" in hitl
    assert "return False" in hitl


def test_tv1_t11_a_client_local_has_no_server_fallback_authority():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    routing = _read("se/src/runtimes/capability/policy.py")
    client_loader = _read("cl/src/loader/local_tools.py")
    target_contract = _read(
        "docs/capability_runtime/"
        "AGENT_ONLY_CAPABILITY_SANDBOX_CAS_CONTRACT_REFREEZE.md"
    )

    for phrase in (
        'execution_locations = ["CLIENT"]',
        "SERVER implementation = MUST NOT EXIST",
        "MCP implementation = MUST NOT EXIST",
        "DECLARATIVE implementation = MUST NOT EXIST",
        "fallback_policy = NONE",
        "Allowed values for this Tools V1 field are exactly",
        "Absence means the historical current behavior:",
    ):
        assert phrase in document

    assert "enabled_v2_capabilities" in client_loader
    assert "same_connection_clients" in routing
    assert "server_or_non_client" in routing
    assert "same_connection_clients" in routing and "server_or_non_client" in routing
    assert "CLIENT_LOCAL MUST NOT silently fall back to SERVER/SANDBOX." in target_contract


def test_tv1_t11_a_exact_f7t_envelope_and_bounds_are_frozen():
    document = CONTRACT.read_text(encoding="utf-8")
    f7t_contract = _read(
        "docs/central_asset/CAS_F7_T_TOOL_GENERATED_MEDIA_CONTRACT_522B543E.md"
    )

    for phrase in (
        "MAX_SCREENSHOT_WIDTH = 7680",
        "MAX_SCREENSHOT_HEIGHT = 4320",
        "MAX_SCREENSHOT_PIXELS = 33177600",
        "MAX_SCREENSHOT_PNG_BYTES = 8388608",
        "max F7-T items = exactly 1",
        f'"contract": "{F7T_CONTRACT}"',
        '"media_kind": "image"',
        '"mime_type": "image/png"',
        '"filename": "desktop-screenshot.png"',
        '"encoding": "base64"',
        "data keys = exactly {\"" + F7T_MEDIA_KEY + "\"}",
        "meta.version = 2.1.0",
        "meta.truncated = false",
        "meta.warnings = []",
        "real TCP/WebSocket near-bound regression",
    ):
        assert phrase in document

    assert '"' + F7T_MEDIA_KEY + '"' in f7t_contract
    assert f'"contract": "{F7T_CONTRACT}"' in f7t_contract
    assert "`data` keys are exactly" in f7t_contract



def test_tv1_t11_a_raw_f7t_base64_is_not_allowed_in_next_inference():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    context_adapter = _read("se/src/runtimes/agent/adapters/context.py")

    assert "content=(" in context_adapter
    assert "_model_facing_success_output(" in context_adapter
    assert "return jsonable(output)" in context_adapter
    assert "jsonable(result.output)" not in context_adapter

    for phrase in (
        "P1-TV1-T11-A-MODEL-CONTEXT-5",
        "raw F7-T base64 must not enter model inference",
        'result.capability_id = desktop.screenshot',
        '"' + F7T_PROJECTION_KEY + '"',
        '"binary_omitted": true',
        "never pass `data_base64` into next inference message",
        "durable ToolResult is never modified",
        "ordinary, resume and recovery context rebuilding",
        "TV1-T11-B PRE-CLAIM = HOLD",
        "TV1-T11-B production CLAIM = NONE",
    ):
        assert phrase in document

def test_tv1_t11_a_primary_display_scope_is_narrow_and_no_disk_transport():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "public input properties = {}",
        "public required inputs = []",
        'bind = {"action": "screenshot"}',
        "capture target = current local primary display",
        "region/window/application selection = CLOSED",
        "multi-monitor aggregation = CLOSED",
        "OCR = CLOSED",
        "No raw image bytes may be written to disk as an intermediate artifact.",
        "save screenshots to repository/temp/user paths",
    ):
        assert phrase in document


def test_tv1_t11_a_replay_safety_is_fail_closed_for_time_varying_capture():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "P1-TV1-T11-A-REPLAY-SAFETY-8",
        "desktop.screenshot.idempotency = UNKNOWN",
        "OUTCOME_UNKNOWN automatic replay = FORBIDDEN",
        "reconciliation UNKNOWN / NOT_FOUND automatic replay = FORBIDDEN",
        "fresh recapture after ambiguous dispatch = REQUIRES NEW EXPLICIT AUTHORITY / NOT THIS STAGE",
        "No producer-local retry loop is permitted.",
    ):
        assert phrase in document


def test_tv1_t11_a_capture_backend_dependency_is_explicit_and_bounded():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "P1-TV1-T11-A-CAPTURE-BACKEND-7",
        "PyAutoGUI==0.9.54",
        "PyScreeze==1.0.1",
        "Pillow pin = ABSENT",
        "requirements owner path = requirements.txt",
        "Pillow dependency = EXPLICIT EXACT PIN REQUIRED",
        "unbounded / unpinned Pillow = FORBIDDEN",
        "new requirements file = FORBIDDEN",
        "server-side screenshot implementation = STILL FORBIDDEN",
        "TV1-T11-B PRE-CLAIM must select one exact Python-3.12-compatible Pillow version",
        "no second image/capture dependency is introduced",
    ):
        assert phrase in document


def test_tv1_t11_a_metadata_contract_can_express_required_risk_fields():
    document = CONTRACT.read_text(encoding="utf-8")
    metadata = _read("tools/v1/_shared/metadata.py")

    assert '"PRIVILEGED"' in metadata
    assert '"HIGH"' in metadata
    for field in (
        '"required_scopes"',
        '"required_permissions"',
        '"danger_patterns"',
        '"base_risk"',
        '"effects"',
        '"idempotency"',
        '"execution_mode"',
    ):
        assert field in metadata

    assert '"effects": ["READ", "PRIVILEGED"]' in document
    assert '"base_risk": "HIGH"' in document
    assert '"idempotency": "UNKNOWN"' in document


def test_tv1_t11_a_exact_two_file_scope_and_b_twelve_path_maximum():
    document = CONTRACT.read_text(encoding="utf-8")

    a_paths = (
        "tools/v1/TV1_T11_F7T_FIRST_PRODUCER_CONTRACT_FREEZE.md",
        "tools/v1/test/test_t11_a_f7t_first_producer_contract.py",
    )
    for path in a_paths:
        assert path in document

    b_paths = (
        "requirements.txt",
        "tools/v1/_shared/metadata.py",
        "tools/v1/desktop_tool.py",
        "se/src/runtimes/capability/local_tool_loader.py",
        "cl/src/loader/local_tools.py",
        "se/src/runtimes/agent/adapters/context.py",
        "cl/config/setting.json",
        "cl/tests/test_t9_c_client_placement.py",
        "tools/v1/test/test_t9_c_metadata.py",
        "cl/tests/test_t9_f_execution_equivalence.py",
        "se/tests/architecture/test_t9_d_real_server_client_parity.py",
        "se/tests/architecture/test_tv1_t11_b_desktop_screenshot_producer.py",
    )
    for path in b_paths:
        assert path in document

    assert "Maximum twelve paths:" in document
    assert (
        "Any required thirteenth path invalidates this maximum and requires a fresh "
        "TV1-T11-B PRE-CLAIM audit before code changes."
    ) in _semantic(document)
    assert "Mandatory cross-track release before B CLAIM" in document
    assert "TV1-T11-B PRE-CLAIM = HOLD" in document
    assert "TV1-T11-B production CLAIM = NONE" in document


def test_tv1_t11_a_does_not_open_cas_a1_or_156_children():
    document = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "P1-CAS-F7T-ACT-PRODUCER-1 = OPEN / GAP-HOLD",
        "concrete enrollment = EMPTY",
        "A1 PRE-CLAIM = HOLD / NOT CLAIMABLE",
        "A1 production CLAIM = NONE",
        "No #156 child is opened or claimed by this contract.",
        "No A1 production CLAIM is implied or authorized here.",
    ):
        assert phrase in document
