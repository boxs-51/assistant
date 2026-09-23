# T4 Window Tool Completion Record

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Implementation-plan baseline: `10b440fdc761707bd74ca764aefe2b4f14381854`  
Final T4 code HEAD: `65bfe19a4ddcc41e7cdc685e0844960efa6e9af2`  
Status: **T4 COMPLETE — FROZEN**

## 1. Completion summary

T4 completes:

```text
tools/v1/window_tool.py
tools/v1/test/test_window_tool.py
```

The implementation adopts the T1 ToolResult contract and closes Window ambiguity, stable selector, geometry, restore, lazy-dependency and verified-close findings.

No T1/T2/T3 production code, live harness, SE or CL file was modified by T4 implementation.

## 2. Implementation commit

```text
65bfe19a4ddcc41e7cdc685e0844960efa6e9af2
feat(tools-v1): implement T4 ambiguity-safe window tool
```

Implementation diff:

```text
M tools/v1/window_tool.py
M tools/v1/test/test_window_tool.py
```

Forbidden implementation diff: `0`.

## 3. Lazy dependency

`window_tool.py` no longer imports PyWinCtl at module import time.

Backend loading is lazy through:

```text
importlib.import_module("pywinctl")
```

Expected import/platform initialization failures become:

```text
DEPENDENCY_UNAVAILABLE
```

Control-flow exceptions are not swallowed.

A subprocess regression proves importing `tools.v1.window_tool` does not import `pywinctl`.

## 4. Session-local selector

Descriptors expose:

```json
{
  "selector": {
    "window_handle": 123456,
    "pid": 4321
  },
  "title": "Editor",
  "title_truncated": false,
  "app_name": "Editor",
  "app_name_truncated": false
}
```

The handle/PID pair is session-local, not durable identity across process/window recreation.

PID may be null when backend PID retrieval is unavailable. A valid handle remains required for a normal descriptor.

## 5. Ambiguity-safe targeting

Single-target actions:

```text
get_geometry
focus
close
minimize
maximize
restore
```

now resolve with:

```text
0 matches  -> NOT_FOUND
1 match    -> execute
2+ matches -> AMBIGUOUS_TARGET
```

Central invariant:

```text
NO UNIQUE TARGET
      =
NO WINDOW SIDE EFFECT
```

Regression tests prove duplicate-title side-effect paths perform zero mutation calls.

Canonical target inputs:

```text
title_query
window_handle
pid
```

Compatibility aliases `title`, `query`, `handle` remain physical aliases only.

## 6. Bounds and deterministic enumeration

```text
MAX_TITLE_QUERY_CHARS     = 512
MAX_WINDOW_TITLE_CHARS    = 4096
MAX_APP_NAME_CHARS        = 1024

MAX_WINDOWS_ENUMERATED    = 2000
MAX_AMBIGUITY_CANDIDATES  = 10

max_results:
  default = 100
  minimum = 1
  maximum = 500
```

Ordering:

```text
title.casefold()
pid or -1
window_handle
```

Enumeration beyond the hard budget returns `WINDOW_ENUMERATION_LIMIT`.

List/find return structured descriptors, exact result truncation via `meta.truncated`, and successful empty results.

## 7. Geometry

Required overall geometry reads are inside a structured property boundary.

Required-property failures return:

```text
WINDOW_PROPERTY_FAILED
```

Public rectangle normalization:

```text
right = left + width
bottom = top + height
```

Client geometry uses `getClientFrame()`.

Optional client-frame failure returns success with:

```text
client_area = null
frame_elements = null
meta.warnings = ["client geometry unavailable"]
```

## 8. Confirmed operations

T4 adds the missing canonical action:

```text
restore
```

The following use backend confirmation:

```text
activate(wait=True)
minimize(wait=True)
maximize(wait=True)
restore(wait=True)
```

False confirmation:

```text
WINDOW_OPERATION_NOT_CONFIRMED
```

Backend exception:

```text
WINDOW_OPERATION_FAILED
```

Focus preserves restore-before-activate when minimized and aborts activation if restore is not confirmed.

## 9. Verified close

Close no longer succeeds merely because `close()` was called.

```text
resolve unique target
capture descriptor
close()
poll isAlive
false -> success
deadline -> WINDOW_CLOSE_NOT_CONFIRMED
```

Bounds:

```text
CLOSE_VERIFY_TIMEOUT_SECONDS = 2.0
CLOSE_VERIFY_POLL_SECONDS = 0.05
```

Observed `isAlive == false` is authoritative. T4 does not force-kill the owning process or bypass application dialogs.

## 10. Stable T4 error codes

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

No-window list/find is success. No-window single-target action is `NOT_FOUND`.

## 11. Compatibility actions

Canonical:

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

Physical aliases retained:

```text
list_windows
find_windows
search
geometry
get_bounds
info
focus_window
activate
close_window
restore_window
```

Wrappers now return canonical ToolResult.

The real-machine live harness remains deferred to T8 and was not modified.

## 12. Final CI evidence

Final code HEAD:

```text
65bfe19a4ddcc41e7cdc685e0844960efa6e9af2
```

Phase 5 Exit Gates:

```text
run 35752695674
40 passed
1.21s
success
```

Architecture Baseline:

```text
run 35752695843
```

Linux full repository suite:

```text
872 passed
1 skipped
14 warnings
62 subtests passed
75.70s
```

Windows client contracts:

```text
68 passed
11.36s
```

Workflow conclusion: `success`.

## 13. Scope evidence

Compare:

```text
base = 10b440fdc761707bd74ca764aefe2b4f14381854
head = 65bfe19a4ddcc41e7cdc685e0844960efa6e9af2
```

Implementation changed only:

```text
tools/v1/window_tool.py
tools/v1/test/test_window_tool.py
```

No changes to:

```text
tools/v1/_shared/**
File/Glob/Terminal production/tests
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
```

## 14. Non-goals

T4 does not add durable window IDs, force-process termination, dialog automation, platform-specific native adapters, authorization/HITL, Metadata V2 exports, live-harness migration or real-GUI CI requirements.

## 15. Completion verdict

```text
T0 Contract Freeze       COMPLETE
T1 Shared Foundation     COMPLETE
T2 File + Glob           COMPLETE
T3 Terminal              COMPLETE
T4 Window                COMPLETE
T4 ambiguity safety      COMPLETE
T4 restore               COMPLETE
T4 verified close        COMPLETE
T4 repository CI         GREEN
T4 scope invariant       GREEN
```

No open T4 P0/P1 was identified after final implementation and regression audit.

T4 is frozen. The next roadmap phase is T5 Desktop Tool; T5 code has not started.
