# T5 Desktop Tool — Exact T4→T5 Boundary Audit & T5-A→T5-G Implementation Plan

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Audit baseline HEAD: `d2472bc61530ca31932d667dab6b1caa68d59a2c`  
T4 status: COMPLETE + FROZEN  
Scope: Desktop Tool only  
Mode: **AUDIT + IMPLEMENTATION PLAN ONLY — NO T5 PRODUCTION CODE IMPLEMENTED**

---

# 1. T4 → T5 boundary decision

T4 is closed.

T5 owns only:

```text
tools/v1/desktop_tool.py
tools/v1/test/test_desktop_automation.py
```

T5 may add one focused import-safety test module if clearer:

```text
tools/v1/test/test_desktop_import_safety.py
```

T5 documentation:

```text
tools/v1/T5_DESKTOP_IMPLEMENTATION_PLAN.md
tools/v1/T5_DESKTOP_COMPLETION.md             # only after green implementation
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md          # status only
```

T5 MUST NOT modify:

```text
tools/v1/_shared/**
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
tools/v1/test/test_terminal_tool.py
tools/v1/test/test_terminal_process_tree.py
tools/v1/test/test_window_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
```

T1–T4 are frozen dependencies.

---

# 2. Consumer blast radius

Direct Desktop Tool consumers found in the repository:

```text
tools/v1/test/test_desktop_automation.py
tools/v1/live/live_integration_real_machine.py
```

Architecture compatibility also depends on the physical root name:

```text
se/tests/architecture/test_local_tool_loader.py
```

which asserts:

```text
desktop_automation
```

Therefore T5 MUST preserve:

```text
TOOL_METADATA["name"] == "desktop_automation"
ToolResult.tool == "desktop_automation"
```

Future logical capabilities remain T7:

```text
desktop.screen_info
desktop.mouse_move
desktop.mouse_click
desktop.mouse_drag
desktop.mouse_scroll
desktop.type_text
desktop.press_key
desktop.hotkey
```

No SE/CL patch is allowed in T5.

---

# 3. Current dependency baseline

Repository requirements include:

```text
PyAutoGUI==0.9.54
pyperclip==1.11.0
```

The repository does NOT declare:

```text
pynput
```

Current production code nevertheless imports:

```python
from pynput.keyboard import Controller as KeyboardController
```

and current tests inject a fake `pynput` into `sys.modules`.

T5 must therefore treat `pynput.keyboard` as optional, not guaranteed.

T5 MUST NOT modify requirements.

---

# 4. Current Desktop findings

Current implementation:

- imports PyAutoGUI, pyperclip and pynput at module import time;
- constructs `KeyboardController()` inside `DesktopAutomation.__init__`;
- creates a module-level default `DesktopAutomation()` during import;
- writes PyAutoGUI process-global `FAILSAFE` and `PAUSE` in the constructor;
- accepts unbounded clicks/presses/scroll/duration/interval/text/hotkey counts;
- accepts bool values where Python integer semantics can silently pass;
- echoes complete typed text in success strings;
- can echo backend exception text;
- restores clipboard only on the all-success path;
- hardcodes `ctrl+v` for clipboard paste;
- assumes direct Unicode can fall back to `pyautogui.write` even when pynput is unavailable;
- has no `mouse_drag` unit regression despite exposing it publicly;
- duplicates public metadata in `TOOL_METADATA` and `ACTIONS_METADATA`;
- returns presentation strings instead of canonical ToolResult;
- mutates global PyAutoGUI configuration even if no desktop action is ever called.

---

# 5. P0-D1 — module import/constructor are environment-sensitive

Current module import performs optional GUI imports.

Current module-level default object then runs:

```python
DesktopAutomation()
```

whose constructor may instantiate:

```text
KeyboardController()
```

and mutate PyAutoGUI globals.

In headless/unsupported environments, Desktop Tool may fail or alter process state before any action is requested.

T5 invariant:

```text
import tools.v1.desktop_tool
        ↓
NO PyAutoGUI import
NO pyperclip import
NO pynput import
NO keyboard controller construction
NO GUI query
NO global FAILSAFE/PAUSE mutation
```

The module-level default object may remain only because the constructor becomes side-effect free.

---

# 6. Lazy dependency architecture

Private loaders:

```text
_load_pyautogui()
_load_clipboard()
_load_unicode_keyboard()
```

## PyAutoGUI

Required by every public action.

Lazy import:

```text
importlib.import_module("pyautogui")
```

Ordinary import/platform initialization failure:

```text
DEPENDENCY_UNAVAILABLE
dependency = "pyautogui"
```

## pyperclip

Loaded only by Unicode clipboard typing.

Failure:

```text
DEPENDENCY_UNAVAILABLE
dependency = "pyperclip"
```

## pynput.keyboard

Loaded and Controller instantiated only for:

```text
type_text(..., force_direct=True)
with non-ASCII text
```

Because it is optional and not declared in requirements.

Failure:

```text
DEPENDENCY_UNAVAILABLE
dependency = "pynput.keyboard"
```

No loader swallows `KeyboardInterrupt` or `SystemExit`.

Loader errors expose bounded `exception_type`, not arbitrary exception strings.

---

# 7. Physical identity

Freeze:

```text
DESKTOP_TOOL_NAME = "desktop_automation"
DESKTOP_TOOL_VERSION = "2.0.0"
```

Canonical physical actions:

```text
get_screen_info
mouse_click
mouse_move
mouse_drag
mouse_scroll
type_text
press_key
hotkey
```

T5 does not rename the root to `desktop_tool`.

T7 may later hide the physical root behind logical `desktop.*` projections.

---

# 8. Hard limits

Freeze:

```text
MAX_COORD_ABS = 1_000_000
MAX_SCREEN_DIMENSION = 1_000_000

MAX_TEXT_CHARS = 32_768
MAX_KEY_CHARS = 64
MAX_HOTKEY_KEYS = 16

CLICK_COUNT:
  default = 1
  minimum = 1
  maximum = 100

PRESS_COUNT:
  default = 1
  minimum = 1
  maximum = 100

MAX_SCROLL_ABS = 10_000

MOVE_DURATION:
  default = 0.2 s
  minimum = 0
  maximum = 30 s

DRAG_DURATION:
  default = 0.5 s
  minimum = 0
  maximum = 30 s

TYPE_INTERVAL:
  default = 0.02 s
  minimum = 0
  maximum = 1 s

PYAUTOGUI_PAUSE:
  default = 0.1 s
  minimum = 0
  maximum = 5 s

CLIPBOARD_COPY_SETTLE_SECONDS = 0.05
CLIPBOARD_PASTE_SETTLE_SECONDS = 0.05
```

The clipboard settle constants are internal and not caller-controlled.

No environment variable disables these hard limits.

---

# 9. T1 primitives

T5 can use existing T1 helpers:

```text
success_result
failure_result

IntLimitSpec
FloatLimitSpec
resolve_int_limit
resolve_float_limit
```

No `_shared` change is needed.

Desktop-local validation and dependency helpers remain private to `desktop_tool.py`.

---

# 10. Exact numeric validation

Coordinates:

- exact int;
- bool rejected;
- range `[-MAX_COORD_ABS, +MAX_COORD_ABS]`;
- negative coordinates are valid for multi-monitor desktops.

Durations/interval/pause:

- int/float allowed;
- bool rejected;
- finite only;
- hard min/max via `FloatLimitSpec`.

Click/press counts:

- exact int;
- bool rejected;
- hard min/max.

Scroll:

- exact int;
- bool rejected;
- zero rejected;
- absolute value <= `MAX_SCROLL_ABS`.

---

# 11. Coordinate-pair semantics

## mouse_click

Allowed:

```text
x=None, y=None
or
x=int, y=int
```

Rejected:

```text
x provided, y omitted
x omitted, y provided
```

This avoids backend-dependent half-coordinate behavior.

## mouse_move

Both x and y required.

## mouse_drag

Target x/y both required.

Optional start coordinate is all-or-none:

```text
start_x=None, start_y=None
or
start_x=int, start_y=int
```

A partial start pair is `INVALID_ARGUMENT`.

---

# 12. Mouse button validation

Canonical buttons:

```text
left
right
middle
```

Input must be string.

T5 may normalize surrounding whitespace/case:

```text
" Left " -> "left"
```

Anything else:

```text
INVALID_ARGUMENT
```

---

# 13. Constructor contract

Keep physical constructor compatibility:

```python
DesktopAutomation(
    failsafe: bool = True,
    pause: float = 0.1,
)
```

Validation:

- failsafe must be exact bool;
- pause uses `PYAUTOGUI_PAUSE`.

Constructor only stores validated configuration.

It does NOT:

- import dependencies;
- instantiate keyboard controller;
- read screen state;
- mutate PyAutoGUI globals.

---

# 14. P1-D5 — scoped PyAutoGUI global state

PyAutoGUI exposes `FAILSAFE` and `PAUSE` as module-global settings.

To preserve legacy constructor semantics without persistent global mutation, T5 uses one module-level reentrant lock:

```text
_PYAUTOGUI_STATE_LOCK
```

and a private context:

```text
_scoped_pyautogui_state()
```

Exact sequence:

```text
acquire lock
load backend
snapshot backend.FAILSAFE / backend.PAUSE
apply instance failsafe / pause
execute one Desktop Tool backend operation
finally:
    restore original FAILSAFE / PAUSE
release lock
```

This serializes Desktop Tool instances against each other.

It does not claim to coordinate unrelated external code that directly manipulates PyAutoGUI.

The key T5 guarantee is:

```text
DesktopAutomation construction
and completed Desktop Tool calls
do not leave FAILSAFE/PAUSE changed
```

---

# 15. PyAutoGUI state restoration errors

If scoped settings cannot be applied before any action:

```text
DESKTOP_BACKEND_CONFIG_FAILED
```

If original PyAutoGUI globals cannot be restored after an attempted operation:

```text
DESKTOP_BACKEND_STATE_RESTORE_FAILED
```

has precedence because process-global backend state is now uncertain.

Details contain only:

```text
exception_type
phase
```

No user text.

---

# 16. Fail-safe behavior

If PyAutoGUI raises its backend `FailSafeException`:

```text
DESKTOP_FAILSAFE_TRIGGERED
```

T5 does not convert fail-safe activation into generic success or silently retry the side effect.

Other expected backend action exceptions become:

```text
DESKTOP_OPERATION_FAILED
```

T5 does not catch `BaseException` around ordinary operations.

---

# 17. get_screen_info contract

PyAutoGUI `size()` and `position()` are called lazily.

Validate backend return values before returning them.

Data keeps the existing flat key names for compatibility:

```json
{
  "screen_width": 1920,
  "screen_height": 1080,
  "mouse_x": 500,
  "mouse_y": 300
}
```

Rules:

- width/height exact positive ints;
- dimensions <= `MAX_SCREEN_DIMENSION`;
- mouse x/y exact bounded ints.

Malformed backend property data:

```text
DESKTOP_PROPERTY_FAILED
```

Backend query exception:

```text
DESKTOP_OPERATION_FAILED
```

---

# 18. mouse_click result

After validation and one backend call:

```json
{
  "x": 100,
  "y": 200,
  "position_mode": "explicit",
  "button": "left",
  "clicks": 2
}
```

Current-position click:

```json
{
  "x": null,
  "y": null,
  "position_mode": "current",
  "button": "right",
  "clicks": 1
}
```

T5 does not perform an extra position query just to populate a success message.

---

# 19. mouse_move result

```json
{
  "x": 400,
  "y": 500,
  "duration_seconds": 0.5
}
```

Backend call remains:

```text
moveTo(x, y, duration=...)
```

No textual success message.

---

# 20. mouse_drag contract

T4 audit history identified missing regression coverage for this existing public action.

T5 freezes:

- target x/y required;
- optional start x/y pair;
- validated button;
- validated drag duration;
- explicit start uses `moveTo(start_x, start_y)`;
- drag uses `dragTo(x, y, duration=..., button=...)`.

Success:

```json
{
  "x": 500,
  "y": 600,
  "start_x": 100,
  "start_y": 100,
  "explicit_start": true,
  "button": "left",
  "duration_seconds": 0.5
}
```

No-start path returns null start fields and `explicit_start=false`.

---

# 21. mouse_scroll contract

Input is one signed nonzero integer.

Success:

```json
{
  "clicks": -300
}
```

The current live harness uses `-300`, which is within the frozen `10_000` absolute hard limit.

T5 does not return a derived prose direction.

---

# 22. press_key contract

Input:

- non-empty string after strip;
- max `MAX_KEY_CHARS`;
- normalized to lowercase;
- presses validated by `PRESS_COUNT`.

Success:

```json
{
  "key": "enter",
  "presses": 2
}
```

No arbitrary backend exception string is returned.

---

# 23. hotkey contract

Canonical runtime input is a list.

Validation:

- list only;
- 1..`MAX_HOTKEY_KEYS` entries;
- each key is non-empty string;
- each key <= `MAX_KEY_CHARS`;
- keys normalized to lowercase.

Success:

```json
{
  "keys": ["ctrl", "c"],
  "key_count": 2
}
```

T5 does not silently accept a single string as an iterable list of characters.

---

# 24. P0-D2 — type_text hard bound

Before any dependency, clipboard or keyboard action:

- `text` must be a string;
- empty string is invalid;
- whitespace-only text remains valid;
- NUL is rejected;
- length <= `MAX_TEXT_CHARS`.

`force_direct` and `restore_clipboard` must be exact bool.

`interval` is always validated even when the eventual Unicode route is clipboard.

This prevents invalid explicit inputs from being silently ignored by route selection.

---

# 25. P0-D3 — secret-safe type_text output

Current success strings include the full user text.

T5 MUST NOT return or log:

```text
text
old clipboard
new clipboard
exception string that may embed user text
```

Success data:

```json
{
  "character_count": 17,
  "method": "clipboard",
  "clipboard_restored": true
}
```

Direct ASCII:

```json
{
  "character_count": 11,
  "method": "pyautogui",
  "clipboard_restored": null
}
```

Direct Unicode:

```json
{
  "character_count": 17,
  "method": "pynput",
  "clipboard_restored": null
}
```

Typed content never appears in ToolResult.

---

# 26. type_text route selection

T5 removes the current `len(text) > 20` clipboard heuristic.

Reason:

Long ASCII text does not inherently require clipboard mutation.

Exact routing:

```text
ASCII text
    -> pyautogui.write(..., interval=...)

non-ASCII + force_direct=False
    -> clipboard paste path

non-ASCII + force_direct=True
    -> optional pynput Controller.type(...)
```

This reduces unnecessary clipboard side effects while preserving reliable Unicode input.

No Unicode text is silently handed to `pyautogui.write` as a fallback.

---

# 27. Optional direct-Unicode behavior

For:

```text
non-ASCII
force_direct=True
```

T5 lazy-loads:

```text
pynput.keyboard.Controller
```

and caches one successfully-created controller per DesktopAutomation instance.

If import or Controller construction fails:

```text
DEPENDENCY_UNAVAILABLE
dependency = "pynput.keyboard"
```

T5 does not add pynput to requirements.

If Controller.type fails after creation:

```text
DESKTOP_OPERATION_FAILED
```

Typed text is not echoed.

---

# 28. P1-D4 — transactional clipboard restoration

Default Unicode paste:

```text
restore_clipboard=True
```

Exact sequence:

```text
load pyperclip
snapshot existing text clipboard
copy new user text
wait bounded settle
send platform paste hotkey
wait bounded settle
finally restore snapshot
```

If snapshot acquisition fails before clipboard mutation:

```text
DESKTOP_CLIPBOARD_ERROR
```

No typing occurs.

Once `copy(text)` succeeds, restoration is attempted in `finally` whenever `restore_clipboard=True`, even if paste/hotkey fails.

---

# 29. Clipboard restoration precedence

If paste/backend operation fails but clipboard restoration succeeds:

```text
return original operation failure
```

If clipboard restoration itself fails after clipboard mutation:

```text
DESKTOP_CLIPBOARD_RESTORE_FAILED
```

takes precedence because system clipboard state is uncertain.

Allowed details:

```text
trigger
exception_type
```

Never include old/new clipboard content.

---

# 30. restore_clipboard=False semantics

When explicitly false:

- T5 does not snapshot old clipboard;
- copies text;
- pastes it;
- leaves clipboard containing the supplied text by caller request.

Success:

```text
clipboard_restored = false
```

If copy/paste fails, return the appropriate structured failure.

The ToolResult still never includes clipboard contents.

---

# 31. Platform paste shortcut

T5 does not hardcode Ctrl+V for all systems.

Freeze:

```text
darwin:
  command + v

other supported platforms:
  ctrl + v
```

The shortcut is generated internally.

No new platform-specific native dependency is introduced.

---

# 32. Metadata single source of truth

Current file hand-maintains both:

```text
TOOL_METADATA
ACTIONS_METADATA
```

and they can drift.

T5 retains `ACTIONS_METADATA` as a compatibility export but generates it from one internal action-spec definition also used to build root V1 metadata.

No duplicated handwritten bounds/defaults.

T5 does NOT add Metadata V2 fields.

T7 still owns:

```text
manifest_version
exports
bind
output_schema
per-export effects/idempotency
expose_root
```

---

# 33. Legacy action aliases

Preserve physical aliases in `execute()`:

```text
screen_info / info -> get_screen_info
click              -> mouse_click
move               -> mouse_move
drag               -> mouse_drag
scroll             -> mouse_scroll
type / write       -> type_text
press              -> press_key
shortcut           -> hotkey
```

Canonical ToolResult.action always uses the canonical action name.

Aliases do not bypass validation.

---

# 34. Wrapper functions

Preserve:

```text
get_screen_info
mouse_click
mouse_move
mouse_drag
mouse_scroll
type_text
press_key
hotkey
run
```

All wrappers return canonical ToolResult after T5.

The module-level default instance remains safe because constructor becomes dependency-free and side-effect-free.

---

# 35. Stable T5 error codes

Freeze:

```text
INVALID_ARGUMENT
DEPENDENCY_UNAVAILABLE

DESKTOP_PROPERTY_FAILED
DESKTOP_OPERATION_FAILED
DESKTOP_FAILSAFE_TRIGGERED

DESKTOP_BACKEND_CONFIG_FAILED
DESKTOP_BACKEND_STATE_RESTORE_FAILED

DESKTOP_CLIPBOARD_ERROR
DESKTOP_CLIPBOARD_RESTORE_FAILED
```

No error details contain typed text or clipboard contents.

---

# 36. Backend error isolation

Expected backend exceptions are caught only around isolated dependency/backend calls.

T5 does not use one broad catch around the whole public method.

Do not swallow:

```text
KeyboardInterrupt
SystemExit
programmer errors outside isolated backend boundary
```

PyAutoGUI fail-safe receives its specific stable error.

---

# 37. T5-A — Identity + lazy dependencies + validation + metadata

Modify:

```text
tools/v1/desktop_tool.py
tools/v1/test/test_desktop_automation.py
```

Implement:

- `DESKTOP_TOOL_NAME = "desktop_automation"`;
- version/hard constants;
- `_DesktopToolError`;
- lazy PyAutoGUI/clipboard/pynput loaders;
- constructor validation with no side effects;
- canonical ToolResult builders;
- coordinate/button/count/duration/text/key/hotkey validation;
- one action metadata source;
- generated compatibility `ACTIONS_METADATA`.

Tests:

- subprocess import safety;
- missing dependencies;
- KeyboardInterrupt/SystemExit propagation;
- constructor does not import dependencies;
- constructor does not touch global state;
- hard limit/type matrix;
- metadata consistency;
- root name remains `desktop_automation`.

---

# 38. T5-B — Scoped PyAutoGUI state + screen/mouse operations

Implement:

- module RLock;
- scoped FAILSAFE/PAUSE snapshot/apply/restore;
- restore in `finally`;
- backend-state restoration precedence;
- fail-safe mapping;
- screen-info validation;
- mouse click;
- mouse move;
- mouse drag;
- mouse scroll.

Tests:

- old FAILSAFE/PAUSE restored after success;
- restored after backend failure;
- restoration failure surfaced;
- two tool instances serialize scoped state;
- screen malformed data;
- coordinate pair rules;
- mouse-drag explicit/no-start cases;
- click/scroll/count/duration limits;
- FailSafeException mapping.

This phase closes P0-D1/P0-D2 for mouse actions and P1-D5.

---

# 39. T5-C — press_key + hotkey completion

Implement:

- key normalization/bounds;
- press count hard bounds;
- hotkey list/count/key bounds;
- canonical results;
- backend fail-safe/error mapping.

Tests:

- press success;
- press zero/bool/over-limit;
- blank/overlong key;
- hotkey success;
- empty list;
- string-not-list rejection;
- >16 keys;
- invalid member;
- alias dispatch.

---

# 40. T5-D — Direct text paths

Implement common type-text preflight:

- text bounds;
- bool validation;
- interval validation;
- ASCII detection.

ASCII:

```text
pyautogui.write(text, interval=...)
```

Unicode + `force_direct=True`:

```text
lazy optional pynput Controller.type(text)
```

Tests:

- short ASCII;
- long ASCII remains direct and never touches clipboard;
- Unicode direct with optional controller;
- Unicode direct dependency unavailable;
- controller construction failure;
- controller type failure;
- NUL/empty/overlong text;
- typed secret absent from result/error.

---

# 41. T5-E — Clipboard Unicode transaction

Implement:

- lazy pyperclip;
- platform paste shortcut;
- restore-enabled snapshot;
- copy;
- bounded settle;
- paste;
- finally restore;
- restoration precedence;
- restore-disabled behavior;
- secret-safe result.

Tests:

- Unicode default paste;
- platform shortcut selection;
- snapshot failure = zero mutation;
- copy failure;
- hotkey failure + successful restore;
- hotkey failure + restore failure precedence;
- success + restore failure precedence;
- restore false skips snapshot and leaves clipboard by contract;
- result only contains character count/method/restore state;
- no clipboard content in errors.

This phase closes P0-D3/P1-D4/P1-D7.

---

# 42. T5-F — Dispatcher/wrappers + compatibility regression

Complete:

- canonical action dispatcher;
- aliases;
- module wrappers;
- JSON-safe ToolResult for all actions;
- remove legacy presentation-string branches;
- compatibility `ACTIONS_METADATA`;
- no global import-time dependencies.

Regression:

- every canonical action route;
- every alias;
- missing required arguments;
- invalid action;
- wrapper outputs;
- `mouse_drag` wrapper;
- tool result identity/version;
- no `se` import;
- no `cl` import;
- T1–T4 tests unchanged.

---

# 43. T5-G — Full gate + completion

Focused gate:

```text
python -m pytest -q \
  tools/v1/test/test_desktop_automation.py \
  tools/v1/test/test_desktop_import_safety.py \
  tools/v1/test/test_shared_contracts.py \
  tools/v1/test/test_shared_limits.py \
  tools/v1/test/test_scope_boundary.py
```

If import-safety tests stay in the main Desktop test file, omit the optional module.

Then:

```text
python -m pytest -q tools/v1/test
```

Then full repository CI.

Only after green:

- create `T5_DESKTOP_COMPLETION.md`;
- mark T5 complete in source-of-truth;
- freeze T5→T6 Web.

T5-G does not modify Web code.

---

# 44. Detailed regression matrix

## Import/dependencies

1. importing Desktop Tool does not import pyautogui;
2. import does not import pyperclip;
3. import does not import pynput;
4. default object construction has no dependency load;
5. PyAutoGUI ImportError/platform init error -> DEPENDENCY_UNAVAILABLE;
6. pyperclip missing -> DEPENDENCY_UNAVAILABLE only on clipboard route;
7. pynput missing -> DEPENDENCY_UNAVAILABLE only on forced Unicode direct route;
8. KeyboardInterrupt/SystemExit not swallowed.

## Constructor/global state

9. failsafe bool validation;
10. pause bool rejected;
11. pause negative rejected;
12. pause non-finite rejected;
13. pause over hard max rejected;
14. constructor leaves backend globals untouched;
15. action applies scoped state;
16. success restores old state;
17. operation failure restores old state;
18. state restoration failure surfaced;
19. two DesktopAutomation instances serialize scoped state.

## Screen/mouse validation

20. valid screen info;
21. malformed size;
22. malformed position;
23. click current position;
24. click explicit pair;
25. click partial coordinate rejected;
26. click count zero;
27. click count bool;
28. click count over max;
29. invalid button;
30. negative multi-monitor coordinates accepted;
31. coordinate hard bound;
32. move target required;
33. move duration zero accepted;
34. move duration over max;
35. drag without start;
36. drag explicit start;
37. drag partial start rejected;
38. drag invalid duration/button;
39. scroll positive;
40. scroll negative -300 compatibility;
41. scroll zero rejected;
42. scroll over hard bound;
43. FailSafeException mapping.

## Keyboard non-text

44. press normalized key;
45. press count bounds;
46. blank key;
47. overlong key;
48. hotkey valid;
49. empty hotkey;
50. string hotkey rejected;
51. >16 hotkey keys;
52. blank hotkey member;
53. overlong hotkey member.

## type_text preflight/direct

54. empty text rejected;
55. whitespace-only text accepted;
56. NUL rejected;
57. max text accepted;
58. max+1 rejected before dependencies;
59. bool flags exact validation;
60. interval bool/nonfinite/overmax rejected;
61. ASCII short direct;
62. ASCII long direct with no clipboard mutation;
63. direct result does not echo text;
64. Unicode force-direct uses pynput;
65. forced Unicode missing pynput;
66. pynput Controller construction error;
67. pynput type error secret-safe.

## Clipboard Unicode

68. Unicode default uses clipboard;
69. restore-enabled snapshots old clipboard;
70. copy new text;
71. paste hotkey invoked;
72. success restores old clipboard;
73. snapshot failure causes zero copy/paste;
74. copy failure structured;
75. paste failure still restores;
76. paste failure + restore failure -> restore failure precedence;
77. success + restore failure -> restore failure precedence;
78. restore false skips snapshot;
79. restore false result records false;
80. macOS uses command+v;
81. non-macOS uses ctrl+v;
82. clipboard contents never in result/error;
83. typed text never in result/error.

## Metadata/dispatch/scope

84. TOOL_METADATA root name remains desktop_automation;
85. ACTIONS_METADATA generated consistently;
86. every canonical action;
87. every alias;
88. missing required parameters;
89. invalid action;
90. every wrapper returns ToolResult;
91. mouse_drag wrapper covered;
92. no se import;
93. no cl import;
94. T1/T2/T3/T4 regressions remain green.

---

# 45. Default tests do not use real desktop input

T5 default CI MUST NOT move the real mouse, click, type, mutate the real clipboard or press real keys.

Use deterministic fake/lazy dependency modules.

Import-safety may use a subprocess with import guards but no real GUI action.

The existing real-machine desktop scenario remains T8 opt-in work.

This is especially important because headless Linux runners may not have a usable DISPLAY/Wayland desktop session.

---

# 46. Live-harness compatibility boundary

Current live harness uses:

```text
mouse_move
mouse_click
mouse_drag
mouse_scroll(-300)
type_text Unicode
press_key
hotkey
```

T5 preserves physical action names and argument shapes needed by those calls.

The harness currently expects presentation strings in several places.

T5 does NOT edit it.

T8 will migrate live assertions/log formatting to structured ToolResult.

---

# 47. ACTIONS_METADATA compatibility boundary

`ACTIONS_METADATA` remains exported in T5 because it is part of the current physical module surface.

However:

```text
handwritten duplicate schema
        ↓
generated compatibility metadata
from one internal action specification
```

T5 does not interpret this as Metadata V2.

T7 remains responsible for logical exports and final metadata conformance.

---

# 48. Security/authorization boundary

Desktop actions directly affect the user's active UI.

T5 owns intrinsic safety:

- strict input types;
- hard counts/durations/text/key bounds;
- lazy/headless-safe dependency loading;
- scoped backend state restoration;
- fail-safe propagation;
- transactional clipboard restoration;
- no typed-secret echo.

Future consumer policy owns:

- whether an agent may click/type;
- user permission scopes;
- HITL;
- capability visibility;
- approval of risky UI actions.

T5 does not add tool-local authorization.

---

# 49. Expected implementation diff

Production:

```text
M tools/v1/desktop_tool.py
```

Tests:

```text
M tools/v1/test/test_desktop_automation.py
A tools/v1/test/test_desktop_import_safety.py   # optional
```

Completion docs only after green:

```text
A tools/v1/T5_DESKTOP_COMPLETION.md
M tools/v1/TOOLS_V1_CONTRACT_FREEZE.md
```

Current plan:

```text
A tools/v1/T5_DESKTOP_IMPLEMENTATION_PLAN.md
```

Forbidden implementation diff:

```text
NO tools/v1/_shared/**
NO File/Glob/Terminal/Window
NO Web
NO live/**
NO se/**
NO cl/**
NO requirements*
```

---

# 50. Completion invariants

T5 may be marked complete only if:

1. module import does not import desktop dependencies;
2. default object construction is side-effect free;
3. PyAutoGUI globals are not changed by constructor;
4. every Desktop Tool PyAutoGUI scoped mutation restores prior FAILSAFE/PAUSE;
5. restoration failure cannot be reported as success;
6. all count/duration/interval/text/key/hotkey inputs are hard bounded;
7. bool cannot silently pass numeric validation;
8. coordinate pair rules are deterministic;
9. mouse_drag has regression coverage;
10. typed text is never echoed in ToolResult/error;
11. clipboard content is never echoed;
12. clipboard restore runs in finally after clipboard mutation;
13. clipboard restore failure is explicit;
14. long ASCII no longer mutates clipboard merely due length;
15. Unicode default uses clipboard;
16. forced direct Unicode never silently falls back to PyAutoGUI ASCII writer;
17. optional pynput absence is deterministic;
18. root physical name remains `desktop_automation`;
19. ACTIONS_METADATA cannot drift from the action-spec source;
20. every public action returns canonical ToolResult;
21. no T1–T4 implementation/test change;
22. no live/se/cl/requirements change;
23. focused tests green;
24. all `tools/v1/test` green;
25. full repository CI green.

---

# 51. T5-A→T5-G implementation order

```text
T5-A  Identity + lazy deps + validation + metadata source
  ↓
T5-B  Scoped PyAutoGUI state + screen/mouse operations
  ↓
T5-C  press_key + hotkey
  ↓
T5-D  direct ASCII/Unicode text paths
  ↓
T5-E  transactional clipboard Unicode path
  ↓
T5-F  dispatcher/wrappers + compatibility regression
  ↓
T5-G  full gates + completion document
```

Do not implement clipboard paste before the restoration/precedence state machine is fixed.

Do not initialize pynput Controller during module import or constructor.

---

# 52. Implementation verdict

T5 can be completed locally using the existing T1 foundation.

Architecture:

```text
side-effect-free module import
        ↓
DesktopAutomation config only
        ↓
lazy dependency per action
        ↓
strict bounded validation
        ↓
scoped PyAutoGUI global state
        ↓
mouse / keyboard / text operation
        ↓
clipboard transaction when required
        ↓
secret-safe canonical ToolResult
```

The central T5 invariants are:

```text
NO DEPENDENCY NEEDED
        =
NO GUI IMPORT / INIT
```

and:

```text
TYPE SECRET
    ≠
RETURN SECRET
```

and:

```text
CLIPBOARD MUTATED + RESTORE REQUESTED
        =
RESTORE ATTEMPTED IN FINALLY
```

**AUDIT STATUS:** COMPLETE  
**T4→T5 BOUNDARY:** FROZEN  
**T5 IMPLEMENTATION DESIGN:** IMPLEMENTED AS FROZEN  
**T5-A→T5-G PLAN:** COMPLETE  
**PRODUCTION CODE STATUS:** COMPLETE  
**FINAL CODE HEAD:** `029188cfb79f0dc8a1918b48459307b66bf994f4`  
**FINAL CI:** Architecture Baseline run `35756693026` — SUCCESS  
**COMPLETION RECORD:** `tools/v1/T5_DESKTOP_COMPLETION.md`  
**NEXT ROADMAP PHASE:** T6 Web Tool boundary audit; no T6 code started.
