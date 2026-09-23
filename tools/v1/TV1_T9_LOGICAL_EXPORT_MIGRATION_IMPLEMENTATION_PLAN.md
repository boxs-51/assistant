# TV1-T9 — Remaining Tools Canonical Logical Export Migration

Repository: `boxs-51/assistant`  
Track: `TV1-T*` — Tools V1 / Metadata / consumer convergence  
Branch at boundary audit: `tools-v1-contract-freeze`  
Frozen TV1-T8 docs HEAD: `f1b0c310552e8aed79355eca6375d0db2a806a8c`  
TV1-T8 code/test candidate: `7a84edf2d228596016cd081a0679f8d5031382d3`  
TV1-T8 checkpoint: Issue #7 — CLOSED / COMPLETED  
Mode: **BOUNDARY AUDIT + IMPLEMENTATION PLAN FREEZE ONLY — NO TV1-T9 PRODUCTION CODE**

---

# 1. Purpose

TV1-T8 established one canonical Metadata V2 consumer contract and proved SERVER/CLIENT convergence with Web.

TV1-T9 completes the next narrow Tools V1 normalization step:

```text
remaining V1 physical dispatcher capabilities
    ->
canonical Metadata V2 logical exports
```

The five remaining physical V1 tools are:

```text
file_tool
find_by_glob
terminal_tool
window_tool
desktop_automation
```

TV1-T9 migrates their model/catalog/client-facing identities to action-specific logical capability IDs while preserving their physical Python `run(...)` entrypoints and ToolResult physical identity.

TV1-T9 does **not** implement every candidate listed in the TV1-T8 handoff. In particular, multi-version routing, aliases/deprecation, provider lowering, and live-harness modernization remain separate work.

---

# 2. Repository-wide namespace boundary

The repository-wide namespace registry on merged `main` reserves:

```text
AE-R*   Agent Execution
TV1-T*  Tools V1
CAS-F*  Central Asset Storage
PTC-*   Provider Tool Contract
```

Therefore this phase is **TV1-T9**, never bare `R9`.

Active adjacent tracks:

```text
AE-R9  = RETRY / branch resolution / Task aggregation
PTC-1→PTC-3 = provider-facing tool compatibility
```

TV1-T9 must not absorb either scope.

Preferred future work branch naming:

```text
work/tv1-t9-<baseline>
```

Historical branch `tools-v1-contract-freeze` may remain the planning/freeze authority until an implementation branch is created.

---

# 3. TV1-T8 authority retained

TV1-T9 inherits without modification:

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

Consumer behavior already proven in TV1-T8:

- SERVER consumes canonical exports and hides physical roots;
- CLIENT canonical V2 selection is exact-ID opt-in;
- V1 wildcard does not enable V2;
- generic bind mappings are immutable;
- physical package version and logical export version are distinct;
- same logical definition may have SERVER + CLIENT implementations;
- divergent same-ID definitions reject before mutation;
- routing priority is unchanged;
- reconnect/ACK/READY and R7 continuation/reconciliation are unchanged.

TV1-T9 must use those consumers rather than invent a new loader/registration dialect.

---

# 4. Boundary audit findings

## P0-TV1-T9-1 — five production tools still expose broad V1 physical dispatcher capabilities

Current metadata for:

```text
file_tool
find_by_glob
terminal_tool
window_tool
desktop_automation
```

still uses the V1 shape:

```text
name
description
base_risk
effects
danger_patterns
parameters
```

For action-dispatch tools, the caller still selects `action` through the broad physical root.

This is intentionally tolerated through TV1-T8, but it is the primary remaining Metadata V2 standardization gap.

Frozen TV1-T9 direction:

```text
physical root remains a Python implementation identity
logical export becomes the capability/model identity
```

## P0-TV1-T9-2 — migration of command-reviewer tools must be atomic

Current:

```json
"tools": ["terminal_tool", "file_tool", "find_by_glob"]
```

If those physical roots become canonical V2 with `expose_root=false` before the Agent manifest migrates, the Agent references missing capability IDs.

Therefore File + Glob + Terminal capability migration and the command-reviewer manifest migration are one atomic TV1-T9 stage.

No intermediate committed state may leave that Agent referencing removed physical capability IDs.

## P0-TV1-T9-3 — current CLIENT default would silently lose migrated tools

TV1-T8 correctly freezes:

```json
"enabled_v2_capabilities": []
```

Current V1 tools are loaded by legacy:

```json
"allowed_local_tools": ["*"]
```

After a tool acquires canonical `manifest_version="2.0"`, the CLIENT loader no longer selects it through the V1 wildcard. It selects only exact logical IDs from `enabled_v2_capabilities`.

Therefore a metadata-only conversion with the allowlist left empty would silently remove current client-side capabilities.

TV1-T9 must explicitly migrate the default CLIENT placement for the non-Web tools to their logical IDs.

This is not permission widening: it replaces the already-discoverable V1 physical action surface with action-specific logical capabilities.

Web remains excluded and SERVER-only by default.

## P0-TV1-T9-4 — logical SERVER/CLIENT contracts must be identical from the first migrated commit

TV1-T8 registration now rejects divergent same-ID definitions before mutation.

Each migrated export must therefore carry the exact same:

- logical ID/version;
- description;
- input/output schema;
- kind;
- execution mode;
- idempotency;
- effects;
- required scopes;
- risk/permissions/danger-pattern semantics

through SERVER and CLIENT normalization.

The same canonical manifest is the authority for both locations.

## P1-TV1-T9-1 — action-specific schemas must remove dispatcher-only arguments

Logical schemas must:

- set `additionalProperties=false`;
- omit caller-controlled `action`;
- omit bound fields such as File `mode`;
- expose only arguments meaningful to that logical action;
- preserve existing runtime hard bounds.

Runtime validation remains authoritative for byte-sensitive or platform-sensitive limits.

## P1-TV1-T9-2 — physical direct-call compatibility is separate from capability identity

TV1-T9 does not remove or rename module entrypoints:

```python
tools.v1.<tool>.run(...)
```

Existing live scripts and direct Python users may still call physical `run` with physical action names.

Capability/catalog/model-facing physical roots are intentionally replaced by logical IDs.

This follows the TV1-T7 Web precedent.

## P1-TV1-T9-3 — no implicit legacy capability aliases

TV1-T9 does not add catalog aliases such as:

```text
file_tool -> file.*
terminal_tool -> terminal.*
```

Such an alias cannot preserve one-to-many action semantics without a new compatibility contract.

If a real external compatibility requirement is proven, stop and perform a separate alias/deprecation sub-audit before adding alias behavior.

## P1-TV1-T9-4 — top-level canonical V2 modules need explicit regression proof

TV1-T8 proved package entrypoints strongly through Web and synthetic packages.

The remaining tools are top-level `*.py` modules.

TV1-T9 must prove that canonical V2 normalization works for top-level module entrypoints on both SERVER and CLIENT without changing loader semantics.

## P1-TV1-T9-5 — existing physical ToolResult identity must not be rewritten

Examples after migration:

```text
logical: file.append
physical ToolResult.tool: file_tool
physical ToolResult.action: write

logical: window.geometry
physical ToolResult.tool: window_tool
physical ToolResult.action: get_geometry

logical: desktop.screen_info
physical ToolResult.tool: desktop_automation
physical ToolResult.action: get_screen_info
```

Logical capability identity belongs to invocation/catalog/projection.
Physical identity remains in ToolResult.

---

# 5. Canonical logical export map

Logical export version for newly published logical contracts:

```text
1.0
```

Physical package versions remain their current physical implementation versions and are implementation provenance only.

## 5.1 File

```text
file.read
  bind = {"action":"read"}
  effects = READ
  idempotency = IDEMPOTENT
  base_risk = HIGH

file.search
  bind = {"action":"search"}
  effects = READ
  idempotency = IDEMPOTENT
  base_risk = HIGH

file.write
  bind = {"action":"write","mode":"w"}
  effects = WRITE
  idempotency = IDEMPOTENT
  base_risk = HIGH

file.append
  bind = {"action":"write","mode":"a"}
  effects = WRITE
  idempotency = NON_IDEMPOTENT
  base_risk = HIGH

file.replace
  bind = {"action":"replace"}
  effects = WRITE
  idempotency = UNKNOWN
  base_risk = HIGH
```

All File exports retain the physical tool's canonical danger-pattern set.

## 5.2 Glob

```text
glob.find
  bind = {}
  effects = READ
  idempotency = IDEMPOTENT
  base_risk = LOW
```

An empty bind is valid canonical Metadata V2. The physical `find_by_glob.run` has no action dispatcher argument.

## 5.3 Terminal

```text
terminal.run
  bind = {"action":"run"}
  effects = EXECUTE + EXTERNAL_SIDE_EFFECT
  idempotency = UNKNOWN
  base_risk = HIGH

terminal.launch
  bind = {"action":"launch"}
  effects = EXECUTE + EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT
  base_risk = HIGH
```

Both retain the existing terminal danger-pattern set.

## 5.4 Window

```text
window.list
  bind = {"action":"list"}
  effects = READ
  idempotency = IDEMPOTENT

window.find
  bind = {"action":"find"}
  effects = READ
  idempotency = IDEMPOTENT

window.geometry
  bind = {"action":"get_geometry"}
  effects = READ
  idempotency = IDEMPOTENT

window.focus
  bind = {"action":"focus"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = UNKNOWN

window.close
  bind = {"action":"close"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT

window.minimize
  bind = {"action":"minimize"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = UNKNOWN

window.maximize
  bind = {"action":"maximize"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = UNKNOWN

window.restore
  bind = {"action":"restore"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = UNKNOWN
```

All Window exports retain `base_risk=MEDIUM` unless a later security review explicitly narrows risk.

## 5.5 Desktop

```text
desktop.screen_info
  bind = {"action":"get_screen_info"}
  effects = READ
  idempotency = IDEMPOTENT

desktop.mouse_move
  bind = {"action":"mouse_move"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = UNKNOWN

desktop.mouse_click
  bind = {"action":"mouse_click"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT

desktop.mouse_drag
  bind = {"action":"mouse_drag"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT

desktop.mouse_scroll
  bind = {"action":"mouse_scroll"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT

desktop.type_text
  bind = {"action":"type_text"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT

desktop.press_key
  bind = {"action":"press_key"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT

desktop.hotkey
  bind = {"action":"hotkey"}
  effects = EXTERNAL_SIDE_EFFECT
  idempotency = NON_IDEMPOTENT
```

Per-export Desktop base risk should preserve the existing `_ACTION_SPECS` risk classification rather than flattening all exports to the physical root risk.

---

# 6. Manifest rules

Each migrated tool uses:

```python
TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": "<physical_name>",
    "version": "<physical_version>",
    "description": "...",
    "expose_root": False,
    "exports": [...],

    # legacy root descriptive fields may remain only when direct physical
    # Python consumers still need them; they are not a second V2 authority.
}
```

Every export uses:

```text
kind = TOOL
execution_mode = ONE_SHOT
required_scopes = []
required_permissions = []
```

and a canonical ToolResult output envelope.

Preferred migration output schema:

```python
tool_result_schema({})
```

unless the existing stable data shape can be encoded without overclaiming or changing runtime behavior.

Do not change physical runtime behavior merely to make a narrower output schema convenient.

---

# 7. Default CLIENT placement after TV1-T9

TV1-T8 default:

```json
"enabled_v2_capabilities": []
```

TV1-T9 target default preserves the existing non-Web client capability surface using exact logical IDs:

```text
file.read
file.search
file.write
file.append
file.replace
glob.find
terminal.run
terminal.launch
window.list
window.find
window.geometry
window.focus
window.close
window.minimize
window.maximize
window.restore
desktop.screen_info
desktop.mouse_move
desktop.mouse_click
desktop.mouse_drag
desktop.mouse_scroll
desktop.type_text
desktop.press_key
desktop.hotkey
```

Web remains absent:

```text
web.search
web.read
web.read_many
```

The V2 wildcard remains forbidden.

`allowed_local_tools=["*"]` remains the legacy V1 policy only.

---

# 8. Agent migration

`agent-command-reviewer` currently references three physical roots.

TV1-T9 target:

```json
"tools": [
  "terminal.run",
  "terminal.launch",
  "file.read",
  "file.search",
  "file.write",
  "file.append",
  "file.replace",
  "glob.find"
]
```

This preserves the physical action surface available through the current three umbrella tools while removing caller-controlled dispatcher `action` from model schemas.

No Desktop/Window capability is added to an Agent manifest by TV1-T9 because no current Agent manifest owns them.

Direct Chat access policy remains unchanged.

---

# 9. Exact scope

Expected production/config/manifest ownership:

```text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py

cl/config/setting.json

agents/v1/command-reviewer/manifest.json
```

Expected regressions:

```text
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
tools/v1/test/test_terminal_tool.py
tools/v1/test/test_window_tool.py
tools/v1/test/test_desktop_automation.py
tools/v1/test/test_shared_metadata.py

se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t8_metadata_v2_convergence.py
new TV1-T9 architecture/projection tests as needed

cl/tests/test_t8_metadata_v2_client_loader.py
new TV1-T9 client placement/execution tests as needed

agent projection regressions for command-reviewer
```

Conditional only if a failing regression proves a consumer gap:

```text
se/src/runtimes/capability/local_tool_loader.py
cl/src/loader/local_tools.py
cl/src/core/capability_runtime.py
se/src/runtimes/capability/registration.py
tools/v1/_shared/metadata.py
```

The preferred TV1-T9 result requires **no consumer production change** because TV1-T8 already implemented the generic canonical consumers.

---

# 10. Explicit do-not-touch

TV1-T9 must not change:

```text
tools/v1/web_tool runtime/security/fetch code
tools/v1/live/**
CapabilityRoutingPolicy priority
CapabilityCatalog identity/state-machine
R7 continuation/reconciliation
AE-R9 task/retry/branch code
provider adapters / provider model selection
MCP ownership/protocol
Central Asset Storage
SQL migrations
requirements*
```

Also excluded:

- multi-version catalog identity;
- semver negotiation/selection;
- legacy capability alias/deprecation routing;
- provider-safe name lowering;
- OpenAI/Ollama adapter work;
- provider tool-capability routing;
- live real-machine harness cleanup.

Provider-facing logical-name/schema lowering belongs to `PTC-1→PTC-3`.

Agent retry/branch work belongs to `AE-R9+`.

---

# 11. TV1-T9 staged implementation plan

## TV1-T9-A — Contract and regression freeze

No production change.

Freeze:

- exact 24 logical export IDs;
- versions/binds/effects/idempotency/risk;
- action-specific schema inventory;
- CLIENT exact-ID target allowlist;
- command-reviewer target manifest;
- root-removal expectations;
- physical ToolResult identity expectations.

Add metadata-only/synthetic regressions first where possible.

Exit: no unresolved contract ambiguity.

## TV1-T9-B — File + Glob + Terminal atomic migration

One green boundary must include together:

```text
file_tool.py
find_by_glob.py
terminal_tool.py
agents/v1/command-reviewer/manifest.json
cl/config/setting.json
related tests
```

Required:

- canonical manifests;
- physical roots hidden from capability consumers;
- command-reviewer uses only logical IDs;
- CLIENT exact-ID allowlist contains migrated exports;
- Web remains excluded;
- direct module `run` behavior unchanged.

Do not commit an intermediate state with a broken Agent manifest.

## TV1-T9-C — Window + Desktop migration

Migrate:

```text
window_tool.py
desktop_tool.py
cl/config/setting.json
related tests
```

Required:

- 16 additional action-specific logical exports;
- exact CLIENT allowlist extension;
- no Agent manifest expansion;
- side-effect schemas do not expose `action`;
- physical ToolResult identity unchanged.

## TV1-T9-D — SERVER/CLIENT canonical parity

Prove for all migrated exports:

- identical logical definition projection from the same manifest;
- matching SERVER + CLIENT implementations coexist;
- no registration rewrite;
- no definition divergence;
- logical version `1.0` vs physical package version separation;
- default routing priority unchanged.

Production consumer patch is forbidden unless a test proves an actual generic-consumer defect.

## TV1-T9-E — Capability/Agent projection cleanup

Prove:

- physical roots are absent from capability registry/catalog/model projection;
- logical IDs are present;
- command-reviewer projection contains exactly the intended logical File/Glob/Terminal capabilities;
- Web researcher remains unchanged;
- Desktop/Window remain catalog/client capabilities but are not silently added to an Agent manifest;
- public capability list/get returns logical definitions and implementation provenance.

## TV1-T9-F — Execution equivalence gate

For each logical family, execute representative bound calls and prove:

```text
logical invocation ID
    ->
immutable bind
    ->
unchanged physical run(...)
    ->
unchanged physical ToolResult.tool/action
```

Mandatory representatives:

- `file.append` proves multi-key bind `action=write, mode=a`;
- `glob.find` proves empty bind;
- `terminal.run`;
- `window.geometry` -> `get_geometry`;
- `desktop.screen_info` -> `get_screen_info`;
- at least one non-idempotent desktop/window side-effect uses mocks/fakes, not a real user machine.

## TV1-T9-G — Compatibility / collision / lifecycle gate

Run:

- V1 compatibility tests for any still-V1 helper/tool;
- V2 collision/tombstone regressions;
- registration batch atomicity;
- reconnect ACK/READY;
- R6/R7 fingerprint/reconciliation fences;
- Agent projection;
- MCP regression;
- no Web default CLIENT advertisement.

No live real-machine tests belong here.

## TV1-T9-H — Full CI / completion / TV1-T10 handoff

Run exact-SHA:

- tools unit suite;
- Architecture Baseline;
- Phase 5 Exit Gates;
- Windows client contracts;
- affected real-TCP WebSocket convergence tests.

Audit changed-file scope.

Create:

```text
tools/v1/TV1_T9_LOGICAL_EXPORT_MIGRATION_COMPLETION.md
```

Then audit TV1-T9→TV1-T10 before live-harness implementation.

---

# 12. Mandatory regression matrix

TV1-T9 cannot close until all are proven:

1. all five remaining production tools validate as canonical Metadata V2;
2. physical package names/versions remain physical provenance;
3. all 24 logical export IDs are unique;
4. all logical export names equal IDs;
5. all logical input schemas are strict objects;
6. no logical schema exposes `action`;
7. `file.write` binds `mode=w`;
8. `file.append` binds `mode=a`;
9. bound keys cannot be overridden;
10. `glob.find` works with empty bind;
11. physical roots are not SERVER capability IDs;
12. physical roots are not CLIENT registry IDs after migration;
13. direct Python physical `run` entrypoints still work;
14. ToolResult physical identity is unchanged;
15. default CLIENT logical allowlist contains the 24 non-Web exports;
16. default CLIENT logical allowlist contains zero Web exports;
17. V2 wildcard remains rejected;
18. command-reviewer contains only logical File/Glob/Terminal IDs;
19. Web researcher remains exactly `web.search/read/read_many`;
20. Desktop/Window are not added to Agent manifests implicitly;
21. same logical SERVER+CLIENT definition converges;
22. divergent registration still rejects atomically;
23. top-level V2 module discovery works on CLIENT;
24. top-level V2 module loading works on SERVER;
25. logical version stays separate from physical version;
26. capability list/get exposes canonical logical definitions;
27. routing priority unchanged;
28. reconnect/ACK/READY unchanged;
29. R7 reconciliation/version fences unchanged;
30. MCP behavior unchanged;
31. no provider/PTC file changes;
32. no AE-R9 file changes;
33. no `tools/v1/live/**` changes;
34. full CI green.

---

# 13. Stop conditions

Stop and return to boundary review if implementation appears to require:

- catalog aliases for physical root IDs;
- `expose_root=true`;
- changing the canonical Metadata V2 schema;
- changing SERVER/CLIENT routing priority;
- changing registration equality semantics;
- changing R7 invocation/reconciliation identity;
- changing provider-native tool naming/schema;
- changing AE-R9 retry/branch state;
- changing physical ToolResult identity;
- changing physical runtime semantics to satisfy metadata;
- modifying live real-machine harnesses during TV1-T9.

---

# 14. Live-harness roadmap reassignment

The old draft T8 live-harness work is still valid but was superseded as a phase number.

Audit of current `tools/v1/live/**` confirms the old findings remain material:

- no `RUN_TOOLS_V1_LIVE` opt-in gate in the four live scripts;
- logs/workspaces are written under the source tree;
- ordered `unittest` methods share mutable scenario state;
- structured ToolResult values are frequently converted with `str(...)`;
- Web URLs are still extracted from stringified results with regex;
- `live_terminal_python_pipeline.py` still hard-codes `D:\assistant\.venv\Scripts\python.exe`;
- GUI scenarios are real-machine side effects and need stronger target isolation.

Canonical reassignment:

```text
TV1-T10 — Live Harness & Real-Machine Exit Gate
```

TV1-T10 should start only after TV1-T9 logical migration is frozen, so the live harness can validate the final Tools V1 logical/physical contract rather than being rewritten twice.

Proposed TV1-T10 scope:

```text
tools/v1/live/**
live-only docs/tests/helpers
```

Proposed TV1-T10 objectives:

1. explicit `RUN_TOOLS_V1_LIVE=1` gate;
2. configured/temp artifact root, no source-tree pollution;
3. portable `sys.executable` / platform command discovery;
4. scenario runner with deterministic setup/teardown instead of ordered shared-state unit tests;
5. structured ToolResult consumption, no presentation-string parsing;
6. unique GUI target creation/handle isolation and safe cleanup;
7. deterministic process cleanup;
8. separate Web network live gate where needed;
9. no inclusion in default unit CI;
10. final real-machine evidence document.

TV1-T10 is not opened by this document; only its phase allocation is reserved.

---

# 15. Cross-roadmap dependency view

```text
TV1:
TV1-T8 FINAL-FROZEN
    |
    v
TV1-T9 remaining logical-export migration
    |
    v
TV1-T10 live harness / real-machine exit gate

Provider:
TV1-T8-H
    |
    v
PTC-1 -> PTC-2 -> PTC-3

Agent:
AE-R8 -> AE-R9 -> AE-R10 -> ...

Asset:
AE-R14 gate
    |
    v
CAS-F5+
```

TV1-T9 may proceed independently of AE-R9 and PTC-1 only while exact file ownership remains disjoint.

If PTC work touches generic capability/metadata consumers or TV1-T9 unexpectedly touches provider code, perform a fresh overlap audit first.

---

# 16. TV1-T9 entry gate

Before production code:

1. confirm Issue #7 remains closed/final-frozen;
2. verify implementation branch descends from the accepted TV1-T8 docs baseline;
3. create a dedicated TV1-T9 checkpoint/coordination Issue;
4. post `[CLAIM] TV1-T9-A @ <HEAD>`;
5. freeze exact schemas and tests;
6. do not modify production until TV1-T9-A is reviewed.

Current state:

```text
TV1-T8  CLOSED / GREEN / FINAL-FROZEN
TV1-T9  BOUNDARY AUDITED / IMPLEMENTATION PLAN FROZEN / CODE NOT STARTED
TV1-T10 PHASE ALLOCATED ONLY / NOT AUDITED / NOT OPEN
```
