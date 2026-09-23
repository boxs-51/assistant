# Tools V1 Standalone Contract Freeze & Completion Roadmap

**Repository:** `boxs-51/assistant`  
**Baseline commit:** `fe6077b81add22f9fcffc9261d08c48a53f5cd8d`  
**Branch:** `tools-v1-contract-freeze`  
**Scope root:** `tools/v1/**`  
**Status:** **TV1-T8 FINAL-FROZEN — TV1-T9 BOUNDARY AUDITED / PLAN FROZEN / CODE NOT STARTED — TV1-T10 PHASE ALLOCATED ONLY**

**Current phase authority:**

```text
T6  CLOSED / GREEN / FROZEN @ 1138a83d
T7  CLOSED / GREEN / AUDIT-APPROVED / FROZEN @ adea67c3
T8  A→H COMPLETE / GREEN / FINAL-FROZEN
    code/test candidate @ 7a84edf2
    completion document @ a2c635ae
    Issue #7 CLOSED / COMPLETED
T9  BOUNDARY AUDITED / IMPLEMENTATION PLAN FROZEN / CODE NOT STARTED
T10 PHASE ALLOCATED ONLY / NOT AUDITED / NOT OPEN
```

The original T0–T6 roadmap treated Tools V1 as a standalone subsystem. T7 and T8 later reopened only the explicitly audited consumer boundary through Issue #6 and Issue #7. Those later checkpoints supersede stale statements in the original roadmap that SE/CL consumer work would never occur inside T1–T8.

---

## 1. Purpose

This document freezes the standalone contract for `tools/v1` and records the completion roadmap that grew from that contract.

The original T0–T6 objective was to finish the physical tools as an independent subsystem. That property remains: production tool code under `tools/v1/**` must not import or depend on `se/**` or `cl/**`.

T7 and T8 then added audited **consumer-side** integration in SE/CL while preserving the dependency direction:

```text
SE / CL  ->  tools/v1 contracts
tools/v1 ─X─> SE / CL
```

The authoritative T7/T8 scope is the accepted implementation/completion documents and durable checkpoints, not the earlier pre-implementation status text in this roadmap.

---

## 2. Original standalone scope boundary (T0–T6)

The rules in this section are the original standalone implementation boundary for T0–T6. They remain architectural constraints on the **tool subsystem itself**, but they were explicitly reopened for audited consumer-side work in T7/T8. T7/T8 did not authorize reverse dependencies from tools into SE/CL.

### 2.1 Allowed changes

For the original T0–T6 standalone track, only paths below `tools/v1/**` were changed, including:

- tool implementations;
- tool-local shared contracts/helpers;
- tool metadata;
- unit/regression tests;
- opt-in live/integration tests;
- documentation owned by `tools/v1`.

### 2.2 Forbidden changes

For T0–T6, this track MUST NOT modify:

- `se/**`;
- `cl/**`;
- AgentRuntime;
- CapabilityRuntime;
- R6/R7 state machines;
- SQL models or migrations;
- Central Asset Storage;
- provider adapters outside `tools/v1`.

### 2.3 Dependency direction

The following invariant is frozen:

```text
tools/v1  ─X─>  se
tools/v1  ─X─>  cl
```

A tool MAY use Python standard-library modules and its explicitly optional third-party dependencies.

Consumers may later depend on the tool contract; tools must not depend on those consumers.

---

## 3. Current inventory at the frozen baseline

Production-facing tools:

```text
tools/v1/
├── desktop_tool.py
├── file_tool.py
├── find_by_glob.py
├── terminal_tool.py
├── window_tool.py
└── web_tool/
    ├── __init__.py
    ├── cap_solver_handler.py
    ├── config.py
    ├── core.py
    ├── extractors.py
    ├── formatters.py
    ├── proxy.py
    ├── scraper.py
    ├── searcher.py
    ├── stealth.py
    └── utils.py
```

Unit/regression tests:

```text
tools/v1/test/
├── test_desktop_automation.py
├── test_file_tool.py
├── test_glob_search_tool.py
├── test_terminal_tool.py
├── test_web_tool.py
└── test_window_tool.py
```

Opt-in real-machine/live harnesses:

```text
tools/v1/live/
├── live_integration_real_machine.py
├── live_multisource_pipeline.py
├── live_real_system_operations.py
└── live_terminal_python_pipeline.py
```

---

# PART I — STANDARD TOOL CONTRACT

## 4. Public module contract

Every production tool module/package MUST expose:

```python
TOOL_METADATA: dict
run(...): JSON-safe result | awaitable producing a JSON-safe result
```

A tool MAY expose implementation classes and compatibility wrappers, but `TOOL_METADATA` and `run` are the frozen public discovery surface.

No public tool may require SE/CL-specific context objects.

---

## 5. Sync/async execution contract

Tools may internally be synchronous or asynchronous.

The public `run(...)` contract MUST satisfy:

1. the returned terminal value is JSON-safe;
2. if `run` returns an awaitable/task, the caller MUST be able to await it to obtain the same terminal JSON-safe contract;
3. an async resource must have one explicit lifecycle owner;
4. resources created on an event loop must be closed on that same loop;
5. cancellation must not be converted into a fake success result.

The existing Web Tool same-event-loop Playwright ownership behavior is retained as a required regression invariant.

---

## 6. Canonical result envelope

The target Tools V1 result contract is:

```json
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
```

Failure:

```json
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
```

Expected validation, dependency, timeout, not-found, ambiguity and operation failures MUST NOT masquerade as successful strings.

`KeyboardInterrupt`, `SystemExit`, and async cancellation MUST NOT be swallowed into ordinary tool errors.

### 6.1 Error code rules

Error codes MUST be stable machine-readable identifiers.

Examples:

```text
INVALID_ARGUMENT
DEPENDENCY_UNAVAILABLE
NOT_FOUND
AMBIGUOUS_TARGET
TIMEOUT
OUTPUT_LIMIT_EXCEEDED
PERMISSION_DENIED
OPERATION_FAILED
INTERNAL_ERROR
```

Tool-specific codes SHOULD use a domain prefix where useful.

### 6.2 Secret-redaction rule

Results/errors MUST NOT echo:

- passwords;
- tokens/API keys;
- proxy credentials;
- cookies;
- authorization headers;
- full text typed into another application when that text can contain secrets.

---

## 7. Input validation and bounds

Every action MUST have deterministic validation before side effects.

All potentially unbounded inputs need hard limits, not only metadata hints.

At minimum, limits must exist for applicable:

- string lengths;
- file bytes/read bytes;
- number of files;
- number of glob results;
- number of queries/replacements;
- terminal command length;
- terminal timeout;
- terminal captured stdout/stderr;
- mouse click/keypress counts;
- desktop operation duration;
- Web query length;
- Web URL batch size;
- Web response bytes;
- rendered DOM/content chars;
- redirect count;
- browser concurrency.

Invalid zero/negative values MUST NOT silently fall back to defaults unless the contract explicitly defines that behavior.

---

## 8. Optional dependency contract

Optional third-party dependencies MUST NOT make the module fail at import time.

Required behavior:

```text
import tool
    ↓
always succeeds when Python itself can import the module
    ↓
action requiring unavailable dependency
    ↓
DEPENDENCY_UNAVAILABLE
```

Heavy/platform-sensitive objects MUST be initialized lazily when practical.

---

## 9. Intrinsic safety vs consumer authorization

Tools own **intrinsic safety**:

- input bounds;
- path/URL structural validation;
- resource caps;
- SSRF/network boundary;
- ambiguity rejection;
- deterministic process cleanup;
- no secret leakage.

Future consumers own **authorization/policy**:

- whether a user/agent may call an action;
- HITL approval;
- user scopes/permissions;
- capability visibility.

A tool MUST NOT rely on a future SE/CL policy layer for intrinsic safety.

---

# PART II — METADATA V2 CONTRACT AND CONSUMER CONVERGENCE

## 10. Backward-compatible root metadata

During Tools V1 implementation, root metadata may retain current fields for compatibility:

```python
TOOL_METADATA = {
    "name": "file_tool",
    "description": "...",
    "base_risk": "HIGH",
    "effects": ["READ", "WRITE"],
    "danger_patterns": [],
    "parameters": {...},

    # Frozen future contract:
    "manifest_version": "2.0",
    "version": "2.0.0",
    "expose_root": False,
    "exports": [...],
}
```

Historical note: at the original T0 contract freeze, SE/CL support for V2 was deferred. That boundary was later explicitly reopened by the T7/T8 checkpoints. T8 now provides canonical SE/CL consumption of `manifest_version="2.0"` while keeping tool implementations independent of SE/CL.

---

## 11. Capability Projection contract

One physical implementation may export multiple logical operations.

Example:

```text
file_tool
├── file.read
├── file.search
├── file.write
├── file.append
└── file.replace
```

Each export MUST define its own security/semantic contract.

Canonical export shape:

```python
{
    "id": "file.append",
    "version": "2.0",
    "name": "file.append",
    "description": "...",

    "bind": {
        "action": "write",
        "mode": "a",
    },

    "input_schema": {...},
    "output_schema": {...},

    "kind": "TOOL",
    "execution_mode": "ONE_SHOT",
    "idempotency": "NON_IDEMPOTENT",
    "effects": ["WRITE"],
    "base_risk": "HIGH",

    "required_scopes": [],
    "required_permissions": [],
    "danger_patterns": [],
}
```

### 11.1 Immutable binding

Keys in `bind` MUST NOT be exposed as caller-controlled fields in the projected input schema.

A caller cannot override:

```text
file.append → action=write, mode=a
```

with:

```json
{"mode": "w"}
```

### 11.2 Version fence

Changes to any of the following require an export version change:

- binding semantics;
- input semantics;
- output semantics;
- effects;
- idempotency;
- execution mode;
- externally observable side effects.

### 11.3 Root visibility

When `exports` exist:

```text
expose_root = false
```

is the target default.

Future SE/CL consumers should expose the logical export IDs, not the broad physical root.

---

# PART III — DEEP AUDIT FINDINGS

## 12. Cross-tool findings

### P0-C1 — No canonical result/error contract

Current tools return mixtures of:

- plain success strings;
- plain error strings;
- lists;
- dictionaries;
- JSON encoded as strings.

A consumer cannot reliably distinguish failure from successful text without parsing Vietnamese strings.

**Required:** T1 canonical result envelope.

### P1-C2 — Schemas lack hard bounds

Most metadata schemas describe types but omit `minLength`, `maxLength`, `minimum`, `maximum`, `maxItems`, and `additionalProperties: false`.

Runtime validation is also inconsistent.

### P1-C3 — Runtime aliases and public metadata diverge

Several tools accept aliases not declared in metadata.

Examples include Desktop and Window action aliases and Web aliases such as `read`.

Aliases may remain as compatibility-only implementation details but MUST NOT become canonical projected operations.

### P1-C4 — No standard output schema/version/idempotency

The existing metadata has useful `effects` and risk data but does not consistently declare:

- version;
- output schema;
- idempotency;
- execution mode.

These are required in Metadata V2 exports.

---

## 13. File Tool audit

File:

`tools/v1/file_tool.py`

### P0-F1 — Existing unreadable file can be treated as empty before write

Current write flow:

```python
if path.exists():
    try:
        old_content = path.read_text(...)
    except Exception:
        old_content = ""
```

A read failure is converted into an empty old value and the flow may continue toward replacement.

This is a data-loss boundary.

**Freeze:** if an existing target cannot be read when the operation requires its prior content, the mutation MUST fail closed.

### P1-F2 — Full-file read/search/replace are unbounded

`read()` may load an entire file.

Search/replace call `readlines()`, also loading the complete file.

Hard byte/line limits are required.

### P1-F3 — Atomic write does not clean temporary files on all failures

`NamedTemporaryFile(delete=False)` + `os.replace` is a good base, but failure between creation and replacement may leave an orphan.

T2 must use guaranteed cleanup.

### P1-F4 — Atomic replacement contract is incomplete

The current implementation does not explicitly preserve prior mode/metadata and has no concurrent-modification fence.

The standalone contract must document whether metadata preservation and optimistic concurrency are guaranteed.

### P1-F5 — Danger-pattern drift

Root metadata includes `config/AGENT.md`; the default runtime list does not.

Also, using:

```python
danger_patterns or defaults
```

means an explicitly empty list cannot disable defaults.

Metadata and runtime policy data must come from one source.

### P1-F6 — Schema/runtime type mismatch

Metadata describes `file_paths`, `queries`, and `replacements` primarily as arrays, while runtime accepts strings in multiple places.

T2 must freeze one canonical public shape and keep aliases only as compatibility paths.

### P1-F7 — Result contract is presentation text

Search and replace return formatted report strings rather than structured matches/change records.

Target data should expose:

- file path;
- line number;
- matched query;
- old/new text;
- match counts;
- changed/skipped state;
- truncation state.

### Existing test coverage

Current tests cover core read/write/search/replace behavior, literal/regex matching, confirmation callback, max search results and dangerous-path detection.

Missing high-value regression coverage includes:

- unreadable-existing-file fail closed;
- large-file bounds;
- temp cleanup failure;
- invalid/negative result limits;
- concurrent modification policy;
- structured error/result shape.

---

## 14. Glob Search audit

File:

`tools/v1/find_by_glob.py`

### P1-G1 — User-controlled `max_results` has no hard upper cap

A caller can request an arbitrarily large result count.

### P1-G2 — Early truncation occurs before sorting

The implementation stops after the first `limit` filesystem traversal results and sorts only that subset.

When a directory contains more entries than the limit, the returned bounded result set is not guaranteed to be deterministic across filesystems/runs.

### P1-G3 — Truncation marker pollutes path result type

The list may contain:

```text
"... [CẢNH BÁO: ...]"
```

as if it were a path.

The target result must use explicit metadata:

```json
{
  "matches": [...],
  "truncated": true
}
```

### P1-G4 — Truncation can be reported without proving more results exist

The current code reports a warning whenever `len(matches) >= limit`.

Correct bounded detection should probe at least one additional result or otherwise know that more results exist.

### P1-G5 — Path normalization contract is undefined

Returned paths can differ in relative/absolute form depending on `root_dir`.

T2 must freeze a deterministic representation.

### Existing test coverage

Tests cover recursive/non-recursive search, subfolder patterns, sorting, limits and invalid roots.

Missing:

- hard upper bound;
- deterministic truncation beyond limit;
- explicit truncation metadata;
- symlink behavior contract;
- relative/absolute path normalization.

---

## 15. Terminal Tool audit

File:

`tools/v1/terminal_tool.py`

### P0-TM1 — Captured stdout/stderr are unbounded

`subprocess.run(capture_output=True)` can accumulate arbitrarily large output in memory before timeout.

A hard per-stream and/or aggregate byte limit is required.

### P1-TM2 — Timeout does not define process-tree cleanup

A timed-out shell command can leave descendants running depending on OS/process topology.

T3 must define deterministic termination semantics for child process trees as far as the supported OS permits.

### P1-TM3 — `confirm_callback` is dead/misleading API

The constructor accepts `confirm_callback` but neither `run` nor `launch` invokes it.

Either the callback becomes an explicit tool-local policy hook or it is removed from the standalone contract. Authorization remains a future consumer concern.

### P1-TM4 — `danger_patterns` are metadata-only

This is acceptable only if documented as declarative consumer policy.

They must not be mistaken for intrinsic enforcement.

### P1-TM5 — Invalid timeout silently falls back

Non-positive timeout values silently use the default.

Target behavior: invalid explicit input is rejected.

### P1-TM6 — `launch` has no durable process descriptor

The caller receives only success text, with no PID/process identity and no way to correlate what was launched.

The result contract should at minimum return PID when available.

### P1-TM7 — Output encoding is environment-dependent

`text=True` uses platform defaults. Cross-platform terminal output requires a documented decoding strategy.

### P1-TM8 — Shell execution is intentionally high risk and must remain explicit

`shell=True` supports the current semantics, but the contract must state that `terminal.run` and `terminal.launch` execute shell command text, not argv-safe process invocation.

Metadata projections must retain `EXECUTE` / `EXTERNAL_SIDE_EFFECT` and HIGH risk.

### Existing test coverage

Tests cover cwd validation, successful run, stderr, timeout, generic errors, launch, dispatcher and global entrypoint.

Missing:

- output byte cap;
- process-tree timeout cleanup;
- PID result;
- invalid negative timeout rejection;
- platform decoding;
- very long command bound.

---

## 16. Window Tool audit

File:

`tools/v1/window_tool.py`

### P0-WN1 — Mutating operations select the first ambiguous title match

`focus`, `close`, `minimize`, `maximize`, and geometry use the first matching window.

A broad title query may therefore operate on the wrong application/window.

**Freeze:** side-effecting operations MUST reject ambiguous matches unless the caller supplies a stable unambiguous selector.

### P1-WN2 — Titles are not stable window identities

`list_windows` returns only strings.

Duplicate titles cannot be distinguished.

Target list/find results should expose a tool-local stable selector/handle where the platform library permits it.

### P1-WN3 — Close success is not verified

`close()` reports success after issuing the close request, not after verifying terminal window state.

The result must distinguish `request_sent` from `closed` if closure cannot be confirmed.

### P1-WN4 — Geometry can raise outside an operation error boundary

Multiple window properties are accessed before detailed error handling.

Geometry extraction must be normalized into deterministic failures/partial results.

### P1-WN5 — Live harness calls an unsupported `restore` action

`live_integration_real_machine.py` invokes:

```text
action="restore"
```

but `WindowTool.execute()` does not implement `restore`.

The implementation or live contract must be made consistent in T4/T8.

### P1-WN6 — Metadata mixes read and side-effect actions

Future projections should separate:

```text
window.list
window.find
window.geometry
window.focus
window.close
window.minimize
window.maximize
window.restore
```

with per-action effects/risk.

### Existing test coverage

Tests cover missing dependency, search/list/find, geometry, focus, close, minimize/maximize and aliases.

Missing:

- ambiguous-target rejection;
- stable selector behavior;
- restore;
- duplicate-title handling;
- closure verification state;
- property-access failures.

---

## 17. Desktop Automation audit

File:

`tools/v1/desktop_tool.py`

### P0-D1 — Optional UI dependencies can still fail at import/initialization time

Imports only catch `ImportError`, while UI libraries can fail for environment/display reasons.

Additionally the module-level default instance calls:

```python
KeyboardController()
```

during import when the class is present.

Headless or unsupported environments can therefore fail before an action is called.

**Freeze:** optional desktop dependencies and controllers must be lazy and import-safe.

### P0-D2 — Repetition/duration inputs are not hard bounded

Examples:

- click count;
- key presses;
- scroll count;
- drag/move duration;
- typing interval;
- hotkey key count;
- text length.

Extreme values can hold the tool for a long time or generate excessive side effects.

### P0-D3 — Typed text is echoed back in success results

Current success messages include the full entered text.

This can leak credentials or private content into logs/model context.

Target results must return only safe metadata such as character count and method.

### P1-D4 — Clipboard restoration is not guaranteed by `finally`

If an exception occurs after overwriting the clipboard and before restoration, the user's clipboard can remain modified.

### P1-D5 — Process-global PyAutoGUI configuration is mutated by construction

Setting `pyautogui.FAILSAFE` and `pyautogui.PAUSE` affects global library state.

The contract must make this explicit or isolate configuration.

### P1-D6 — Canonical action metadata already exists twice and can drift

`TOOL_METADATA` and `ACTIONS_METADATA` duplicate action schema/risk information.

T7 should replace this with one V2 export source of truth.

### P1-D7 — Unicode direct typing fallback is underspecified

When Unicode input is requested without clipboard and the optional keyboard backend is unavailable, PyAutoGUI direct typing may not provide the claimed Unicode behavior.

### Existing test coverage

Tests cover screen info, click, move, scroll, ASCII/Unicode typing, key press, hotkey, dispatcher and wrappers.

Notably missing:

- `mouse_drag` regression;
- lazy dependency initialization;
- clipboard restoration after failure;
- action hard bounds;
- secret-safe results;
- Unicode backend failure.

---

## 18. Web Tool audit

Files:

`tools/v1/web_tool/**`

### P0-WEB1 — SSRF/private-network boundary is absent

Current URL validation mainly requires `http://` or `https://`.

There is no complete protection against:

- loopback;
- private IPv4;
- private/link-local IPv6;
- link-local;
- metadata endpoints;
- DNS resolving to private addresses;
- public URL redirecting to private addresses.

This must be fixed before Web V2 is considered complete.

### P0-WEB2 — Browser subrequests have no private-network policy

Even if top-level URL validation is later added, page JavaScript can request internal targets unless browser routing enforces the same network boundary.

### P0-WEB3 — Chromium security isolation is explicitly weakened

Current flags include:

```text
--no-sandbox
--disable-web-security
--disable-features=IsolateOrigins,site-per-process
```

These must not be production defaults.

### P0-WEB4 — `scrape_many` can create an unbounded number of tasks

Dynamic browser work has a semaphore, but the batch itself can create arbitrary tasks and static fetches are not globally bounded by that semaphore.

Hard batch size and total concurrency limits are required.

### P0-WEB5 — Proxy credentials can leak into formatted output

The scrape formatter includes the proxy string.

Authenticated proxy URLs can therefore expose credentials in model/log output.

Proxy identity is internal telemetry and must be redacted or omitted.

### P1-WEB6 — Blocking CapSolver calls occur inside async flow

`CapSolverHandler` uses synchronous `requests.post` and `time.sleep`, while it can be called from async Playwright flow.

This can block the event loop for a long period.

### P1-WEB7 — Proxy refresh is synchronous inside async scrape orchestration

`ProxyManager.refresh_proxy_pool()` performs synchronous thread-pool work and can block the calling async path.

### P1-WEB8 — Search provider failure is indistinguishable from valid empty results

DDGS failures are swallowed and fallback failure can ultimately return `[]`.

The caller cannot distinguish "no results" from "all providers failed".

### P1-WEB9 — Retry policy is incomplete

Static retry behavior is mainly special-cased around HTTP 403.

There is no canonical policy for:

- 429;
- 5xx;
- Retry-After;
- bounded redirects;
- categorized timeout/network errors.

### P1-WEB10 — User-provided limits are not consistently validated

Examples include `max_results`, `max_chars`, timeout and URL list size.

`max_chars=0` currently falls back via truthiness rather than being rejected or honored explicitly.

### P1-WEB11 — Canonical result is presentation text

Search/read results are Markdown/Text/JSON strings.

Structured evidence/provenance is lost or difficult to consume programmatically.

### P1-WEB12 — Provenance contract is incomplete

Missing/unstable fields include:

- requested URL;
- final URL;
- redirect information;
- final status;
- canonical URL;
- fetched timestamp;
- content hash;
- extractor/render mode;
- truncation reason.

### P1-WEB13 — Latent async extractor bug

`extract_tables_and_charts` calls:

```python
page_obj.content()
```

without `await` when supplied a non-string primary input.

Current main callers usually pass HTML strings, so this is latent but incorrect.

### P1-WEB14 — Requested selector timeout is swallowed

A caller asking to wait for a selector can receive extracted output even when the selector condition failed.

The contract must define whether that is warning, retryable failure, or successful best-effort extraction.

### P1-WEB15 — CAPTCHA solving is mixed into ordinary read semantics

CAPTCHA solving introduces external API calls, latency, possible monetary cost and a different risk boundary.

It should not be a default part of canonical `web.read`. If retained, it must be an explicit optional operation/adapter with separate metadata.

### P2-WEB16 — Formatter correctness issues

The text formatter currently contains the typo:

```text
KẾT QUẢ TÌM KIẾM CHÓ
```

Markdown table cells are also not robustly escaped.

These are low-risk but should be removed during structured-output migration.

### Existing test coverage

Current Web tests cover:

- proxy latency sorting;
- fastest-proxy selection;
- DDG search;
- static scrape;
- JS fallback;
- 403 proxy removal;
- single-owner concurrent browser initialization;
- max chars;
- execute routing;
- global `run` resource cleanup.

Missing priority coverage:

- SSRF/private IP;
- DNS rebinding-style resolution policy;
- redirect to private network;
- browser private subrequest block;
- batch hard limit;
- aggregate concurrency;
- 429/5xx retry semantics;
- proxy credential redaction;
- structured result schema;
- provider failure vs empty search;
- selector failure semantics;
- cancellation during browser fetch;
- async CapSolver isolation/removal.

---

## 19. Live harness audit

Directory:

`tools/v1/live/**`

These scripts are useful real-machine smoke tests but MUST NOT be normal unit-test gates.

### P1-L1 — No explicit opt-in environment gate

Real-machine tests open applications, type, click, close windows, execute shell commands and write files.

T8 must require an explicit opt-in such as:

```text
RUN_TOOLS_V1_LIVE=1
```

### P1-L2 — Logs/generated artifacts are written below the source tree

Live runs create `tools/v1/live/logs/**`.

Target harnesses should use a configured artifact/temp directory and clean up by default.

### P1-L3 — Platform assumptions are inconsistent

Examples include:

- hard-coded `D:\assistant\.venv\Scripts\python.exe`;
- Windows `dir` commands in otherwise cross-platform scenarios;
- OS-specific application title assumptions.

### P1-L4 — Stateful ordered tests are brittle

Multiple live suites depend on prior numbered test methods and shared mutable class state.

The final live gate should be an explicit scenario/pipeline runner with deterministic setup/teardown.

### P1-L5 — Web live tests parse presentation strings

Current pipelines regex URLs out of Web Tool formatted text.

After T6 they should consume structured search/read data directly.

### P1-L6 — Real-window cleanup inherits ambiguous-title risk

The live GUI scenario may close the wrong matching user window if multiple windows share a title keyword.

T4 stable/unique window targeting must land before declaring the live GUI gate safe.

---

# PART IV — TARGET PER-TOOL PROJECTIONS

## 20. File

```text
file.read       READ       IDEMPOTENT
file.search     READ       IDEMPOTENT
file.write      WRITE      IDEMPOTENT for same full replacement payload
file.append     WRITE      NON_IDEMPOTENT
file.replace    WRITE      operation-specific replay contract; default UNKNOWN until finalized
```

## 21. Glob

```text
glob.find       READ       IDEMPOTENT
```

## 22. Terminal

```text
terminal.run       EXECUTE + EXTERNAL_SIDE_EFFECT   UNKNOWN
terminal.launch    EXECUTE + EXTERNAL_SIDE_EFFECT   NON_IDEMPOTENT
```

`terminal.run` cannot globally claim idempotency because arbitrary shell commands may mutate external state.

## 23. Window

```text
window.list       READ                         IDEMPOTENT
window.find       READ                         IDEMPOTENT
window.geometry   READ                         IDEMPOTENT
window.focus      EXTERNAL_SIDE_EFFECT         IDEMPOTENCY UNKNOWN
window.close      EXTERNAL_SIDE_EFFECT         NON_IDEMPOTENT
window.minimize   EXTERNAL_SIDE_EFFECT         IDEMPOTENCY UNKNOWN
window.maximize   EXTERNAL_SIDE_EFFECT         IDEMPOTENCY UNKNOWN
window.restore    EXTERNAL_SIDE_EFFECT         IDEMPOTENCY UNKNOWN
```

## 24. Desktop

```text
desktop.screen_info   READ
desktop.mouse_move    EXTERNAL_SIDE_EFFECT
desktop.mouse_click   EXTERNAL_SIDE_EFFECT
desktop.mouse_drag    EXTERNAL_SIDE_EFFECT
desktop.mouse_scroll  EXTERNAL_SIDE_EFFECT
desktop.type_text     EXTERNAL_SIDE_EFFECT
desktop.press_key     EXTERNAL_SIDE_EFFECT
desktop.hotkey        EXTERNAL_SIDE_EFFECT
```

All side-effecting desktop actions default to NON_IDEMPOTENT or UNKNOWN unless a narrower semantic proof exists.

## 25. Web

```text
web.search        READ   IDEMPOTENT in side-effect sense
web.read          READ   IDEMPOTENT in side-effect sense
web.read_many     READ   IDEMPOTENT in side-effect sense
```

Idempotent here means replay does not intentionally create an external mutation; it does not promise byte-identical results over time.

CAPTCHA solving, if retained, is not part of ordinary `web.read`.

---

# PART V — T0 → T8 COMPLETION ROADMAP

## T0 — Contract Freeze

**Status:** CLOSED.

Deliverables:

- this document;
- exact scope boundary;
- standard result/error contract;
- Metadata V2 projection contract;
- P0/P1 inventory;
- T1–T8 exit gates.

Exit gate:

```text
branch exists
document committed
no production tool code changed
no path outside tools/v1 changed
```

---

## T1 — Shared Standalone Tool Foundation

**Status:** COMPLETE.

Completion evidence:

- shared production foundation added only under `tools/v1/_shared/**`;
- conformance/scope tests added only under `tools/v1/test/**`;
- focused HEAD validation: **32 tests + 22 subtests passed**;
- GitHub Actions full repository pytest suite passed on production-foundation commit `4a8d66f2c004f918f3ea0d60f49bc1a4e456e410` (Phase 5.7 Exit Gate run `35732654547`);
- post-foundation HEAD changes are T1 tests plus a typing-only `ToolResultMeta` required-field correction, covered by the focused HEAD suite;
- compare from the frozen T1 plan commit shows no modifications to any physical tool, `tools/v1/live/**`, `se/**`, or `cl/**`.

Target package, kept intentionally small:

```text
tools/v1/_shared/
├── contracts.py
├── errors.py
├── limits.py
├── metadata.py
└── validation.py
```

Responsibilities:

- canonical result envelope;
- canonical errors;
- JSON-safe validation;
- common limits helpers;
- Metadata V2 validation;
- projection/bind contract validation;
- redaction helpers.

Non-goals:

- no runtime;
- no registry;
- no policy engine;
- no SE/CL imports.

Exit:

- unit tests for shared contracts;
- invalid metadata fails deterministically;
- security-critical bounds cannot be disabled accidentally.

---

## T2 — Filesystem Tools Completion

**Status:** COMPLETE.

Completion record:

```text
tools/v1/T2_FILESYSTEM_TOOLS_COMPLETION.md
```

Final T2 code HEAD before completion-doc commit:

```text
c1e96899d5b7a94d091c3b3490738c4f7c4a2f17
```

Final CI evidence is recorded in the completion document.

Scope:

```text
file_tool.py
find_by_glob.py
test_file_tool.py
test_glob_search_tool.py
```

Required fixes:

- fail closed when existing file cannot be read;
- bounded file read/search/replace;
- deterministic temp cleanup;
- explicit mutation semantics;
- canonical path representation;
- deterministic glob truncation;
- hard max results;
- structured matches/results;
- remove warning strings from path arrays;
- metadata/runtime single source for danger patterns.

Exit:

- filesystem unit/regression suite green;
- no unbounded default read/search path;
- failure cannot overwrite an unreadable existing file;
- glob result shape is deterministic.

---

## T3 — Terminal Completion

**Status:** COMPLETE.

Boundary source:

```text
tools/v1/T3_TERMINAL_BOUNDARY_FREEZE.md
```

Implementation plan:

```text
tools/v1/T3_TERMINAL_IMPLEMENTATION_PLAN.md
```

Completion record:

```text
tools/v1/T3_TERMINAL_COMPLETION.md
```

Final T3 code HEAD before completion-doc commit:

```text
fa21ebaff83d8c5995d2a12fd5c872db8a14229b
```

Final repository CI is green.

Scope:

```text
terminal_tool.py
test_terminal_tool.py
```

Required fixes:

- bounded stdout/stderr;
- explicit invalid timeout handling;
- process-tree timeout cleanup contract;
- PID/process metadata for launch;
- command length limits;
- cross-platform decoding strategy;
- remove or correctly define dead confirmation callback;
- structured exit result.

Exit:

- timeout leaves no expected managed child process behind in supported test scenarios;
- huge output cannot exhaust memory;
- launch result is correlatable;
- unit tests require no real destructive command.

---

## T4 — Window Completion

**Status:** COMPLETE.

Implementation plan:

```text
tools/v1/T4_WINDOW_IMPLEMENTATION_PLAN.md
```

Completion record:

```text
tools/v1/T4_WINDOW_COMPLETION.md
```

Final T4 code HEAD before completion-doc commits:

```text
65bfe19a4ddcc41e7cdc685e0844960efa6e9af2
```

Final repository CI is green.

Scope:

```text
window_tool.py
test_window_tool.py
```

Required fixes:

- stable window descriptor/selector where supported;
- ambiguity rejection for side effects;
- implement/freeze `restore`;
- normalized geometry;
- closure request vs verified state;
- structured list/find results;
- lazy dependency behavior.

Exit:

- duplicate-title scenario cannot mutate an arbitrary first window;
- live harness no longer calls undefined actions;
- missing dependency is deterministic and import-safe.

---

## T5 — Desktop Completion

**Status:** COMPLETE.

Implementation plan:

```text
tools/v1/T5_DESKTOP_IMPLEMENTATION_PLAN.md
```

Completion record:

```text
tools/v1/T5_DESKTOP_COMPLETION.md
```

Final T5 code HEAD before completion-doc commits:

```text
029188cfb79f0dc8a1918b48459307b66bf994f4
```

Final repository CI is green.

Scope:

```text
desktop_tool.py
test_desktop_automation.py
```

Required fixes:

- lazy dependency/controller initialization;
- bounds for counts/durations/text/hotkeys;
- clipboard restoration in `finally`;
- secret-safe result messages;
- explicit Unicode typing behavior;
- `mouse_drag` tests;
- reduce process-global side effects.

Exit:

- module imports in headless/missing-dependency tests;
- no full typed secret returned in result;
- clipboard is restored after injected failure;
- all side-effect counts/durations are hard bounded.

---

## T6 — Web Tool Completion

**Status:** **COMPLETE / GREEN / FROZEN @ `1138a83d`**

Implementation plan:

```text
tools/v1/T6_WEB_IMPLEMENTATION_PLAN.md
```

Durable boundary/completion authority:

```text
Issue #6 — [CHECKPOINT] T6 → T7 Metadata V2 plan freeze @ 1138a83d
```

T6 completed the physical Web Tool hardening before any logical Metadata V2 projection. The frozen physical implementation is the baseline preserved by T7/T8.

Scope completed:

```text
tools/v1/web_tool/**
tools/v1/test/test_web_tool.py
```

The T6 completion closes the Web P0 safety boundary, including the URL/network/private-address controls, browser subrequest policy, safe browser defaults, hard batch/concurrency/output bounds, structured results, provider/retry semantics, credential redaction, async/resource cleanup, provenance, and extractor correctness required by the T6 plan.

Frozen T6 invariant carried forward:

- T7/T8 do not rewrite T6 network/security/fetch behavior;
- physical package identity remains `web_tool`;
- physical actions remain `search`, `scrape`, and `scrape_many`;
- ToolResult remains the physical result boundary.

---

## T7 — Metadata V2 Logical Web Projection

**Status:** **COMPLETE / GREEN / AUDIT-APPROVED / FROZEN @ `adea67c3`**

Implementation plan:

```text
tools/v1/T7_METADATA_V2_IMPLEMENTATION_PLAN.md
```

Completion record:

```text
tools/v1/T7_METADATA_V2_COMPLETION.md
```

T7 introduced the logical/physical split for Web:

```text
web.search     -> web_tool.run(action="search", ...)
web.read       -> web_tool.run(action="scrape", ...)
web.read_many  -> web_tool.run(action="scrape_many", ...)
```

and froze:

```text
CapabilityInvocation.capability_id = logical capability ID
ToolResult.tool                     = physical tool identity
ToolResult.action                   = canonical physical action
```

T7 used a transitional server-owned Metadata V2 dialect while proving logical projection, fixed physical bindings, zero-partial-registration preflight, Agent Web Researcher migration, and V1 compatibility.

Historical note: T7's transitional `metadata_version=2 / capabilities / binding / parameters` shape is **not** the final Metadata V2 authority. T8 migrated the repository to the canonical T1 `manifest_version="2.0" / exports / bind / input_schema / output_schema` contract.

---

## T8 — Metadata V2 Consumer Convergence & Final Gate

**Status:** **T8-A→T8-H COMPLETE / GREEN / FINAL-FREEZE READY**

Implementation plan:

```text
tools/v1/T8_METADATA_V2_CONVERGENCE_IMPLEMENTATION_PLAN.md
```

Completion record:

```text
tools/v1/T8_METADATA_V2_COMPLETION.md
```

Frozen code/test candidate:

```text
7a84edf2d228596016cd081a0679f8d5031382d3
```

Completion-document commit:

```text
a2c635aeaa232cbc16b572a0e584717059af72ec
```

T8 converged SERVER and CLIENT consumers on one canonical Metadata V2 authority:

```text
manifest_version = "2.0"
exports[]
bind
input_schema
output_schema
```

Canonical validator:

```text
tools/v1/_shared/metadata.py
validate_tool_manifest_v2()
```

T8 completion guarantees:

- SE consumes canonical logical exports;
- CL discovers package entrypoints and normalizes canonical V2 exports;
- V2 CLIENT placement is explicit exact-ID opt-in through `enabled_v2_capabilities`;
- repository default advertises no Web V2 CLIENT exports;
- legacy V1 wildcard does not implicitly enable V2;
- generic bind keys are immutable/non-overridable;
- logical export version is distinct from physical package version;
- matching SERVER + CLIENT logical definitions may coexist;
- divergent same-ID logical contracts reject before catalog mutation;
- registration preflight remains fail-closed for foreseeable conflicts;
- reconnect reuses the canonical logical definition while preserving existing ACK/READY lifecycle;
- routing priority, R7 continuation/reconciliation, MCP ownership, and T6 Web runtime/security semantics remain unchanged;
- existing capability list/get endpoints expose the canonical logical definition and implementations.

Exact-SHA gate for `7a84edf2`:

```text
Architecture Baseline — SUCCESS
  Linux: 1014 passed, 1 skipped, 16 warnings, 159 subtests
  Windows client contracts: 94 passed, 2 warnings

Phase 5 Exit Gates — SUCCESS
  40 passed
```

### T8 roadmap reconciliation

The original pre-T6 version of this document assigned the name **T8** to a live-harness cleanup/full-tools gate:

- rewrite live scenarios around structured outputs;
- remove source-tree artifact pollution;
- portable temporary workspaces;
- safe GUI target isolation;
- terminal/Web live smoke.

That draft phase was **superseded before implementation** by the accepted T7→T8 Metadata V2 convergence checkpoint and implementation plan. The accepted T8 plan explicitly kept `tools/v1/live/**` out of scope.

Therefore:

```text
old draft T8 live-harness bullets != unfinished current T8 work
```

Those live-harness improvements remain potential future standalone work and require a fresh scope/phase assignment before code.

T8→T9 rule:

```text
T9 requires a fresh boundary audit.
No T9 code is implicitly authorized by T8 completion.
```

---

## TV1-T9 — Remaining Tools Canonical Logical Export Migration

**Status:** **BOUNDARY AUDITED / IMPLEMENTATION PLAN FROZEN / CODE NOT STARTED**

Canonical plan:

```text
tools/v1/TV1_T9_LOGICAL_EXPORT_MIGRATION_IMPLEMENTATION_PLAN.md
```

TV1-T9 owns the remaining migration from broad physical V1 capability roots to canonical action-specific Metadata V2 logical exports for:

```text
file_tool
find_by_glob
terminal_tool
window_tool
desktop_automation
```

The phase must preserve physical Python `run(...)` entrypoints and physical ToolResult identity while migrating SERVER/CLIENT/catalog/Agent-facing capability IDs.

The current plan freezes 24 logical IDs across:

```text
file.*
glob.find
terminal.*
window.*
desktop.*
```

TV1-T9 also owns the atomic migration of `agent-command-reviewer` and the exact CLIENT V2 allowlist needed to avoid capability loss after the physical V1 roots become canonical V2.

Explicitly not TV1-T9:

- Web runtime/security changes;
- multi-version catalog identity;
- alias/deprecation routing;
- provider lowering/adapters;
- AE-R9 retry/branching;
- `tools/v1/live/**`.

---

## TV1-T10 — Live Harness & Real-Machine Exit Gate

**Status:** **PHASE ALLOCATED ONLY / NOT AUDITED / NOT OPEN**

The superseded draft T8 live-harness work is reassigned to TV1-T10.

TV1-T10 is ordered after TV1-T9 so the live suites validate the final logical/physical Tools V1 contract.

Reserved future scope:

```text
tools/v1/live/**
live-only docs/tests/helpers
```

Target concerns include:

- explicit `RUN_TOOLS_V1_LIVE=1` gate;
- no source-tree log/workspace pollution;
- portable Python/command discovery;
- deterministic scenario setup/teardown;
- structured ToolResult consumption instead of string parsing;
- safe GUI target isolation;
- deterministic process cleanup;
- separate opt-in Web network smoke.

No TV1-T10 implementation is authorized until a fresh boundary audit.

---

# PART VI — FREEZE RULES

## 26. Changes requiring contract review

After T0, the following require an explicit contract update before implementation:

- renaming a public tool/export ID;
- changing an export binding;
- changing effect/idempotency semantics;
- changing canonical result envelope;
- removing a documented stable error code;
- widening an intrinsic security boundary;
- making a formerly bounded input unbounded;
- adding any dependency from `tools/v1` to `se` or `cl`.

---

## 27. Changes allowed without reopening architecture

Implementation details may change freely when the public contract is preserved, including:

- internal helper layout;
- extraction algorithms;
- search provider implementation;
- browser pooling implementation;
- process launch mechanism;
- filesystem traversal implementation;
- optional dependency implementation.

---

## 28. T0 decision and current reconciled state

The original T0 decision remains the architectural source of truth for the **tool subsystem**:

```text
SOURCE OF TRUTH
    tools/v1/TOOLS_V1_CONTRACT_FREEZE.md

BRANCH
    tools-v1-contract-freeze

ORIGINAL BASELINE
    fe6077b8

TOOL DEPENDENCY INVARIANT
    tools/v1 ─X─> se
    tools/v1 ─X─> cl
```

The original "only tools/v1 may change" write boundary applied to the standalone T0–T6 implementation track. It was explicitly reopened for consumer-side integration by the later durable checkpoints:

```text
Issue #6
    T6 → T7 logical Web projection

Issue #7
    T7 → T8 canonical Metadata V2 consumer convergence
```

Current phase state:

```text
T0–T5  COMPLETE
T6     CLOSED / GREEN / FROZEN @ 1138a83d
T7     CLOSED / GREEN / AUDIT-APPROVED / FROZEN @ adea67c3
T8     A→H COMPLETE / GREEN / FINAL-FROZEN
       code/test candidate @ 7a84edf2
       completion document @ a2c635ae
       Issue #7 CLOSED / COMPLETED
T9     BOUNDARY AUDITED / PLAN FROZEN / CODE NOT STARTED
T10    PHASE ALLOCATED ONLY / NOT AUDITED / NOT OPEN
```

Current canonical Metadata V2 handoff:

```text
manifest_version="2.0"
exports
bind
input_schema
output_schema
```

Current Web placement:

```text
web.search     SERVER by default
web.read       SERVER by default
web.read_many  SERVER by default

CLIENT V2
    explicit exact-ID opt-in only
    default enabled_v2_capabilities=[]
```

Current next action:

```text
create/open the TV1-T9 checkpoint, claim TV1-T9-A, and freeze exact schemas/tests before any production code
```

Remaining V1-tool logical-export migration is now assigned to TV1-T9. The old live-harness work is assigned to TV1-T10, but TV1-T10 remains unopened until a fresh boundary audit.
