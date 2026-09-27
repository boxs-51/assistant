# CTX user and Agent memory scopes — planning addendum

**Baseline reviewed:** `main@a524ba870aec3ac73be7311d068d43fc1c963cda` (2026-09-27)
**Authority:** Issue #15 / existing `CTX-F*` roadmap
**State:** `RESERVED / NOT OPEN` for the scope changes below; no new production claim
**Opening date:** unassigned

## 1. Reason for this addendum

The user's target model is one stable Agent instance per user, with its own long-term memory, alongside user-wide personalization and searchable history across that user's sessions. CTX already owns Context, long-term Memory and future Personalization. This addendum places those three logical scopes inside CTX without assigning a second CTX namespace or changing the released meaning of `CTX-F5`/`CTX-F6`.

Current `AgentDefinition.name` is a definition name and `AgentMemoryConfig` is a conversation-window setting, not a durable per-user Agent identity or Memory scope. `AgentExecution.agent_id` names the executing Agent but does not, by itself, prove ownership of a per-user Agent instance. Current durable `MemoryRecordRow` carries `owner_user_id` but no canonical Agent-instance scope. This is a planning gap, not a claim that existing Memory rows are shared with every Agent.

## 2. Identity and three logical zones

`agent_instance_id` is an opaque stable identifier for one Agent instance belonging to one `owner_user_id`. The server derives the owner from authenticated authority and binds the instance to a versioned Agent definition/template. Two users selecting the same definition receive different instances. `agent_instance_id` is distinct from `AgentDefinition.name`, `execution_id`, `session_id`, `task_id`, `client_id` and `connection_id`. Recreating/reinstalling a client must not silently change the Agent instance or merge memories.

| Zone | Canonical source and stored view | Default visibility |
|---|---|---|
| User profile and personalization | Explicit user rules and versioned derived facts from authorized session evidence; each fact has provenance, confidence, status and correction history | User and Agents explicitly permitted to use the relevant profile fields |
| User-wide session history | Existing Session/Task/Branch/Transcript owners remain canonical; CTX maintains authorized discovery, digests and search projections across that user's sessions | User; Agent sees only sessions and excerpts within its granted context scope |
| Agent-private history and Memory | Agent-scoped CTX Memory and authorized references to sessions/tasks in which that instance participated; transcript bytes remain under their existing owner | That Agent instance and the owner user, subject to user policy; no implicit access for another Agent |

These are access and lifecycle scopes, not a requirement for three separate databases. A session may contribute evidence to more than one zone through separate authorized projections. Copying a transcript into another zone does not create a new transcript authority.

## 3. Personalization contract to freeze at CTX-F6

- Reserve a normalized user-profile fact shape: `profile_fact_id`, `owner_user_id`, fact kind/key/value, source (`USER_RULE` or `INFERRED_HABIT`), evidence references/versions, confidence, validity window, status and revision. The exact table/index design needs a separate F6 schema contract.
- User-authored rules are explicitly labeled and take precedence over inferred habits when they conflict. Neither model output nor a single session summary may silently become a user rule.
- Automatic inference from many sessions requires a released promotion policy: trusted source provenance, eligibility, deduplication, confidence threshold, bounded update cadence, expiry and user review/correction/deletion. The current CTX-F5 contracts keep automatic promotion CLOSED.
- A profile fact records its owner, source identities/versions, reason, confidence, creation/update time and status. Stale or revoked source evidence triggers revalidation before future model use.
- The user can inspect, correct, pin, suppress or erase a fact. Corrections must not be overwritten by the next automatic synthesis pass without a recorded policy decision.
- Profile retrieval and Working Set selection are separate gates; storing a fact does not authorize every Agent to read it or every model call to receive it.

## 4. Agent-private Memory contract to freeze within CTX-F5/F9

- A durable Agent Memory record must bind `(owner_user_id, agent_instance_id)` when it is private to an Agent. User-wide Memory remains a separate explicit scope. Migration of legacy owner-only rows requires classification; never assign them to an Agent by guesswork.
- Promotion from transcript or Task evidence needs the existing CTX source-proof/reservation authority and a proof that the Agent instance belongs to the user and participated in or was granted the source. No caller/model-supplied owner or Agent ID grants promotion.
- Another Agent can consume a selected item only through an explicit user-authorized sharing rule and a fresh read check; communication between Agents does not automatically share private Memory.
- Deleting an Agent instance must define revocation, retention/export and erasure semantics for its private Memory before the deletion API is opened. Physical transcript/asset retention remains with AE/CAS owners.
- CTX-F9 Working Set may include authorized profile facts, session excerpts and Agent-private memories with source labels and token limits. It must not merge the zones into one unlabelled prompt blob.

## 5. Read and authorization path

```text
authenticated user + server-resolved agent_instance_id
  -> per-zone authorization and current source proof
  -> CTX discovery/search/retrieval
  -> bounded Working Set selection
  -> model-visible item with provenance and scope label
```

An Agent-created asset remains CAS-owned by the user; CTX may hold only a source reference. Asset access at discovery and read time must be rechecked through CAS grants. A revoked CAS grant must not remain usable because CTX cached an ASSET digest or a prior ContextSnapshot.

## 6. Stage mapping and acceptance gates

| CTX authority | Addendum deliverable when separately released |
|---|---|
| Existing `CTX-F3/F4` | Inherit Session/Task/Branch source identity and finite structural access; do not reopen completed stages by implication |
| New CTX substage, number pending Issue #15 | User-wide cross-session index/search with owner and Agent visibility filters; source records remain canonical |
| `CTX-F5` | Explicit `USER_WIDE` versus `AGENT_PRIVATE` Memory scope, trusted Agent-instance binding, migration/erasure rules and source-proof admission |
| `CTX-F6` | Explicit rules plus derived personalization facts, automatic update policy, user correction and revocation |
| `CTX-F9` | Authorized Working Set composition from all three zones with provenance, scope labels and bounded selection |
| `CTX-F11/F12` | Chat/Agent integration, user controls, cross-user/cross-Agent denial tests and quality/erasure audit |

Before any per-Agent schema/runtime change, freeze a durable Agent-instance identity with the Agent registration owner and AIC-0 identity contract. Issue #15 must assign an exact stage for the new cross-session index/search work before its CLAIM; completed F3/F4 stages are inherited authority only. Each CTX stage still needs its own exact-head CLAIM, issue authority, audit and CI. This addendum does not open automatic promotion, model-visible retrieval, or migration of existing Memory rows.

Acceptance must prove: same template under two users yields distinct Agent instances; two Agents of one user cannot read each other's private Memory by default; owner-wide history remains searchable without duplicating transcript authority; explicit user rule wins over inferred habit; revoked sources and CAS grants disappear from future Working Sets; legacy owner-only Memory is not silently reclassified.
