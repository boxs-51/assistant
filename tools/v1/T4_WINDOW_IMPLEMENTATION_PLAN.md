# T4 Window Tool — Exact T3→T4 Boundary Audit & T4-A→T4-G Implementation Plan

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Audit baseline HEAD: `930d4bc39b86a0e7dd03c359aa3178406a3bf2ae`  
T3 status: COMPLETE + FROZEN  
Scope: Window Tool only  
Mode: **AUDIT + IMPLEMENTATION PLAN ONLY — NO T4 PRODUCTION CODE IMPLEMENTED**

---

# 1. T3 → T4 boundary decision

T3 is closed.

T4 owns only:

```text
tools/v1/window_tool.py
tools/v1/test/test_window_tool.py
```

T4 may add one focused test module only if import-safety tests are clearer in isolation:

```text
tools/v1/test/test_window_import_safety.py
```

T4 documentation:

```text
tools/v1/T4_WINDOW_IMPLEMENTATION_PLAN.md
tools/v1/T4_WINDOW_COMPLETION.md             # only after implementation is green
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md         # status only
```

T4 MUST NOT modify:

```text
tools/v1/_shared/**
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
tools/v1/test/test_terminal_tool.py
tools/v1/test/test_terminal_process_tree.py
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
```

T1/T2/T3 are frozen dependencies, not editable surfaces.

---

# 2. Current dependency baseline

Repository dependency:

```text
PyWinCtl==0.4.1
```

The pinned API exposes the primitives needed for this design:

```text
getAllWindows()
getWindowsWithTitle(...)
Window.getHandle()
Window.getPID()
Window.getAppName()
Window.getClientFrame()

Window.close()
Window.minimize(wait=True)
Window.maximize(wait=True)
Window.restore(wait=True)
Window.activate(wait=True)
```

Therefore T4 does not need platform-specific Win32/X11/macOS code to invent a window identity.

T4 treats PyWinCtl handles/PIDs as **session-local selectors**, not durable identifiers across reboots/process recreation/handle reuse.

---

# 3. Consumer blast radius

Repository search found direct Window Tool consumers in:

```text
tools/v1/test/test_window_tool.py
tools/v1/live/live_integration_real_machine.py
```

The live harness currently calls:

```text
list
find
get_geometry
focus
minimize
maximize
restore
close
```

and still asserts legacy presentation strings such as `"Thành công"`.

Live harness migration remains T8 and MUST NOT be changed in T4.

T4 must nevertheless implement `restore`, so the live harness no longer references an undefined physical action.

No SE/CL production call-site needs modification.

---

# 4. Current Window Tool findings

The current implementation:

- imports `pywinctl` at module import time;
- returns strings/lists/dicts rather than canonical ToolResult;
- finds by title and mutates `windows[0]`;
- cannot distinguish duplicate-title windows safely;
- exposes no stable selector;
- does not implement `restore`;
- accesses geometry properties outside a complete backend error boundary;
- returns success after `close()` request without verifying window death;
- mixes expected operational failures with Vietnamese presentation strings;
- catches broad backend exceptions and embeds arbitrary `str(e)`;
- list/find return titles only, losing PID/handle identity;
- has no hard query/result bounds.

---

# 5. P0-WN1 — title-only mutation can affect the wrong window

Current mutation pattern:

```python
windows = getWindowsWithTitle(...)
win = windows[0]
win.activate() / close() / minimize() / maximize()
```

If multiple windows share or contain the same title, the selected target depends on backend ordering.

This is a side-effect correctness violation.

T4 invariant:

```text
side-effecting action
    ↓
target resolution
    ↓
0 matches → NOT_FOUND
1 match   → execute
2+ matches → AMBIGUOUS_TARGET
```

T4 never mutates an arbitrary first match.

---

# 6. P0-WN2 — dependency import is not lazy

Current module executes:

```python
try:
    import pywinctl as pwc
except Exception:
    pwc = None
```

at import time.

Even though exceptions are caught, importing an optional GUI/platform package at module import can trigger environment-specific work or failure before any window action is requested.

T4 freeze:

- `import tools.v1.window_tool` must not import PyWinCtl;
- PyWinCtl is loaded lazily inside a private loader;
- loader failure maps to `DEPENDENCY_UNAVAILABLE`;
- `KeyboardInterrupt`/`SystemExit` are not swallowed;
- successful Python module import has no GUI/window enumeration side effect.

---

# 7. P1-WN3 — titles are not stable identities

Titles can:

- be duplicated;
- change over time;
- include document/file state;
- be localized.

T4 returns a structured selector derived from backend identity:

```json
{
  "window_handle": 123456,
  "pid": 4321
}
```

A handle+PID pair is the strongest T4 selector.

A handle without PID is accepted when the backend cannot provide PID, but is explicitly weaker.

The pair is **session-local only**.

T4 does not promise persistence after the target process/window is destroyed and recreated.

---

# 8. Exact selector inputs

Canonical target inputs:

```text
title_query: str | None
window_handle: int | None
pid: int | None
```

Compatibility aliases accepted only by `execute()`:

```text
title
query
handle
```

Canonical metadata uses only:

```text
title_query
window_handle
pid
```

Validation:

```text
window_handle:
  exact int
  bool rejected
  1 .. 2^64-1

pid:
  exact int
  bool rejected
  1 .. 2^31-1
```

At least one target selector is required for:

```text
get_geometry
focus
close
minimize
maximize
restore
```

`find` requires `title_query`.

`list` requires no target.

---

# 9. Exact target-resolution algorithm

## 9.1 Handle supplied

When `window_handle` is supplied:

1. enumerate windows with `getAllWindows()`;
2. filter exact `getHandle()`;
3. if `pid` supplied, require exact `getPID()`;
4. if `title_query` supplied, additionally require case-insensitive containment;
5. apply exact-cardinality rules.

The handle is never interpreted as a list index.

## 9.2 PID supplied without handle

1. enumerate all windows;
2. filter exact PID;
3. optionally narrow by `title_query`;
4. 0 -> NOT_FOUND;
5. 1 -> target;
6. >1 -> AMBIGUOUS_TARGET.

A PID is not assumed to represent a single window.

## 9.3 Title only

Use current compatibility semantics:

```text
CONTAINS + IGNORECASE
```

0 -> NOT_FOUND  
1 -> target  
>1 -> AMBIGUOUS_TARGET for single-target actions.

## 9.4 Ambiguity details

Error details are bounded:

```json
{
  "candidate_count": 3,
  "candidates": [
    {
      "window_handle": 100,
      "pid": 20
    }
  ]
}
```

Return at most:

```text
MAX_AMBIGUITY_CANDIDATES = 10
```

No unbounded candidate dump.

---

# 10. Stable descriptor shape

Every list/find/target result uses the same descriptor shape:

```json
{
  "selector": {
    "window_handle": 123456,
    "pid": 4321
  },
  "title": "Editor - file.txt",
  "title_truncated": false,
  "app_name": "Editor",
  "app_name_truncated": false
}
```

Rules:

- `window_handle` is required for a normal descriptor;
- `pid` may be null only if backend PID retrieval is unavailable for that window;
- `app_name` may be null;
- title/app display fields are bounded;
- selector values are not derived from truncated text.

If an essential handle cannot be obtained, descriptor construction fails with `WINDOW_PROPERTY_FAILED`.

A failure to read optional app name does not make the window unusable; `app_name=null`.

---

# 11. Hard bounds

Freeze:

```text
WINDOW_TOOL_VERSION = "2.0.0"

MAX_TITLE_QUERY_CHARS = 512
MAX_WINDOW_TITLE_CHARS = 4_096
MAX_APP_NAME_CHARS = 1_024

MAX_WINDOWS_ENUMERATED = 2_000
MAX_AMBIGUITY_CANDIDATES = 10

WINDOW_RESULTS:
  default = 100
  minimum = 1
  maximum = 500

CLOSE_VERIFY_TIMEOUT_SECONDS = 2.0
CLOSE_VERIFY_POLL_SECONDS = 0.05
```

No environment variable disables these limits.

If a backend enumeration returns more than `MAX_WINDOWS_ENUMERATED`, T4 fails rather than silently selecting from an incomplete nondeterministic subset.

---

# 12. Enumeration/result determinism

For list/find:

1. backend result count must be <= `MAX_WINDOWS_ENUMERATED`;
2. build stable descriptors;
3. sort deterministically by:

```text
(
  title.casefold(),
  pid or -1,
  window_handle
)
```

4. return first `max_results`;
5. set `meta.truncated=true` only when additional result records exist.

Backend enumeration order is not part of the public contract.

---

# 13. Display-field truncation

Window/application names can theoretically be unbounded.

T4 does not fail the action merely because display text is long.

Instead:

```text
title:
  return at most MAX_WINDOW_TITLE_CHARS
  title_truncated=true when shortened

app_name:
  return at most MAX_APP_NAME_CHARS
  app_name_truncated=true when shortened
```

`meta.truncated` is reserved for omitted window records, not per-field text shortening.

---

# 14. list result contract

Identity:

```text
tool = window_tool
action = list
version = 2.0.0
```

Data:

```json
{
  "returned_count": 2,
  "total_count": 2,
  "windows": [
    {
      "selector": {
        "window_handle": 100,
        "pid": 200
      },
      "title": "App",
      "title_truncated": false,
      "app_name": "App",
      "app_name_truncated": false
    }
  ]
}
```

No windows is successful:

```text
ok=true
returned_count=0
total_count=0
windows=[]
```

No presentation string is used.

---

# 15. find result contract

Identity:

```text
action = find
```

Data:

```json
{
  "returned_count": 2,
  "total_count": 2,
  "windows": [...]
}
```

No match is successful for `find`:

```text
ok=true
windows=[]
```

This differs from single-target actions, where no match is `NOT_FOUND`.

---

# 16. P1-WN4 — geometry reads escape the error boundary

Current code constructs:

```python
result = {
    "left": win.left,
    ...
}
```

before the client-area try/except.

Any backend/property failure can escape the intended operational contract.

T4 wraps all required geometry reads in one backend property boundary.

Required overall geometry:

```text
left
top
width
height
right
bottom
```

If any required overall geometry field fails:

```text
WINDOW_PROPERTY_FAILED
```

---

# 17. Geometry result contract

Single-target selection rules apply.

Data:

```json
{
  "window": { "...descriptor..." },
  "overall": {
    "left": 100,
    "top": 100,
    "width": 400,
    "height": 500,
    "right": 500,
    "bottom": 600
  },
  "client_area": {
    "left": 105,
    "top": 130,
    "width": 390,
    "height": 460,
    "right": 495,
    "bottom": 590
  },
  "frame_elements": {
    "titlebar_height": 30,
    "border_left": 5,
    "border_right": 5,
    "border_bottom": 10
  }
}
```

Use pinned PyWinCtl `getClientFrame()` as the canonical client-frame API.

Client frame is optional.

If client-frame retrieval fails:

```text
client_area = null
frame_elements = null
ok = true
meta.warnings += ["client geometry unavailable"]
```

Overall geometry failure is not optional.

All geometry numbers must be exact ints; bool is invalid.

---

# 18. Geometry normalization

T4 verifies internal consistency before returning:

```text
width >= 0
height >= 0
right == left + width
bottom == top + height
```

If backend exposes an inconsistent rectangle, recompute:

```text
right = left + width
bottom = top + height
```

for the normalized public shape.

For client frame returned as left/top/right/bottom:

```text
width = max(0, right - left)
height = max(0, bottom - top)
```

Frame-element values may be negative on unusual decorations; T4 reports backend-derived differences rather than silently clamping them.

---

# 19. P1-WN5 — restore is missing

Current live harness calls:

```text
action="restore"
```

but WindowTool does not implement it.

T4 adds canonical action:

```text
restore
```

and compatibility aliases:

```text
restore_window
```

Canonical metadata enum must include `restore`.

No live harness edit is needed in T4.

---

# 20. Mutation result contract

Actions:

```text
focus
minimize
maximize
restore
```

all use exact-cardinality selection.

Success data:

```json
{
  "window": { "...descriptor..." },
  "confirmed": true
}
```

No human success message is mixed into terminal data.

---

# 21. Action confirmation semantics

Pinned PyWinCtl methods support a wait mode for:

```text
activate(wait=True)
minimize(wait=True)
maximize(wait=True)
restore(wait=True)
```

T4 uses `wait=True`.

A false return is not reported as success.

It becomes:

```text
WINDOW_OPERATION_NOT_CONFIRMED
```

Exceptions from the isolated backend method call become:

```text
WINDOW_OPERATION_FAILED
```

T4 does not add its own extra polling loop for these four operations because the pinned backend already supplies wait-confirmation semantics.

---

# 22. Focus semantics

Current behavior restores a minimized window before activation.

T4 preserves that useful behavior safely:

```text
read isMinimized
    ↓ true
restore(wait=True)
    ↓
activate(wait=True)
```

If restore cannot be confirmed, activation is not attempted.

If `isMinimized` property itself cannot be read:

```text
WINDOW_PROPERTY_FAILED
```

No implicit first-match target is allowed.

---

# 23. P1-WN6 — close reports request as success

Current:

```python
win.close()
return "success"
```

does not prove closure.

The pinned backend documents `close()` as returning a boolean but also notes that application dialogs may prevent closure.

T4 therefore verifies observable post-state.

---

# 24. Close verification state machine

Single-target selection first.

Then:

```text
capture descriptor
      ↓
win.close()
      ↓
poll win.isAlive
      ↓
false → success
true until deadline → WINDOW_CLOSE_NOT_CONFIRMED
property error → WINDOW_PROPERTY_FAILED
```

Polling:

```text
timeout = 2.0 s
interval = 0.05 s
```

The boolean returned by `close()` is advisory.

The final `isAlive == false` state is authoritative.

This permits a backend `close()` return race while still preventing false success.

Success data:

```json
{
  "window": { "...pre-close descriptor..." },
  "closed": true
}
```

The tool does not attempt force termination of the owning process.

---

# 25. Close non-goal

T4 close means:

```text
request normal window close
+ verify window no longer alive
```

It does NOT mean:

- kill process;
- discard unsaved changes;
- click confirmation dialogs;
- bypass application shutdown policy.

If an application refuses closure:

```text
WINDOW_CLOSE_NOT_CONFIRMED
```

is the correct result.

---

# 26. Lazy dependency loader

Recommended private helper:

```text
_load_backend()
```

Implementation:

- use `importlib.import_module("pywinctl")`;
- no module-level PyWinCtl import;
- catch ordinary import/backend-initialization `Exception` only inside loader;
- map failure to `DEPENDENCY_UNAVAILABLE`;
- error details include only bounded `exception_type`;
- do not emit installation instructions as terminal data.

No GUI/window call occurs during module import.

---

# 27. Backend-call isolation

Recommended helpers:

```text
_backend_enumerate_all(...)
_backend_find_title(...)
_window_handle(...)
_window_pid(...)
_window_app_name(...)
_window_title(...)
_window_descriptor(...)
```

Backend exceptions are caught only around isolated third-party operations.

Do not place one giant broad `try/except Exception` around the entire public method.

This preserves programmer errors while normalizing backend operational failures.

---

# 28. Stable error codes

Freeze:

```text
INVALID_ARGUMENT
DEPENDENCY_UNAVAILABLE
NOT_FOUND
AMBIGUOUS_TARGET

WINDOW_ENUMERATION_LIMIT
WINDOW_ENUMERATION_FAILED
WINDOW_PROPERTY_FAILED
WINDOW_OPERATION_FAILED
WINDOW_OPERATION_NOT_CONFIRMED
WINDOW_CLOSE_NOT_CONFIRMED
```

No-window result for `list/find` is successful.

No-window result for a single-target action is `NOT_FOUND`.

---

# 29. Ambiguity is a safety error, not a search error

Example:

```text
title_query = "Untitled"
matches = 3
action = close
```

Result:

```text
AMBIGUOUS_TARGET
```

with bounded selectors.

T4 MUST NOT:

- select first;
- select active window implicitly;
- choose the smallest handle;
- choose the newest/oldest PID;
- guess by geometry.

The caller must refine using handle/PID/title.

---

# 30. list/find vs single-target selector contract

Read-many actions:

```text
list
find
```

never reject merely because multiple results exist.

Single-target actions:

```text
get_geometry
focus
close
minimize
maximize
restore
```

require exactly one target after selector filtering.

This distinction is part of the public contract.

---

# 31. Legacy aliases

T4 preserves physical compatibility aliases in `execute()`:

```text
list_windows → list
find_windows/search → find
geometry/get_bounds/info → get_geometry
focus_window/activate → focus
close_window → close
restore_window → restore
```

Canonical metadata exposes only canonical action names.

Aliases are not future Metadata V2 projection names.

---

# 32. Wrapper functions

Keep compatibility wrappers:

```text
list_windows(...)
find_windows(...)
focus_window(...)
close_window(...)
```

Add:

```text
restore_window(...)
```

All wrappers return canonical ToolResult after T4.

No wrapper returns legacy strings/lists.

---

# 33. TOOL_METADATA boundary

T4 may correct legacy V1 metadata:

Actions:

```text
list
find
get_geometry
focus
close
minimize
maximize
restore
```

Inputs:

```text
title_query:
  maxLength = 512

window_handle:
  integer
  minimum = 1

pid:
  integer
  minimum = 1

max_results:
  integer
  minimum = 1
  maximum = 500
```

Description must state ambiguity-safe targeting.

T4 MUST NOT add:

```text
manifest_version
exports
expose_root
V2 bind
V2 output_schema
V2 per-export effects/idempotency
```

Those remain T7.

---

# 34. T4-A — Contract identity + lazy dependency + validation

Modify:

```text
tools/v1/window_tool.py
tools/v1/test/test_window_tool.py
```

Implement:

- `WINDOW_TOOL_VERSION = "2.0.0"`;
- hard constants;
- `_WindowToolError`;
- lazy `_load_backend()`;
- canonical ToolResult builders;
- action/query/handle/PID/max_results validation;
- metadata bounds;
- no PyWinCtl import at module import time.

Tests:

- import safety;
- missing dependency;
- invalid selector values;
- exact bool-vs-int rejection;
- overlong query;
- invalid action.

---

# 35. T4-B — Descriptor + deterministic enumeration + selector resolution

Implement:

- descriptor construction;
- handle/PID extraction;
- bounded title/app-name display;
- `MAX_WINDOWS_ENUMERATED`;
- deterministic sorting;
- title/handle/PID target resolution;
- exact-cardinality helper;
- bounded ambiguity details.

Tests:

- duplicate titles;
- handle selects exact second window;
- handle+PID mismatch;
- PID-only multiple-window ambiguity;
- deterministic backend-order independence;
- over-enumeration failure;
- descriptor JSON safety.

This phase closes P0-WN1.

---

# 36. T4-C — Structured list/find

Implement:

- `list` using window descriptors;
- `find` using case-insensitive contains compatibility;
- hard `max_results`;
- exact record truncation;
- successful empty results;
- canonical ToolResult.

Tests:

- empty list;
- multiple results;
- exact max vs max+1;
- deterministic sort;
- long title/app-name truncation;
- backend enumeration failure;
- no presentation strings.

---

# 37. T4-D — Geometry hardening

Implement:

- exact target selection;
- full overall-property error boundary;
- normalized overall rect;
- `getClientFrame()`;
- optional client/frame warning behavior;
- descriptor attached to geometry result.

Tests:

- normal geometry;
- no client frame;
- client frame exception -> warning success;
- overall property exception -> WINDOW_PROPERTY_FAILED;
- inconsistent right/bottom normalization;
- duplicate title -> AMBIGUOUS_TARGET and no geometry property read on arbitrary target.

---

# 38. T4-E — Focus/minimize/maximize/restore

Implement:

- safe unique target;
- `restore` public action;
- `wait=True` confirmation;
- focus minimized-window restore flow;
- false backend return -> WINDOW_OPERATION_NOT_CONFIRMED;
- isolated backend exception -> WINDOW_OPERATION_FAILED;
- structured mutation result.

Tests for each action:

- unique target success;
- duplicate-title rejection with zero mutation calls;
- backend false result;
- backend exception;
- focus restore-before-activate;
- focus aborts if restore not confirmed;
- restore dispatcher/wrapper.

---

# 39. T4-F — Verified close + compatibility dispatch

Implement close state machine:

```text
select unique
close request
bounded isAlive polling
verified false -> success
deadline -> failure
```

Also complete:

- action aliases;
- compatibility wrappers;
- selector aliases;
- structured invalid-action behavior.

Tests:

- close true + dies;
- close false + dies -> verified success;
- close true + stays alive -> WINDOW_CLOSE_NOT_CONFIRMED;
- close exception;
- isAlive property error;
- duplicate close target -> zero close calls;
- aliases route canonical selector correctly.

---

# 40. T4-G — Regression gate + completion

Focused:

```text
python -m pytest -q   tools/v1/test/test_window_tool.py   tools/v1/test/test_window_import_safety.py   tools/v1/test/test_shared_contracts.py   tools/v1/test/test_shared_limits.py   tools/v1/test/test_scope_boundary.py
```

If import-safety tests stay in `test_window_tool.py`, omit the optional file.

Then:

```text
python -m pytest -q tools/v1/test
```

Then full repository CI.

Only after green:

- create `T4_WINDOW_COMPLETION.md`;
- mark T4 complete in source-of-truth;
- freeze T4→T5.

T4-G does not modify Desktop code.

---

# 41. Required detailed regression matrix

## Dependency/import

1. importing `window_tool` does not import PyWinCtl;
2. lazy dependency success;
3. lazy dependency ImportError -> DEPENDENCY_UNAVAILABLE;
4. lazy dependency platform initialization exception -> DEPENDENCY_UNAVAILABLE;
5. KeyboardInterrupt/SystemExit not swallowed by dependency loader.

## Validation

6. invalid action;
7. blank find query;
8. overlong title query;
9. window_handle zero;
10. window_handle negative;
11. window_handle bool;
12. PID zero;
13. PID bool;
14. max_results zero;
15. max_results bool;
16. max_results over hard maximum;
17. target action with no selector.

## Enumeration/descriptor

18. handle+PID descriptor;
19. PID unavailable -> nullable PID;
20. optional app-name failure;
21. title truncation;
22. app-name truncation;
23. deterministic sorting;
24. exact N results -> truncated=false;
25. N+1 -> truncated=true;
26. enumeration > 2000 -> WINDOW_ENUMERATION_LIMIT;
27. enumeration backend exception.

## Targeting

28. unique title selection;
29. duplicate title ambiguity;
30. handle exact selection;
31. handle+PID exact selection;
32. handle+PID mismatch -> NOT_FOUND;
33. PID-only unique;
34. PID-only multiple -> AMBIGUOUS_TARGET;
35. handle + title narrowing;
36. ambiguity details capped at 10;
37. no mutation is called during ambiguous selection.

## list/find

38. list empty success;
39. list structured descriptors;
40. find empty success;
41. find multiple descriptors;
42. result is JSON-safe.

## geometry

43. overall geometry;
44. client geometry;
45. normalized right/bottom;
46. client error warning;
47. required property error;
48. geometry unique selector by handle/PID.

## focus

49. normal activate(wait=True);
50. minimized -> restore(wait=True) then activate;
51. restore false aborts activate;
52. activate false -> not confirmed;
53. property error;
54. backend operation exception.

## minimize/maximize/restore

55. minimize confirmed;
56. minimize false;
57. maximize confirmed;
58. maximize false;
59. restore confirmed;
60. restore false;
61. restore metadata/dispatcher present.

## close

62. close request + isAlive false;
63. backend close false but observed dead -> success;
64. backend close true but remains alive -> close-not-confirmed;
65. close method exception;
66. isAlive property exception;
67. bounded verification polling;
68. no process kill/force-close fallback.

## aliases/scope

69. `title` alias;
70. `query` alias;
71. `handle` alias;
72. action aliases;
73. wrapper ToolResult;
74. no `se` import;
75. no `cl` import;
76. T1/T2/T3 tests unchanged and green.

---

# 42. No default real-GUI test requirement

Unlike T3, T4 default CI does not require destructive or environment-sensitive real GUI interaction.

Reason:

- Linux CI may be headless/Wayland;
- macOS Accessibility permissions are external;
- Windows CI desktop-session behavior is runner-dependent.

T4 correctness is proven with deterministic backend fakes/mocks in the default suite.

The existing real-machine harness remains an opt-in T8 gate.

T4 must make that future harness semantically possible by implementing `restore` and structured selectors, but does not edit the harness now.

---

# 43. Platform limitations are explicit

T4 does not claim identical availability on all display servers.

Examples:

- Linux Wayland may not expose all windows;
- macOS may require Accessibility/AppleScript permissions;
- minimized-window enumeration can vary by backend/platform.

These backend limitations map to empty enumeration or structured backend operation errors; T4 does not fabricate missing windows.

No platform-specific bypass is added.

---

# 44. Security/authorization boundary

Window actions are external side effects.

T4 owns intrinsic safety:

- exact validation;
- bounded enumeration/output;
- deterministic target resolution;
- ambiguity rejection;
- operation confirmation;
- verified close semantics.

Future consumer layer owns:

- whether the agent/user may focus/close/minimize a window;
- HITL;
- permission scopes;
- capability visibility.

T4 does not add tool-local authorization.

---

# 45. Expected implementation diff

Production:

```text
M tools/v1/window_tool.py
```

Tests:

```text
M tools/v1/test/test_window_tool.py
A tools/v1/test/test_window_import_safety.py   # optional
```

Completion docs only after green:

```text
A tools/v1/T4_WINDOW_COMPLETION.md
M tools/v1/TOOLS_V1_CONTRACT_FREEZE.md
```

Current plan:

```text
A tools/v1/T4_WINDOW_IMPLEMENTATION_PLAN.md
```

Forbidden implementation diff:

```text
NO tools/v1/_shared/**
NO File/Glob/Terminal
NO Desktop/Web
NO live/**
NO se/**
NO cl/**
```

---

# 46. Completion invariants

T4 may be marked complete only if:

1. importing Window Tool does not import/initialize PyWinCtl;
2. missing backend dependency is deterministic;
3. no side-effect action uses arbitrary `windows[0]`;
4. duplicate-title mutation returns AMBIGUOUS_TARGET;
5. list/find expose reusable handle/PID selectors;
6. selector identity is documented as session-local;
7. handle/PID/title filters are deterministic;
8. enumeration/output is hard bounded;
9. list/find output ordering is deterministic;
10. `restore` is implemented;
11. geometry property failures cannot escape structured errors;
12. client geometry failure remains an optional warning;
13. focus/minimize/maximize/restore use backend confirmation;
14. close success means observed `isAlive == false`;
15. refused/unconfirmed close is not reported as success;
16. public actions return canonical ToolResult;
17. legacy aliases do not become Metadata V2 projections;
18. Metadata V2 remains deferred to T7;
19. no T1/T2/T3 implementation file changes;
20. no live/se/cl changes;
21. focused tests green;
22. all `tools/v1/test` green;
23. full repository CI green.

---

# 47. T4-A→T4-G order

```text
T4-A  Contract + lazy dependency + validation
  ↓
T4-B  Stable descriptor + selector resolution
  ↓
T4-C  Structured list/find + deterministic bounds
  ↓
T4-D  Geometry normalization/error boundary
  ↓
T4-E  focus/minimize/maximize/restore confirmation
  ↓
T4-F  verified close + aliases/wrappers
  ↓
T4-G  full regression + completion document
```

Do not implement close verification before unique target selection is complete.

Do not implement action aliases as a bypass around canonical validation.

---

# 48. Implementation verdict

T4 can be completed locally without reopening T1/T2/T3 or consumer runtimes.

Architecture:

```text
lazy PyWinCtl backend
        ↓
bounded enumeration
        ↓
stable descriptor
(handle + PID)
        ↓
exact selector
   ├── read-many: list/find
   └── single-target:
       geometry/focus/close/min/max/restore
        ↓
canonical ToolResult
```

The central safety invariant is:

```text
NO UNIQUE TARGET
      =
NO WINDOW SIDE EFFECT
```

**AUDIT STATUS:** COMPLETE  
**T3→T4 BOUNDARY:** FROZEN  
**T4 IMPLEMENTATION DESIGN:** IMPLEMENTED AS FROZEN  
**T4-A→T4-G PLAN:** COMPLETE  
**PRODUCTION CODE STATUS:** COMPLETE  
**FINAL CODE HEAD:** `65bfe19a4ddcc41e7cdc685e0844960efa6e9af2`  
**FINAL CI:** Architecture Baseline run `35752695843` — SUCCESS  
**COMPLETION RECORD:** `tools/v1/T4_WINDOW_COMPLETION.md`  
**NEXT ROADMAP PHASE:** T5 Desktop Tool boundary audit; no T5 code started.
