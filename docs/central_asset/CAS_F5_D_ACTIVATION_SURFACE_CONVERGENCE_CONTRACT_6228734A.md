# CAS-F5-D — Activation / Surface-Convergence Contract Freeze

**Primary authority:** Issue #74 / CAS-F5  
**Policy:** Issue #85 v2  
**Canonical baseline:** `main@6228734ae7a380719bb14fa520e3307c5330aa31`  
**Post-wave evidence:** Architecture #1383 GREEN/GREEN  
**Parent production slice:** PR #104 / CAS-F5-D-P1 LANDED / HEALTHY  
**Candidate type:** contract / authority freeze only  
**Production/runtime/schema/migration delta:** 0  
**Production activation authority:** CLOSED until a separately released implementation CLAIM reaches its own gate.

## 1. Purpose

CAS-F5-D-P1 landed the dormant provider-attempt hydration/projection mechanism, but the
gateway still has execution surfaces with different trusted-owner guarantees. This
contract freezes the exact authority, dependency, readiness, surface and failure rules
that a later production activation slice must obey before the current global
`ASSET_HYDRATION_REQUIRED` guard may be narrowed.

This document does **not** activate canonical asset inference. It does not wire the
projection hook into production bootstrap and it does not remove or weaken any current
asset guard.

## 2. Current source facts frozen by this gate

### 2.1 DIRECT / AGENT are the only first-slice release candidates

The landed DIRECT and AGENT paths already converge on the shared provider handler:

```text
DIRECT -> DirectChatRuntime -> ProviderInferenceAdapter -> ChatExecutionHandler
AGENT  -> AgentRuntime      -> ProviderInferenceAdapter -> ChatExecutionHandler
```

Their `owner_user_id` comes from trusted execution identity and is passed separately
from provider body/metadata. The provider-attempt hook remains:
- exact-provider only;
- entered only after routing/health/capability eligibility;
- bounded by the single handler-owned ProviderCallBudget;
- fallback-terminal after hook entry;
- transient request-copy only;
- non-mutating to canonical request/history.

The first later activation implementation may release **DIRECT + AGENT only**.

### 2.2 ProviderRuntime bootstrap is currently dormant

Current `ProviderRuntime.initialize()`:
- creates the canonical runtime-owned `ProviderRegistry`;
- discovers providers into that exact registry;
- stores the shared `context.http_client`;
- creates one `ChatExecutionHandler` from existing provider/routing/executor authority;
- does **not** construct `CanonicalAssetHydrationService`;
- does **not** construct `CanonicalAssetProviderProjectionHook`;
- does **not** inject `asset_projection_hook` into the production chat handler.

That dormancy remains normative until the later production CLAIM is explicitly released.

## 3. Exact dependency ownership for future production wiring

A later activation implementation MUST construct the F5-D dependencies from existing
server-owned application/runtime dependencies only:

1. **Provider authority:** use the exact `ProviderRuntime.provider_registry` created by
   `ProviderRuntime.initialize()`. CAS MUST NOT create a second ProviderRegistry,
   rediscover providers, select a provider, or own routing/model/fallback decisions.
2. **UoW authority:** use `RuntimeContext.uow_factory` / the application CAS UoW factory.
3. **Object storage authority:** use the configured asset driver
   `context.config.assets.storage_driver` through the existing `StorageEngine`
   (`context.storage.get_object_storage_driver(...)`). The configured driver must be
   proven available before readiness may become true.
4. **HTTP authority:** use the shared application `context.http_client`; the hydration
   service MUST NOT create or own a second application HTTP client.
5. **Provider namespace authority:** use the exact selected provider's server-owned
   `ProviderConfig.file_binding_namespace`, re-proved by the landed F5-D hook.
6. **Hydration lifetime:** one runtime-owned `CanonicalAssetHydrationService` instance
   may be constructed for ProviderRuntime lifetime from the dependencies above.
7. **Projection lifetime:** construct `CanonicalAssetProviderProjectionHook` from that
   hydration service and inject it into the exact production
   `ProviderRuntime.chat_handler` only.
8. **Deadline authority:** any hydration-service timeout is subordinate to the existing
   handler-owned ProviderCallBudget. CAS MUST NOT create a second logical-call budget.

The hydration service is an application helper, not a new routing layer.

## 4. Required activation ordering

A later production implementation MUST preserve this exact order:

```text
1. ProviderRuntime exact ProviderRegistry initialized
2. configured CAS ObjectStorage driver proven available
3. CanonicalAssetHydrationService constructed from:
     exact ProviderRegistry
     application uow_factory
     configured ObjectStorage driver
     shared application http_client
4. CanonicalAssetProviderProjectionHook constructed
5. hook injected into the exact production ChatExecutionHandler
6. server-owned runtime readiness proves the hook is installed/usable
7. only then may a separately released implementation narrow WorkflowRuntime's asset guard
```

A failure at any step MUST leave canonical asset execution fail-closed. There is no
fallback to raw provider inference.

## 5. Positive readiness / authority rule for guard replacement

The current global WorkflowRuntime asset guard MUST NOT be deleted.

A later implementation may replace it only with a positive rule equivalent to:

```text
canonical asset request
AND trusted authenticated owner is available from the released execution surface
AND production F5-D hook is installed and server-readiness == ready
AND execution surface is explicitly released
    -> allow dispatch toward the shared provider-attempt hydration/projection boundary
ELSE
    -> fail closed before raw provider inference
```

Readiness is server-owned runtime state. Request metadata, model metadata, attachment
fields, session metadata, or client-supplied flags MUST NOT assert readiness or owner
authority.

Provider selection still occurs only in `ChatExecutionHandler` after existing
routing/health/capability eligibility. WorkflowRuntime and CAS MUST NOT preselect a
provider.

## 6. First implementation surface matrix

| Surface | First activation slice | Trusted owner rule | Asset behavior |
| --- | --- | --- | --- |
| DIRECT non-stream | RELEASE CANDIDATE | authenticated DIRECT Identity -> `owner_user_id` | may pass only when hook readiness is true |
| DIRECT stream | RELEASE CANDIDATE | same | same |
| AGENT non-stream | RELEASE CANDIDATE | `AgentExecutionContext.identity.user_id` | may pass only when hook readiness is true |
| AGENT stream | RELEASE CANDIDATE | same | same |
| legacy `provider.chat.execute` event | **CLOSED** | current handler has no trusted `owner_user_id` handoff | canonical assets fail closed |
| session regeneration | **CLOSED** | route authenticates owner but current provider call omits `owner_user_id` | existing asset-history guard remains |

The first production activation CLAIM MUST NOT broaden this matrix without a new
independent contract revision.

## 7. Legacy provider-event rule

Current `ProviderRuntime._handle_execute_chat()` invokes
`execute_with_fallback(..., body)` / `stream_with_fallback(..., body)` without
`owner_user_id`.

Therefore the first activation implementation MUST keep asset-bearing generic
`provider.chat.execute` execution closed.

For any future legacy release:
- trusted Identity must come from server-owned event context;
- only `Identity.user_id` may be handed separately to ChatExecutionHandler;
- request/client metadata MUST NOT become owner authority;
- a generic provider event without trusted identity MUST fail closed before raw provider
  inference for canonical assets.

## 8. Session regeneration rule

`POST /v1/sessions/{session_id}/regenerate` currently proves session ownership and then
explicitly blocks canonical asset history before calling
`provider_runtime.chat_handler.execute_with_fallback(...)` without `owner_user_id`.

The first activation implementation MUST keep this asset-history guard CLOSED. Merely
installing the production F5-D hook MUST NOT unlock regeneration.

A later regeneration release requires its own frozen trusted
`identity.user_id -> owner_user_id` handoff, stream/non-stream behavior if applicable,
history/replay evidence and independent audit.

## 9. Failure and fallback invariants carried forward from F5-D-P1

For every released surface:
- provider selection remains ProviderRuntime/ChatExecutionHandler authority;
- exact provider + server namespace are re-proved before hydration;
- hook entry latches the asset-bearing logical inference fallback-terminal before the
  first hydration result;
- no provider #2 hydration/projection after hook entry;
- partial multi-asset UNKNOWN/failure remains terminal on the exact provider attempt;
- missing/blank Gemini provider URI fails closed;
- foreign-owner or missing-owner asset hydration fails closed;
- ProviderCallBudget deadline/cancellation remains one logical-call authority;
- deadline/cancellation cancels/drains the hook and cannot continue to another provider;
- canonical request/history is not mutated;
- provider-native identity is never persisted into canonical messages.

## 10. Required implementation evidence matrix

The later production activation candidate MUST prove at least:

- DIRECT non-stream canonical asset flow;
- DIRECT stream canonical asset flow;
- AGENT non-stream canonical asset flow;
- AGENT stream canonical asset flow;
- exact server-selected provider and `file_binding_namespace`;
- wrapped `data.attachment` and flat `type=file / data=<GatewayAttachment>` forms;
- trusted owner propagation and foreign-owner rejection;
- missing Gemini `provider_uri/fileUri` fail-closed behavior;
- partial multi-asset UNKNOWN/failure terminal behavior;
- shared ProviderCallBudget deadline/cancellation behavior;
- original canonical request/history non-mutation;
- hook unavailable/not-ready -> fail closed before raw provider inference;
- legacy provider event with no trusted identity -> fail closed for canonical assets;
- legacy asset event remains closed in the first slice even if the hook is installed;
- session regeneration asset history remains blocked;
- no provider #2 hydration after hook entry;
- no provider-native identity persisted into canonical messages;
- startup/readiness does not report F5-D ready until dependencies + exact hook injection
  are proven.

## 11. Zero-production candidate evidence

This contract candidate itself MUST remain exactly docs/tests evidence. It must prove the
current main still has the pre-activation fences:
- ProviderRuntime owns one ProviderRegistry and shared http_client;
- production ProviderRuntime bootstrap does not instantiate F5-C/F5-D services;
- WorkflowRuntime still publishes `ASSET_HYDRATION_REQUIRED` / `MESSAGE_ASSET` / 409
  before DIRECT, AGENT or legacy dispatch for canonical assets;
- legacy `provider.chat.execute` has no trusted owner handoff;
- session regeneration explicitly blocks canonical asset history;
- current DIRECT/AGENT owner propagation converges on ChatExecutionHandler;
- landed provider-attempt deadline/fallback/transient rules remain available.

## 12. Still CLOSED

This contract does not release:
- production bootstrap / F5-D hook wiring;
- WorkflowRuntime guard replacement or removal;
- public end-to-end canonical asset activation;
- asset-bearing legacy `provider.chat.execute`;
- asset-bearing session regeneration;
- automatic UNKNOWN / stale PROCESSING recovery;
- provider remote delete / reclamation;
- READY FileAsset deletion / release;
- FileBlob / physical CAS GC / reconciliation;
- CTX source-proof / Memory-promotion authority transfer;
- R11/R12 authority expansion;
- provider routing/fallback/deadline/model-selection ownership;
- CAS-F6.

## 13. Exit gate

This contract/evidence candidate may reach FINAL GREEN only when:
- changed files remain docs/tests only;
- production/runtime/schema/migration delta remains zero;
- fresh exact-head Architecture Linux + Windows is GREEN/GREEN;
- independent Issue #74 audit finds no blocking P0/P1;
- canonical-main drift is classified under Policy #85 v2;
- unresolved review threads are zero.

Contract FINAL GREEN may allow an auditor to release the bounded production activation
CLAIM. It does **not** itself authorize production activation or guard replacement.
