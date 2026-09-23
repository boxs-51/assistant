# T5 Desktop Tool Completion Record

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Implementation-plan baseline: `274c1dbb07e3441f9fdc9e104112467f3ab72191`  
Final T5 code HEAD: `029188cfb79f0dc8a1918b48459307b66bf994f4`  
Status: **T5 COMPLETE — FROZEN**

---

## 1. Completion summary

T5 completes the standalone Desktop Tool phase for:

```text
tools/v1/desktop_tool.py
tools/v1/test/test_desktop_automation.py
```

The phase adopts the T1 canonical ToolResult contract while preserving the physical root name:

```text
desktop_automation
```

Metadata V2 logical projections remain deferred to T7.

No T1/T2/T3/T4 production/test file, Web Tool, live harness, SE/CL file or requirements file was modified by T5 implementation.

---

## 2. Implementation commits

Primary implementation:

```text
83e253a6b634dbd67b92837e48f056965d77d70c
feat(tools-v1): implement T5 bounded desktop automation
```

State-restoration hardening:

```text
753343946bc597ad7787d9edae17c0150bafc6ac
fix(tools-v1): harden T5 state restoration semantics
```

Final regression alignment:

```text
029188cfb79f0dc8a1918b48459307b66bf994f4
test(tools-v1): align T5 screen property regression
```

Final code baseline:

```text
029188cfb79f0dc8a1918b48459307b66bf994f4
```

---

## 3. Lazy dependency / headless-safe import

Module import no longer imports or initializes:

```text
pyautogui
pyperclip
pynput
pynput.keyboard.Controller
```

The module-level default DesktopAutomation object is safe because the constructor only validates/stores configuration.

Dependencies are loaded lazily by action:

```text
pyautogui       -> desktop actions
pyperclip       -> Unicode clipboard route
pynput.keyboard -> forced direct Unicode route only
```

Expected dependency/platform initialization failures return:

```text
DEPENDENCY_UNAVAILABLE
```

Control-flow exceptions are not swallowed.

---

## 4. Physical compatibility identity

Frozen physical identity:

```text
DESKTOP_TOOL_NAME = "desktop_automation"
DESKTOP_TOOL_VERSION = "2.0.0"
```

This preserves local-tool-loader compatibility.

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

Future `desktop.*` logical capabilities remain T7 work.

---

## 5. Hard bounds

Final limits include:

```text
MAX_COORD_ABS = 1,000,000
MAX_SCREEN_DIMENSION = 1,000,000

MAX_TEXT_CHARS = 32,768
MAX_KEY_CHARS = 64
MAX_HOTKEY_KEYS = 16

clicks:  1..100
presses: 1..100
abs(scroll) <= 10,000, zero rejected

move duration: 0..30s
drag duration: 0..30s
type interval: 0..1s
PyAutoGUI pause: 0..5s
```

Numeric bool values are rejected.

Negative coordinates remain valid for multi-monitor desktops.

---

## 6. Scoped PyAutoGUI global state

DesktopAutomation construction no longer modifies process-global:

```text
pyautogui.FAILSAFE
pyautogui.PAUSE
```

Each PyAutoGUI operation executes under a module `RLock`:

```text
snapshot old globals
apply instance config
execute one scoped Desktop operation
finally restore old globals
```

Stable failures:

```text
DESKTOP_BACKEND_CONFIG_FAILED
DESKTOP_BACKEND_STATE_RESTORE_FAILED
```

Expected Tool errors may be superseded by state-restore failure because global backend state is then uncertain.

Programmer/control-flow exceptions are restored-through and re-raised rather than converted to fake Tool errors.

---

## 7. Fail-safe semantics

PyAutoGUI fail-safe activation maps to:

```text
DESKTOP_FAILSAFE_TRIGGERED
```

T5 does not retry the side effect.

Other isolated backend action failures map to:

```text
DESKTOP_OPERATION_FAILED
```

---

## 8. Screen and mouse operations

`get_screen_info` keeps the flat compatibility data shape:

```json
{
  "screen_width": 1920,
  "screen_height": 1080,
  "mouse_x": 500,
  "mouse_y": 300
}
```

Malformed backend screen/position data returns:

```text
DESKTOP_PROPERTY_FAILED
```

Mouse actions now enforce deterministic coordinate-pair, button, count and duration rules.

`mouse_drag` now has direct regression coverage for:

```text
current-position start
explicit start_x/start_y
partial start rejection
button/duration bounds
wrapper/dispatcher
```

---

## 9. Keyboard operations

`press_key`:

- normalizes key names;
- enforces key length;
- enforces press count.

`hotkey`:

- requires a list;
- requires 1..16 keys;
- bounds each key;
- rejects a string masquerading as a key list.

Both return structured ToolResult data.

---

## 10. Secret-safe type_text

T5 removes the old result strings that echoed the complete typed text.

All routes return only non-secret metadata such as:

```json
{
  "character_count": 17,
  "method": "clipboard",
  "clipboard_restored": true
}
```

Typed text is not returned in:

```text
success data
error message
error details
```

Arbitrary backend exception strings are not propagated.

---

## 11. Exact text-routing semantics

The old heuristic:

```text
long text (>20 chars) -> clipboard
```

was removed.

Final routing:

```text
ASCII
  -> pyautogui.write

non-ASCII + force_direct=false
  -> clipboard paste

non-ASCII + force_direct=true
  -> lazy optional pynput Controller.type
```

Long ASCII remains direct and does not mutate the clipboard merely because of length.

Unicode never silently falls back to PyAutoGUI's ASCII writer.

---

## 12. Optional pynput contract

The repository does not declare `pynput` as a required dependency.

Therefore forced direct Unicode uses it only when available.

Import or Controller construction failure returns:

```text
DEPENDENCY_UNAVAILABLE
dependency = "pynput.keyboard"
```

Controller typing failure returns:

```text
DESKTOP_OPERATION_FAILED
```

T5 does not modify requirements.

---

## 13. Transactional clipboard restoration

Unicode clipboard typing with `restore_clipboard=true` uses:

```text
snapshot old text clipboard
copy user text
paste
finally restore snapshot
```

Snapshot failure occurs before mutation.

After successful clipboard mutation, restoration is attempted in `finally` even when paste/backend input fails.

Stable failures:

```text
DESKTOP_CLIPBOARD_ERROR
DESKTOP_CLIPBOARD_RESTORE_FAILED
```

Clipboard restoration failure takes precedence over an expected Tool operation failure because clipboard state is then uncertain.

KeyboardInterrupt/programmer errors are not masked by restore failure.

No old/new clipboard contents appear in ToolResult.

When `restore_clipboard=false`, T5 intentionally skips snapshot and leaves the supplied text in the clipboard by caller request.

---

## 14. Platform paste shortcut

Internal shortcut:

```text
macOS  -> command + v
other  -> ctrl + v
```

No new platform-native dependency was introduced.

---

## 15. Metadata consistency

Current physical compatibility exports remain:

```text
TOOL_METADATA
ACTIONS_METADATA
```

T5 generates action metadata from one internal action-spec source instead of maintaining two handwritten schemas.

Root V1 metadata remains broad enough for the shared physical dispatcher; per-action ACTIONS_METADATA retains stricter action semantics.

Metadata V2 fields remain T7 non-goals.

---

## 16. Stable T5 error codes

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

---

## 17. Default CI isolation

T5 unit tests do not perform real desktop side effects.

They do not:

```text
move the real mouse
click the real desktop
type real keys
modify the real clipboard
```

They use deterministic fake/lazy backends.

Real-machine Desktop coverage remains T8 opt-in work.

---

## 18. Final CI evidence

Final T5 code HEAD:

```text
029188cfb79f0dc8a1918b48459307b66bf994f4
```

### Phase 5 Exit Gates

Run:

```text
35756693358
```

Result:

```text
40 passed
1.17s
```

Conclusion:

```text
success
```

### Architecture Baseline

Run:

```text
35756693026
```

Linux full repository suite:

```text
892 passed
1 skipped
14 warnings
119 subtests passed
73.39s
```

Windows client contracts:

```text
68 passed
11.20s
```

Workflow conclusion:

```text
success
```

---

## 19. Scope evidence

Comparison:

```text
base = 274c1dbb07e3441f9fdc9e104112467f3ab72191
head = 029188cfb79f0dc8a1918b48459307b66bf994f4
```

Implementation changed only:

```text
tools/v1/desktop_tool.py
tools/v1/test/test_desktop_automation.py
```

Forbidden implementation diff:

```text
0
```

No T5 implementation change was made to:

```text
tools/v1/_shared/**
File/Glob/Terminal/Window production/tests
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
requirements*
```

---

## 20. Intentional non-goals

T5 does not implement:

- real GUI default-CI interaction;
- authorization/HITL;
- window targeting for desktop actions;
- PTY/native accessibility automation;
- persistent clipboard history;
- Metadata V2 logical exports;
- live harness migration;
- adding pynput to requirements.

These are not silently claimed.

---

## 21. Completion verdict

```text
T0 Contract Freeze            COMPLETE
T1 Shared Foundation          COMPLETE
T2 File + Glob                COMPLETE
T3 Terminal                   COMPLETE
T4 Window                     COMPLETE
T5 Desktop                    COMPLETE
T5 lazy/headless safety       COMPLETE
T5 hard bounds                COMPLETE
T5 secret-safe typing         COMPLETE
T5 clipboard transaction      COMPLETE
T5 repository CI              GREEN
T5 scope invariant            GREEN
```

No open T5 P0/P1 was identified after final implementation and regression audit.

T5 is frozen. The next roadmap phase is T6 Web Tool; T6 production code has not started.
