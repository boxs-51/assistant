# T1 Shared Standalone Tool Foundation — Exact Boundary Audit & Implementation Plan

Repository: boxs-51/assistant  
Branch: tools-v1-contract-freeze  
T0 baseline: fe6077b81add22f9fcffc9261d08c48a53f5cd8d  
T0 freeze commit: 586d851b576de672c7f25d4501de32c3fd6f21c6  
Scope: tools/v1/** only  
Mode: IMPLEMENTED — T1 COMPLETE

---

# 1. Purpose

T1 creates the smallest shared standalone foundation required by T2–T8.

T1 is not a migration phase. After T1:

- existing tools keep current behavior;
- existing TOOL_METADATA does not yet need Metadata V2;
- existing run(...) functions do not yet need the new result envelope;
- no physical tool is refactored;
- SE/CL remain untouched.

T1 exists so T2–T7 can adopt one already-tested contract.

---

# 2. Exact write boundary

T1 may add:

~~~text
tools/v1/_shared/
├── __init__.py
├── contracts.py
├── errors.py
├── limits.py
├── metadata.py
└── validation.py
~~~

T1-specific tests:

~~~text
tools/v1/test/
├── test_shared_contracts.py
├── test_shared_limits.py
├── test_shared_metadata.py
├── test_shared_validation.py
└── test_scope_boundary.py
~~~

Documentation:

~~~text
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md
tools/v1/T1_SHARED_FOUNDATION_IMPLEMENTATION_PLAN.md
~~~

T1 MUST NOT modify:

~~~text
tools/v1/desktop_tool.py
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
~~~

Existing production tool adoption is deferred to T2–T7. Live migration is T8.

---

# 3. Foundation architectural boundary

T1 is a pure library.

Allowed responsibilities:

- result/error contract construction;
- strict JSON-safety checks;
- numeric/text/collection limit mechanics;
- deterministic secret redaction helpers;
- Metadata V2 typed shapes and validation;
- projection/bind contract validation.

Forbidden responsibilities:

- execution dispatch;
- registry/catalog;
- policy/HITL/authorization;
- identity/session handling;
- network access;
- filesystem I/O;
- subprocess execution;
- browser/GUI lifecycle;
- background tasks;
- physical-tool imports;
- SE/CL imports.

The only acceptable non-stdlib dependency is lazy JSON Schema meta-validation from the repository's existing jsonschema package. Importing tools.v1._shared itself must not import jsonschema eagerly.

---

# 4. Boundary decisions

## 4.1 No generic execution wrapper

T1 MUST NOT add decorators/wrappers that catch arbitrary tool exceptions.

Reasons:

- cancellation must not become fake success/failure;
- programmer errors must remain distinguishable;
- each tool needs its own audited conversion boundary.

## 4.2 T1 defines limit mechanics, not tool-specific values

T1 defines immutable LimitSpec-like primitives and resolvers.

T2 chooses File/Glob limits.
T3 chooses Terminal limits.
T5 chooses Desktop limits.
T6 chooses Web limits.

## 4.3 No security inheritance in Metadata V2

Each export explicitly defines:

- effects;
- idempotency;
- execution mode;
- base risk;
- required scopes;
- required permissions;
- danger patterns.

Root compatibility metadata is not inherited.

## 4.4 Empty bind is valid

An export may use:

~~~python
"bind": {}
~~~

when the physical function already maps directly to one logical operation, e.g. find_by_glob.run -> glob.find.

## 4.5 Bound fields cannot be public

If bind contains:

~~~python
{"action": "write", "mode": "a"}
~~~

then action and mode cannot appear in projected public input_schema properties or required.

## 4.6 output_schema meaning

Metadata V2 output_schema describes the terminal value returned by the exported operation.

Because Tools V1 standardizes the terminal ToolResult envelope, T7 output schemas must describe the full envelope, not only nested data.

---

# 5. errors.py

T1 generic error codes:

~~~text
INVALID_ARGUMENT
DEPENDENCY_UNAVAILABLE
NOT_FOUND
AMBIGUOUS_TARGET
TIMEOUT
OUTPUT_LIMIT_EXCEEDED
PERMISSION_DENIED
OPERATION_FAILED
INTERNAL_ERROR
~~~

Cancellation is not a normal result error code.

Foundation exception hierarchy:

~~~text
ToolFoundationError
├── ToolContractError
├── ToolMetadataError
├── ToolLimitConfigError
└── ToolJsonSafetyError
~~~

These exceptions indicate malformed foundation/configuration contracts, not ordinary tool failures.

Error-code identifiers follow:

~~~text
^[A-Z][A-Z0-9_]*$
~~~

Tool-specific phases may add stable prefixed codes such as FILE_TOO_LARGE or WEB_URL_BLOCKED.

---

# 6. contracts.py

Responsibilities:

1. strict conceptual JSON type aliases;
2. ToolErrorPayload;
3. ToolResultMeta;
4. ToolResult;
5. pure success_result(...) builder;
6. pure failure_result(...) builder;
7. optional pure ToolResult JSON Schema builder.

Canonical success:

~~~json
{
  "ok": true,
  "tool": "file_tool",
  "action": "read",
  "data": {},
  "error": null,
  "meta": {
    "version": "2.0.0",
    "truncated": false,
    "warnings": []
  }
}
~~~

Canonical failure:

~~~json
{
  "ok": false,
  "tool": "file_tool",
  "action": "read",
  "data": null,
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "The requested file does not exist.",
    "retryable": false,
    "details": {}
  },
  "meta": {
    "version": "2.0.0",
    "truncated": false,
    "warnings": []
  }
}
~~~

Builder invariants:

- return fresh plain dicts;
- do not mutate caller data/details/meta;
- tool/action/version are non-empty;
- terminal value is strictly JSON-safe;
- warnings are copied;
- reserved meta fields cannot be overridden;
- no implicit timestamp;
- no random ID;
- no logging/I/O.

Strict JSON values exclude bytes, Path, sets, arbitrary objects and non-finite floats.

---

# 7. limits.py

Define an immutable numeric limit specification conceptually containing:

~~~text
name
default
minimum
maximum
~~~

Required invariants:

~~~text
minimum <= default <= maximum
maximum is mandatory
name is non-empty
bool is not accepted as int
NaN/Infinity are rejected
~~~

Resolution behavior:

~~~text
None -> default
explicit minimum -> accepted
explicit maximum -> accepted
below minimum -> rejected
above maximum -> rejected
0 when min=1 -> rejected
-1 -> rejected
True for integer -> rejected
~~~

T1 must eliminate the semantic anti-pattern:

~~~python
value if value and value > 0 else default
~~~

because invalid explicit input must not silently change behavior.

No unlimited/environment-variable bypass belongs in T1.

---

# 8. validation.py

## 8.1 Strict JSON safety

Recursive validator rejects:

- non-string dict keys;
- bytes/bytearray;
- pathlib.Path;
- sets/frozensets;
- arbitrary objects;
- NaN/Infinity;
- self-referential structures.

Errors should include logical location such as:

~~~text
$.data.items[2].payload
~~~

## 8.2 Pure common validators

May include helpers for:

- non-empty strings;
- bounded strings;
- string lists;
- unique string lists;
- mappings;
- finite numbers.

These raise foundation exceptions and do not construct ToolResult envelopes.

## 8.3 Structured secret redaction

At minimum recognize sensitive key names case-insensitively:

~~~text
password
passwd
secret
token
access_token
refresh_token
api_key
apikey
authorization
cookie
cookies
client_key
proxy_password
~~~

Redaction:

- preserves structure;
- uses one fixed redaction marker;
- recursively handles mappings/lists;
- never mutates the source value.

## 8.4 URL credential redaction

A URL such as:

~~~text
http://user:pass@proxy.example:8080
~~~

must not be emitted unchanged.

This helper will be required by Web T6.

## 8.5 No arbitrary text scrubber

T1 must not try to guess secrets inside arbitrary terminal/page/file text.

That is tool-specific policy.

---

# 9. metadata.py

Use lightweight typing/standard Python structures rather than Pydantic.

## 9.1 Root V2 required fields

~~~text
manifest_version = "2.0"
name
version
description
expose_root
exports
~~~

Legacy root fields may coexist for compatibility but are not semantic inheritance.

## 9.2 Export required fields

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

No silent security defaults.

## 9.3 Frozen vocabularies

Kind:

~~~text
TOOL
~~~

Execution modes allowed for executable tools:

~~~text
ONE_SHOT
STREAMING
LONG_RUNNING
~~~

CONTEXT_ONLY is rejected in tools/v1 Metadata V2.

Idempotency:

~~~text
IDEMPOTENT
DEDUPLICATED
NON_IDEMPOTENT
UNKNOWN
~~~

Effects:

~~~text
READ
WRITE
EXECUTE
EXTERNAL_SIDE_EFFECT
PRIVILEGED
~~~

Base risk:

~~~text
LOW
MEDIUM
HIGH
~~~

## 9.4 Identifier rules

Physical root name: lowercase snake_case.

Logical export ID: lowercase dotted namespace, for example:

~~~text
file.read
terminal.launch
desktop.mouse_click
web.read_many
~~~

For V2 Tools, export.name must equal export.id.

## 9.5 Uniqueness

Validator rejects duplicate:

- export IDs;
- effects;
- scopes;
- permissions.

## 9.6 Bind validation

bind:

- is a mapping;
- may be empty;
- is strictly JSON-safe;
- has non-empty string keys;
- cannot collide with input_schema properties;
- cannot appear in input_schema required;
- is immutable semantic configuration.

T1 validates binding metadata only; it does not apply bindings at runtime.

## 9.7 JSON Schema

Both input_schema and output_schema must be valid JSON Schema documents.

Validation order:

1. deterministic local structural checks;
2. lazy import jsonschema;
3. complete schema meta-validation;
4. missing jsonschema during requested full validation -> deterministic ToolMetadataError, never silent skip.

Input schema additional requirements:

~~~text
type = object
properties = mapping
required = list
additionalProperties = false
~~~

Every required key must exist in properties.

Output schema top-level:

~~~text
type = object
~~~

and must represent terminal ToolResult semantics.

## 9.8 danger_patterns

Each danger pattern must compile as a valid Python regex.

Malformed regex rejects the manifest.

---

# 10. _shared/__init__.py

Keep it side-effect free.

It may re-export stable foundation primitives.

It MUST NOT:

- import physical tools;
- create singleton objects;
- scan files;
- auto-validate all manifests;
- initialize GUI/Web/process resources;
- eagerly import jsonschema.

Importing tools.v1._shared must perform no external action.

---

# 11. Scope guard

Add:

~~~text
tools/v1/test/test_scope_boundary.py
~~~

Using Python AST, scan production Python sources under tools/v1 and reject imports rooted at:

~~~text
se
cl
~~~

It should also verify:

- tools.v1._shared imports without physical tool imports;
- _shared has no network, subprocess, GUI or browser dependencies;
- generated/live log artifacts are excluded from scanning.

This test protects T2–T8 dependency direction.

---

# 12. Exact test matrix

## test_shared_contracts.py

Required:

1. exact success envelope;
2. exact failure envelope;
3. success has error=null;
4. failure has data=null and error object;
5. fresh containers each call;
6. caller values not mutated;
7. warnings copied;
8. invalid tool/action/version rejected;
9. invalid error code rejected;
10. non-JSON-safe data rejected;
11. non-finite float rejected;
12. reserved meta cannot be overridden;
13. generated result schema accepts valid envelope;
14. generated result schema rejects malformed envelope.

## test_shared_limits.py

Required:

1. None uses default;
2. explicit min accepted;
3. explicit max accepted;
4. below min rejected;
5. above max rejected;
6. zero rejected when not valid;
7. negative rejected;
8. bool-as-int rejected;
9. invalid LimitSpec rejected;
10. hard maximum mandatory;
11. NaN/Infinity rejected for float;
12. invalid explicit input never silently falls back.

## test_shared_validation.py

Required:

1. nested JSON-safe value accepted;
2. bytes rejected with path;
3. Path rejected with path;
4. set rejected;
5. non-string key rejected;
6. recursive container rejected;
7. NaN/Infinity rejected;
8. nested sensitive values redacted;
9. source not mutated;
10. URL credentials redacted;
11. ordinary URL preserved;
12. unrelated fields not over-redacted.

## test_shared_metadata.py

Required:

1. canonical valid manifest accepted;
2. wrong manifest_version rejected;
3. required root field missing rejected;
4. empty exports rejected;
5. duplicate export ID rejected;
6. name != id rejected;
7. invalid export ID rejected;
8. invalid kind rejected;
9. CONTEXT_ONLY rejected;
10. invalid idempotency rejected;
11. invalid effect rejected;
12. duplicate effect rejected;
13. invalid base risk rejected;
14. missing explicit security field rejected;
15. empty bind accepted;
16. non-empty bind accepted;
17. bind/property collision rejected;
18. bind/required collision rejected;
19. non-JSON-safe bind rejected;
20. malformed input schema rejected;
21. additionalProperties != false rejected;
22. required key not in properties rejected;
23. malformed output schema rejected;
24. invalid danger regex rejected;
25. duplicate scopes/permissions rejected;
26. legacy root fields do not become export inheritance;
27. missing jsonschema produces deterministic ToolMetadataError.

## test_scope_boundary.py

Required:

1. no tools/v1 production source imports se;
2. no tools/v1 production source imports cl;
3. tools.v1._shared import has no physical-tool side effects;
4. _shared has no network/file/subprocess/browser/GUI execution dependency.

---

# 13. Patch order

## T1-A — Skeleton + error vocabulary

Add:

~~~text
tools/v1/_shared/__init__.py
tools/v1/_shared/errors.py
~~~

No current tool imports them.

## T1-B — Validation + redaction

Add:

~~~text
tools/v1/_shared/validation.py
tools/v1/test/test_shared_validation.py
~~~

## T1-C — Result contracts

Add:

~~~text
tools/v1/_shared/contracts.py
tools/v1/test/test_shared_contracts.py
~~~

## T1-D — Limit mechanics

Add:

~~~text
tools/v1/_shared/limits.py
tools/v1/test/test_shared_limits.py
~~~

No tool-specific limit values.

## T1-E — Metadata V2 validator

Add:

~~~text
tools/v1/_shared/metadata.py
tools/v1/test/test_shared_metadata.py
~~~

No current TOOL_METADATA is migrated.

## T1-F — Scope guard + full regression

Add:

~~~text
tools/v1/test/test_scope_boundary.py
~~~

Run focused T1 tests and then all tools/v1 unit tests.

Only after green tests may TOOLS_V1_CONTRACT_FREEZE.md be updated to mark T1 complete.

---

# 14. Recommended dependency graph

~~~text
errors.py       -> stdlib
validation.py   -> errors + stdlib
limits.py       -> errors + stdlib
contracts.py    -> errors + validation + stdlib
metadata.py     -> errors + validation + contracts + lazy jsonschema
__init__.py     -> stable _shared modules only
~~~

No cycle is allowed.

---

# 15. Explicit non-goals

T1 does NOT:

- fix File unreadable-overwrite;
- bound Terminal stdout;
- create Window stable selectors;
- lazy-load Desktop controller;
- fix Web SSRF;
- alter Chromium launch flags;
- change Web event-loop ownership;
- change live tests;
- apply projection bindings at runtime;
- create registry/catalog;
- enforce authorization/HITL;
- enforce danger_patterns against actions;
- add retry systems;
- add telemetry;
- add background tasks;
- integrate Asset Store;
- modify SE/CL.

Those remain T2–T8 or future consumer work.

---

# 16. Backward-compatibility gate

Because no physical tool changes in T1, calls such as:

~~~python
from tools.v1.file_tool import run
run(action="read", ...)
~~~

must behave exactly as before T1.

Same for:

~~~text
desktop_tool
find_by_glob
terminal_tool
window_tool
web_tool
~~~

This is the main T1 blast-radius fence.

---

# 17. Exit commands

Focused T1 gate:

~~~text
python -m pytest -q   tools/v1/test/test_shared_contracts.py   tools/v1/test/test_shared_limits.py   tools/v1/test/test_shared_metadata.py   tools/v1/test/test_shared_validation.py   tools/v1/test/test_scope_boundary.py
~~~

Then full standalone regression:

~~~text
python -m pytest -q tools/v1/test
~~~

Live tests remain excluded.

T1 is complete only when:

1. focused foundation tests pass;
2. full tools/v1/test passes;
3. diff contains no path outside tools/v1/**;
4. existing production tool files remain byte-for-byte unchanged from T0;
5. no se/cl import exists under tools/v1;
6. importing tools.v1._shared causes no I/O, process, browser or GUI initialization;
7. Metadata V2 validation is deterministic;
8. no tool adopts the new result or metadata contract prematurely.

---

# 18. Expected T1 final diff

New foundation:

~~~text
A tools/v1/_shared/__init__.py
A tools/v1/_shared/contracts.py
A tools/v1/_shared/errors.py
A tools/v1/_shared/limits.py
A tools/v1/_shared/metadata.py
A tools/v1/_shared/validation.py
~~~

New tests:

~~~text
A tools/v1/test/test_shared_contracts.py
A tools/v1/test/test_shared_limits.py
A tools/v1/test/test_shared_metadata.py
A tools/v1/test/test_shared_validation.py
A tools/v1/test/test_scope_boundary.py
~~~

Documentation:

~~~text
A tools/v1/T1_SHARED_FOUNDATION_IMPLEMENTATION_PLAN.md
M tools/v1/TOOLS_V1_CONTRACT_FREEZE.md   # status only after green T1 implementation
~~~

Unchanged during T1:

~~~text
= tools/v1/file_tool.py
= tools/v1/find_by_glob.py
= tools/v1/terminal_tool.py
= tools/v1/window_tool.py
= tools/v1/desktop_tool.py
= tools/v1/web_tool/**
= tools/v1/live/**
~~~

Forbidden diff:

~~~text
NO se/**
NO cl/**
NO DB
NO R7
NO Asset Store
~~~

---

# 19. Verdict

T1 is a low-blast-radius additive foundation patch.

Correct shape:

~~~text
T1 = contract primitives + validators + conformance tests
~~~

Incorrect shape:

~~~text
T1 = foundation + migrate every tool
~~~

Keeping tool adoption outside T1 allows T2–T7 to close each tool's P0/P1 findings while adopting the standard contract exactly once.

AUDIT STATUS: COMPLETE  
T1 PLAN: IMPLEMENTED AS FROZEN  
CODE STATUS: COMPLETE  
FOCUSED HEAD TESTS: 32 tests + 22 subtests passed  
REMOTE FULL-SUITE EVIDENCE: success on production-foundation commit 4a8d66f2c004f918f3ea0d60f49bc1a4e456e410 (GitHub Actions run 35732654547)  
SCOPE STATUS: no physical tool, live, se/**, or cl/** modification  
NEXT ALLOWED PHASE: T2 — Filesystem Tools Completion audit/implementation.


---

# 20. Implementation completion record

Implemented on branch `tools-v1-contract-freeze`.

Production foundation:

~~~text
tools/v1/_shared/__init__.py
tools/v1/_shared/contracts.py
tools/v1/_shared/errors.py
tools/v1/_shared/limits.py
tools/v1/_shared/metadata.py
tools/v1/_shared/validation.py
~~~

Conformance tests:

~~~text
tools/v1/test/test_shared_contracts.py
tools/v1/test/test_shared_limits.py
tools/v1/test/test_shared_metadata.py
tools/v1/test/test_shared_validation.py
tools/v1/test/test_scope_boundary.py
~~~

The physical tool implementations remain unchanged from the T0/T1-plan boundary. T1 adoption by individual tools remains intentionally deferred to T2–T7.

