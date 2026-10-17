"""RED-first deny-only S0 negative tests; NOT PG17 production gate evidence.

Run: python -m pytest -q se/tests/architecture/test_aic31b_external_barrier.py
"""
import json
import unittest

from ops.aic31b.barrier import BarrierDenied, BarrierUnavailable, DenyOnlyBarrier, Record


TARGET = "a" * 64
OPERATOR = "b" * 64
REVIEWER = "c" * 64
OUTSIDER = "d" * 64


class FakeExternalStore:
    def __init__(self):
        self.value = None
        self.revision = 0
        self.fail = False
        self.lose_next_cas = False

    def read(self, target):
        if self.fail:
            raise BarrierUnavailable("quorum lost")
        return self.value, self.revision

    def cas(self, target, revision, value):
        if self.fail:
            raise BarrierUnavailable("quorum lost")
        if self.lose_next_cas:
            self.lose_next_cas = False
            return False
        if revision != self.revision:
            return False
        self.value = value
        self.revision += 1
        return True


def barrier(store=None, actor=OPERATOR, target=TARGET, reviewer=REVIEWER):
    store = store or FakeExternalStore()
    return DenyOnlyBarrier(
        store=store, target_sha256=target, actor_cert_sha256=actor,
        operator_cert_sha256=OPERATOR, reviewer_cert_sha256=reviewer
    )


class ExternalBarrierTests(unittest.TestCase):
    def test_unknown_target_is_never_an_admission_grant(self):
        control = barrier()
        self.assertIsNone(control.inspect())
        with self.assertRaises(BarrierDenied):
            control.require_admitted()

    def test_seal_new_target_epoch_monotonic_intent_only(self):
        control = barrier()
        receipt = control.seal(expected_epoch=0, ticket="MAINT-381")
        self.assertEqual((receipt.epoch, receipt.state), (1, "SEAL_REQUESTED"))
        with self.assertRaises(BarrierDenied):
            control.require_admitted()

    def test_duplicate_ticket_returns_same_epoch_without_new_write(self):
        store = FakeExternalStore()
        control = barrier(store)
        original = control.seal(expected_epoch=0, ticket="M1")
        revision = store.revision
        self.assertEqual(control.seal(expected_epoch=1, ticket="M1"), original)
        self.assertEqual(store.revision, revision)

    def test_stale_epoch_cannot_seal(self):
        control = barrier()
        control.seal(expected_epoch=0, ticket="M1")
        for old in (0, 2, -1, True):
            with self.subTest(old=old), self.assertRaises(BarrierDenied):
                control.seal(expected_epoch=old, ticket="M1")

    def test_unfinished_epoch_cannot_be_overwritten(self):
        control = barrier()
        control.seal(expected_epoch=0, ticket="M1")
        with self.assertRaises(BarrierDenied):
            control.seal(expected_epoch=1, ticket="M2")

    def test_unknown_role_cannot_seal_or_quarantine(self):
        store = FakeExternalStore()
        other = barrier(store, actor=OUTSIDER)
        with self.assertRaises(BarrierDenied):
            other.seal(expected_epoch=0, ticket="M1")
        barrier(store).seal(expected_epoch=0, ticket="M1")
        with self.assertRaises(BarrierDenied):
            other.quarantine(expected_epoch=1)

    def test_reviewer_can_quarantine_but_cannot_seal(self):
        store = FakeExternalStore()
        barrier(store).seal(expected_epoch=0, ticket="M1")
        reviewer = barrier(store, actor=REVIEWER)
        with self.assertRaises(BarrierDenied):
            reviewer.seal(expected_epoch=1, ticket="M1")
        quarantined = reviewer.quarantine(expected_epoch=1)
        self.assertEqual(quarantined.state, "QUARANTINED")
        with self.assertRaises(BarrierDenied):
            reviewer.require_admitted()

    def test_quarantined_cannot_reopen_or_reseal(self):
        store = FakeExternalStore()
        operator = barrier(store)
        operator.seal(expected_epoch=0, ticket="M1")
        first = operator.quarantine(expected_epoch=1)
        self.assertEqual(operator.quarantine(expected_epoch=1), first)
        with self.assertRaises(BarrierDenied):
            operator.seal(expected_epoch=1, ticket="M1")

    def test_compare_and_swap_loser_does_not_admit(self):
        store = FakeExternalStore()
        store.lose_next_cas = True
        with self.assertRaisesRegex(BarrierDenied, "CAS lost"):
            barrier(store).seal(expected_epoch=0, ticket="M1")
        self.assertIsNone(store.value)

    def test_external_store_outage_fail_closed(self):
        store = FakeExternalStore()
        store.fail = True
        control = barrier(store)
        with self.assertRaises(BarrierUnavailable):
            control.inspect()
        with self.assertRaises(BarrierUnavailable):
            control.seal(expected_epoch=0, ticket="M1")
        with self.assertRaises(BarrierDenied):
            control.require_admitted()

    def test_target_mismatch_and_bad_record_fail_closed(self):
        store = FakeExternalStore()
        store.value = Record(
            target_sha256="e" * 64, epoch=1, state="SEAL_REQUESTED",
            ticket="M1", operator_sha256=OPERATOR
        )
        store.revision = 1
        with self.assertRaises(BarrierDenied):
            barrier(store).inspect()
        with self.assertRaises(BarrierDenied):
            Record.decode(b'{"epoch":1}')
        with self.assertRaises(BarrierDenied):
            Record.decode(b'{"epoch":1,"state":"OPEN","unexpected":true}')

    def test_even_injected_open_record_never_grants(self):
        store = FakeExternalStore()
        store.value = Record(
            target_sha256=TARGET, epoch=7, state="OPEN",
            ticket="M1", operator_sha256=OPERATOR
        )
        store.revision = 4
        with self.assertRaises(BarrierDenied):
            barrier(store).require_admitted()
        # An old/stale operator cannot reseal an OPEN epoch.
        with self.assertRaises(BarrierDenied):
            barrier(store).seal(expected_epoch=6, ticket="M2")
        # New seal takes a strictly larger epoch but still denies admission.
        record = barrier(store).seal(expected_epoch=7, ticket="M2")
        self.assertEqual(record.epoch, 8)
        with self.assertRaises(BarrierDenied):
            barrier(store).require_admitted()

    def test_distinct_actor_requirement_is_structural(self):
        with self.assertRaises(BarrierDenied):
            barrier(reviewer=OPERATOR)

    def test_bad_ticket_or_nonhash_identity_denied(self):
        with self.assertRaises(BarrierDenied):
            barrier().seal(expected_epoch=0, ticket="../bad ticket")
        with self.assertRaises(BarrierDenied):
            barrier(target="not-a-digest")

    def test_record_roundtrip_is_canonical_and_strict(self):
        record = Record(
            target_sha256=TARGET, epoch=3, state="QUARANTINED",
            ticket="M1", operator_sha256=OPERATOR
        )
        self.assertEqual(Record.decode(record.encode()), record)
        damaged = json.loads(record.encode())
        damaged["epoch"] = True
        with self.assertRaises(BarrierDenied):
            Record.decode(json.dumps(damaged).encode())


if __name__ == "__main__":
    unittest.main()
