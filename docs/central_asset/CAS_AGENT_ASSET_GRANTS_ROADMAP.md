# CAS Agent asset access grants — planning addendum

**Baseline reviewed:** `main@a524ba870aec3ac73be7311d068d43fc1c963cda` (2026-09-27)
**Authority:** Issue #74 / existing `CAS-F*` roadmap
**State:** `RESERVED / NOT OPEN`; stage number and opening date unassigned
**Implementation authority:** none

## 1. Goal and current boundary

CAS stores canonical assets under `FileAsset.owner_user_id`. The user must be able to grant or deny a particular Agent instance access to those assets without transferring ownership. Current asset endpoints derive the owner from authenticated user identity, and CTX-F2C projects already-authorized asset descriptors. Neither provides a durable per-Agent grant contract. This addendum reserves that future CAS capability without altering active CAS-F6/F7/F8 authority or assuming a particular stage number.

## 2. Grant model to freeze

An Agent grant binds `owner_user_id`, canonical `asset_id`, server-resolved `agent_instance_id`, an explicit permission set, grant/revoke revisions, optional expiry and a trusted grantor. The first production slice should be per-asset; collection/project grants require a separate expansion and inheritance rule.

Recommended permission vocabulary for the initial freeze:

```text
DISCOVER   asset may appear in Agent-visible listings/search
READ       Agent may obtain authorized content
USE        Agent may attach/hydrate the asset for an authorized task/model call
```

Creating, modifying, deleting or sharing an asset are separate authorities. A grant never changes `FileAsset.owner_user_id`, `asset_id`, blob ownership, provider binding or asset lifecycle. An Agent-created asset is still owned by the user; any immediate creator-use exception must be a short-lived, explicit task-scoped grant, with a migration plan for existing owner-context flows. The future target is deny by default for per-Agent reads, but existing authenticated user access must not break merely because a grant row is absent before migration.

## 3. Enforcement path

```text
authenticated user ownership + server-resolved Agent instance
  -> current grant/revocation check for requested operation
  -> CAS READY/readability and content authority
  -> CTX projection or provider hydration only after CAS permits it
```

Check at both discovery and use time. A stale ContextSnapshot, cached descriptor, `asset://` URI, provider handle or message reference is not permission. Revocation prevents future reads/uses and invalidates derived Agent-visible CTX results; handling an already-dispatched external provider operation follows its own reconciliation contract. A disconnected Agent, expired grant or mismatched user/Agent pair fails closed.

## 4. Reserved implementation slices

1. Exact-head CAS/CTX/Agent overlap audit, identity and permission DTO contract, existing owner-path compatibility analysis.
2. Durable grant/revocation model and migration with user-authenticated grant/revoke/list APIs and idempotent CAS/revision updates.
3. CAS read/discover/use enforcement at every server path, including provider hydration and asset-bearing Agent calls; no client-supplied owner or Agent ID authority.
4. CTX invalidation/read recheck and UI grant controls; Agent interconnect messages carry references, never implicit rights.
5. Multi-worker/restart/revocation race, cross-user denial, migration and full Architecture gates.

The stage number is intentionally deferred until Issue #74 reserves an exact CAS-F* slot. Opening requires a dedicated CAS claim, current migration-head audit, CTX/Agent identity handoff and independent review under Issue #85. No grant API, schema or provider behavior is opened by this document.

Acceptance must prove owner retains access; ungranted Agent cannot discover/read/use; one Agent's grant does not permit another; expiration/revocation blocks future use after cache/reconnect; generated assets remain owned by the user; CTX references and AIC messages do not bypass CAS.
