from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/game_automation/GAC_0_ARCHITECTURE_CONTRACT_FREEZE_23D24E1A.md"
CLIENT_RUNTIME = ROOT / "cl/src/core/client_runtime.py"
CAPABILITY_RUNTIME = ROOT / "cl/src/core/capability_runtime.py"
CAPABILITY_DISPATCHER = ROOT / "cl/src/core/capability_dispatcher.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gac0_freeze_binds_exact_baseline_policy_and_two_file_scope() -> None:
    text = _read(DOC)

    for token in (
        "Issue #222",
        "Issue #221 GAME-AUTO-CLIENT",
        "main@23d24e1a9f6753e7da0ffe11d23b22cab81e33f0",
        "Issue #85 v2.5",
        "Issue #156",
        "Issue #161 CRT-1",
        "Issue #107 AE-R12",
        "Production/runtime/schema/migration delta:** ZERO",
        "Production CLAIM:** NONE",
    ):
        assert token in text

    for owned_path in (
        "docs/game_automation/GAC_0_ARCHITECTURE_CONTRACT_FREEZE_23D24E1A.md",
        "cl/tests/test_gac_0_architecture_contract_freeze.py",
    ):
        assert owned_path in text

    assert (
        "GAC-0 must not modify `cl/src/**`, `se/src/**`, `tools/**`, migrations, "
        "dependency manifests, ML model files, packaging, or workflow production semantics."
        in text
    )


def test_gac0_reuses_existing_client_runtime_spine_without_redefining_it() -> None:
    client_runtime = _read(CLIENT_RUNTIME)
    capability_runtime = _read(CAPABILITY_RUNTIME)
    dispatcher = _read(CAPABILITY_DISPATCHER)
    contract = _read(DOC)

    assert "class ClientRuntime:" in client_runtime
    assert "Own auth, realtime generations, capability registration and reconnect." in client_runtime
    assert "class CapabilityRuntime:" in capability_runtime
    assert "Owns client-side capability advertisement and invocation dispatch." in capability_runtime
    assert "def build_registration(" in capability_runtime
    assert "class CapabilityDispatcher:" in dispatcher
    assert "Correlate realtime invocations with the canonical local executor." in dispatcher
    assert "def dispatch(" in dispatcher

    for rule in (
        "MUST NOT replace `ClientRuntime`",
        "create a parallel WebSocket protocol",
        "bypass `CapabilityDispatcher`",
        "invent a second client invocation recovery authority",
    ):
        assert rule in contract


def test_gac0_freezes_local_first_world_model_action_flow() -> None:
    text = _read(DOC)

    ordered_concepts = (
        "FrameCapture",
        "PerceptionPipeline",
        "ObjectTracker",
        "WorldModel",
        "LocalPlanner / BehaviorEngine",
        "ActionScheduler",
        "InputExecutor",
    )
    for concept in ordered_concepts:
        assert concept in text

    for semantic_type in (
        "GameSessionIdentity",
        "GameWindowBinding",
        "FrameObservation",
        "Detection",
        "TrackedEntity",
        "LayoutObservation",
        "WorldState",
        "ActionIntent",
        "ActionOutcome",
        "BehaviorResult",
        "EscalationDecision",
        "GameAdapterProfile",
    ):
        assert semantic_type in text

    assert "The LLM MUST NOT be required for every frame" in text
    assert "Server round-trip latency MUST NOT sit inside the inner" in text
    assert "A raw detector callback cannot directly inject keyboard/mouse input." in text


def test_gac0_freezes_generation_fencing_threading_and_backpressure() -> None:
    text = _read(DOC)

    assert "(automation_session_id, binding_generation, frame_sequence)" in text
    assert "results produced for an older binding generation are stale" in text
    assert "WebSocket reconnect does not by itself create a second local automation owner." in text

    for invariant in (
        "GAC0-I01",
        "GAC0-I02",
        "GAC0-I03",
        "GAC0-I04",
        "GAC0-I05",
        "GAC0-I06",
        "GAC0-I07",
        "GAC0-I08",
    ):
        assert invariant in text

    for worker_rule in (
        "UI thread",
        "capture worker",
        "perception worker",
        "behavior/action scheduler",
        "ClientRuntime realtime receiver",
        "Capture/perception/action handoff queues are bounded.",
        "One bound game session has one serialized action scheduling/execution authority.",
    ):
        assert worker_rule in text


def test_gac0_freezes_fail_closed_target_and_action_safety() -> None:
    text = _read(DOC)

    for invariant in (
        "GAC0-I09",
        "GAC0-I10",
        "GAC0-I11",
        "GAC0-I12",
        "GAC0-I13",
        "GAC0-I14",
    ):
        assert invariant in text

    for rule in (
        "target loss fails closed",
        "Emergency stop is process-local",
        "does not depend on server reachability",
        "No infinite blind click/key loop is acceptable.",
        "Repeated side-effecting actions require observable progress/verification",
    ):
        assert rule in text

    for pacing_term in (
        "configurable base action delay",
        "minimum inter-action interval",
        "action-specific cooldown",
        "observation/verification wait",
        "bounded retry count/backoff",
        "action deadline",
        "pause/cancellation",
        "emergency stop",
    ):
        assert pacing_term in text


def test_gac0_freezes_perception_llm_adapter_and_high_level_capability_boundaries() -> None:
    text = _read(DOC)

    for invariant in (
        "GAC0-I15",
        "GAC0-I16",
        "GAC0-I17",
        "GAC0-I18",
        "GAC0-I19",
        "GAC0-I20",
        "GAC0-I21",
    ):
        assert invariant in text

    for capability in (
        "game.observe",
        "game.navigate",
        "game.collect",
        "game.build",
        "game.combat",
        "game.execute_task",
        "game.stop",
    ):
        assert capability in text

    assert "YOLO is a detector implementation candidate behind a detector interface." in text
    assert "ByteTrack is a tracker implementation candidate behind a tracker interface." in text
    assert "LLM/Agent inference is not part of the mandatory frame cadence." in text
    assert "Raw keyboard/mouse primitives" in text
    assert "are not the normal Agent/LLM capability surface." in text


def test_gac0_freezes_cross_track_authority_boundaries() -> None:
    text = _read(DOC)

    for token in (
        "target_class = CLIENT_LOCAL",
        "Issue #161 remains RESERVED / NOT CLAIMED",
        "CapabilityInvocationTarget",
        "ResourceScope",
        "CapabilityRoutingPolicy",
        "Issue #107 / AE-R12 owns durable Agent execution lease/recovery/fencing semantics.",
        "UBQ #141-#149 owns user budget/quota and timeout migration.",
        "GAC-0 does not claim CAS #74",
    ):
        assert token in text

    assert "CLIENT_LOCAL game resource never silently falls back to a server or foreign client" in text
    assert "local stale-result fencing is distinct from AE-R12" in text
    assert "cannot mutate UBQ durable counters" in text
    assert "Any durable upload/persistence bridge requires separate CAS-compatible authority." in text


def test_gac0_freezes_security_non_scope_roadmap_and_gac1_gate() -> None:
    text = _read(DOC)

    for prohibited in (
        "process memory reading or writing",
        "DLL/code injection",
        "process patching",
        "anti-cheat bypass",
        "kernel drivers",
        "credential/session extraction",
        "hidden privilege escalation",
        "arbitrary host command execution through a game adapter",
    ):
        assert prohibited in text

    for stage in (
        "GAC-0  architecture / ownership contract freeze",
        "GAC-1  GameSession + WindowBinding + capture baseline",
        "GAC-2  Action Runtime + scheduler + cancellation/emergency stop",
        "GAC-3  Perception pipeline + YOLO adapter",
        "GAC-4  ByteTrack + WorldModel + layout/HUD state",
        "GAC-5  Local Behavior Runtime: navigation + collect first",
        "GAC-6  Assistant client capability integration (game.*)",
        "GAC-7  LLM escalation / high-level planning",
        "GAC-8  Recorder + deterministic replay + evaluation",
        "GAC-9  Multi-game adapter/profile SDK",
    ):
        assert stage in text

    assert "Only after GAC-0 is canonical may GAC-1 request an independent PRE-CLAIM/release" in text
    assert "GAC-1 does not inherit production authority automatically from GAC-0." in text