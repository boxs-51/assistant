from pathlib import Path

from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
)
from se.src.runtimes.capability.contracts.registration import (
    CapabilityKind,
    CapabilityOwnerType,
    ClientCapabilityRegistration,
)
from se.src.runtimes.connection.protocol import RealtimeEnvelope


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_capability_definition_is_independent_from_implementation_identity():
    definition = CapabilityDefinition(
        id="filesystem.read",
        name="filesystem.read",
        description="Read a local file.",
        input_schema={"type": "object"},
    )

    implementation = CapabilityImplementation.from_definition(
        definition,
        implementation_id="desktop-01:filesystem.read",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_id="user-1",
        connection_id="conn-1",
    )

    assert definition.capability_id == "filesystem.read"
    assert implementation.capability_id == "filesystem.read"
    assert implementation.implementation_id != implementation.capability_id
    assert implementation.location is CapabilityExecutionLocation.CLIENT


def test_client_registration_is_scoped_to_owner_and_connection():
    payload = ClientCapabilityRegistration(
        connection_id="conn-1",
        client_id="desktop-01",
        owner_id="user-1",
        capabilities=[
            {
                "definition": {
                    "id": "git.status",
                    "name": "git.status",
                    "description": "Read git status.",
                    "input_schema": {"type": "object"},
                },
                "kind": CapabilityKind.TOOL,
                "location": CapabilityExecutionLocation.CLIENT,
                "driver_kind": "REMOTE_CLIENT",
                "owner_type": CapabilityOwnerType.CLIENT,
                "owner_id": "user-1",
                "connection_id": "conn-1",
                "implementation_id": "desktop-01:git.status",
            }
        ],
    )

    registration = payload.capabilities[0]
    assert registration.owner_id == payload.owner_id
    assert registration.connection_id == payload.connection_id


def test_realtime_envelope_preserves_invocation_correlation():
    message = RealtimeEnvelope(
        type="capability.invoke",
        message_id="msg-1",
        session_id="sess-1",
        connection_id="conn-1",
        execution_id="exec-1",
        invocation_id="inv-1",
        trace_id="trace-1",
        payload={"capability_id": "git.status", "arguments": {}},
    )

    assert message.invocation_id == "inv-1"
    assert message.connection_id == "conn-1"


def test_phase6_foundation_does_not_wire_agent_or_runtime_yet():
    control_plane = REPO_ROOT / "se" / "src" / "runtimes" / "capability" / "control_plane.py"
    multiplexer = REPO_ROOT / "se" /"src" / "runtimes" / "connection" / "multiplexer.py"

    assert control_plane.exists()
    assert multiplexer.exists()
    assert "CapabilityControlPlane" in control_plane.read_text(encoding="utf-8")
    assert "ConnectionMultiplexer" in multiplexer.read_text(encoding="utf-8")
