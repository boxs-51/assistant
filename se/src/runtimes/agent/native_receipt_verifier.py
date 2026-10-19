"""AE A1: unactivated SE native-broker receipt boundary for durable Agent results.

An Agent result, Tool ok=True, SQL COMMITTED, JSON receipt, PID or
VERIFIED_EMPTY marker is NOT a privileged broker attestation. No authenticated
SE native reader ships on main, so native success MUST remain inaccessible
at write, load, resume, recovery and continuation. This policy never spawns a
process, mints an SE authority or promotes a native result to success.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..capability.native_tree_recovery_b0 import NativeTreeHold

# Canonical Native IDs plus the already recognized process aliases used by
# SBX3 Agent policy. A renamed registration is still a process capability;
# neither caller metadata nor alias spelling can bypass the receipt boundary.
NATIVE_AGENT_CAPABILITIES = frozenset({
    "terminal.run", "terminal.launch", "python.run",
    "plugin.terminal.run", "plugin.terminal.launch", "plugin.python.run",
})


class NativeReceiptUnavailable(NativeTreeHold):
    """AE durable receipt DENY also satisfies the legacy Native tree HOLD.

    Both exceptions are negative-only. This compatibility subtype cannot be
    treated as an OS VERIFIED_EMPTY proof or an authority to resume/remove.
    """
    code = "AE_NATIVE_RECEIPT_UNAVAILABLE"

    def __init__(self, phase: str):
        self.phase = phase
        super().__init__(
            "AE_NATIVE_RECEIPT_UNAVAILABLE: authenticated OS-owned issuer, "
            "generation/revocation, invocation/tool_call, process-tree and "
            "durable readthrough evidence unavailable; native SUCCESS DENIED"
        )


@dataclass(frozen=True, slots=True)
class NativeReceiptIdentity:
    """Future private broker lookup key, not a proof/caller authorization.

    The private issuer must eventually authenticate ALL fields from durable
    SE records in a revocation-fenced transaction. This type is not accepted
    as evidence and must not be minted by Agent/model/tool metadata.
    """

    issuer_principal: str
    owner_id: str
    execution_id: str
    sandbox_id: str
    lease_generation: str
    invocation_id: str
    tool_call_id: str
    broker_epoch: str
    native_ledger_revision: int
    process_tree_ref: str


def is_native_agent_capability(capability_id: object) -> bool:
    return type(capability_id) is str and capability_id in NATIVE_AGENT_CAPABILITIES


def require_native_receipt(
    capability_id: object, success: object, *, phase: str
) -> None:
    """Deny unverified native successes; leave unrelated tool semantics intact.

    This is a negative gate ONLY. It cannot be overridden with metadata,
    environment variables, simulated receipt objects or caller-supplied
    callbacks. Native errors remain representable as FAILED/UNKNOWN.
    Future authenticated SE readthrough will require a reviewed replacement
    with same-store generation CAS; do not just return True from here.
    """
    if is_native_agent_capability(capability_id) and success is not False and success is not None:
        raise NativeReceiptUnavailable(phase)


def require_native_record(record: object, *, phase: str) -> None:
    if record is not None:
        require_native_receipt(
            getattr(record, "capability_id", None),
            getattr(record, "success", None),
            phase=phase,
        )
