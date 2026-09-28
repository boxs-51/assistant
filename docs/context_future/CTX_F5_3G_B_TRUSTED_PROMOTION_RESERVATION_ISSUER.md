# CTX-F5-3G-B — Trusted Durable PromotionReservationIssuer

Status: implementation contract for the independently released CTX-F5-3G-B production slice.

## Scope

Production scope is exactly:

`se/src/infrastructure/storage/services/promotion_reservation_issuer.py`

Dedicated evidence is limited to this document plus the released unit, integration, and architecture tests.

Schema delta: ZERO.  
Migration delta: ZERO.  
Concrete supported production source kinds remain NONE.

CTX-F5-3G-B implements only the landed `PromotionReservationIssuer.reserve(*, intent)` protocol. It does not implement atomic reservation-to-Memory admission, Memory persistence, reservation consumption/revocation, source re-proof, runtime/container wiring, or HTTP/model-visible promotion.

## Trusted issuance sequence

The issuer must:

1. validate `MemoryPromotionIntent` integrity;
2. generate a server-owned opaque candidate `promotion_authority_id`;
3. open exactly one issuer-owned `AsyncSession` context through an injected session-context factory;
4. bind exactly one `DurablePromotionReservationRepository` to that session;
5. call `insert_or_converge_issued_candidate(...)`;
6. treat the returned durable winner as authoritative;
7. allow success only when the winner state is `ISSUED`;
8. commit exactly once;
9. construct and return `PromotionReservation` only after the commit succeeds, using `winner.promotion_authority_id` and `winner.intent`.

The generated authority ID is candidate material only. It is never authoritative merely because an insert was attempted.

## Terminal winner fence

A converged durable winner may already be terminal.

`ISSUED` is the only successful issuance state.

`CONSUMED` must raise the existing `PromotionReservationAlreadyConsumedError`. The issuer must not return success, mint a replacement authority, mutate the terminal row, or resurrect it.

`REVOKED` must raise the existing `PromotionReservationRevokedError`. The issuer must not return success, mint a replacement authority, mutate the terminal row, or resurrect it.

This keeps issuer success consistent with the canonical trusted verifier: successful issuance can only return authority that the verifier can accept as currently `ISSUED`.

## Convergence and proof authority

Sequential, concurrent, crash-recovery, and response-loss retries for the same exact canonical intent converge through the existing repository uniqueness boundary to one canonical durable reservation identity.

The freshly generated candidate ID may differ across retries. The successful returned envelope must still use the durable winner's identity.

A proof-authority tuple reused across a different exact canonical intent remains a fail-closed repository conflict. The issuer does not search for, mint, or substitute another authority to bypass that conflict.

No second idempotency key, cache, distributed lock, or bespoke persistence retry loop is introduced.

## Transaction, failure, and cancellation boundary

The issuer owns one short issuance transaction only.

The session-context dependency produces one fresh `AsyncSession` context per `reserve()` invocation. The issuer does not construct an engine or sessionmaker and does not modify `SqlAlchemyUnitOfWork` or `DatabaseDriver`.

The repository remains session-owned and never owns commit authority. The issuer commits at most once, and only an `ISSUED` winner reaches commit.

Persistence failure or commit failure must never leak a successful reservation. Commit failure uses the existing promotion-reservation persistence-unavailable error surface.

`asyncio.CancelledError` propagates and is never converted into success. Cancellation or failure before durable commit yields no successful issuance response. If durable commit succeeds but response delivery is lost or cancelled afterward, a fresh retry of the same exact intent must converge to the already committed durable winner.

The issuer calls no explicit rollback; the injected session context owns safe close/rollback behavior on exceptional exit.

## Closed authority

CTX-F5-3G-B does not modify or own:

- `se/src/context/memory_promotion.py`;
- promotion-reservation repository/model/schema/migration;
- F5-3G-A verifier behavior;
- Memory repository/model or Memory admission;
- `mark_consumed` or `mark_revoked`;
- source authority or source re-proof;
- runtime/container/main wiring;
- HTTP/API/model-callable surfaces;
- CAS lifecycle/provider authority;
- R11 retention/destructive-GC authority;
- R12 execution/recovery authority;
- F6/F7/F9/F10.

Atomic reservation-to-Memory admission remains CLOSED and requires a later independently audited stage.
