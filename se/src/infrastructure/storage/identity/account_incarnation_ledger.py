"""A3-01: nonwired original-account-incarnation ledger boundary.

This module deliberately has no backing table, issuer, enrollment side effect,
cryptographic trust root, or runtime registration.  The PREP migration and the
authenticated creation-time authority have NOT landed.  An existing user ID,
email, session, OAuth claim, or historical row is never incarnation evidence.

Even if a caller supplies a SQL session and claims a trusted creation event,
this implementation always fails closed.  It does not mint W/K or event rows,
open writers, reconcile history, or commit/rollback a caller-owned session.
"""

from __future__ import annotations

from typing import NoReturn, final


class OriginalIncarnationUnavailable(PermissionError):
    """There is no authenticated, durable original-incarnation authority."""


_CLOSED_REASON = (
    "A3-01 CLOSED: no landed PREP schema, authenticated creation-time "
    "incarnation authority, or independently bound database epoch"
)


@final
class ClosedAccountIncarnationLedger:
    """Nonwired deny-only adapter; construction does not grant authority.

    A future SQL-backed implementation requires its own predecessor/custody
    release and must enroll a verified original user and its account incarnation
    with source/event/W/K in the *caller's existing SQL transaction*.  No
    independent connection, commit, post-commit repair, or synthetic backfill
    may be introduced by this class.
    """

    __slots__ = ()

    @property
    def is_available(self) -> bool:
        """Always false until a separately authorized implementation exists."""
        return False

    async def read_original(
        self,
        *,
        owner_user_id: str,
        caller_sql_session: object,
        database_identity: str,
        external_epoch: int,
    ) -> NoReturn:
        """Reject even plausible-looking identity values without live proof."""
        raise OriginalIncarnationUnavailable(_CLOSED_REASON)

    async def enroll_at_creation(
        self,
        *,
        owner_user_id: str,
        caller_sql_session: object,
        creation_event: object,
        database_identity: str,
        external_epoch: int,
    ) -> NoReturn:
        """Reject new or replayed enrollment; never write or mint W/K."""
        raise OriginalIncarnationUnavailable(_CLOSED_REASON)


__all__ = ["ClosedAccountIncarnationLedger", "OriginalIncarnationUnavailable"]
