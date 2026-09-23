# T8 Metadata V2 Convergence — Exact Implementation Plan

Repository: boxs-51/assistant  
Branch: tools-v1-contract-freeze  
Frozen T7 baseline: adea67c374bf814914bab47a3f9b36caf50c1d1c  
Boundary checkpoint: Issue #7 — [CHECKPOINT] T7 → T8 Metadata V2 convergence boundary freeze @ adea67c3  
Mode: PLAN FREEZE ONLY — NO T8 PRODUCTION CODE IMPLEMENTED

---

# 1. Purpose

T8 converges the Tools V1 metadata/control-plane boundary after T7.

T7 already froze the logical/physical split:

~~~text
web.search     -> web_tool.run(action="search", ...)
web.read       -> web_tool.run(action="scrape", ...)
web.read_many  -> web_tool.run(action="scrape_many", ...)
~~~

and:

~~~text
CapabilityInvocation.capability_id = logical capability ID
ToolResult.tool                     = physical tool identity
ToolResult.action                   = canonical physical action
~~~

T8 does not change those semantics.

T8 exists to close the post-T7 architecture gap:

1. two incompatible Metadata V2 dialects exist;
2. SE and CL do not consume one canonical manifest;
3. CL cannot discover package tool entrypoints;
4. package discovery could accidentally alter execution placement;
5. client registration currently allows last-writer-wins definition replacement;
6. the catalog supports one canonical version per capability ID, not true multi-version coexistence.

Target:

~~~text
one canonical Metadata V2 manifest
        |
        +--> SERVER consumer
        |
        +--> CLIENT consumer, explicit opt-in only
        |
        v
one canonical logical capability contract
        |
        +--> one or more physical implementations
~~~

---

# 2. Canonical Metadata V2 authority

The canonical authority is the T1 shared foundation:

~~~text
tools/v1/_shared/metadata.py
validate_tool_manifest_v2()
~~~

Canonical root fields:

~~~text
manifest_version = "2.0"
name
version
description
expose_root
exports
~~~

Canonical export fields:

~~~text
id
version
name
description
bind
input_schema
output_schema
kind
execution_mode
idempotency
effects
base_risk
required_scopes
required_permissions
danger_patterns
~~~

The T7 Web shape is transitional only:

~~~text
metadata_version = 2
capabilities
binding
parameters
~~~

T8 exit MUST leave only manifest_version="2.0" / exports / bind / input_schema / output_schema as the final Metadata V2 authority.

No third dialect may be introduced.

---

# 3. Frozen identity model

T8 distinguishes four identities.

## 3.1 Physical package identity

Example:

~~~text
name    = web_tool
version = 2.0.0
~~~

This identifies the Python implementation package.

When expose_root=false, it is not a model-visible logical capability.

## 3.2 Logical capability identity

Examples:

~~~text
web.search
web.read
web.read_many
~~~

This identity is used by:

- Agent capability projection;
- CapabilityInvocation.capability_id;
- catalog definition lookup;
- policy;
- capability introspection;
- remote invocation and reconciliation.

## 3.3 Logical export version

Example:

~~~text
web.read version = 1.0
~~~

This versions the logical contract and is independent from web_tool version 2.0.0.

## 3.4 Physical implementation identity

Examples:

~~~text
server:web.read
<connection_id>:web.read
~~~

Package name/version/bind are implementation provenance, not logical capability identity.

---

# 4. Logical contract equality vs implementation provenance

CapabilityDefinition currently mixes logical semantics and physical provenance.

The cross-location equality contract in T8 must compare logical semantics, not raw physical provenance.

Logical semantic fields:

~~~text
capability_id
version
name
description
input_schema
output_schema
kind
execution_mode
idempotency
effects
require_auth
required_scopes
semantic metadata
~~~

For canonical Tools V1 V2, semantic metadata is:

~~~text
base_risk
required_permissions
danger_patterns
~~~

Physical/provenance fields must not create false logical conflicts:

~~~text
source
execution_kind
client_id
physical_tool
physical_version
bind
binding_action
manifest_version
metadata_version
local_name
connection_id
implementation_id
driver_kind
owner information
~~~

Therefore T8-F MUST use a canonical logical-contract comparator/fingerprint instead of raw CapabilityDefinition BaseModel equality.

It MUST fail closed for differences in:

- logical ID;
- logical version;
- name;
- description;
- input schema;
- output schema;
- kind;
- execution mode;
- idempotency;
- effects;
- require_auth;
- required scopes;
- base risk;
- required permissions;
- danger patterns.

Set-like fields such as effects/scopes/permissions/danger patterns should be normalized deterministically before comparison.

---

# 5. Version boundary

Current catalog identity is:

~~~text
definitions[capability_id]
implementations -> definition.version
routing -> capability_id
~~~

T8 therefore freezes:

~~~text
one active canonical logical definition/version per capability_id
~~~

All implementations of that ID must match the canonical logical contract.

T8 MUST NOT implement:

- definitions[(capability_id, version)];
- hot multi-version coexistence;
- semver selection;
- version negotiation;
- alias-to-version routing.

Those are T9+ topics.

---

# 6. Generic immutable bind contract

Canonical T1 Metadata V2 supports arbitrary immutable JSON-safe bind maps.

Example:

~~~python
"bind": {
    "action": "write",
    "mode": "a",
}
~~~

Both SE and CL consumers must implement generic bind behavior.

Conceptual wrapper:

~~~python
def bound_handler(**arguments):
    collisions = set(arguments).intersection(bound_values)
    if collisions:
        raise TypeError("logical capability cannot override immutable bound fields")

    return physical_handler(
        **bound_values,
        **arguments,
    )
~~~

Required invariants:

- caller cannot override any bound key;
- bind is copied before closure capture;
- manifest is not mutated;
- caller arguments are not mutated;
- result is not rewritten;
- event-loop ownership is unchanged;
- exceptions are not swallowed;
- physical handler remains the execution implementation.

T8 removes the T7 assumption that V2 bind must contain exactly one action.

Two logical exports may bind the same physical action if other immutable bind values differ.

The shared validator remains responsible for bind/public-schema collision rejection.

---

# 7. Canonical V2 to CapabilityDefinition projection

For each validated export, the server canonical consumer constructs:

~~~text
id              = export.id
version         = export.version
name            = export.name
description     = export.description
input_schema    = export.input_schema
output_schema   = export.output_schema
kind            = export.kind
execution_mode  = export.execution_mode
idempotency     = export.idempotency
effects         = export.effects
required_scopes = export.required_scopes
~~~

For Tools V1 canonical V2:

~~~text
require_auth = false
~~~

Reason: require_auth is not part of the frozen T1 manifest. T8 must not invent a second optional V2 security field and must not infer auth from scopes.

Definition semantic metadata:

~~~python
{
    "base_risk": export["base_risk"],
    "required_permissions": export["required_permissions"],
    "danger_patterns": export["danger_patterns"],
}
~~~

Physical implementation metadata:

~~~python
{
    "kind": "TOOL",
    "manifest_version": "2.0",
    "physical_tool": manifest["name"],
    "physical_version": manifest["version"],
    "bind": export["bind"],
}
~~~

Physical package/bind provenance MUST NOT remain in the canonical logical definition metadata.

---

# 8. Canonical Web manifest after T8-C

tools/v1/web_tool/config.py migrates to:

~~~python
TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": "web_tool",
    "version": "2.0.0",
    "description": "...",
    "expose_root": False,
    "exports": [...],

    # legacy root compatibility fields may remain if required by
    # non-V2 direct consumers; they are not a second V2 dialect.
}
~~~

The transitional T7 V2 authority must disappear:

~~~text
metadata_version
capabilities
binding
parameters-as-export-schema
~~~

Canonical exports remain exactly:

~~~text
web.search
web.read
web.read_many
~~~

Bindings:

~~~text
web.search     -> {"action": "search"}
web.read       -> {"action": "scrape"}
web.read_many  -> {"action": "scrape_many"}
~~~

All three freeze:

~~~text
kind                 = TOOL
execution_mode       = ONE_SHOT
idempotency          = UNKNOWN
effects              = READ + EXTERNAL_SIDE_EFFECT
base_risk            = MEDIUM
required_scopes      = []
required_permissions = []
danger_patterns      = []
~~~

UNKNOWN idempotency is deliberate because T7 currently reaches the CapabilityDefinition default UNKNOWN. T8 must not silently change replay semantics.

Input schemas remain the T7 logical schemas.

Output schema should use:

~~~python
tool_result_schema({})
~~~

unless an already-stable exact data schema can be encoded without changing T6 behavior.

This freezes the ToolResult envelope without overclaiming the inner T6 data shape.

---

# 9. Client placement policy

Current V1 client policy remains:

~~~json
{
  "tools_config": {
    "allowed_local_tools": ["*"],
    "blocked_local_tools": ["delete_path"]
  }
}
~~~

T8 adds one exact V2 logical allowlist:

~~~json
{
  "tools_config": {
    "allowed_local_tools": ["*"],
    "blocked_local_tools": ["delete_path"],
    "enabled_v2_capabilities": []
  }
}
~~~

Frozen semantics:

- default = [];
- entries are exact logical capability IDs;
- "*" is rejected for enabled_v2_capabilities in T8;
- package discovery does not add unlisted V2 exports to registry.tools;
- unlisted exports are not advertised via capability.register;
- existing V1 wildcard semantics remain unchanged.

Default T8 placement:

~~~text
SERVER:
  web.search
  web.read
  web.read_many

CLIENT:
  no Web logical exports advertised
~~~

CapabilityRoutingPolicy priority must not change.

---

# 10. Client package discovery contract

Current CL scans top-level tools/v1/*.py only.

T8-D adds direct package entrypoints:

~~~text
tools/v1/<package>/__init__.py
~~~

Rules:

- direct child package only;
- package-aware submodule_search_locations;
- relative imports work;
- module inserted into sys.modules before exec;
- failed import cleans sys.modules;
- helper/non-tool packages remain skippable;
- V1 top-level behavior unchanged;
- no recursive arbitrary nested scan.

Do not treat these as executable tool bundles merely because they are directories:

~~~text
tools/v1/_shared/**
tools/v1/test/**
tools/v1/live/**
~~~

Canonical V2 detection:

~~~text
TOOL_METADATA.manifest_version == "2.0"
~~~

Canonical V2 validation:

~~~python
validate_tool_manifest_v2(TOOL_METADATA)
~~~

No CL-local duplicate of the full validator.

---

# 11. Client normalized logical registry entry

For one explicitly enabled V2 export:

~~~python
registry.tools["logical.id"] = {
    "metadata": {
        "name": export["name"],
        "version": export["version"],
        "description": export["description"],
        "parameters": export["input_schema"],
        "input_schema": export["input_schema"],
        "output_schema": export["output_schema"],
        "kind": export["kind"],
        "execution_mode": export["execution_mode"],
        "idempotency": export["idempotency"],
        "effects": export["effects"],
        "require_auth": False,
        "required_scopes": export["required_scopes"],
        "base_risk": export["base_risk"],
        "required_permissions": export["required_permissions"],
        "danger_patterns": export["danger_patterns"],
        "physical_tool": manifest["name"],
        "physical_version": manifest["version"],
        "bind": export["bind"],
        "manifest_version": "2.0",
    },
    "func": bound_handler,
    "is_internal": False,
    "file_path": ".../__init__.py",
}
~~~

The logical export version is used by:

- CapabilityDispatcher capability_version;
- request fingerprints;
- reconciliation;
- capability.register.

Physical package version must never replace logical export version.

---

# 12. Client capability.register definition projection

cl/src/core/capability_runtime.py must not inject physical/client provenance into the logical definition for V2.

Current V1-style definition metadata:

~~~python
"metadata": {
    "client_id": self.client_id,
}
~~~

must not be the canonical V2 logical definition metadata.

V2 logical definition metadata:

~~~python
{
    "base_risk": ...,
    "required_permissions": ...,
    "danger_patterns": ...,
}
~~~

Client/physical provenance belongs in CapabilityRegistration.metadata:

~~~python
{
    "client_id": self.client_id,
    "local_name": capability_id,
    "physical_tool": ...,
    "physical_version": ...,
    "bind": ...,
    "manifest_version": "2.0",
}
~~~

V2 registration definition must also preserve:

~~~text
output_schema
kind
execution_mode
idempotency
effects
required_scopes
~~~

V1 client behavior remains compatible.

T8 must not change:

- connection generation ownership;
- registration ACK ordering;
- READY transition;
- dispatcher snapshot timing;
- reconnect lifecycle.

---

# 13. Client local execution path

No new dispatcher is introduced.

Existing path remains:

~~~text
capability.invoke
    -> CapabilityDispatcher
    -> LocalCapabilityExecutor
    -> registry.tools[logical_id]
    -> bound physical handler
~~~

Enabled V2 logical capability must resolve to its bound wrapper.

Existing LocalCapabilityExecutor context injection semantics remain unchanged.

Do not modify R6/R7 replay/cancel/reconcile behavior to support V2.

---

# 14. Cross-location definition convergence

Current ClientCapabilityRegistrationService uses allow_update=True.

T8-F removes last-writer-wins logical definition replacement.

Required semantics:

## Existing definition absent

Register only after complete batch preflight.

## Existing definition present and logically equal

Reuse the existing canonical catalog definition.

Do not rewrite it because source, execution location, physical package or client provenance differ.

A new implementation may coexist.

## Existing definition present and logically different

Reject the entire registration batch before mutation.

Examples:

- version mismatch;
- input schema mismatch;
- output schema mismatch;
- effects mismatch;
- idempotency mismatch;
- execution-mode mismatch;
- scope mismatch;
- risk mismatch;
- permissions mismatch;
- danger-pattern mismatch.

## Existing implementation ID

Exact same implementation contract, normalized for lifecycle state, remains idempotent.

Reusing an implementation ID with different capability/location/owner/binding contract is rejected before mutation.

Do not retain allow_update=True as a compatibility shortcut.

---

# 15. Atomic client registration requirement

Preflight:

1. validate active connection and ownership;
2. validate every CLIENT/location/driver/owner binding;
3. validate duplicate implementation IDs;
4. canonicalize every logical definition contract;
5. validate duplicate logical IDs in batch;
6. compare all existing definitions;
7. validate existing implementation-ID collisions;
8. build implementation objects;
9. no catalog mutation yet.

Commit:

1. register missing canonical definitions;
2. register missing implementations;
3. transition new implementations to ENABLED;
4. return existing exact idempotent implementations where applicable.

Unexpected commit exceptions must surface.

T8 does not introduce a new transactional CapabilityCatalog abstraction.

Foreseeable conflicts must be eliminated during preflight.

---

# 16. Safe server transition

T8-B may temporarily support both parser paths only to keep the branch runnable while T8-C migrates Web.

Transition:

~~~text
T8-B:
  add canonical T1 V2 consumer
  transitional T7 parser may temporarily remain

T8-C:
  migrate Web to canonical manifest
  remove transitional T7 parser
  old dialect becomes rejected/skipped
~~~

By the end of T8-C:

~~~text
manifest_version="2.0" is the only V2 discriminator
~~~

V1 remains the path for metadata without canonical manifest_version.

Unsupported explicit manifest_version values fail closed.

---

# 17. Exact production scope

Expected production changes:

~~~text
tools/v1/web_tool/config.py

se/src/runtimes/capability/local_tool_loader.py
se/src/runtimes/capability/registration.py

cl/src/loader/local_tools.py
cl/src/core/capability_runtime.py
cl/config/setting.json
~~~

Conditional only if proven necessary:

~~~text
tools/v1/_shared/metadata.py
tools/v1/_shared/__init__.py
cl/src/loader/registry.py
~~~

The shared validator already appears sufficient. Do not edit conditional files only for refactoring symmetry.

Expected regression files:

~~~text
tools/v1/test/test_shared_metadata.py
tools/v1/test/test_web_tool.py

se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
se/tests/architecture/test_phase6_5_client_registration.py
se/tests/transport/test_realtime_registration.py

cl/tests/test_t8_metadata_v2_client_loader.py
cl/tests/test_realtime_dispatcher_contract.py
cl/tests/test_r6_c_reconciliation_contract.py

se/tests/architecture/test_t8_metadata_v2_convergence.py
~~~

Completion document:

~~~text
tools/v1/T8_METADATA_V2_COMPLETION.md
~~~

---

# 18. Explicit do-not-touch

Do not change T6 Web runtime/security/fetch code:

~~~text
tools/v1/web_tool/core.py
tools/v1/web_tool/searcher.py
tools/v1/web_tool/scraper.py
tools/v1/web_tool/network_policy.py
tools/v1/web_tool/proxy.py
tools/v1/web_tool/extractors.py
tools/v1/web_tool/stealth.py
tools/v1/web_tool/cap_solver_handler.py
~~~

Do not migrate these V1 physical tools in T8:

~~~text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
~~~

Do not change:

~~~text
tools/v1/live/**
agents/v1/**
CapabilityRoutingPolicy priority
CapabilityCatalog key/state-machine semantics
CapabilityRuntime invocation identity
R7 continuation/resume/reconciliation state machines
AgentRuntime execution loop
Direct Chat access policy
MCP ownership/protocol
requirements*
~~~

unless a fresh boundary audit explicitly reopens scope.

---

# 19. T8-A → T8-H exact implementation plan

## T8-A — Canonical Metadata V2 contract freeze

Goal:

Make the existing T1 validator the explicit single authority.

Ownership:

~~~text
tools/v1/_shared/metadata.py       # only if a real gap exists
tools/v1/_shared/__init__.py       # only if helper export is required
tools/v1/test/test_shared_metadata.py
~~~

Required proof:

1. manifest_version exactly "2.0";
2. exports non-empty;
3. duplicate export IDs rejected;
4. export name equals ID;
5. generic JSON-safe bind accepted;
6. bind/public-property collisions rejected;
7. input schema additionalProperties=false;
8. output schema is ToolResult envelope;
9. effects explicit and non-empty;
10. idempotency explicit;
11. base risk explicit;
12. scopes/permissions/danger patterns explicit;
13. defensive copy preserved;
14. no SE/CL imports.

Preferred result:

No production change to _shared/metadata.py if current validator already satisfies the contract.

Exit:

SE and CL must reuse this validator.

---

## T8-B — Server canonical V2 consumer

Goal:

Teach se/src/runtimes/capability/local_tool_loader.py to consume canonical T1 manifests.

Ownership:

~~~text
se/src/runtimes/capability/local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
se/tests/architecture/test_local_tool_loader.py
~~~

Required implementation:

1. detect manifest_version="2.0";
2. validate through shared validator;
3. construct complete logical plan in memory;
4. generic immutable-bind wrappers;
5. project all canonical export semantic fields;
6. move physical provenance to implementation metadata;
7. preserve V1 path;
8. preserve zero-partial-registration preflight;
9. keep transitional T7 parser only until T8-C.

Required regression:

- generic multi-key bind;
- non-overridable bound keys;
- output_schema preserved;
- idempotency/mode/kind/effects/scopes preserved;
- expose_root=false hides root;
- physical vs logical version separation;
- V1 regression;
- malformed third export => zero registrations.

Stop if CapabilityRuntime, CapabilityCatalog or T6 Web runtime must change.

---

## T8-C — Web canonical migration and old-dialect removal

Goal:

Migrate Web to canonical manifest and remove the T7 parser dialect.

Ownership:

~~~text
tools/v1/web_tool/config.py
tools/v1/test/test_web_tool.py
se/src/runtimes/capability/local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
~~~

Frozen Web logical IDs:

~~~text
web.search
web.read
web.read_many
~~~

Frozen binds:

~~~text
web.search     -> {"action":"search"}
web.read       -> {"action":"scrape"}
web.read_many  -> {"action":"scrape_many"}
~~~

All use:

~~~text
kind=TOOL
execution_mode=ONE_SHOT
idempotency=UNKNOWN
effects=READ+EXTERNAL_SIDE_EFFECT
base_risk=MEDIUM
expose_root=false
~~~

By T8-C exit, old metadata_version/capabilities/binding dialect is not accepted as final V2.

Preserve:

- WEB_TOOL_NAME=web_tool;
- WEB_TOOL_VERSION=2.0.0;
- physical actions;
- ToolResult identity;
- T6 security/runtime;
- Web Researcher logical visibility.

---

## T8-D — Client package discovery and V2 normalization

Goal:

CL discovers package entrypoints and normalizes canonical exports without auto-advertising them.

Ownership:

~~~text
cl/src/loader/local_tools.py
cl/tests/test_t8_metadata_v2_client_loader.py
~~~

Conditional:

~~~text
cl/src/loader/registry.py
~~~

Required behavior:

- preserve V1 top-level scan;
- direct package __init__.py discovery;
- relative imports work;
- shared canonical validator;
- logical export expansion;
- generic immutable bind wrapper;
- only enabled_v2_capabilities enter registry.tools;
- V1 wildcard does not select V2;
- MCP merge behavior unchanged.

Tests:

1. package relative import works;
2. helper package ignored;
3. malformed enabled package fails/skips deterministically;
4. valid package + empty V2 allowlist => zero entries;
5. exact enabled logical ID => one entry;
6. V2 wildcard rejected;
7. caller cannot override bind;
8. Web discoverable but not selected by default.

Exit:

Default client startup advertises no new Web capability.

---

## T8-E — Explicit CLIENT advertisement policy

Goal:

Serialize explicitly enabled V2 exports as complete logical definitions.

Ownership:

~~~text
cl/src/core/capability_runtime.py
cl/config/setting.json
cl/tests/test_t8_metadata_v2_client_loader.py
cl/tests/test_realtime_dispatcher_contract.py
~~~

Config freeze:

~~~json
"enabled_v2_capabilities": []
~~~

Exact IDs only.

Registration payload requirements:

- logical ID/version;
- input/output schemas;
- kind;
- execution mode;
- idempotency;
- effects;
- scopes;
- semantic definition metadata.

Do not put client_id or physical package/bind provenance in logical definition metadata.

Keep client/physical provenance in implementation registration metadata.

Tests:

- disabled export absent;
- enabled export exactly once;
- logical version advertised;
- physical version only provenance;
- dispatcher snapshot uses logical ID;
- READY only after current ACK flow;
- V1 registration compatible.

No repository-default non-empty V2 allowlist is permitted in T8.

---

## T8-F — Cross-location definition convergence

Goal:

Remove last-writer-wins client logical-definition replacement.

Ownership:

~~~text
se/src/runtimes/capability/registration.py
se/tests/architecture/test_phase6_5_client_registration.py
se/tests/architecture/test_t8_metadata_v2_convergence.py
~~~

Implementation:

- deterministic logical semantic comparator/fingerprint;
- provenance excluded from semantic equality;
- existing equivalent definition reused;
- divergent definition rejects entire batch;
- no allow_update=True compatibility shortcut;
- implementation-ID reuse only for equivalent contract.

Required regressions:

1. exact client registration idempotent;
2. SERVER + matching CLIENT definition coexist;
3. source/provenance differences alone do not conflict;
4. version mismatch rejects;
5. input schema mismatch rejects;
6. output schema mismatch rejects;
7. effects mismatch rejects;
8. idempotency mismatch rejects;
9. scope/risk/permissions/danger-pattern mismatch rejects;
10. rejected batch causes zero mutation;
11. removed implementation remains non-revivable.

Do not change catalog keying or routing.

---

## T8-G — End-to-end convergence gate

Goal:

Prove canonical metadata works across SERVER and CLIENT without changing placement or continuation semantics.

Required scenarios:

### G1 Server Web

~~~text
web.search/read/read_many
 -> SERVER implementations
 -> logical invocation IDs
 -> physical web_tool ToolResult
~~~

### G2 Default client placement

~~~text
Web CLIENT advertisements = none
~~~

### G3 Synthetic enabled CLIENT V2 capability

Use a synthetic capability, not Web:

~~~text
package manifest
 -> client logical registry
 -> capability.register
 -> server catalog
 -> remote invocation
 -> bound local callable
 -> result
~~~

### G4 Hybrid same logical definition

Matching SERVER + CLIENT implementations coexist.

Existing same-connection routing behavior may select CLIENT.

Do not change routing priority.

### G5 Divergent client definition

Prove rejection with zero definition/implementation mutation.

### G6 Reconnect

Fresh connection re-advertises same logical contract.

Logical definition reused; connection-bound implementation follows current lifecycle.

ACK/READY unchanged.

### G7 Version/reconciliation fence

Existing dispatcher/R6/R7 version fingerprint and reconciliation tests remain green.

### G8 Introspection

Existing GET /v1/capabilities/ and GET /v1/capabilities/{id} return canonical logical definition and implementations.

No new endpoint.

---

## T8-H — Final CI and completion

Targeted minimum:

~~~text
tools/v1/test/test_shared_metadata.py
tools/v1/test/test_web_tool.py

se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
se/tests/architecture/test_phase6_5_client_registration.py
se/tests/architecture/test_t8_metadata_v2_convergence.py
se/tests/transport/test_realtime_registration.py

cl/tests/test_t8_metadata_v2_client_loader.py
cl/tests/test_realtime_dispatcher_contract.py
cl/tests/test_r6_c_reconciliation_contract.py
~~~

Also run affected real WebSocket/continuation suites.

Full gate:

- Architecture Baseline;
- Phase 5 Exit Gates;
- Linux full suite;
- Windows client contracts where configured.

Scope audit:

- only approved T8 production files;
- no T6 Web runtime/security diff;
- no routing/R7 state-machine diff.

Create:

~~~text
tools/v1/T8_METADATA_V2_COMPLETION.md
~~~

Update Issue #7 with final HEAD, changed files, canonical contract, test/CI evidence, unresolved risks and T8→T9 handoff.

Then audit T8→T9 before T9 code.

---

# 20. Mandatory regression matrix

T8 cannot close until all are green:

1. canonical shared V2 manifest accepted;
2. defensive copy preserved;
3. old T7 dialect not a final V2 authority;
4. V1 server tools unchanged;
5. V1 client tools unchanged;
6. MCP behavior unchanged;
7. server canonical V2 registers exports;
8. expose_root=false hides physical root;
9. generic multi-key bind works;
10. bound keys non-overridable;
11. logical vs physical version separation;
12. logical output schema preserved;
13. Web physical mappings unchanged;
14. ToolResult.tool remains web_tool;
15. ToolResult.action remains canonical physical action;
16. logical invocation ID preserved;
17. CL discovers package entrypoint;
18. CL default advertises no V2 package exports;
19. exact enabled_v2_capabilities ID selects one export;
20. V2 wildcard rejected;
21. registration advertises logical version;
22. logical definition excludes client provenance;
23. implementation metadata retains client/physical provenance;
24. enabled client export executes bound physical callable;
25. equivalent SERVER + CLIENT definitions coexist;
26. divergent same-ID definition fails before mutation;
27. reconnect reuses logical definition;
28. conflicting implementation-ID reuse rejected;
29. capability.register ACK/READY unchanged;
30. request fingerprint/version fence unchanged;
31. reconciliation unchanged;
32. capability list/get returns canonical definition;
33. Web Researcher still sees only web.search/read/read_many;
34. Direct Chat policy unchanged;
35. full CI green.

---

# 21. Parallel-agent ownership

T8 may parallelize only after T8-A is frozen.

## T8-CONTRACT

Owns:

~~~text
tools/v1/_shared/metadata.py
tools/v1/_shared/__init__.py
tools/v1/test/test_shared_metadata.py
~~~

Phase: T8-A.

Must not edit SE/CL.

## T8-SERVER

Owns:

~~~text
se/src/runtimes/capability/local_tool_loader.py
tools/v1/web_tool/config.py
tools/v1/test/test_web_tool.py
se/tests/architecture/test_local_tool_loader.py
se/tests/architecture/test_t7_web_metadata_v2.py
~~~

Phases: T8-B, T8-C.

Must not edit CL or client registration service.

## T8-CLIENT

Owns:

~~~text
cl/src/loader/local_tools.py
cl/src/core/capability_runtime.py
cl/config/setting.json
cl/tests/test_t8_metadata_v2_client_loader.py
cl/tests/test_realtime_dispatcher_contract.py
~~~

Conditional: cl/src/loader/registry.py.

Phases: T8-D, T8-E.

Must not change routing or SE catalog semantics.

## T8-REGISTRATION

Owns:

~~~text
se/src/runtimes/capability/registration.py
se/tests/architecture/test_phase6_5_client_registration.py
se/tests/architecture/test_t8_metadata_v2_convergence.py
~~~

Phase: T8-F.

Must not edit CapabilityCatalog key model or routing.

## T8-GATE

Owns:

~~~text
cross-workstream regression
integration/e2e validation
full CI
T8 completion document
Issue #7 updates
~~~

Phases: T8-G, T8-H.

Gate reviewer must not silently patch production code.

---

# 22. Dependency graph

~~~text
T8-A CONTRACT
    |
    +---------------------+
    |                     |
    v                     v
T8-B SERVER          T8-D CLIENT
    |                     |
    v                     v
T8-C WEB             T8-E CLIENT POLICY
    |                     |
    +----------+----------+
               |
               v
        T8-F REGISTRATION
               |
               v
           T8-G GATE
               |
               v
           T8-H CLOSE
~~~

T8-D may develop in parallel with T8-B/C after T8-A.

T8-E remains default-disabled.

No non-empty repository-default V2 client allowlist may be committed before T8-F is green.

---

# 23. Issue coordination protocol

Issue #7 is the durable coordination surface.

Before starting/resuming:

1. re-read Issue #7;
2. re-read this plan;
3. verify branch HEAD;
4. inspect active claims;
5. post a workstream claim.

Markers:

~~~text
[CLAIM]
[PROGRESS]
[BLOCKED]
[HANDOFF]
[DONE]
[AUDIT]
~~~

Header:

~~~text
[MARKER] T8-<workstream> @ <observed HEAD>
~~~

A HANDOFF must include:

- current HEAD;
- files changed;
- invariants established;
- tests run;
- unresolved findings;
- exact next action;
- whether scope is safe for another agent.

Cross-scope edits require HANDOFF or BLOCKED before changing another workstream.

---

# 24. Stop conditions

Return to contract review if implementation appears to require:

- changing CapabilityInvocation.capability_id;
- changing physical ToolResult identity;
- changing Web physical action names;
- changing T6 Web security/fetch/runtime;
- keeping both V2 dialects as final authorities;
- creating a third V2 shape;
- exposing bound fields publicly;
- V1 wildcard implicitly enabling V2;
- default client Web advertisement;
- routing-priority changes;
- client overwrite of divergent existing logical definition;
- raw CapabilityDefinition equality as cross-location semantic equality despite provenance differences;
- multi-version coexistence;
- catalog key redesign;
- R7 continuation/reconciliation changes;
- MCP lifecycle changes;
- migration of unrelated V1 tools.

---

# 25. T8 exit gate

T8 completes only when:

~~~text
ONE canonical V2:
  manifest_version="2.0"
  exports
  bind
  input_schema
  output_schema
~~~

and no final authority remains for:

~~~text
metadata_version=2
capabilities
binding
parameters-as-export-schema
~~~

Web remains:

~~~text
web.search
  -> web_tool.run(action="search", ...)

web.read
  -> web_tool.run(action="scrape", ...)

web.read_many
  -> web_tool.run(action="scrape_many", ...)
~~~

Default placement:

~~~text
Web SERVER implementations: present
Web CLIENT implementations: absent
~~~

Cross-location convergence:

~~~text
same logical ID + same logical contract
    -> multiple implementations allowed

same logical ID + different logical contract
    -> whole registration rejected before mutation
~~~

Version semantics:

~~~text
one canonical logical version per capability_id
~~~

And all of these remain true:

~~~text
T6 runtime/security unchanged
R7 continuation/reconciliation unchanged
V1 tools unchanged
MCP unchanged
full CI green
~~~

---

# 26. T8 → T9 handoff candidates

T9 requires a fresh audit.

Candidates only:

- aliases/deprecation;
- legacy physical-name compatibility aliases;
- multi-version catalog identity;
- semver negotiation/selection;
- version-aware Agent manifests;
- File/Glob/Terminal/Window/Desktop canonical logical-export migration;
- generalized package placement;
- common logical-contract hashing beyond Tools V1;
- capability contract hashes/introspection;
- cross-provider/cross-MCP convergence.

None belong to T8 without a fresh boundary audit.

---

# 27. Frozen phase state

~~~text
T1-T6  CLOSED / FROZEN
T7     CLOSED / GREEN / AUDIT-APPROVED / FROZEN @ adea67c3
T8     IMPLEMENTATION PLAN FROZEN / PRODUCTION CODE NOT STARTED
T9     NOT AUDITED
~~~

## NEXT EXACT ACTION

1. Verify branch HEAD still descends cleanly from adea67c3.
2. Re-read Issue #7 and current claims.
3. Start with [CLAIM] T8-CONTRACT @ <HEAD>.
4. Implement T8-A exactly.
5. After T8-A, T8-SERVER and T8-CLIENT may proceed in parallel.
6. Complete T8-F before any non-empty repository-default V2 client placement.
7. Run T8-G/H gates.
8. Audit T8→T9 before any T9 code.
