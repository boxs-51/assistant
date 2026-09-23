# T7 Metadata V2 Completion

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
T6 frozen baseline: `1138a83da1233332e4e690043951da22d966e360`  
T7 code/test candidate HEAD: `49d801f1a7caa5d3ad6bcdacbeee3faf15f253e4`  
Status: **T7-A→T7-G IMPLEMENTED / GREEN / FROZEN**

---

# 1. Objective completed

T7 replaced the model-facing umbrella Web capability:

```text
web_tool + caller-controlled action
```

with three logical capabilities:

```text
web.search
web.read
web.read_many
```

while preserving the frozen T6 physical implementation.

Frozen binding now implemented:

```text
web.search
    -> web_tool.run(action="search", ...)

web.read
    -> web_tool.run(action="scrape", ...)

web.read_many
    -> web_tool.run(action="scrape_many", ...)
```

---

# 2. Identity contract proven

Logical and physical identities remain intentionally separate.

```text
CapabilityInvocation.capability_id = logical capability ID

ToolResult.tool   = "web_tool"
ToolResult.action = canonical physical T6 action
```

T7 does not rewrite the T6 ToolResult contract.

The runtime-level regression executes a bound Metadata V2 capability through
`CapabilityRuntime.execute_capability()` and verifies that the durable invocation
stores the logical capability ID while the physical handler receives the bound action.

---

# 3. Metadata V2 contract implemented

`tools/v1/web_tool/config.py` now declares:

```text
metadata_version = 2
physical name    = web_tool
physical version = 2.0.0
```

and exactly three logical entries:

```text
web.search     version 1.0 -> search
web.read       version 1.0 -> scrape
web.read_many  version 1.0 -> scrape_many
```

Logical schemas:

- use `additionalProperties=false`;
- do not expose `action`;
- do not expose `captcha_api_key`;
- do not expose `output_format`;
- retain the frozen T6 argument bounds;
- retain `READ + EXTERNAL_SIDE_EFFECT`.

The top-level physical V1-compatible parameters remain present as package compatibility
metadata, but the Metadata V2 server loader does not register the physical `web_tool`
umbrella as an executable logical capability.

---

# 4. Metadata V2 loader implemented

`se/src/runtimes/capability/local_tool_loader.py` now supports:

```text
Metadata V1:
  one module -> one capability

Metadata V2:
  one physical module
      -> full-bundle preflight
      -> N logical capability definitions
      -> N fixed-action Python drivers
      -> N server implementations
```

Metadata V1 behavior remains available for existing local tools.

Metadata V2 preflight rejects before registration:

- non-integer/unsupported metadata versions;
- empty physical name/version;
- empty/non-list capabilities;
- malformed capability entries;
- duplicate logical capability IDs;
- logical ID equal to physical package name;
- malformed/duplicate binding actions;
- non-object logical schemas;
- logical schemas exposing `action`;
- invalid JSON Schema;
- missing required `effects` field;
- unsupported effects;
- invalid scope/permission lists;
- conflicts in CapabilityRegistry;
- conflicts in ToolRegistry;
- conflicts in CapabilityCatalog;
- conflicts in CapabilityDriverRegistry.

Expected metadata/preflight failures produce zero V2 registration side effects.

Unexpected failures after successful preflight are surfaced as bootstrap failures rather
than being silently accepted as a partially registered V2 bundle.

---

# 5. Bound handler semantics

Each logical capability receives a loader-created fixed-action wrapper.

The wrapper:

- injects the frozen physical action;
- rejects caller-supplied `action`;
- delegates only to the module `run()` entrypoint;
- preserves sync/Task-return behavior;
- does not call `WebTool` methods directly;
- does not rewrite physical ToolResult output.

The Agent JSON Schema boundary independently rejects caller-supplied `action` via
`JsonSchemaToolArgumentValidator`.

---

# 6. Registry/catalog state after T7

Expected and regression-tested state:

```text
CapabilityRegistry:
  web.search
  web.read
  web.read_many

ToolRegistry:
  web.search
  web.read
  web.read_many

CapabilityCatalog:
  server:web.search
  server:web.read
  server:web.read_many

CapabilityDriverRegistry:
  server:web.search
  server:web.read
  server:web.read_many
```

Forbidden executable/model-visible umbrella:

```text
web_tool
```

The physical Python package continues to export `web_tool.run()`,
`TOOL_METADATA["name"] == "web_tool"`, and the frozen T6 implementation.

---

# 7. Agent Web Researcher migration

`agents/v1/web-researcher/manifest.json` now declares:

```json
"tools": [
  "web.search",
  "web.read",
  "web.read_many"
]
```

The capability-projection regression proves:

- the Agent sees the three logical Web capability IDs;
- the Agent does not see `web_tool`;
- even if a physical `web_tool` driver exists globally, the manifest does not project it
  into model context.

No Agent runtime or policy implementation was changed.

---

# 8. Direct Chat policy preserved

T7 does not change `CapabilityAccessPolicy.DIRECT_READ_ONLY`.

Logical Web capabilities retain:

```text
READ
EXTERNAL_SIDE_EFFECT
```

Therefore T7 does not silently broaden Web visibility in Direct Chat.

Any future Web-in-Direct-Chat policy change requires a separate audit.

---

# 9. Client boundary preserved

T7 does not modify `cl/**`.

Metadata V2 in this phase is a server-owned local capability projection.

Generic Client/package Metadata V2 discovery remains future work.

---

# 10. Production scope

Production changes from T6 baseline:

```text
tools/v1/web_tool/config.py
se/src/runtimes/capability/local_tool_loader.py
agents/v1/web-researcher/manifest.json
```

No T6 Web runtime/security file changed.

Specifically untouched:

```text
tools/v1/web_tool/core.py
tools/v1/web_tool/searcher.py
tools/v1/web_tool/scraper.py
tools/v1/web_tool/network_policy.py
tools/v1/web_tool/proxy.py
tools/v1/web_tool/extractors.py
tools/v1/web_tool/stealth.py
tools/v1/web_tool/cap_solver_handler.py
```

Also untouched:

```text
tools/v1/_shared/**
File/Glob/Terminal/Window/Desktop production code
tools/v1/live/**
cl/**
CapabilityRuntime core execution semantics
CapabilityCatalog state-machine semantics
AgentRuntime execution loop
Direct Chat policy
requirements*
```

---

# 11. Test scope

Changed/added regression files:

```text
tools/v1/test/test_web_tool.py
se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
se/tests/architecture/test_t7_web_agent_projection.py
```

The regression matrix proves:

1. Metadata V1 tools still load.
2. Valid V2 bundles register logical capabilities.
3. Malformed V2 bundle leaves zero partial registrations.
4. Duplicate logical capability IDs fail before mutation with zero registration.
5. Missing required `effects` fails preflight with zero registration.
6. Pre-existing conflicts prevent earlier entries from being registered.
7. Non-integer metadata versions fail closed.
8. Logical IDs bind to fixed physical actions.
9. Bound action cannot be overridden at handler boundary.
10. Logical schema rejects caller-provided physical action.
11. Web physical package is not registered as executable umbrella.
12. Three Web logical definitions and server implementations are enabled.
13. Physical Web Metadata identity remains `web_tool` / `2.0.0`.
14. Logical capability versions remain `1.0`.
15. Agent Web Researcher exposes only logical Web IDs.
16. Durable CapabilityInvocation keeps the logical capability ID.
17. Existing client/tool and Phase 5 gates remain green.

---

# 12. Commit sequence

T7 implementation commits after the T6 baseline:

```text
867d51d3  docs(tools-v1): freeze T7 Metadata V2 implementation plan
6c6393dc  feat(tools-v1): define T7 Web Metadata V2 capabilities
c2100405  test(tools-v1): freeze T7 Web Metadata V2 contract
57e87704  feat(tools-v1): load Metadata V2 logical capabilities
77f29eca  test(tools-v1): expect logical Web capabilities from loader
3212798f  test(tools-v1): cover Metadata V2 loader invariants
d2a922d3  fix(tools-v1): reject non-integer metadata versions
fb7d8441  test(tools-v1): reject ambiguous metadata versions
dc06ffa9  feat(tools-v1): migrate web researcher to logical Web capabilities
9bc9265d  test(tools-v1): prove Web logical capability projection
7c548e09  test(tools-v1): prove logical invocation identity
b68f27fb  test(tools-v1): route logical runtime invocation through catalog
8ee68c13  test(tools-v1): enforce logical schema isolation
c45f2a2d  docs(tools-v1): close T7 Metadata V2
a9e290ce  fix(tools-v1): require Metadata V2 capability effects
49d801f1  test(tools-v1): close T7 Metadata V2 audit gaps
```

---

# 13. CI evidence on code/test HEAD

Code/test candidate:

```text
49d801f1a7caa5d3ad6bcdacbeee3faf15f253e4
```

## Architecture Baseline

Run:

```text
35811644338
```

Jobs:

```text
linux-full-suite          SUCCESS
windows-client-contracts SUCCESS
```

Linux pytest result:

```text
950 passed
1 skipped
14 warnings
138 subtests passed
65.59s
```

The two additional passing tests are the reopened T7-F audit regressions for
missing required `effects` and duplicate logical capability IDs.

## Phase 5 Exit Gates

Run:

```text
35811644348
```

Result:

```text
Phase 5.6-5.11 exit gates SUCCESS
```

## Intermediate loader evidence

The T7-META + T7-LOADER checkpoint `fb7d8441` also independently completed:

```text
Architecture Baseline SUCCESS
Phase 5 Exit Gates    SUCCESS

944 passed
1 skipped
14 warnings
138 subtests passed
```

This separately proves the loader migration was green before the Agent manifest migration.

---

# 14. Corrective audit closure

The first close marker `c45f2a2d` was CI-green but was reopened by Issue #6 cross-check
because two frozen-contract requirements were not yet satisfied:

1. Metadata V2 capability `effects` was documented as required but the loader accepted
   omission via `entry.get("effects", [])`.
2. T7-F required an explicit duplicate logical capability-ID zero-registration
   regression, but only duplicate binding-action coverage existed.

Corrective commits:

```text
a9e290ce  require explicit Metadata V2 capability effects
49d801f1  add missing-effects and duplicate-ID zero-registration regressions
```

The loader now fails preflight when `effects` is absent, and both reopened cases are
covered by committed regressions that assert no CapabilityRegistry, ToolRegistry,
CapabilityCatalog definition/implementation, or DriverRegistry mutation occurs.

The corrective code/test HEAD `49d801f1` passed Architecture Baseline and Phase 5 Exit
Gates before this completion document was amended.

---

# 15. Historical unrelated CI note

A docs-only pre-implementation commit `867d51d3` had one Architecture Baseline failure:

```text
1 failed, 936 passed, 1 skipped
```

The failing test was:

```text
se/tests/e2e/test_r7_j_real_network_exit_gate.py::
test_r7_j_real_tcp_two_resume_requests_have_one_authority_winner
```

with duplicate reconciliation registration.

That failure was unrelated to T7 and did not reproduce on the green T7 candidate HEAD.

---

# 16. T7 exit gate

All T7 exit conditions are satisfied:

```text
model-visible:
  web.search
  web.read
  web.read_many

not model-visible:
  web_tool
  caller-controlled action
```

and:

```text
web.search     -> physical search
web.read       -> physical scrape
web.read_many  -> physical scrape_many
```

and:

```text
CapabilityInvocation.capability_id = logical ID
ToolResult.tool                     = web_tool
ToolResult.action                   = canonical physical action
```

V1 local tool loading remains compatible.

---

# 17. Fresh T7 → T8 boundary audit

T7 ends at **server-owned logical Web capability projection**.

T8 is not implemented.

The following are explicitly outside T7 and require a new boundary audit before code:

- generic/shared Metadata V2 schema/model instead of loader-local dictionaries;
- Metadata V2 for Client/package discovery;
- logical capability aliases/deprecation policy;
- capability version negotiation;
- public capability introspection;
- policy-aware capability grouping;
- generic multi-implementation metadata declarations;
- migration of other V1 local tools to Metadata V2.

T8 must not reopen the frozen T6 physical Web implementation merely to generalize metadata.

---

# 18. Coordination checkpoint

Canonical coordination surface:

```text
GitHub Issue #6
[CHECKPOINT] T6 → T7 Metadata V2 plan freeze @ 1138a83d
```

Workstreams completed:

```text
T7-META    DONE
T7-LOADER  DONE; reopened P1 contract defect corrected @ a9e290ce
T7-AGENT   DONE
T7-GATE    DONE on corrected code/test candidate @ 49d801f1; final docs-HEAD CI follows this document commit
```

---

# 19. Final state

```text
T1–T5  CLOSED / FROZEN
T6     CLOSED / GREEN / FROZEN @ 1138a83d
T7-A   COMPLETE
T7-B   COMPLETE
T7-C   COMPLETE
T7-D   COMPLETE
T7-E   COMPLETE
T7-F   COMPLETE
T7-G   COMPLETE
T7     GREEN / FROZEN
T8     NOT IMPLEMENTED
```

The final documentation commit that adds this file must receive the same branch CI gates
before Issue #6 is marked complete.
