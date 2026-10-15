"""A3-02: nonwired, fail-closed original-incarnation/W-K issuer verifier.

Neither a caller-provided signature, token, issuer string, user ID, timestamp,
database generation, nor an in-process "verified" flag proves provenance.
Positive verification requires a separately authorized issuer/trust anchor,
authenticated original account-creation event, durable incarnation ledger,
revocation/replay custody and external monotonic epoch binding.

This module does not parse an envelope, load keys, contact a database/network,
write W/K receipts, issue credentials or expose an admission/open operation.
"""

from __future__ import annotations

from typing import NoReturn, final


class WkIssuerVerificationUnavailable(PermissionError):
    """Independently authenticated W/K issuance cannot be established."""


_CLOSED_REASON = (
    "A3-02 CLOSED: no trusted issuer or revocation authority, "
    "original account-incarnation evidence, or rollback-resistant epoch"
)


@final
class ClosedWkIssuerVerifier:
    """Pure deny-only verification boundary; no positive return value exists.

    Supplying apparently correct claims or a nonempty signature MUST NOT
    convert this source-only contract into an authenticated principal or
    enrollment capability.  No default-accept path or test-only bypass.
    """

    __slots__ = ()

    @property
    def is_available(self) -> bool:
        """No issuer has been configured or authenticated."""
        return False

    def verify_enrollment(
        self,
        *,
        envelope: bytes,
        expected_owner_user_id: str,
        expected_incarnation_id: str,
        expected_source_kind: str,
        expected_source_id: str,
        expected_database_identity: str,
        expected_external_epoch: int,
    ) -> NoReturn:
        """Always reject until independently anchored proof can be checked."""
        raise WkIssuerVerificationUnavailable(_CLOSED_REASON)


__all__ = ["ClosedWkIssuerVerifier", "WkIssuerVerificationUnavailable"]
