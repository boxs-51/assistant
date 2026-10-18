"""Isolated #359/#242 SQLite contract checks (NOT production security proof)."""
import multiprocessing as mp
import sqlite3
from pathlib import Path

import pytest

from se.src.runtimes.capability.direct_ui_effect_ledger_reference import (
    AuthorityDenied, FingerprintConflict, SQLiteDirectEffectReference,
)


def _claim_in_pid(path, auth, invocation_id, ready, output):
    ledger = SQLiteDirectEffectReference(path, boot="A" * 32, now=lambda: 1000.0)
    ready.wait(timeout=15)
    try:
        output.put(ledger.claim_effect_once(auth, invocation_id, "mail.send", "v7", {"to": "x"}))
    except Exception as exc:
        output.put(f"ERROR:{type(exc).__name__}:{exc}")


@pytest.fixture
def active(tmp_path):
    path = tmp_path / "direct_effect.sqlite"
    ledger = SQLiteDirectEffectReference(path, boot="A" * 32, now=lambda: 1000.0)
    ledger.bootstrap_disposable_only()
    auth = ledger.register_trusted_session("jwt", "person-1", "verified-session-1")
    return path, ledger, auth


def _approved(ledger, auth):
    invocation, challenge = ledger.issue_trusted_intent(
        auth, "mail.send", "v7", {"to": "x"}, ttl_seconds=120,
    )
    assert ledger.record_human_decision(
        auth, invocation, challenge, "mail.send", "v7", {"to": "x"}, approved=True,
    ) == "APPROVED"
    return invocation


def test_n3_stale_session_logout_blocks_pending_and_claim(active):
    _, ledger, auth = active
    invocation = _approved(ledger, auth)
    assert ledger.revoke_trusted_session(auth) == 2
    with pytest.raises(AuthorityDenied):
        ledger.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"})
    assert ledger.inspect_disposable_only(invocation)["state"] == "APPROVED"


def test_n4_changed_fingerprint_is_conflict_even_same_id(active):
    _, ledger, auth = active
    invocation = _approved(ledger, auth)
    with pytest.raises(FingerprintConflict):
        ledger.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "evil"})
    with pytest.raises(FingerprintConflict):
        ledger.claim_effect_once(auth, invocation, "mail.send", "v8", {"to": "x"})
    assert ledger.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "EFFECT_STARTED_NEW_CLAIM"
    assert ledger.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "UNKNOWN_NONREPLAYABLE"


def test_n4_two_real_processes_one_durable_claim(active):
    path, ledger, auth = active
    invocation = _approved(ledger, auth)
    ctx = mp.get_context("spawn")
    go, outputs = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_claim_in_pid, args=(path, auth, invocation, go, outputs)) for _ in range(2)]
    for proc in workers:
        proc.start()
    go.set()
    result = [outputs.get(timeout=25) for _ in range(2)]
    for proc in workers:
        proc.join(timeout=25)
        assert proc.exitcode == 0
    assert sorted(result) == ["EFFECT_STARTED_NEW_CLAIM", "UNKNOWN_NONREPLAYABLE"]
    assert ledger.inspect_disposable_only(invocation)["revision"] == 2


def test_n5_restart_requires_fresh_human_before_claim(active):
    path, old, auth = active
    invocation = _approved(old, auth)
    new = SQLiteDirectEffectReference(path, boot="B" * 32, now=lambda: 1000.0)
    assert new.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "FRESH_HUMAN_REQUIRED"
    challenge = new.refresh_challenge_after_restart(
        auth, invocation, "mail.send", "v7", {"to": "x"},
    )
    assert new.record_human_decision(
        auth, invocation, challenge, "mail.send", "v7", {"to": "x"}, approved=True,
    ) == "APPROVED"
    assert new.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "EFFECT_STARTED_NEW_CLAIM"
    after = SQLiteDirectEffectReference(path, boot="C" * 32, now=lambda: 1000.0)
    assert after.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "UNKNOWN_NONREPLAYABLE"
    with pytest.raises(AuthorityDenied):
        after.refresh_challenge_after_restart(auth, invocation, "mail.send", "v7", {"to": "x"})


def test_n6_sqlite_fk_off_does_not_reanimate_revoked_session(active):
    path, ledger, auth = active
    invocation = _approved(ledger, auth)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=OFF")
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    con.close()
    ledger.revoke_trusted_session(auth)
    reopened = SQLiteDirectEffectReference(path, boot="A" * 32, now=lambda: 1000.0)
    with pytest.raises(AuthorityDenied):
        reopened.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"})


def test_missing_authoritative_principal_session_and_reuse_denied(active):
    _, ledger, auth = active
    with pytest.raises(AuthorityDenied):
        ledger.register_trusted_session("guest", "", "client-opaque")
    with pytest.raises(AuthorityDenied):
        ledger.register_trusted_session(auth.kind, auth.principal, auth.session)
    invocation = _approved(ledger, auth)
    forged = type(auth)(auth.kind, auth.principal, "attacker-client", auth.generation)
    with pytest.raises(AuthorityDenied):
        ledger.claim_effect_once(forged, invocation, "mail.send", "v7", {"to": "x"})


def test_pending_never_claims_and_denied_is_terminal(active):
    _, ledger, auth = active
    invocation, challenge = ledger.issue_trusted_intent(auth, "mail.send", "v7", {"to": "x"})
    assert ledger.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "PENDING"
    assert ledger.record_human_decision(
        auth, invocation, challenge, "mail.send", "v7", {"to": "x"}, approved=False,
    ) == "DENIED"
    assert ledger.claim_effect_once(auth, invocation, "mail.send", "v7", {"to": "x"}) == "DENIED"
    with pytest.raises(AuthorityDenied):
        ledger.record_human_decision(
            auth, invocation, challenge, "mail.send", "v7", {"to": "x"}, approved=True,
        )
