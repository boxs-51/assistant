# CAS-F5 — Integration / Exit Gate

Primary authority: Issue #74 / CAS-F5
Policy: Issue #85 v2.5
Candidate baseline: main@cb24dfe92696c7ca25108f1b024a68803c5a9f41
Candidate class: COMPLETION / CONTRACT-EVIDENCE
Production/runtime/schema/migration delta: 0
CAS-F6 authority: CLOSED pending successful independent exact-head exit audit

## Purpose

This document consolidates completion evidence for the initial CAS-F5 provider-hydration milestone.

It adds no runtime behavior. It does not reinterpret intentionally closed lifecycle, recovery, destructive, cross-track, or provider-routing surfaces as completed. A successful exit means only that the released F5 provider-hydration milestone is complete on its frozen authority boundary.

## Canonical entry state

PR #110 = MERGED
PR #110 frozen HEAD = 5fef0dc36b0e2cc50e2fee15a648a0c2c6ab30c5
merge commit / canonical baseline = cb24dfe92696c7ca25108f1b024a68803c5a9f41
post-merge Architecture #1399 = GREEN/GREEN
linux-full-suite = SUCCESS
windows-client-contracts = SUCCESS
rollback required = NO
CAS-F5-D-P2 = LANDED / CANONICAL / HEALTHY

At this baseline there is no known blocking landed CAS-F5 P0/P1.

## Landed F5 evidence chain

### F5-0 contract and P1 closure

Canonical contract:
docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md

The normative P1-closure addendum freezes:
- explicit server-owned ProviderConfig.file_binding_namespace;
- live-slot key (file_id, provider_name, provider_namespace);
- live_claim_token=LIVE database serialization for PROCESSING / ACTIVE / UNKNOWN;
- durable source_blob_id + source_sha256 fingerprint authority;
- nullable provider identity before ACTIVE;
- explicit UNKNOWN state;
- typed provider upload outcome authority;
- fail-closed UNKNOWN and stale-PROCESSING semantics;
- no automatic UNKNOWN recovery;
- no implicit provider deletion or CAS/blob deletion.

### Durable binding foundation

Canonical production evidence includes:
- FileProviderBindingRecord with source_blob_id, source_sha256, live_claim_token, UNKNOWN, live-slot uniqueness, ACTIVE provider-identity constraint and live-claim constraint;
- Alembic revision 20a_cas_f5_binding_foundation;
- architecture coverage in test_cas_f5_1_binding_foundation.py.

The migration itself explicitly keeps provider upload orchestration, remote deletion, UNKNOWN recovery, READY asset deletion and physical CAS GC outside the foundation scope.

### Provider upload outcome and hydration orchestration

Canonical F5-C production contains CanonicalAssetHydrationService and typed hydration result states:
REUSED
HYDRATED
HYDRATION_IN_PROGRESS
HYDRATION_OUTCOME_UNKNOWN
HYDRATION_FAILED_SAFE
HYDRATION_RACE_LOST
HYDRATION_FINGERPRINT_DRIFT
HYDRATION_PERSISTENCE_CONFLICT

Hydration consumes trusted owner_user_id, canonical asset_id, exact server-selected provider_name, server-configured provider namespace, and canonical ObjectStorage bytes/fingerprint state.

The service does not gain provider routing/model/fallback ownership.

### Provider-pinned transient projection

Canonical contract:
docs/central_asset/CAS_F5_D_PROVIDER_PINNED_TRANSIENT_PROJECTION_CONTRACT_912CF1AC.md

Landed evidence establishes:
- exact provider selection remains inside ChatExecutionHandler;
- F5-D engages only after provider health/capability eligibility;
- projection is a transient request copy;
- canonical asset identity/history is not replaced by provider identity;
- hook entry is fallback-terminal for the logical asset-bearing inference;
- partial multi-asset failure does not hydrate into provider #2;
- missing required provider-native identity fails closed.

### Activation and surface convergence

Canonical activation contract:
docs/central_asset/CAS_F5_D_ACTIVATION_SURFACE_CONVERGENCE_CONTRACT_6228734A.md

Landed PR #110 implements the first activation boundary:

DIRECT -> DirectChatRuntime -> ProviderInferenceAdapter -> ChatExecutionHandler
AGENT  -> AgentRuntime      -> ProviderInferenceAdapter -> ChatExecutionHandler
                                               -> CanonicalAssetProviderProjectionHook
                                               -> CanonicalAssetHydrationService

ProviderRuntime owns the activation chain from existing server dependencies:
- exact existing ProviderRuntime.provider_registry;
- RuntimeContext.uow_factory;
- configured ObjectStorage through existing StorageEngine;
- shared application context.http_client;
- exact production ChatExecutionHandler.

Server-owned readiness is positive only while the hook is configured, injected into the exact chat handler and runtime activation is ready.

WorkflowRuntime releases canonical assets only to authenticated DIRECT/AGENT execution when readiness is positive.

Architecture #1396 proved the replacement composition evidence on exact PR #110 HEAD, and post-merge Architecture #1399 proved canonical health.

## Canonical identity and authority invariants at exit

1. Canonical asset identity remains asset_id / asset://<asset_id>.
2. Provider file id/URI/namespace remain secondary execution/provider-binding state.
3. Trusted owner authority comes from authenticated server execution identity, never attachment/request metadata.
4. Provider namespace is server configuration authority.
5. ACTIVE reuse is bound to the canonical blob fingerprint.
6. UNKNOWN/stale PROCESSING is fail-closed retry authority.
7. One logical provider call retains one ProviderCallBudget across projection/hydration/execution.
8. F5-D projection is transient and does not mutate canonical history.
9. Asset-hook entry is fallback-terminal for the exact provider attempt.
10. DIRECT + AGENT are the only released asset-bearing execution surfaces in this initial milestone.

## Intentionally CLOSED after F5 exit

The following are not missing F5 work and are not completed by this exit:
- asset-bearing legacy provider.chat.execute;
- asset-bearing session regeneration;
- automatic UNKNOWN reconciliation/recovery;
- automatic stale-PROCESSING recovery;
- provider remote file delete/reclamation/cleanup;
- binding DELETING/DELETED production lifecycle;
- READY FileAsset deletion/release;
- FileBlob / physical CAS GC or reconciliation;
- orphan provider-file cleanup;
- CTX Memory promotion/source-proof authority;
- R11 retention/deletion authority expansion;
- R12 Agent lease/recovery/checkpoint authority;
- provider routing/fallback/deadline/model-selection ownership;
- CAS-F6.

These items require separate future authority releases. This completion evidence intentionally does not encode permanent live-source absence assertions for them; a later audited stage may change those surfaces without contradicting this historical F5 exit record.

## Cross-track disposition

At this exit baseline:
- CTX work does not own CAS provider hydration/binding lifecycle;
- R11 does not own CAS provider hydration or destructive CAS lifecycle;
- R12 does not own CAS provider hydration, ObjectStorage reconciliation or CAS GC;
- current late blockers in independent CTX/R12 candidates do not invalidate landed CAS-F5-D-P2;
- no current cross-track dependency requires CAS-F5 to absorb external lifecycle authority.

Any later material cross-track authority change must be reclassified under Issue #85 v2.5 before a future CAS stage.

## Exit evidence requirements

The exit candidate is valid only if:
- changed files are completion document + architecture evidence only;
- production/runtime/schema/migration delta remains zero;
- fresh Linux + Windows Architecture is GREEN;
- independent Issue #74 audit finds no blocking CAS-F5 P0/P1;
- unresolved blocking review threads = 0;
- canonical-main drift is classified under Policy #85 v2.5;
- no CLOSED surface is silently reinterpreted as released.

## Exit disposition

Before independent exact-head FINAL GREEN:
CAS-F5 initial provider-hydration milestone = EXIT CANDIDATE
CAS-F6 = CLOSED
merge authority = NONE

After this exact zero-production exit candidate independently reaches FINAL GREEN and is integrated under applicable Policy #85 v2.5 authority:
CAS-F5 initial provider-hydration milestone = COMPLETE
closed lifecycle/recovery/destructive surfaces = STILL CLOSED
CAS-F6 roadmap transition = ELIGIBLE FOR SEPARATE RELEASE

The exit does not itself CLAIM or implement CAS-F6.
