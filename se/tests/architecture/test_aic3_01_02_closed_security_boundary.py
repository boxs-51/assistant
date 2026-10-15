"""A3-01/02 T1: executable RED-first CLOSED security boundary evidence.

This is evidence only, stacked on unchanged PR540.  It exercises the actual
nonwired code while its trusted PREP/issuer/epoch predecessors do not exist.
Every attempted read, enrollment and verifier admission MUST DENY.  A future
positive W/K implementation is out of scope and cannot turn these into
positive tests without a separate owner/security decision.

The intentional unsafe controls below demonstrate that this strict oracle
would be RED against an implementation that returned an authority grant.
No production source, workflow, database or external service is modified.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from se.src.infrastructure.storage.identity.account_incarnation_ledger import (
    ClosedAccountIncarnationLedger,
    OriginalIncarnationUnavailable,
)
from se.src.infrastructure.storage.identity.wk_issuer_verifier import (
    ClosedWkIssuerVerifier,
    WkIssuerVerificationUnavailable,
)


SE = Path(__file__).resolve().parents[2]
IDENTITY = SE / "src" / "infrastructure" / "storage" / "identity"
LEDGER_FILE = IDENTITY / "account_incarnation_ledger.py"
VERIFIER_FILE = IDENTITY / "wk_issuer_verifier.py"


class _NoTouch:
    """Would fail immediately if an untrusted session or event were consumed."""

    def __getattribute__(self, name: str) -> object:
        raise AssertionError(f"untrusted object accessed: {name}")

    def __bool__(self) -> bool:
        raise AssertionError("untrusted object truth-tested")

    def __str__(self) -> str:
        raise AssertionError("untrusted object stringified")


def _ledger_args(*, owner: object, epoch: object) -> dict[str, object]:
    return {
        "owner_user_id": owner,
        "caller_sql_session": _NoTouch(),
        "database_identity": "db:unverified-restored-snapshot",
        "external_epoch": epoch,
    }


def _issuer_args(*, envelope: object, epoch: object) -> dict[str, object]:
    return {
        "envelope": envelope,
        "expected_owner_user_id": "alice",
        "expected_incarnation_id": "claimed-created-once",
        "expected_source_kind": "CAS_FILE_ASSET",
        "expected_source_id": "claimed-source-id",
        "expected_database_identity": "db:unverified",
        "expected_external_epoch": epoch,
    }


async def _require_async_deny(awaitable: object) -> None:
    """A positive return MUST fail this test, never be treated as success."""
    try:
        await awaitable
    except PermissionError:
        return
    raise AssertionError("security oracle accepted an unauthorized positive result")


def _require_sync_deny(call: object, **kwargs: object) -> None:
    try:
        call(**kwargs)
    except PermissionError:
        return
    raise AssertionError("security oracle accepted an unauthorized positive result")


def test_a3_closed_adapters_expose_no_available_authority() -> None:
    ledger = ClosedAccountIncarnationLedger()
    verifier = ClosedWkIssuerVerifier()
    assert ledger.is_available is False
    assert verifier.is_available is False
    assert issubclass(OriginalIncarnationUnavailable, PermissionError)
    assert issubclass(WkIssuerVerificationUnavailable, PermissionError)
    assert not hasattr(ledger, "__dict__")
    assert not hasattr(verifier, "__dict__")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["alice", "", "replayed-id", "__untrusted_object__"])
@pytest.mark.parametrize("epoch", [-1, 0, 42])
async def test_a3_01_original_lookup_never_authenticates_claimed_owner(
    owner: object, epoch: int
) -> None:
    ledger = ClosedAccountIncarnationLedger()
    with pytest.raises(OriginalIncarnationUnavailable, match="A3-01 CLOSED"):
        await ledger.read_original(**_ledger_args(owner=_NoTouch() if owner == "__untrusted_object__" else owner, epoch=epoch))
    assert ledger.is_available is False


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["alice", "", "stale-guest", "__untrusted_object__"])
@pytest.mark.parametrize("epoch", [-1, 0, 42])
async def test_a3_01_new_or_replayed_enrollment_never_mints_wk(
    owner: object, epoch: int
) -> None:
    ledger = ClosedAccountIncarnationLedger()
    with pytest.raises(OriginalIncarnationUnavailable, match="A3-01 CLOSED"):
        await ledger.enroll_at_creation(
            **_ledger_args(owner=_NoTouch() if owner == "__untrusted_object__" else owner, epoch=epoch),
            creation_event=_NoTouch(),
        )
    assert ledger.is_available is False


@pytest.mark.parametrize(
    "envelope",
    [b"", b'{"verified":true,"issuer":"trusted"}', b"signed-looking-wk-receipt", "__untrusted_envelope__"],
)
@pytest.mark.parametrize("epoch", [-1, 0, 42])
def test_a3_02_forged_or_replayed_envelope_always_denied(
    envelope: object, epoch: int
) -> None:
    verifier = ClosedWkIssuerVerifier()
    with pytest.raises(WkIssuerVerificationUnavailable, match="A3-02 CLOSED"):
        verifier.verify_enrollment(
            **_issuer_args(
                envelope=_NoTouch() if envelope == "__untrusted_envelope__" else envelope,
                epoch=epoch,
            )
        )
    assert verifier.is_available is False


def test_a3_both_modules_remain_pure_nonwired_python_contracts() -> None:
    """Detect accidental IO/key/DB imports or a wired production consumer."""
    for path in (LEDGER_FILE, VERIFIER_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert imports == {"__future__", "typing"}
        assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
        assert not any(
            isinstance(node, (ast.Global, ast.Nonlocal))
            for node in ast.walk(tree)
        )
    # These two source-only adapters must not become reachable from existing
    # runtime modules through direct imports, registration or dynamic names.
    for path in (SE / "src").rglob("*.py"):
        if path in (LEDGER_FILE, VERIFIER_FILE):
            continue
        source = path.read_text(encoding="utf-8")
        assert "ClosedAccountIncarnationLedger" not in source, path
        assert "ClosedWkIssuerVerifier" not in source, path
        assert "account_incarnation_ledger" not in source, path
        assert "wk_issuer_verifier" not in source, path


@pytest.mark.asyncio
async def test_red_first_sensitivity_rejects_unsafe_positive_ledger_control() -> None:
    """A known-bad allow implementation cannot satisfy the denial oracle."""

    class UnsafeLedger:
        async def read_original(self, **_kwargs: object) -> dict[str, bool]:
            return {"authorized": True}

        async def enroll_at_creation(self, **_kwargs: object) -> dict[str, bool]:
            return {"wk_minted": True}

    unsafe = UnsafeLedger()
    with pytest.raises(AssertionError, match="unauthorized positive"):
        await _require_async_deny(unsafe.read_original(**_ledger_args(owner="alice", epoch=1)))
    with pytest.raises(AssertionError, match="unauthorized positive"):
        await _require_async_deny(
            unsafe.enroll_at_creation(
                **_ledger_args(owner="alice", epoch=1),
                creation_event=object(),
            )
        )


def test_red_first_sensitivity_rejects_unsafe_positive_issuer_control() -> None:
    """A forged verifier acceptance must fail the exact same oracle."""

    def unsafe_verify(**_kwargs: object) -> dict[str, bool]:
        return {"valid": True}

    with pytest.raises(AssertionError, match="unauthorized positive"):
        _require_sync_deny(unsafe_verify, **_issuer_args(envelope=b"spoofed", epoch=1))


@pytest.mark.asyncio
async def test_a3_real_methods_pass_the_same_strict_sensitivity_oracle() -> None:
    ledger = ClosedAccountIncarnationLedger()
    await _require_async_deny(ledger.read_original(**_ledger_args(owner="alice", epoch=1)))
    await _require_async_deny(
        ledger.enroll_at_creation(
            **_ledger_args(owner="alice", epoch=1),
            creation_event=_NoTouch(),
        )
    )
    _require_sync_deny(
        ClosedWkIssuerVerifier().verify_enrollment,
        **_issuer_args(envelope=b"forged", epoch=1),
    )
