# T8 Metadata V2 Convergence — Completion

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Frozen T7 baseline: `adea67c374bf814914bab47a3f9b36caf50c1d1c`  
Final T8 code/test candidate: `7a84edf2d228596016cd081a0679f8d5031382d3`  
Coordination checkpoint: Issue #7

## Status

**T8-A → T8-H COMPLETE / GREEN / FINAL-FREEZE READY**

T8 converged the repository on one canonical Tools V1 Metadata V2 contract while preserving T7 logical/physical identity semantics, existing routing priority, R7 continuation/reconciliation behavior, V1 compatibility, MCP behavior, and T6 Web runtime/security behavior.

No production semantic gap was reproduced by the T8-G end-to-end gate. T8-H therefore makes no production-code change.

## Canonical Metadata V2 authority

The final V2 authority is:

```text
manifest_version = "2.0"
exports[]
bind
input_schema
output_schema
```

Validation authority:

```text
tools/v1/_shared/metadata.py
validate_tool_manifest_v2()
```

The transitional T7 dialect:

```text
metadata_version = 2
capabilities[]
binding
parameters-as-export-schema
```

is not a second final V2 authority.

## Frozen Web logical/physical mapping

```text
web.search
  -> web_tool.run(action="search", ...)

web.read
  -> web_tool.run(action="scrape", ...)

web.read_many
  -> web_tool.run(action="scrape_many", ...)
```

Identity remains:

```text
CapabilityInvocation.capability_id = logical capability ID
ToolResult.tool                     = physical tool identity
ToolResult.action                   = canonical physical action
```

Physical package version remains implementation provenance and is distinct from logical export version.

## Placement and registration invariants

Repository default:

```text
SERVER:
  web.search
  web.read
  web.read_many

CLIENT:
  no Web V2 exports advertised by default
```

Client V2 advertisement is explicit exact-ID opt-in through:

```text
enabled_v2_capabilities = []
```

The legacy V1 wildcard does not implicitly opt V2 exports into CLIENT placement.

Cross-location definition convergence is frozen as:

```text
same capability_id + same logical contract
    -> canonical logical definition reused
    -> multiple implementations may coexist

same capability_id + different logical contract
    -> reject whole registration batch before mutation
```

One active canonical logical definition/version per `capability_id` remains the T8 catalog boundary. Multi-version coexistence is not implemented.

## T8 phase results

### T8-A — Canonical contract

- canonical shared validator retained as the single validation authority;
- generic JSON-safe immutable bind contract preserved;
- no SE/CL dependency introduced into the shared tools layer.

### T8-B/C — SERVER + Web canonical migration

- SE consumes canonical `manifest_version="2.0"` exports;
- Web migrated to canonical `exports/bind/input_schema/output_schema`;
- physical root remains hidden when `expose_root=false`;
- transitional Web V2 dialect is no longer final authority;
- T6 Web runtime/security/fetch implementation was not changed.

### T8-D/E — CLIENT discovery and explicit placement

- direct package `__init__.py` discovery added;
- V1 top-level discovery remains supported;
- canonical V2 exports normalize to logical client registry entries;
- generic immutable bind wrappers preserve bound values;
- default V2 client placement remains empty;
- logical export version is advertised, physical package version stays provenance;
- capability.register ACK/READY lifecycle remains unchanged.

### T8-F — Definition convergence

- last-writer-wins logical definition replacement removed;
- equivalent definitions reuse the canonical definition;
- divergent same-ID contracts reject before catalog mutation;
- duplicate logical IDs / implementation IDs fail preflight;
- exact implementation registration remains idempotent;
- REMOVED implementation IDs remain non-revivable;
- collision handling is fail-closed and deterministic across package discovery orders.

### T8-G — End-to-end convergence

Added test-only gate:

```text
se/tests/e2e/test_t8_g_metadata_v2_convergence.py
```

Proved:

1. Web logical SERVER execution returns physical `web_tool` ToolResult identity;
2. repository-default CLIENT advertises zero Web V2 exports;
3. explicitly enabled synthetic CLIENT V2 traverses package discovery → registration → catalog → remote invocation → bound callable → result;
4. SERVER-first→CLIENT and CLIENT-first→SERVER converge without canonical-definition rewrite;
5. divergent realtime batch rejects with zero earlier-entry mutation;
6. reconnect reuses logical definition while old connection implementation becomes REMOVED and fresh one ENABLED through the existing lifecycle;
7. version/fingerprint/reconciliation fences remain unchanged;
8. existing capability list/get introspection exposes canonical logical definition plus implementations.

No production patch was required by G.

## Exact changed-file scope from T7 baseline to T8 code candidate

From `adea67c3` to `7a84edf2`:

```text
cl/config/setting.json
cl/src/core/capability_runtime.py
cl/src/loader/local_tools.py
cl/tests/test_t8_metadata_v2_client_loader.py

se/src/runtimes/capability/local_tool_loader.py
se/src/runtimes/capability/registration.py
se/tests/architecture/test_phase6_5_client_registration.py
se/tests/architecture/test_t7_web_metadata_v2.py
se/tests/architecture/test_t8_metadata_v2_convergence.py
se/tests/e2e/test_t8_g_metadata_v2_convergence.py

tools/v1/T8_METADATA_V2_CONVERGENCE_IMPLEMENTATION_PLAN.md
tools/v1/test/test_shared_metadata.py
tools/v1/test/test_web_tool.py
tools/v1/web_tool/config.py
```

Scope audit result:

- no `CapabilityRoutingPolicy` priority change;
- no `CapabilityCatalog` key/state-machine redesign;
- no R7 continuation/resume/reconciliation change;
- no T6 Web runtime/security/fetch diff;
- no provider/MCP/requirements scope drift;
- no migration of unrelated V1 physical tools.

## Exact-SHA CI evidence

Code/test candidate:

```text
7a84edf2d228596016cd081a0679f8d5031382d3
```

Architecture Baseline run `35824871578` — **SUCCESS**

- Linux full suite job `107064316833` — SUCCESS
- `1014 passed, 1 skipped, 16 warnings, 159 subtests passed`
- Windows client contracts job `107064316653` — SUCCESS
- `94 passed, 2 warnings`

Phase 5 Exit Gates run `35824871561` — **SUCCESS**

- job `107064316690`
- `40 passed`

T8-G itself is contained in that exact-SHA full-suite evidence.

## P0/P1 closure

No open in-scope T8 P0/P1 remains at the final code/test candidate.

Closed during T8 include:

- dual Metadata V2 authority;
- missing package discovery;
- accidental wildcard V2 CLIENT placement;
- last-writer-wins client logical-definition overwrite;
- logical/physical version confusion;
- generic bind immutability;
- cross-location order dependence;
- V1↔V2 and V2↔V2 collision nondeterminism;
- transitive collision resurrection;
- registration batch preflight/atomicity coverage;
- default Web CLIENT placement proof;
- reconnect/introspection E2E convergence coverage.

Provider-specific convergence questions tracked outside Issue #7 remain deferred to the separately scoped future roadmap and are not T8 blockers.

## Explicit non-goals retained

T8 does not implement:

- aliases/deprecation routing;
- multi-version catalog identity;
- semver negotiation/selection;
- routing-priority changes;
- catalog key redesign;
- R7 continuation/reconciliation changes;
- migration of File/Glob/Terminal/Window/Desktop to canonical logical exports;
- provider/MCP redesign;
- new capability introspection endpoints.

## T8 → T9 handoff

T9 requires a fresh boundary audit before code.

Candidate topics only:

- aliases/deprecation;
- legacy physical-name compatibility;
- multi-version catalog identity;
- semantic-version negotiation;
- version-aware agent manifests;
- migration of remaining V1 tools to logical Metadata V2 exports;
- generalized package placement;
- common logical-contract hashing/introspection.

No T9 candidate is implicitly authorized by this completion.

## Final freeze statement

```text
T7  CLOSED / GREEN / AUDIT-APPROVED / FROZEN @ adea67c3
T8  COMPLETE / GREEN / FINAL-FREEZE READY @ 7a84edf2
T9  NOT AUDITED / NOT OPEN
```

Issue #7 is the durable coordination record for the final T8 freeze.
