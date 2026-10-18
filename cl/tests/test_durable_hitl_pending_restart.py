"""E-P1B dormant durable approval fence: crash, expiry and replay negatives.

These tests use actual OS process exit on native Linux/Windows. They prove the
local pre-effect state machine only. They DO NOT activate the production HITL
UI/dispatcher or claim a composed S1/CL/server hard-restart exit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from cl.src.hitl.durable_pending_consent import (
    ConsentConflict,
    ConsentNotRecoverable,
    DurablePendingConsentStore,
)

BOUND = {
    "client_id": "cl-1",
    "principal_id": "principal-A",
    "invocation_id": "invocation-A",
    "capability_id": "local.dangerous",
    "request_fingerprint": "f" * 64,
}
RISK = "HIGH"


def _child_crash(path: Path, receipt: Path, phase: str) -> dict:
    """Real independent Python interpreter; os._exit bypasses normal cleanup."""
    assert phase in {"pending", "approved", "denied", "effect_started"}
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(root), env.get("PYTHONPATH", "")) if part
    )
    script = """
import json
import os
from pathlib import Path
from cl.src.hitl.durable_pending_consent import DurablePendingConsentStore
db = DurablePendingConsentStore(Path(os.environ['P1B_SQLITE']))
keys = json.loads(os.environ['P1B_KEYS'])
record = db.open_pending(**keys, risk_level='HIGH')
if os.environ['P1B_PHASE'] in ('approved','denied','effect_started'):
    assert db.respond(**keys, approval_id=record.approval_id,
                      approve=os.environ['P1B_PHASE'] != 'denied')
if os.environ['P1B_PHASE'] == 'effect_started':
    assert db.claim_effect(**keys, approval_id=record.approval_id)
Path(os.environ['P1B_RECEIPT']).write_text(json.dumps({
    'pid': os.getpid(), 'token': record.approval_id,
    'boot_generation': record.boot_generation,
}), encoding='utf-8')
os._exit(79)
"""
    env.update({
        "P1B_SQLITE": str(path),
        "P1B_RECEIPT": str(receipt),
        "P1B_PHASE": phase,
        "P1B_KEYS": json.dumps(BOUND),
    })
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        out, err = child.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise
    assert child.returncode == 79, (
        f"OS crash fixture exited {child.returncode}: "
        + err.decode(errors="replace")[-2500:]
        + out.decode(errors="replace")[-1000:]
    )
    assert receipt.is_file(), "Crash happened before durable receipt"
    record = json.loads(receipt.read_text(encoding="utf-8"))
    assert record["pid"] == child.pid
    return record


def test_pending_os_crash_requires_new_human_decision(tmp_path):
    db_path = tmp_path / "consent.sqlite3"
    original = _child_crash(db_path, tmp_path / "old.json", "pending")
    reboot = DurablePendingConsentStore(db_path)
    before = reboot.inspect(**{
        k: BOUND[k] for k in ("client_id", "principal_id", "invocation_id")
    })
    assert before is not None and before.state == "PENDING"
    assert reboot.recovery_disposition(before) == "REAUTHENTICATION_REQUIRED"
    assert not reboot.respond(
        **BOUND, approval_id=original["token"], approve=True,
    )
    assert not reboot.claim_effect(**BOUND, approval_id=original["token"])
    rotated = reboot.reissue_after_restart(**BOUND, risk_level=RISK)
    assert rotated.approval_id != original["token"]
    assert rotated.boot_generation != original["boot_generation"]
    assert not reboot.respond(**BOUND, approval_id=original["token"], approve=True)
    assert reboot.respond(**BOUND, approval_id=rotated.approval_id, approve=True)
    assert reboot.claim_effect(**BOUND, approval_id=rotated.approval_id)
    assert not reboot.claim_effect(**BOUND, approval_id=rotated.approval_id)
    assert reboot.recovery_disposition(reboot.inspect(
        **{k: BOUND[k] for k in ("client_id","principal_id","invocation_id")}
    )) == "OUTCOME_UNKNOWN_NONREPLAYABLE"


@pytest.mark.parametrize("phase", ["approved", "effect_started", "denied"])
def test_precrash_decisions_never_become_implicit_approval(tmp_path, phase):
    db_path = tmp_path / "consent.sqlite3"
    receipt = _child_crash(db_path, tmp_path / "old.json", phase)
    reboot = DurablePendingConsentStore(db_path)
    original = reboot.inspect(**{
        k: BOUND[k] for k in ("client_id", "principal_id", "invocation_id")
    })
    assert original is not None
    assert not reboot.respond(**BOUND, approval_id=receipt["token"], approve=True)
    assert not reboot.claim_effect(**BOUND, approval_id=receipt["token"])
    if phase == "approved":
        assert reboot.recovery_disposition(original) == "REAUTHENTICATION_REQUIRED"
        reissued = reboot.reissue_after_restart(**BOUND, risk_level=RISK)
        assert reissued.state == "PENDING"
        assert reissued.approval_id != receipt["token"]
    elif phase == "effect_started":
        assert reboot.recovery_disposition(original) == "OUTCOME_UNKNOWN_NONREPLAYABLE"
        with pytest.raises(ConsentNotRecoverable):
            reboot.reissue_after_restart(**BOUND, risk_level=RISK)
    else:
        assert reboot.recovery_disposition(original) == "DENIED_TERMINAL"
        with pytest.raises(ConsentNotRecoverable):
            reboot.reissue_after_restart(**BOUND, risk_level=RISK)


def test_fingerprint_principal_and_invocation_id_are_immutable(tmp_path):
    store = DurablePendingConsentStore(tmp_path / "consent.sqlite3")
    record = store.open_pending(**BOUND, risk_level=RISK)
    changed = dict(BOUND, request_fingerprint="a" * 64)
    with pytest.raises(ConsentConflict):
        store.open_pending(**changed, risk_level=RISK)
    assert not store.respond(**changed, approval_id=record.approval_id, approve=True)
    assert not store.claim_effect(**changed, approval_id=record.approval_id)
    foreign = dict(BOUND, principal_id="principal-B")
    assert not store.respond(**foreign, approval_id=record.approval_id, approve=True)
    assert not store.claim_effect(**foreign, approval_id=record.approval_id)
    with pytest.raises(ValueError):
        store.open_pending(**dict(BOUND, risk_level="MEDIUM"))


def test_expired_or_replayed_or_late_ui_response_fails_closed(tmp_path):
    now = [1000.0]
    store = DurablePendingConsentStore(
        tmp_path / "consent.sqlite3", now=lambda: now[0]
    )
    record = store.open_pending(**BOUND, risk_level=RISK, ttl_seconds=3)
    now[0] = 1004.0
    assert not store.respond(**BOUND, approval_id=record.approval_id, approve=True)
    assert not store.claim_effect(**BOUND, approval_id=record.approval_id)
    assert store.recovery_disposition(store.inspect(**{
        k: BOUND[k] for k in ("client_id","principal_id","invocation_id")
    })) == "EXPIRED_DENY"
    reboot = DurablePendingConsentStore(
        tmp_path / "consent.sqlite3", now=lambda: now[0]
    )
    with pytest.raises(ConsentNotRecoverable):
        reboot.reissue_after_restart(**BOUND, risk_level=RISK)
    assert not reboot.respond(**BOUND, approval_id=record.approval_id, approve=True)


def test_two_simultaneous_ui_responses_only_one_wins(tmp_path):
    store = DurablePendingConsentStore(tmp_path / "consent.sqlite3")
    record = store.open_pending(**BOUND, risk_level=RISK)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(
            lambda decision: store.respond(
                **BOUND, approval_id=record.approval_id, approve=decision
            ), (True, False),
        ))
    assert answers.count(True) == 1
    assert answers.count(False) == 1
    latest = store.inspect(**{
        k: BOUND[k] for k in ("client_id","principal_id","invocation_id")
    })
    assert latest.state in {"APPROVED", "DENIED"}
    if latest.state == "APPROVED":
        assert store.claim_effect(**BOUND, approval_id=record.approval_id)
        assert not store.claim_effect(**BOUND, approval_id=record.approval_id)
    else:
        assert not store.claim_effect(**BOUND, approval_id=record.approval_id)


def test_active_generation_cannot_reissue_and_duplicate_approval_cannot_replay(tmp_path):
    store = DurablePendingConsentStore(tmp_path / "consent.sqlite3")
    r = store.open_pending(**BOUND, risk_level=RISK)
    with pytest.raises(ConsentNotRecoverable):
        store.reissue_after_restart(**BOUND, risk_level=RISK)
    assert store.respond(**BOUND, approval_id=r.approval_id, approve=True)
    assert not store.respond(**BOUND, approval_id=r.approval_id, approve=True)
    assert store.claim_effect(**BOUND, approval_id=r.approval_id)
    assert not store.respond(**BOUND, approval_id=r.approval_id, approve=False)
    with pytest.raises(ConsentNotRecoverable):
        store.open_pending(**BOUND, risk_level=RISK)
