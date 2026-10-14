# #359 × #242 — direct UI effect: P0 implementer handoff (2026-10-14)

**State:** HOLD for production; NO MERGE, NO NATIVE. This branch has only
additive experimental SQLite reference and disposable regressions. It does NOT
claim to implement secure gateway logout, a PostgreSQL authority, durable HITL
in the production dispatcher, or an OS-owned physical start fence.

## Reviewed canonical sources and current evidence

Source base: main af0feb268b59cc9828c536eda1629c3a64c39bed.
Findings: #359 comment 6161997005; #242 comment 6162000622.

- main TokenService.logout deletes ONLY SHA-256(refresh token);
  verify_access_token decodes access JWT without DB revocation query.
- TokenService.create_user_tokens creates access and refresh independently;
  Identity.session_id is decoded JWT jti, not verified against a durable
  server session/generation registry.
- Identity has api_key_id for API keys; admin keys may lack user_id. A verified
  API key id does NOT itself establish one authenticated client/session binding.
- Guest JWTs encode guest sub/type; no proof of durable server session/generation.
- CapabilityExecutionRequest.invocation_id/session_id are optional caller
  fields; capability_router forwards them; CapabilityExecutionContext.create
  may mint a new random invocation id.
- Client UIBridge._auth_generation/PR501 lock are local memory; neither is the
  issuer of a server authority or a cross-PID dispatch witness.
- PR482 is a dormant CLIENT SQLite HITL consent store, not the gateway intent
  authority. PR508 N1/N2 prove slow-byte trickle and manual-new-ID duplicate
  exposure, not corrected deadline/no-replay. PR510/515/516 clamp unsafe
  retry advisories, but have no durable one-use dispatch claim.

## Implemented on THIS branch (test-only, opt-in)

1. se/src/runtimes/capability/direct_ui_effect_ledger_reference.py (ADD):
   disposable SQLite authority/intent tables, canonical JSON SHA256,
   server-shape session/generation binding, BEGIN IMMEDIATE serialized
   issue/approval/claim/revoke, terminal readthrough as UNKNOWN_NONREPLAYABLE,
   changed immutable fingerprint conflict, restart-fresh-human requirement.
2. se/tests/architecture/test_ae359_cl242_direct_effect_reference.py (ADD):
   narrow SQLite N3/N4/N5/N6-style regressions including spawned 2-PID claim,
   stale auth, FK-OFF, restart and same-ID readthrough. Not real HTTP/PG17.
3. This design/handoff document (ADD).

THIS REFERENCE IS NOT AN AUTHENTICATED ISSUER: register_trusted_session and
record_human_decision are deliberately test-only internal API methods.
They accept trusted-callsite inputs as a precondition and DO NOT validate
live JWT, API key, guest, human proof or binding signatures themselves.
No production code imports the module. Do not wire these methods directly to
client JSON or use its bootstrap_disposable_only() in production.

## Production target: immutable identity, no caller authority

Require a trusted server auth service to derive (never receive as a model/JS
claim) immutable:
(principal_kind, principal_id, server_validated_session_or_client_id,
 auth_generation, invocation_id, capability_id, authoritative_version,
 canonical_args_hash, expiry).
- JWT: issue durable session row and signed stable session claim/opaque ID
  paired to refresh/access token family; verify current generation during
  direct-effect admission and logout, reject old access JWT immediately.
  Do not confuse JWT jti with stable login session unless issuer/mapping proves it.
- API key: read validated key record/key-id and status, plus registered opaque
  client/invocation binding. Authenticated API key alone does not identify the
  client. Key revocation and per-client generation must share the same fence.
- Guest: require durable guest session with verified TTL and client binding;
  missing authority = DENY (not guest upgrade by caller-supplied fields).
- No fallback via caller session_id, capability metadata, client generation,
  approval_id, model arguments, retry boolean, or access JWT with stale registry.

SQL target: separate auth_session_generation and direct_effect_intent/consent
tables with immutable identity fields, constraints, indexes and ALTER/retention
policy; never guess a 32a Alembic revision. #156/#381 owns migration
parent and shared UoW path; canonical AIC 31a/31b is still Draft.

## Exact transaction order

PostgreSQL SERIALIZATION:
  BEGIN;
  SELECT canonical (principal_kind, principal_id, session_id)
      FROM auth_session_generation FOR UPDATE;  -- ALWAYS FIRST
  validate active, generation, authenticated credential, expiry;
  SELECT direct_effect_intent(invocation_id) FOR UPDATE;
  SELECT bound durable consent / approval revision FOR UPDATE;
  immutable fingerprint/expiry/version checks;
  UPDATE intent state='EFFECT_STARTED', revision=revision+1 exactly once;
  INSERT immutable effect_claim_event with UNIQUE(invocation_id);
  COMMIT;  -- BEFORE calling external service
  then optional dispatch ONLY through the separately verified broker fence.

SQLite: BEGIN IMMEDIATE BEFORE any authority read, then same logical row
order (authority, intent, consent, effect). Must not share a separate
SQLAlchemy unit-of-work with a reversed lock order or consume stale cached
identity. Logout/revoke obtains authority first, rotates generation and marks
revoked, commits before returning an AUTH_REVOKED ACK. Claim-after-revoke
DENY; claim-before-revoke may be admitted: ordering alone does NOT prevent
the previously admitted claim from physically starting later.

Same invocation:
- byte/canonical-fingerprint, principal, client/session, generation or
  capability-version mismatch => CONFLICT, NEVER dispatch.
- DENIED/EXPIRED/TERMINAL => readonly classification, NEVER dispatch.
- EFFECT_STARTED, missing authenticated receipt, process killed, timeout,
  partial HTTP response or cancellation after commit => UNKNOWN_NONREPLAYABLE,
  NEVER dispatch again on same ID. A fresh invocation ID is a NEW operation;
  UI must not silently mint one after UNKNOWN.
- PENDING/APPROVED pre-claim after process restart => fresh human approval
  bound to same intent and current generation, rotated challenge, not replay.
- Without a protected receipt, do NOT transition UNKNOWN to SUCCESS.

## Physical post-logout guarantee (separate hard gate)

Product requirement "no external dispatch may START after logout ACK" needs
an OS/network-owner fence, not only transaction-level EFFECT_STARTED.
Proposed broker protocol:
  1. authenticated dispatch token includes session/generation/invocation and
     immutable claim revision, never a Python verified flag;
  2. privileged broker atomically checks live revoke epoch and acquires a
     STARTING/RUNNING lease BEFORE first OS/network dispatch instruction;
  3. logout enters FENCING under same coordinator epoch, disallows new leases,
     waits for STARTING leases to reach started or safely cancelled, and
     drains/quarantines unstarted work across ALL PIDs/brokers;
  4. only after documented physical broker witness may logout return the
     stronger PHYSICAL_NO_POST_ACK_START ACK. If broker inaccessible:
     fail closed, do NOT acknowledge physical fence success.
Already-started external effects may continue; physical exactly-once completion
is NOT proven by a start fence or SQL claim. External idempotency/receipt
protocol is independently required for any stronger result.

## Exact source ownership / branch prerequisite

- #359 server/runtime owns trusted issuer, direct-effect service, exact
  invocation/claim/reconcile readthrough.
- #156 + #381 SQL models/migration/shared UoW and canonical parent, revoke
  serialization; require MIGRATION_PARENT_ACK and schema/data policy.
- Auth owner: TokenService/JWTAuthenticator/APIKeyService/guest service and
  Identity schema; ACK needed for stable refresh/access family and logout.
- #242 owns client UIBridge, HitlManager, LocalCapabilityExecutor,
  CapabilityDispatcher, GatewayLLMClient and PR482 adapter.
- #417 owns actual PG17 Linux/Windows & real HTTP/process-kill CI.
- #164/#371 retain native Agent DENY, no host Popen fallback or activation.

Frozen Drafts remain separate: PR482@898436f8; PR501@bd40a0e6 ->
PR507@222273c0 -> PR508@6b10e820; PR510@f5274d04 ->
PR515@d46e0ad9 -> PR516@b874d3e0. None is ancestor of this branch
and no GREEN may be inherited from a sibling.

## Exit matrix — mandatory remaining evidence

| Gate | Required real evidence | Current state |
| --- | --- | --- |
| N3 | stale approval / delayed logout / confirmed server epoch | HOLD; SQLite reference only |
| N4 | PG17 two-PID claim same ID and changed hash | HOLD; SQLite reference only |
| N5 | K1/K2 crash PENDING / APPROVED / EFFECT_STARTED | HOLD; restart-only reference |
| N6 | SQLite FK-OFF AND PG17 Linux/Windows migrations | HOLD; SQLite reference only |
| N7 | real partial HTTP/truncated response + SQL receipt | NOT RUN |
| N8 | cancel preclaim/postclaim and OS physical count | NOT RUN |
| N9 | forged JWT/API key/guest/client/generation/args | NOT RUN |
| N10 | slow trickle absolute wall-clock deadline | PR508 NEGATIVE PASS; FIX HOLD |
| N11 | reversed lock-order/deadlock race all entrypoints | NOT RUN |
| N12 | process-restart same-ID readonly, SQL revisions | HOLD; SQLite reference only |
| K1/K2 | real PG17 Linux/Windows OS hard kill / physical effect count | NOT RUN |
| LOGOUT | broker physical no-post-ACK-start | HOLD |
| EXACTLY_ONCE | authenticated provider receipt + downstream idempotency | NOT CLAIMED |

Required before touching production: bilateral #359/#242 ACK, auth owner ACK,
#156/#381 migration and shared transaction ACK, #417 CI owner ACK. Do not
merge or enable native on a test-reference branch.
