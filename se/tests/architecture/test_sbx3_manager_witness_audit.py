"""SBX3 B0 negative Manager/kernel-witness handoff tests.

The manager performs read-through ONLY. This file never proves native isolation
or a production cleanup permit; real kernel proof is in the cgroup2 CI job.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from se.src.runtimes.capability.contracts.sandbox import SandboxProfile
from se.src.runtimes.capability.native_tree_cgroup_b0 import (
    CgroupTreeIdentity,
    CgroupTreeMismatch,
    EmptyTreeAuditObservation,
    LinuxCgroupTreeLedger,
    VerifiedEmptyWitness,
)
from se.src.runtimes.capability.sandbox import (
    NativeTreeUnverifiedError,
    SandboxManager,
)


def _enrolled(tmp_path):
    manager = SandboxManager((tmp_path / "root").resolve())
    lease = manager.acquire_for_execution(
        execution_id="e-1",
        owner_user_id="user-1",
        profile=SandboxProfile(profile_id="native-obs"),
    )
    manager.quarantine_native_tree(lease)
    identity = CgroupTreeIdentity(
        owner_id=lease.owner_user_id,
        execution_id=lease.execution_id,
        sandbox_id=lease.sandbox_id,
        generation=uuid4().hex,
    )
    return manager, lease, identity


def _test_only_mock_ledger():
    # Real Linux and Windows fail-close native checks are in the kernel CI.
    # This type-compatible stub only exercises manager routing and no-delete.
    return object.__new__(LinuxCgroupTreeLedger)


def test_forged_python_witness_does_not_mint_destroy_rights(tmp_path):
    manager, lease, identity = _enrolled(tmp_path)
    forged = VerifiedEmptyWitness(
        identity.owner_id, identity.execution_id, identity.sandbox_id,
        identity.generation, 1, 1, "VERIFIED_EMPTY", True,
    )
    assert forged.production_usable is True
    with pytest.raises(CgroupTreeMismatch):
        manager.inspect_native_cleanup_evidence(
            lease, ledger=forged, identity=identity
        )
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert lease.root.is_dir()


def test_exact_identity_readthrough_is_diagnostic_not_cleanup_authority(tmp_path):
    manager, lease, identity = _enrolled(tmp_path)
    fake = _test_only_mock_ledger()
    observation = EmptyTreeAuditObservation(
        identity.owner_id, identity.execution_id, identity.sandbox_id,
        identity.generation, 12, 34,
    )
    with patch.object(
        LinuxCgroupTreeLedger, "audit_committed_empty", return_value=observation
    ) as verify:
        result = manager.inspect_native_cleanup_evidence(
            lease, ledger=fake, identity=identity
        )
    verify.assert_called_once_with(identity)
    assert result == observation
    assert result.delete_permitted is False
    with pytest.raises(NativeTreeUnverifiedError):
        manager.release_execution("e-1", owner_user_id="user-1")
    assert lease.root.exists()


@pytest.mark.parametrize("field", ["owner_id", "execution_id", "sandbox_id"])
def test_foreign_lease_identity_does_not_query_kernel(tmp_path, field):
    manager, lease, identity = _enrolled(tmp_path)
    foreign = CgroupTreeIdentity(
        owner_id="other-owner" if field == "owner_id" else identity.owner_id,
        execution_id="other-execution" if field == "execution_id" else identity.execution_id,
        sandbox_id="other-sandbox" if field == "sandbox_id" else identity.sandbox_id,
        generation=identity.generation,
    )
    with patch.object(
        LinuxCgroupTreeLedger,
        "audit_committed_empty",
        side_effect=AssertionError("kernel audit must not be reached"),
    ) as verify:
        with pytest.raises(CgroupTreeMismatch):
            manager.inspect_native_cleanup_evidence(
                lease, ledger=_test_only_mock_ledger(), identity=foreign
            )
    verify.assert_not_called()
    assert lease.root.exists()


def test_missing_quarantine_tombstone_denies_even_valid_readthrough(tmp_path):
    manager, lease, identity = _enrolled(tmp_path)
    marker = manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id
    marker.unlink()
    with patch.object(
        LinuxCgroupTreeLedger,
        "audit_committed_empty",
        side_effect=AssertionError("kernel audit must not be reached"),
    ) as verify:
        with pytest.raises(NativeTreeUnverifiedError):
            manager.inspect_native_cleanup_evidence(
                lease, ledger=_test_only_mock_ledger(), identity=identity
            )
    verify.assert_not_called()
    assert lease.root.exists()


def test_restart_orphan_root_stays_undeletable_even_after_python_witness(tmp_path):
    manager, lease, identity = _enrolled(tmp_path)
    restarted = SandboxManager(manager.base_root)
    assert lease.sandbox_id in restarted._orphan_root_ids
    with pytest.raises(NativeTreeUnverifiedError):
        restarted._remove_root(lease.root)
    assert lease.root.exists()
