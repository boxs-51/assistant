# TV1-T9-A — Logical Export Contract & Regression Freeze

**Repository:** `boxs-51/assistant`  
**Issue:** #12  
**Implementation branch:** `work/tv1-t9-a-1ae243a7`  
**Base:** `1ae243a76bfda26628a348d3aad31592b649ee8c`  
**Status:** TV1-T9-A FROZEN — production migration not started

## 1. Scope

TV1-T9-A freezes the exact public Metadata V2 contracts for the 24 logical
exports that will replace the five remaining broad V1 capability roots:

```text
file_tool
find_by_glob
terminal_tool
window_tool
desktop_automation
```

It does not change any physical `run(...)` behavior.

Global export rules:

```text
manifest_version = "2.0"
expose_root = false
export.version = "1.0"
export.name = export.id
kind = "TOOL"
execution_mode = "ONE_SHOT"
required_scopes = []
required_permissions = []
output_schema = tool_result_schema({})
input_schema.type = "object"
input_schema.additionalProperties = false
```

Public logical inputs keep the existing physical parameter names. TV1-T9 does
not rename runtime arguments. Immutable dispatcher fields are injected only by
`bind`.

## 2. File exports

Shared schemas:

```text
single_file_paths:
  anyOf:
    - string minLength=1 maxLength=4096
    - array minItems=1 maxItems=1
      items: string minLength=1 maxLength=4096

multi_file_paths:
  anyOf:
    - string minLength=1 maxLength=4096
    - array minItems=1 maxItems=32
      items: string minLength=1 maxLength=4096

queries:
  anyOf:
    - string minLength=1 maxLength=2048
    - array minItems=1 maxItems=32
      items: string minLength=1 maxLength=2048

replacements:
  anyOf:
    - string maxLength=65536
    - array minItems=1 maxItems=32
      items: string maxLength=65536

encoding:
  string minLength=1 maxLength=64
```

### file.read

```text
bind:
  action = "read"

required:
  file_paths

properties:
  file_paths  = single_file_paths
  start_line  = integer 1..10_000_000
  num_lines   = integer 1..100_000
  encoding    = encoding
  max_chars   = integer 1..1_000_000

effects: READ
idempotency: IDEMPOTENT
base_risk: HIGH
danger_patterns: existing file danger patterns
```

### file.search

```text
bind:
  action = "search"

required:
  file_paths
  queries

properties:
  file_paths           = multi_file_paths
  queries              = queries
  use_regex            = boolean
  case_sensitive       = boolean
  max_results_per_file = integer 1..500
  encoding             = encoding

effects: READ
idempotency: IDEMPOTENT
base_risk: HIGH
danger_patterns: existing file danger patterns
```

### file.write

```text
bind:
  action = "write"
  mode   = "w"

required:
  file_paths
  content

properties:
  file_paths = single_file_paths
  content    = string
  encoding   = encoding

effects: WRITE
idempotency: IDEMPOTENT
base_risk: HIGH
danger_patterns: existing file danger patterns
```

No character max is added to `content`: the physical runtime enforces the
existing encoded-byte limit (`MAX_WRITE_CONTENT_BYTES`). TV1-T9 must not
replace that byte-sensitive rule with an inaccurate character limit.

### file.append

Same public schema as `file.write`.

```text
bind:
  action = "write"
  mode   = "a"

effects: WRITE
idempotency: NON_IDEMPOTENT
base_risk: HIGH
```

Physical ToolResult identity remains:

```text
tool   = "file_tool"
action = "write"
```

### file.replace

```text
bind:
  action = "replace"

required:
  file_paths
  queries
  replacements

properties:
  file_paths           = multi_file_paths
  queries              = queries
  replacements         = replacements
  use_regex            = boolean
  case_sensitive       = boolean
  max_results_per_file = integer 1..500
  encoding             = encoding

effects: WRITE
idempotency: UNKNOWN
base_risk: HIGH
danger_patterns: existing file danger patterns
```

Runtime cardinality rules between queries/replacements remain authoritative.

## 3. Glob export

### glob.find

```text
bind = {}

required:
  pattern

properties:
  pattern     = string minLength=1 maxLength=2048
  root_dir    = string minLength=1 maxLength=4096
  recursive   = boolean
  max_results = integer 1..5000

effects: READ
idempotency: IDEMPOTENT
base_risk: LOW
danger_patterns: []
```

Physical ToolResult identity:

```text
tool   = "find_by_glob"
action = "find"
```

The empty bind is intentional and must remain valid.

## 4. Terminal exports

Shared properties:

```text
command  = string minLength=1 maxLength=32768
cwd      = string minLength=1 maxLength=4096
encoding = string minLength=1 maxLength=64
timeout  = integer 1..3600
```

### terminal.run

```text
bind:
  action = "run"

required:
  command

properties:
  command
  timeout
  cwd
  encoding

effects:
  EXECUTE
  EXTERNAL_SIDE_EFFECT

idempotency: UNKNOWN
base_risk: HIGH
danger_patterns: existing terminal danger patterns
```

### terminal.launch

```text
bind:
  action = "launch"

required:
  command

properties:
  command
  cwd

effects:
  EXECUTE
  EXTERNAL_SIDE_EFFECT

idempotency: NON_IDEMPOTENT
base_risk: HIGH
danger_patterns: existing terminal danger patterns
```

`timeout` and `encoding` are intentionally absent from `terminal.launch`.

## 5. Window exports

Shared selector properties:

```text
title_query   = string minLength=1 maxLength=512
window_handle = integer 1..18446744073709551615
pid           = integer 1..2147483647
max_results   = integer 1..500
```

For single-target actions the public schema exposes
`title_query/window_handle/pid`; at least one selector must be present.
Multiple selectors may be combined and are interpreted conjunctively by the
existing physical resolver. Runtime still rejects zero matches and ambiguous
multi-match resolution.

### window.list

```text
bind: action="list"
required: []
properties: max_results
effects: READ
idempotency: IDEMPOTENT
base_risk: MEDIUM
```

### window.find

```text
bind: action="find"
required: [title_query]
properties:
  title_query
  max_results
effects: READ
idempotency: IDEMPOTENT
base_risk: MEDIUM
```

### window.geometry

```text
bind: action="get_geometry"
required selector condition: at least one of title_query/window_handle/pid
properties:
  title_query
  window_handle
  pid
effects: READ
idempotency: IDEMPOTENT
base_risk: MEDIUM
```

Physical ToolResult action remains `get_geometry`.

### window.focus

```text
bind: action="focus"
selector schema: same single-target selector contract
effects: EXTERNAL_SIDE_EFFECT
idempotency: UNKNOWN
base_risk: MEDIUM
```

### window.close

```text
bind: action="close"
selector schema: same single-target selector contract
effects: EXTERNAL_SIDE_EFFECT
idempotency: NON_IDEMPOTENT
base_risk: MEDIUM
```

### window.minimize / window.maximize / window.restore

Each uses the same selector schema.

```text
bind action = matching physical action
effects = EXTERNAL_SIDE_EFFECT
idempotency = UNKNOWN
base_risk = MEDIUM
```

## 6. Desktop exports

Existing hard bounds:

```text
coordinate              -1_000_000 .. 1_000_000
mouse click count        1 .. 100
scroll clicks            -10_000 .. 10_000, excluding 0
move duration            0.0 .. 30.0
drag duration            0.0 .. 30.0
text length              1 .. 32_768 chars
type interval            0.0 .. 1.0
key length               1 .. 64 chars
press count              1 .. 100
hotkey keys              1 .. 16
mouse button             left | middle | right
```

All Desktop exports:

```text
danger_patterns = []
effects for screen_info = READ
effects for all other exports = EXTERNAL_SIDE_EFFECT
```

### desktop.screen_info

```text
bind: action="get_screen_info"
required: []
properties: {}
idempotency: IDEMPOTENT
base_risk: LOW
```

### desktop.mouse_move

```text
bind: action="mouse_move"
required: [x, y]
properties:
  x = coordinate
  y = coordinate
  duration = number 0.0..30.0
idempotency: UNKNOWN
base_risk: LOW
```

### desktop.mouse_click

```text
bind: action="mouse_click"
required: []
properties:
  x = coordinate
  y = coordinate
  button = left|middle|right
  clicks = integer 1..100

cross-field runtime rule:
  x and y are either both omitted or both supplied

idempotency: NON_IDEMPOTENT
base_risk: HIGH
```

The pair constraint may be represented in JSON Schema or retained as a runtime
validation invariant, but the published schema must never imply that a
one-coordinate call is valid.

### desktop.mouse_drag

```text
bind: action="mouse_drag"
required: [x, y]
properties:
  x       = coordinate
  y       = coordinate
  start_x = coordinate
  start_y = coordinate
  button  = left|middle|right
  duration = number 0.0..30.0

cross-field runtime rule:
  start_x/start_y are both omitted or both supplied

idempotency: NON_IDEMPOTENT
base_risk: HIGH
```

### desktop.mouse_scroll

```text
bind: action="mouse_scroll"
required: [clicks]
properties:
  clicks = integer -10000..10000 AND != 0
idempotency: NON_IDEMPOTENT
base_risk: LOW
```

### desktop.type_text

```text
bind: action="type_text"
required: [text]
properties:
  text              = string minLength=1 maxLength=32768
  force_direct      = boolean
  restore_clipboard = boolean
  interval          = number 0.0..1.0
idempotency: NON_IDEMPOTENT
base_risk: HIGH
```

### desktop.press_key

```text
bind: action="press_key"
required: [key]
properties:
  key     = string minLength=1 maxLength=64
  presses = integer 1..100
idempotency: NON_IDEMPOTENT
base_risk: HIGH
```

### desktop.hotkey

```text
bind: action="hotkey"
required: [keys]
properties:
  keys = array minItems=1 maxItems=16
         items string minLength=1 maxLength=64
idempotency: NON_IDEMPOTENT
base_risk: HIGH
```

## 7. CLIENT placement freeze

After all T9-B/T9-C migrations, the exact default V2 allowlist is:

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

No Web capability is added to default CLIENT placement.

Legacy `allowed_local_tools=["*"]` remains a V1-only policy and does not select
canonical V2 exports.

## 8. command-reviewer freeze

The atomic T9-B target is:

```json
{
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
}
```

No Window/Desktop capability is added to the Agent by TV1-T9.

## 9. Physical identity freeze

Logical export identity must not alter the existing ToolResult envelope.

Representative mappings:

```text
file.read              -> file_tool / read
file.append            -> file_tool / write
glob.find              -> find_by_glob / find
terminal.run           -> terminal_tool / run
terminal.launch        -> terminal_tool / launch
window.geometry        -> window_tool / get_geometry
desktop.screen_info    -> desktop_automation / get_screen_info
desktop.mouse_click    -> desktop_automation / mouse_click
```

Physical package version remains implementation provenance. Logical export
version remains `1.0`.

## 10. Regression freeze

T9-B→T9-H must prove at minimum:

### Metadata/schema

1. all five manifests validate under `validate_tool_manifest_v2()`;
2. exactly 24 unique logical export IDs exist;
3. each export name equals its ID;
4. every input schema is strict with `additionalProperties=false`;
5. no public logical schema exposes `action`;
6. `file.write` exposes no `mode` and binds `mode=w`;
7. `file.append` exposes no `mode` and binds `mode=a`;
8. no input-schema property collides with a bind key;
9. Web logical IDs are unchanged.

### Loader/projection

10. physical roots are absent from SERVER capability IDs after migration;
11. physical roots are absent from CLIENT registry IDs after migration;
12. SERVER and CLIENT project identical logical definitions;
13. same logical definition may retain SERVER+CLIENT implementations;
14. definition divergence rejects atomically;
15. V2 wildcard remains rejected;
16. default CLIENT allowlist contains all 24 IDs and zero Web IDs;
17. command-reviewer resolves only the eight logical File/Glob/Terminal IDs.

### Execution equivalence

18. direct physical module calls still work;
19. immutable bind values cannot be overridden by callers;
20. `file.append` reaches physical `action=write, mode=a`;
21. `glob.find` proves empty-bind execution;
22. `window.geometry` reaches physical `get_geometry`;
23. `desktop.screen_info` reaches physical `get_screen_info`;
24. ToolResult `tool/action/version` physical identity remains unchanged;
25. side-effect execution tests use mocks/fakes rather than the user's desktop.

### Lifecycle/non-regression

26. T8 registration batch atomicity remains green;
27. reconnect ACK/READY remains green;
28. R6/R7 capability fingerprint/reconciliation fences remain green;
29. MCP regressions remain green;
30. no `tools/v1/live/**` or real-machine harness is introduced in T9.

## 11. Consumer audit conclusion

No generic T8 consumer defect was demonstrated during T9-A.

Existing authority already proves:

- Metadata V2 requires strict object input schemas;
- bind keys may not collide with public input properties;
- CLIENT bound handlers reject attempts to override immutable bind fields;
- canonical V2 CLIENT selection is exact-ID only.

Therefore T9-B should begin as a tool metadata/config/Agent-manifest migration.
Consumer production changes remain forbidden unless a red regression proves an
actual generic defect.

## 12. T9-A exit

```text
24 export IDs: FROZEN
logical versions: FROZEN
binds: FROZEN
effects/idempotency/risk: FROZEN
input schema inventory: FROZEN
CLIENT placement: FROZEN
command-reviewer target: FROZEN
ToolResult physical identity: FROZEN
regression matrix: FROZEN
consumer production patch: NOT JUSTIFIED
```

Next allowed phase is TV1-T9-B.
