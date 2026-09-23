# T7 Metadata V2 — T6→T7 Exact Boundary & Implementation Plan

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Audited code HEAD: `1138a83da1233332e4e690043951da22d966e360`  
T6 status: **CLOSED / GREEN / FROZEN**  
T7 status: **PLANNED ONLY — NO T7 PRODUCTION CODE IMPLEMENTED**  
Primary objective: replace the model-facing umbrella Web capability `web_tool + action` with three logical capabilities while preserving the frozen T6 physical implementation.

---

# 1. T6 → T7 boundary decision

T6 owns the physical Web implementation and remains frozen.

T6 physical identity:

```text
package:
  tools/v1/web_tool

physical tool name:
  web_tool

physical entrypoint:
  run(action, **kwargs)

canonical physical actions:
  search
  scrape
  scrape_many

compatibility aliases:
  read            -> scrape
  scrape_webpage  -> scrape
  read_many       -> scrape_many
```

T7 owns only the logical capability projection:

```text
web.search
web.read
web.read_many
```

Frozen binding:

```text
web.search
    -> web_tool.run(action="search", ...)

web.read
    -> web_tool.run(action="scrape", ...)

web.read_many
    -> web_tool.run(action="scrape_many", ...)
```

T7 MUST NOT rewrite T6 fetching, browser, network policy, proxy, extraction, retry, timeout, structured output, or lifecycle semantics.

---

# 2. Core identity invariant

T7 must preserve a strict distinction between:

```text
logical capability identity
        !=
physical implementation identity
```

Example:

```text
Model-visible call:
  capability_id = "web.read"
  arguments = {"url": "https://example.com"}

Durable logical invocation:
  CapabilityInvocation.capability_id = "web.read"

Physical execution:
  web_tool.run(action="scrape", url="https://example.com")

Physical ToolResult:
  tool   = "web_tool"
  action = "scrape"
```

T7 MUST NOT rewrite the T6 ToolResult contract into:

```text
tool = "web.read"
```

The logical identity belongs to CapabilityRuntime/Agent execution state.
The physical result identity belongs to the T6 Web implementation.

---

# 3. Current audited architecture at HEAD 1138a83d

Current Web metadata exports one umbrella definition:

```python
TOOL_METADATA = {
    "name": "web_tool",
    ...
    "parameters": {
        "properties": {
            "action": {
                "enum": ["search", "scrape", "scrape_many"]
            },
            ...
        },
        "required": ["action"],
    },
}
```

Current server local tool loading is one-module → one-capability:

```text
module.TOOL_METADATA["name"]
        ↓
CapabilityDefinition
        ↓
PythonCapabilityDriver(definition, module.run)
        ↓
CapabilityRegistry
        ↓
CapabilityCatalog
        ↓
ToolRegistry
```

Current `PythonCapabilityDriver` calls:

```python
result = handler(**arguments)
```

Therefore merely adding three metadata records inside `web_tool/config.py` is insufficient.
T7 must also create an exact logical-to-physical binding layer.

Current Web researcher manifest:

```json
"tools": ["web_tool"]
```

Current Agent capability projection resolves only capability IDs present in the Agent definition.

Therefore loader migration and Agent manifest migration form one atomic T7 boundary.

---

# 4. Exact T7 P0 findings

## P0-T7-1 — Metadata V1 can only expose one logical capability

The current loader consumes only:

```text
metadata["name"]
metadata["description"]
metadata["parameters"]
metadata["effects"]
...
```

No multi-capability package contract exists.

T7 must introduce Metadata V2 semantics while preserving Metadata V1 for File/Glob/Terminal/Window/Desktop and other existing tools.

## P0-T7-2 — logical capability needs bound physical arguments

`web.read({"url": ...})` cannot directly use the current handler because:

```text
run()
requires:
  action
```

The logical schema must not expose `action`.
The T7 loader must bind a fixed action before creating the Python driver.

## P0-T7-3 — umbrella `web_tool` must not remain model-visible

If T7 registers:

```text
web_tool
web.search
web.read
web.read_many
```

then the model can see duplicate contracts for the same implementation and continue bypassing the logical capability split through `action`.

T7 therefore freezes:

```text
TOOL_METADATA["name"] == "web_tool"
```

as physical package identity only for Metadata V2.

The V2 loader must register only the entries in `capabilities` as executable logical capabilities.

## P0-T7-4 — loader registration must be fail-closed and atomic at module granularity

A malformed third logical capability must not leave:

```text
web.search registered
web.read registered
web.read_many missing
```

T7 must validate the complete V2 bundle before performing any registry/catalog/tool-registry mutation.

## P0-T7-5 — Agent manifest must migrate in the same implementation phase

Current Agent Web Researcher references `web_tool`.

After T7 the canonical manifest must reference:

```text
web.search
web.read
web.read_many
```

Otherwise the Agent capability resolver will correctly hide the new capabilities because the manifest does not list them.

---

# 5. Exact T7 P1 findings

## P1-T7-1 — physical version and logical contract version are distinct

T6 physical implementation:

```text
WEB_TOOL_VERSION = 2.0.0
```

Logical CapabilityDefinition version should remain an independently versioned API contract.

Freeze initial logical version:

```text
web.search     version = 1.0
web.read       version = 1.0
web.read_many  version = 1.0
```

Do not infer logical version from `WEB_TOOL_VERSION`.

## P1-T7-2 — Web effects must not be weakened to force Direct Chat visibility

Current Web metadata declares:

```text
READ
EXTERNAL_SIDE_EFFECT
```

`DIRECT_READ_ONLY` currently allows only effects that are a subset of `{READ}`.

T7 MUST preserve Web effects.

Whether Web capabilities are visible in Direct Chat is a separate policy decision and is outside T7.

## P1-T7-3 — logical schemas must remove physical compatibility knobs

Logical Web capabilities must not expose:

```text
action
captcha_api_key
output_format
```

These remain physical/legacy implementation compatibility only.

## P1-T7-4 — bound action must be non-overridable

The fixed action must be injected by the loader binding and cannot be caller-controlled through extra arguments.

Logical schemas should use:

```json
"additionalProperties": false
```

and the bound handler must still reject an unexpected `action` key defensively.

---

# 6. Metadata V2 contract freeze

T7 introduces module-level Metadata V2 with this exact shape:

```python
TOOL_METADATA = {
    "metadata_version": 2,

    # Physical package identity.
    "name": "web_tool",
    "version": "2.0.0",
    "description": "...",

    "capabilities": [
        {
            "id": "web.search",
            "version": "1.0",
            "description": "...",
            "base_risk": "MEDIUM",
            "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
            "require_auth": False,
            "required_scopes": [],
            "required_permissions": [],
            "binding": {
                "action": "search",
            },
            "parameters": {...},
        },
        {
            "id": "web.read",
            "version": "1.0",
            "description": "...",
            "base_risk": "MEDIUM",
            "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
            "require_auth": False,
            "required_scopes": [],
            "required_permissions": [],
            "binding": {
                "action": "scrape",
            },
            "parameters": {...},
        },
        {
            "id": "web.read_many",
            "version": "1.0",
            "description": "...",
            "base_risk": "MEDIUM",
            "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
            "require_auth": False,
            "required_scopes": [],
            "required_permissions": [],
            "binding": {
                "action": "scrape_many",
            },
            "parameters": {...},
        },
    ],
}
```

Required V2 bundle fields:

```text
metadata_version == 2
name
version
capabilities
```

Required V2 capability fields:

```text
id
version
description
effects
binding.action
parameters
```

Optional capability fields preserve existing loader semantics:

```text
base_risk
require_auth
required_scopes
required_permissions
```

---

# 7. Metadata V2 validation rules

Validation must finish before the first registration side effect.

Fail closed if any of the following is true:

- `metadata_version` is not exactly integer `2`;
- `name` is empty;
- `version` is empty;
- `capabilities` is missing, empty, or not a list;
- duplicate capability IDs exist in the same bundle;
- a capability ID is empty;
- a capability version is empty;
- description is missing/non-string;
- parameters is not a JSON Schema object;
- JSON Schema validation fails;
- effects contains an unsupported CapabilityEffect;
- binding is missing;
- `binding.action` is absent or empty;
- `binding.action` is duplicated only where explicitly allowed by future contract;
- logical schema exposes `action`;
- logical schema requires an implementation-only field;
- V2 physical package `name` collides with one of its logical IDs;
- caller-visible ID conflicts with an already registered executable capability with a different contract.

The initial Web V2 bundle must require three unique IDs exactly:

```text
web.search
web.read
web.read_many
```

T7 implementation may keep the generic loader capable of other V2 bundles, but Web regression tests must assert the exact three-entry contract.

---

# 8. Logical schemas

## 8.1 `web.search`

Required:

```text
query
```

Allowed:

```text
query
max_results
timeout
```

Forbidden from logical schema:

```text
action
url
urls
force_js
wait_selector
max_chars
captcha_api_key
output_format
clean_noise
deduplicate
```

Schema limits must reuse T6 constants.

## 8.2 `web.read`

Required:

```text
url
```

Allowed:

```text
url
force_js
wait_selector
timeout
max_chars
clean_noise
deduplicate
```

Forbidden:

```text
action
query
urls
max_results
captcha_api_key
output_format
```

## 8.3 `web.read_many`

Required:

```text
urls
```

Allowed:

```text
urls
force_js
wait_selector
timeout
max_chars
clean_noise
deduplicate
```

Forbidden:

```text
action
query
url
max_results
captcha_api_key
output_format
```

All three logical schemas freeze:

```json
"additionalProperties": false
```

---

# 9. Bound handler contract

The loader must create one bound handler per logical capability.

Conceptual contract:

```python
def bound_handler(**arguments):
    if "action" in arguments:
        raise TypeError("logical Web capability cannot override bound action")

    return physical_run(
        action=BOUND_ACTION,
        **arguments,
    )
```

The wrapper must preserve the physical `run()` sync/async behavior:

```text
sync caller:
  physical run -> terminal ToolResult

active asyncio loop:
  physical run -> Task[ToolResult]
  PythonCapabilityDriver awaits it
```

No new event loop ownership is introduced by T7.

The bound handler must not:

- alter ToolResult;
- rewrite `tool`;
- rewrite `action`;
- catch T6 programmer/control-flow exceptions differently;
- bypass the T6 lifecycle;
- call WebTool methods directly.

---

# 10. Loader V1/V2 compatibility

The server local loader must support both paths:

```text
Metadata V1:
  one module
      -> one CapabilityDefinition
      -> existing behavior unchanged

Metadata V2:
  one module
      -> validate complete capability bundle
      -> N bound CapabilityDefinition/driver pairs
      -> register logical capabilities
      -> physical package name not executable
```

Existing V1 tools are frozen dependencies.

T7 MUST NOT require Metadata V2 migration for:

```text
file_tool
find_by_glob
terminal_tool
window_tool
desktop_automation
```

No changes to those tool modules are allowed for T7.

---

# 11. Atomic registration semantics

T7 must distinguish:

```text
preflight
commit
```

Preflight phase:

1. load module;
2. inspect metadata version;
3. validate complete V2 shape;
4. validate every logical JSON Schema;
5. construct all CapabilityDefinition objects;
6. construct all bound handlers/drivers;
7. detect intra-bundle duplicate IDs;
8. detect conflicts against current registries/catalog;
9. perform no mutation yet.

Commit phase:

1. register all executable drivers;
2. register all ToolRegistry definitions;
3. register catalog definitions;
4. register implementations;
5. enable implementations;
6. bind implementation drivers.

Preferred implementation architecture:

- extract registration planning into a small internal helper;
- build an immutable registration plan before mutation;
- keep V1 path behavior unchanged.

If the current registries cannot support rollback safely, T7 must ensure all foreseeable validation/conflict failures happen in preflight.
Unexpected exceptions during commit must be treated as bootstrap failure and surfaced; do not silently continue with a partially registered V2 bundle.

---

# 12. Registry/catalog identity after T7

Expected server state:

```text
CapabilityRegistry:
  web.search
  web.read
  web.read_many

ToolRegistry:
  web.search
  web.read
  web.read_many

CapabilityCatalog definitions:
  web.search
  web.read
  web.read_many

CapabilityCatalog implementations:
  server:web.search
  server:web.read
  server:web.read_many
```

Forbidden model-visible/executable registration:

```text
web_tool
```

The physical package still exports:

```python
TOOL_METADATA["name"] == "web_tool"
run(...)
WebTool
```

for legacy direct import compatibility.

---

# 13. Agent Web Researcher migration

Change:

```json
"tools": ["web_tool"]
```

to:

```json
"tools": [
  "web.search",
  "web.read",
  "web.read_many"
]
```

No Agent runtime logic change should be required.

The existing `RegistryAgentCapabilityResolver` should naturally project:

```text
web.search
web.read
web.read_many
```

from the Agent definition once the logical capabilities exist in registry/catalog.

T7 tests must prove:

```text
Agent sees:
  web.search
  web.read
  web.read_many

Agent does not see:
  web_tool
```

---

# 14. Direct Chat boundary

T7 does not change:

```python
CapabilityAccessPolicy.DIRECT_READ_ONLY
```

Web logical capabilities retain:

```text
effects:
  READ
  EXTERNAL_SIDE_EFFECT
```

Therefore current Direct Chat visibility behavior may remain unchanged.

Do not weaken Web effects to `READ` merely to make Web visible in Direct Chat.

Any future Web-in-Direct-Chat decision requires a separate policy audit.

---

# 15. Client boundary

Current `cl/src/loader/local_tools.py` only scans top-level `*.py` files and does not currently load the `tools/v1/web_tool/` package like the server loader.

T7 is therefore frozen as a server-owned logical capability projection.

T7 MUST NOT modify:

```text
cl/**
```

A future generic Client Metadata V2/package loader is separate work.

---

# 16. T7 implementation scope

Production files expected to change:

```text
tools/v1/web_tool/config.py
se/src/runtimes/capability/local_tool_loader.py
agents/v1/web-researcher/manifest.json
```

Regression files expected to change/add:

```text
tools/v1/test/test_web_tool.py
se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py    # recommended
```

Documentation:

```text
tools/v1/T7_METADATA_V2_IMPLEMENTATION_PLAN.md
tools/v1/T7_METADATA_V2_COMPLETION.md               # only after green implementation
```

---

# 17. Explicit T7 do-not-touch

Do not change T6 runtime/security implementation:

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

unless a fresh audit proves a Metadata V2 binding bug cannot be solved outside those files.

Do not change:

```text
tools/v1/_shared/**
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py

tools/v1/live/**

cl/**

R7 continuation/invocation semantics
CapabilityRuntime execution semantics
CapabilityCatalog core state machine
AgentRuntime execution loop
Direct Chat policy
requirements*
```

---

# 18. T7-A → T7-G implementation plan

## T7-A — Metadata V2 contract + regression freeze

Goal:

- encode exact Metadata V2 shape;
- preserve physical `name=web_tool`;
- define three logical contracts;
- add regression tests proving schema separation.

Expected files:

```text
tools/v1/web_tool/config.py
tools/v1/test/test_web_tool.py
```

Tests:

- `metadata_version == 2`;
- physical `name == web_tool`;
- exactly three logical IDs;
- unique IDs;
- correct bound actions;
- no logical schema exposes `action`;
- no logical schema exposes `captcha_api_key`;
- no logical schema exposes `output_format`;
- `additionalProperties == false`;
- T6 physical action enum compatibility remains available only where intentionally retained.

Stop after metadata/tests if loader implementation would require changing T6 runtime.

## T7-B — generic V2 preflight parser

Goal:

- teach server local loader to distinguish V1 vs V2;
- validate complete V2 bundle before mutation;
- construct logical definitions + bindings in memory.

Expected file:

```text
se/src/runtimes/capability/local_tool_loader.py
```

Required invariants:

- V1 behavior unchanged;
- malformed V2 fails closed;
- duplicate logical IDs rejected;
- invalid JSON Schema rejected before registration;
- unsupported effects rejected;
- missing/invalid binding rejected;
- no partial registry mutation during preflight.

## T7-C — bound logical handlers

Goal:

- bind each logical capability to the frozen physical action;
- preserve `run()` lifecycle and Task-return semantics.

Bindings:

```text
web.search     -> search
web.read       -> scrape
web.read_many  -> scrape_many
```

Tests:

- calling `web.search` does not require `action`;
- calling `web.read` invokes physical `scrape`;
- calling `web.read_many` invokes physical `scrape_many`;
- caller cannot override bound action;
- physical ToolResult still reports `tool=web_tool`;
- physical ToolResult action remains canonical.

## T7-D — atomic logical registration

Goal:

- register all three logical definitions/drivers into Registry, ToolRegistry, Catalog, DriverRegistry;
- never register umbrella `web_tool` as executable V2 capability.

Expected tests:

```text
runtime.registry.get_driver("web.search")     != None
runtime.registry.get_driver("web.read")       != None
runtime.registry.get_driver("web.read_many")  != None
runtime.registry.get_driver("web_tool")       == None

tools.get("web.search")     != None
tools.get("web.read")       != None
tools.get("web.read_many")  != None
tools.get("web_tool")       == None

catalog.get_implementation("server:web.search")     ENABLED
catalog.get_implementation("server:web.read")       ENABLED
catalog.get_implementation("server:web.read_many")  ENABLED
```

V1 tools must still register successfully in the same loader pass.

## T7-E — Agent Web Researcher migration

Goal:

- move Agent manifest from physical bundle name to logical capabilities.

Expected file:

```text
agents/v1/web-researcher/manifest.json
```

Tests must prove first inference capability projection contains exactly the intended logical Web capabilities and not the umbrella physical name.

## T7-F — regression matrix / blast-radius proof

Run targeted regressions across:

```text
tools/v1/test/test_web_tool.py
se/tests/architecture/test_local_tool_loader.py
T7-specific Metadata V2 architecture tests
Agent context/capability projection tests
PythonCapabilityDriver Task-return regression
```

Mandatory regression matrix:

1. Metadata V1 tool still loads unchanged.
2. Valid Web Metadata V2 registers exactly three logical capabilities.
3. Invalid third capability causes zero V2 registrations.
4. Duplicate IDs fail before mutation.
5. Bound action cannot be overridden.
6. Logical argument validation rejects extra physical fields.
7. `web.search` maps to physical `search`.
8. `web.read` maps to physical `scrape`.
9. `web.read_many` maps to physical `scrape_many`.
10. Physical ToolResult identity remains `web_tool`.
11. Logical CapabilityInvocation identity remains logical ID.
12. Agent Web Researcher sees three logical capabilities.
13. Agent Web Researcher does not see `web_tool`.
14. Direct Chat policy behavior is unchanged.
15. Existing async active-loop Web execution regression remains green.

## T7-G — final CI, completion, checkpoint

Before declaring T7 closed:

1. verify branch still descends from the audited baseline;
2. verify diff contains only approved T7 scope;
3. run targeted tests;
4. run full CI;
5. create `T7_METADATA_V2_COMPLETION.md`;
6. create/update the GitHub Issue checkpoint with:
   - final implementation HEAD;
   - exact changed files;
   - regression evidence;
   - CI evidence;
   - frozen T7→T8 boundary;
   - unresolved risks;
   - next exact action.

---

# 19. Parallel-agent work partition

T7 may be split only by non-overlapping ownership.

## Workstream T7-META

Ownership:

```text
tools/v1/web_tool/config.py
tools/v1/test/test_web_tool.py   # metadata-only sections
```

Responsibilities:

- T7-A only;
- no loader edits;
- no Agent manifest edits.

## Workstream T7-LOADER

Ownership:

```text
se/src/runtimes/capability/local_tool_loader.py
se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
```

Responsibilities:

- T7-B/C/D;
- no Web runtime edits;
- no Agent manifest edits.

## Workstream T7-AGENT

Ownership:

```text
agents/v1/web-researcher/manifest.json
Agent capability-projection regression tests only
```

Responsibilities:

- T7-E;
- no loader implementation edits.

## Workstream T7-GATE

Ownership:

```text
integration/regression validation
completion documentation
checkpoint issue updates
```

Responsibilities:

- T7-F/G;
- must not silently patch production code while acting as gate reviewer.

Any agent that discovers a cross-scope requirement must stop and post a `[HANDOFF]` checkpoint comment before editing another workstream's files.

---

# 20. GitHub Issue checkpoint communication protocol

The T7 checkpoint Issue is the shared coordination surface.

Every agent should post one of these markers:

```text
[CLAIM]
[PROGRESS]
[BLOCKED]
[HANDOFF]
[DONE]
[AUDIT]
```

Required comment header:

```text
[MARKER] T7-<workstream> @ <observed HEAD>
```

Example:

```text
[CLAIM] T7-LOADER @ 1138a83d

Scope:
- se/src/runtimes/capability/local_tool_loader.py
- se/tests/architecture/test_local_tool_loader.py

Will not touch:
- tools/v1/web_tool/**
- agents/v1/**
```

A `[HANDOFF]` comment must include:

- current HEAD;
- files changed;
- invariants established;
- tests run;
- unresolved findings;
- exact next action;
- whether another agent may safely edit the released scope.

Agents must re-read the checkpoint Issue and current branch HEAD before starting or resuming work.

---

# 21. Stop conditions

Return to contract review before implementation continues if any change requires:

- changing `ToolResult.tool` away from `web_tool`;
- changing T6 physical action names;
- changing T6 Web network/security/fetch behavior;
- exposing `action` to logical capabilities;
- exposing CAPTCHA API key through logical metadata;
- registering `web_tool` alongside logical Web capabilities as model-visible executable capability;
- weakening Web effects to bypass Direct Chat policy;
- changing Client loader/runtime;
- changing CapabilityRuntime invocation/continuation semantics;
- changing CapabilityCatalog state-machine semantics;
- partial V2 registration being accepted as normal behavior;
- using current Agent runtime code to rewrite physical Web results.

---

# 22. T7 exit gate

T7 is complete only when all of the following are true:

```text
model-visible:
  web.search
  web.read
  web.read_many

not model-visible:
  web_tool
  action
  scrape
  scrape_many
```

and:

```text
web.search(query=...)
    -> web_tool.run(action="search", ...)

web.read(url=...)
    -> web_tool.run(action="scrape", ...)

web.read_many(urls=[...])
    -> web_tool.run(action="scrape_many", ...)
```

and:

```text
CapabilityInvocation.capability_id
    = logical capability ID

ToolResult.tool
    = "web_tool"

ToolResult.action
    = canonical physical action
```

and all V1 local tools still register unchanged.

---

# 23. T7 → T8 handoff candidate

T7 ends at logical Web capability projection.

T8 must be audited separately before code.

Candidate T8 topics:

- generic Metadata V2 beyond Web;
- common metadata schema/model instead of loader-local dictionaries;
- Client/package Metadata V2 discovery;
- capability aliases/deprecation policy;
- public capability introspection/version negotiation;
- policy-aware capability grouping;
- cross-location logical implementations.

None of these belong to T7 unless a fresh boundary audit explicitly reopens scope.

---

# 24. Current frozen state

```text
T1–T5  CLOSED / FROZEN
T6     CLOSED / GREEN / FROZEN @ 1138a83d
T7     IMPLEMENTATION PLAN FROZEN / NOT IMPLEMENTED
T8     NOT AUDITED
```

## NEXT EXACT ACTION

1. Re-read branch HEAD and this checkpoint before implementation.
2. Claim one non-overlapping T7 workstream in the GitHub checkpoint Issue.
3. Implement T7-A→T7-G in order, preserving the physical T6 contract.
4. After each major workstream, post a `[PROGRESS]` or `[HANDOFF]` comment.
5. Before declaring T7 complete, run full CI and perform a fresh exact T7→T8 boundary audit.
