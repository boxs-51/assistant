# UBQ-1 Durable User Budget Representation

Status: **CLAIMED / implementation evidence in progress**  
Canonical claim baseline: `main@92afb23270705c8fc1afbf58599b9d70182ac607`  
Migration: `25a_ubq1_user_budget_foundation -> 24a_r12_stale_lease_scan_index`

## Scope

UBQ-1 introduces durable representation and persistence primitives only. It does
not wire live admission/charging, alter TaskBudget, change Agent recovery,
change timeout behavior, or create UBQ-2 runtime authority.

The canonical renewable owner remains the trusted server-resolved user. Every
durable policy, account, window, per-capability usage row and reservation
receipt is bound to that owner.

## Durable records

- `UserBudgetPolicyRecord` is immutable/versioned policy authority.
- `UserBudgetAccountRecord` is the per-user policy-selection and epoch/CAS anchor.
- `UserBudgetWindowRecord` preserves historical epochs and captures exact
  governing policy id/version/fingerprint.
- `UserToolBudgetUsageRecord` keys per-tool accounting by canonical
  `capability_id`.
- `UserBudgetReservationRecord` provides owner-scoped, cross-window
  idempotency through `UNIQUE(owner_user_id, idempotency_key)`.

At most one ACTIVE window may exist for an owner.

## Numeric representation

All durable accounting uses signed BIGINT atomic units. Calls/tokens/tool calls
have quantum 1. Compute units and USD cost have quantum `1e-8`. Decimal inputs
are normalized with `ROUND_HALF_EVEN` and converted to integer atomic units.
Binary floating point is not accounting authority.

Policy fingerprints are SHA-256 over canonical JSON built from the same
normalized semantic representation used by storage.

## Rollover

Window creation is lazy and anchored to trusted aware-UTC server time.

SQLite obtains `BEGIN IMMEDIATE` before the first authoritative account,
policy or window read. PostgreSQL uses account-row locking plus revision/CAS.
The account's `next_window_epoch` is the only epoch allocator; `MAX(epoch)+1`
is forbidden.

A concurrent caller that arrives after another caller has committed an active
unexpired epoch returns that durable winner instead of minting another window.

## SQLite integrity

The shared SQLite driver is intentionally unchanged. Migration 25a installs
UBQ-local triggers that provide owner/reference/history protection even when
`PRAGMA foreign_keys == 0`.

The triggers protect:
- child owner/window/policy references;
- immutable policy identity/history;
- immutable window provenance/history;
- account pointer/epoch monotonicity;
- immutable usage/reservation identity;
- deletion of a User that still has UBQ history.

PostgreSQL uses native composite foreign keys and RESTRICT semantics.

## Downgrade

Downgrade is allowed only when every UBQ table is empty. SQLite triggers are
removed first, followed by child-to-parent table teardown. Durable accounting
history is never deleted to make downgrade succeed.

## Explicitly outside UBQ-1

- owner resolution/auth wiring;
- live quota admission or settlement orchestration;
- TaskBudget demotion/deletion/backfill;
- AgentRepository, AgentRuntime, recovery or coordinator changes;
- timeout/deadline behavior;
- UBQ-2+ dual-accounting/runtime integration;
- TBO/AAT/AIC production behavior.
