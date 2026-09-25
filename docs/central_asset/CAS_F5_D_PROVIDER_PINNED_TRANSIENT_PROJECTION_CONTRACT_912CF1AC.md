# CAS-F5-D — Provider-Pinned Transient Projection Contract

## Authority and baseline

This document is the contract / authority freeze for CAS-F5-D.

```text
primary workspace = Issue #74
canonical stacked-development policy = Issue #85 v1
stage = CAS-F5-D
mode = CONTRACT / AUTHORITY FREEZE
canonical main = fd3c3a146f0775ce357b846341f99475af55c665
post-main Architecture #1337 = GREEN/GREEN
production/runtime/schema/migration delta = 0
```

CAS-F5-C is already canonical and healthy. F5-D does **not** inherit any
authority that was intentionally closed by F5-C.

This freeze exists because current runtime still blocks canonical asset content
with `ASSET_HYDRATION_REQUIRED`, while exact provider selection occurs later
inside provider fallback execution. Removing that guard before freezing the
provider-pinned projection boundary would let CAS guess provider identity too
early or mutate canonical messages with provider-native identity.

## 1. Provider selection handoff

F5-D MUST NOT select or reconstruct provider identity from:
- request model name;
- client metadata;
- attachment metadata;
- persisted message metadata;
- provider_file_id/provider URI supplied by a client;
- WorkflowRuntime, DirectChatRuntime, or AgentRuntime guesses.

The exact provider authority for one inference attempt exists only inside the
provider handler's per-provider attempt after:
1. RoutingPolicy has produced the fallback chain;
2. circuit-breaker/health filtering has accepted the candidate provider;
3. request-scoped CHAT / TOOL_CALLING capability eligibility has passed;
4. and before ProviderExecutor mutates the remote provider.

Normative hook location:

```text
ChatExecutionHandler.execute_with_fallback()
  -> routing / healthy chain
  -> for exact provider attempt
  -> capability eligibility PASS
  -> F5-D provider-pinned asset hydration/projection hook
  -> ProviderExecutor.execute(provider=exact_provider, ...)
```

The streaming form must obey the same authority ordering before
`ProviderExecutor.execute_stream(...)`.

The future attempt hook MUST receive the exact server-selected provider object
or equivalent exact provider identity, plus the server-owned
`ProviderConfig.file_binding_namespace`. CAS does not own RoutingPolicy,
health/circuit-breaker policy, capability selection, retry budget, or model
selection.

## 2. Shared DIRECT / AGENT / legacy convergence

DIRECT and AGENT must not implement separate hydration algorithms.

Current convergence is authoritative evidence:

```text
DIRECT -> DirectChatRuntime -> InferencePort.complete()
AGENT  -> AgentRuntime      -> InferencePort.complete()
                         -> ProviderInferenceAdapter.complete()
                         -> ChatExecutionHandler.execute_with_fallback()
```

F5-D production wiring, if later released, must attach at the shared
per-provider attempt boundary rather than duplicating CAS logic in
DirectChatRuntime or AgentRuntime.

The legacy `provider.chat.execute` event path also reaches
`ChatExecutionHandler.execute_with_fallback()` through ProviderRuntime. It must
be classified under the same provider-pinned rule before the WorkflowRuntime
asset guard can be replaced.

## 3. Authenticated owner and canonical asset authority

`owner_user_id` MUST come from trusted authenticated `Identity.user_id` (or
an equivalent trusted server execution context). It MUST NOT be reconstructed
from request attachment metadata, client metadata, model output, provider
metadata, or provider-native file identity.

`asset_id` comes only from the canonical asset attachment.

The provider namespace remains server-owned configuration authority.

A persisted canonical `GatewayAttachment(asset_id=...)` remains:

```text
source = "asset"
uri    = "asset://<asset_id>"
```

and must not persist:
- `provider_file_id`;
- provider file URI;
- base64 bytes;
- raw bytes;
- local provider path.

Provider identity is secondary execution state, never canonical CAS identity.

## 4. Transient request-copy projection

F5-D projection operates on a provider-facing **request copy only**.

It MUST NOT mutate:
- the persisted GatewayAttachment;
- canonical message/history objects;
- stored Context / transcript content;
- the source `asset://<asset_id>` identity.

The request-copy projection may consume provider identity only from:
- a valid ACTIVE exact FileProviderBinding reused by F5-C; or
- an F5-C hydration result that is `REUSED` or `HYDRATED` and carries the
  exact stable provider identity for the selected provider/namespace.

No projection layer may trigger a second provider upload.

## 5. Hydration-result mapping

Before inference mutation, F5-D freezes this mapping:

```text
REUSED                         -> eligible for transient provider projection
HYDRATED                       -> eligible for transient provider projection
HYDRATION_IN_PROGRESS          -> FAIL CLOSED before inference
HYDRATION_OUTCOME_UNKNOWN      -> FAIL CLOSED before inference
HYDRATION_FINGERPRINT_DRIFT    -> FAIL CLOSED before inference
HYDRATION_PERSISTENCE_CONFLICT -> FAIL CLOSED before inference
HYDRATION_FAILED_SAFE          -> bounded hydration failure; FAIL CLOSED
HYDRATION_RACE_LOST            -> re-read/re-evaluate only under F5-C contract;
                                  never infer provider fallback authority
```

A SAFE local hydration failure is not permission to try another provider.
UNKNOWN is never automatic re-upload or automatic recovery authority.

## 6. Fallback after hydration side effects

This contract deliberately chooses the conservative fail-closed policy.

**Once an asset-bearing exact-provider attempt reaches REUSED or HYDRATED and a
transient provider-native projection is built, that provider attempt is
fallback-terminal for the logical inference.**

After that point:
- a `ProviderError`, transport error, HTTP status error, timeout, cancellation,
  or provider inference failure MUST NOT silently continue to the next provider;
- the same logical inference MUST NOT hydrate/project the same canonical asset
  into a second provider;
- F5-D MUST NOT create multi-provider remote asset state as an implicit
  consequence of ChatExecutionHandler's generic fallback loop.

Provider fallback remains ProviderRuntime/provider-handler authority **before**
the F5-D hook is engaged, for example when a provider is rejected by health or
capability eligibility without asset hydration/projection.

Changing this fallback-terminal rule requires a separately audited contract
revision. F5-D production code must not invent a weaker rule.

For streaming, existing "fallback before first visible chunk" behavior is not
sufficient once provider-specific asset projection has been engaged. Asset
projection makes that attempt fallback-terminal even if no visible stream chunk
has been emitted yet.

## 7. Gemini provider-native projection contract

The current Gemini attachment conversion primarily produces `inlineData`,
reloads local filesystem content as base64, or emits URL/path fallback text.
That behavior is not the hydrated-provider projection.

For a stable Gemini File API identity, F5-D must later provide a distinct native
request-copy projection equivalent to:

```json
{
  "fileData": {
    "mimeType": "<canonical/server-proven mime>",
    "fileUri": "<stable Gemini provider URI>"
  }
}
```

Normative rules:
- `fileUri` comes only from the exact successful/reused F5-C provider identity;
- mime type comes from trusted canonical/server state;
- no local-path reload;
- no base64 reload of an already hydrated provider file;
- no provider URI copied back into canonical attachment/message persistence;
- no client-supplied `fileData` becomes CAS authorization.

Provider-specific shape belongs at the provider request conversion boundary,
not in canonical domain persistence.

## 8. Attempt-scoped request-copy contract

A future production hook may conceptually consume an attempt context containing:

```text
exact provider object/name
server-owned provider namespace
trusted authenticated owner_user_id
canonical request body/messages
deadline/cancellation context already owned by provider execution
```

and return a provider-facing request copy plus an internal indication that
asset projection was engaged.

The original request body/messages remain canonical/provider-neutral.
The projection indication is execution-local and must not be persisted as
canonical message authority.

The exact production type/name is intentionally not frozen by this zero-delta
contract; the authority and ordering above are normative.

## 9. Legacy and execution-surface matrix

| Surface | Current route | F5-D rule |
| --- | --- | --- |
| DIRECT | DirectChatRuntime -> ProviderInferenceAdapter -> ChatExecutionHandler | shared per-provider attempt hook only |
| AGENT | AgentRuntime -> ProviderInferenceAdapter -> ChatExecutionHandler | shared per-provider attempt hook only |
| legacy provider event | provider.chat.execute -> ProviderRuntime -> ChatExecutionHandler | same provider-pinned rule before guard replacement |
| non-stream | execute_with_fallback | projection-engaged attempt is fallback-terminal |
| stream | stream_with_fallback | same rule; no post-projection cross-provider fallback |

No surface may duplicate CanonicalAssetHydrationService semantics.

## 10. Still-closed authority

This contract does not release:
- WorkflowRuntime `ASSET_HYDRATION_REQUIRED` guard replacement;
- production hydration/projection wiring;
- provider selection/routing/fallback policy ownership;
- automatic UNKNOWN/stale PROCESSING recovery;
- provider remote delete/reclamation;
- READY FileAsset deletion/release;
- FileBlob/CAS physical GC or reconciliation;
- CTX search/read/Memory/Personalization authority;
- R11 retention/destructive-GC authority;
- R12 lease/recovery authority;
- CAS-F6.

The CTX-F5-3A request for a future CAS-owned ASSET source re-proof result is a
separate contract dependency. Provider binding/provider identity MUST NOT become
Memory promotion authority, and CTX does not acquire ObjectStorage, hydration,
lifecycle, or GC authority through that handoff.

## 11. Production release gate

F5-D production implementation remains CLOSED until all of the following are
true on an exact candidate:
- this contract/evidence candidate has fresh Linux + Windows Architecture GREEN;
- independent exact-head contract audit = FINAL GREEN;
- exact canonical main remains accepted GREEN/GREEN;
- no blocking CAS P0/P1 or ownership/dependency conflict exists;
- the production CLAIM separately records the exact hook scope and closed
  authorities.

Contract/evidence FINAL GREEN does not itself authorize production merge or
runtime wiring.
