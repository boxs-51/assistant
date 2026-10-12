# Security #408 — Stage-A Skill publication contract and rollout

## Scope and compatibility

Stage A introduces a separately migrated PostgreSQL authority for caller-private
DIRECT-context Skills, server SYSTEM namespace origin, permanent ID collision
fences, revision CAS, and caller WebSocket capability registration. This feature
is **opt-in** through \`ASSISTANT_SKILL_PUBLICATION_DATABASE_URL\`; the existing
non-PG single-process registration behavior remains the legacy mode and is
**not guaranteed safe for multiworker callers**.

**Intentional compatibility break while PG is configured:** HTTP Gateway
\`GET /v1/capabilities/?kind=SKILL\` and \`GET /v1/capabilities/{id}\`
return **SQL-visible ACTIVE DIRECT_CONTEXT publications only**. The legacy
Gateway listed permitted server-manifest Skill *metadata* even before their
instruction was read. Under PG, lazily discovered server Skills have only a
\`NAMESPACE_RESERVATION\` (\`visibility=NONE\`) and **do not appear** in
Gateway GET until a separate trusted server publication. AGENT-eligible
descriptors continue to be checked by the AGENT authorization policy; an
Agent's \`skill.load\` is a private read, **not** permission to publish its
instruction to another identity or DIRECT prompts. Existing Gateway consumers
must not assume legacy metadata-list parity after opting into PostgreSQL.

## Explicit trusted server publication (operator-controlled, optional)

An operator who has reviewed and approved a server-owned Skill manifest and
instruction may explicitly configure a comma-separated allowlist, e.g.:

\`\`\`text
ASSISTANT_SKILL_PUBLICATION_DATABASE_URL=postgresql+asyncpg://<database-connection>
ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS=approved-skill-a,approved-skill-b
\`\`\`

Do **not** embed real connection credentials in logs, GitHub issues, PRs, or
evidence archives. Apply Alembic migrations from
\`alembic_skill_publications.ini\` before enabling the authority.

This allowlist is an **operator-origin publication decision at boot**, not a
value accepted from HTTP/WebSocket/Agent inputs. Empty config means *no*
automatic SYSTEM publications. Wildcards, duplicate/empty IDs, unknown
manifest IDs and missing PG authority fail closed; all IDs and instruction
bodies are validated before the first public SQL write. On startup, the
server reserves all builtin SYSTEM namespaces, then materializes only
the selected validated local manifests and calls
\`publish_system_direct_context(expected_revision=<current DB revision>)\`.
The repository checks immutable SYSTEM origin, publisher, kind, active
state and CAS before changing a reservation into \`SERVER_PUBLIC\`.
The definition retains manifest \`require_auth\`, \`required_scopes\`, and
\`metadata.required_permissions\`; each reader is re-authorized, and DIRECT
still reads the canonical SQL publication instead of Catalog/cache.

The operation is deliberately **not** triggered by browsing the catalog,
calling AGENT \`skill.load\`, or adding caller-provided metadata. It is
possible to make server Skills visible safely with explicit operator intent
without expanding trusted authority to arbitrary callers.

## Deployment, rollback and retention caveats

1. Test the migration in a staging database. Verify the five main jobs:
   Architecture Linux and two native Windows jobs, real PG17.11 Linux and
   native Windows with G3 + WebSocket negatives; also run the unchanged
   cross-principal R0 tests and a real authorized/unauthorized GET.
2. Inventory any consumer depending on lazy Skill metadata in the old
   Gateway list/detail response. Upgrade the consumer to distinguish
   AGENT private discovery from SQL DIRECT publication, or explicitly
   allowlist the appropriate reviewed server-manifest Skill at boot.
   Do **not** accept caller input or AGENT requests as publishing intent.
3. A successful trusted publication is **durable**. Removing an ID from the
   allowlist in a later deployment **does not revoke an existing row**.
   Stopping the application does not revoke publication either. If access
   must be withdrawn, fail closed at routing/deployment level and use a
   separately reviewed administrative SYSTEM-revocation/migration
   procedure; never delete the SQL row or resurrect a tombstone in place.
4. A failure halfway through a multi-ID bootstrap can leave earlier
   publications committed. The app should fail startup and require an
   operator to inspect durable records before retrying. This bootstrap
   does not claim cross-ID distributed atomicity.
5. Do not claim a non-PG legacy worker, or a standalone PR #440 that lacks
   the WebSocket fence, is multiworker namespace-safe.
6. Re-check the exact deployment commit, current main and branch rules.
   The owner's temporary suspension of repository-authored Policy #85
   does not alter GitHub platform protections or security requirements.

## Known Stage-A limitations

- PostgreSQL publication intentionally changes legacy Gateway Skill
  listing semantics under a configured authority. This document is an
  explicit migration contract, **not** a claim that all legacy clients
  remain behavior-compatible.
- No HTTP endpoint is exposed for trusted SYSTEM publication, and no
  automatic revocation of old SYSTEM publications exists here.
- R0 automated identities prove application-layer A/B separation; they
  are not an independent cryptographic JWT production verification.
- The operator allowlist is a sensitive high-trust deployment decision.
  It must be managed with the same review and retention controls as other
  changes that expose prompt content.
