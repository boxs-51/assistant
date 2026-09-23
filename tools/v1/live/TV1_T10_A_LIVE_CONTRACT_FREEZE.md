# TV1-T10-A Live Harness Contract Freeze

Repository: `boxs-51/assistant`  
Track: `TV1-T*`  
Coordination authority: Issue #38  
Canonical base: `main@78479a64a97353094817b92a090449191782d366`  
Mode: **CONTRACT FREEZE / DOCS ONLY / NO REAL-MACHINE EXECUTION**

---

# 1. Objective

TV1-T10 modernizes the historical `tools/v1/live/**` real-machine harness without
changing the production Tool contracts frozen through TV1-T9.

T10-A freezes the live execution contract before any live harness code is rewritten.

This stage does **not**:

- launch processes;
- access the network;
- open or control GUI applications;
- type/click/scroll;
- modify production tools;
- add live scenarios to default CI.

---

# 2. Safety gate hierarchy

Every live scenario MUST fail closed unless the required explicit environment gates are
present.

## 2.1 Master gate

Canonical master opt-in:

```text
RUN_TOOLS_V1_LIVE=1
```

Exact rule:

```text
environment value == literal string "1"  -> enabled
anything else / unset                     -> disabled
```

No truthy aliases such as `true`, `yes`, or `on` are accepted for this harness.

The master gate MUST be checked before:

- artifact directory creation;
- cwd changes;
- subprocess creation;
- network requests;
- GUI discovery;
- GUI mutation;
- reading or writing user files.

A disabled master gate exits without real-machine side effects.

## 2.2 Network gate

Network-live scenarios require both:

```text
RUN_TOOLS_V1_LIVE=1
RUN_TOOLS_V1_LIVE_NETWORK=1
```

Exact rule for the network gate is also literal `"1"`.

Network opt-in never implies GUI opt-in.

## 2.3 GUI gate

Window/Desktop scenarios require both:

```text
RUN_TOOLS_V1_LIVE=1
RUN_TOOLS_V1_LIVE_GUI=1
```

Exact rule for the GUI gate is literal `"1"`.

GUI opt-in never implies network opt-in.

## 2.4 Gate matrix

```text
local File/Glob/Terminal     master
network Web                  master + network
Window/Desktop GUI           master + gui
combined network+GUI         master + network + gui
```

T10 should avoid combined network+GUI scenarios unless a later audit proves they are
necessary.

---

# 3. Artifact-root contract

Live evidence and generated files MUST NOT be written beneath the repository source
tree.

Canonical optional configuration:

```text
TOOLS_V1_LIVE_ARTIFACT_ROOT=<absolute-path>
```

## 3.1 Resolution order

After the required live gate succeeds:

1. if `TOOLS_V1_LIVE_ARTIFACT_ROOT` is set:
   - it MUST be an absolute path;
   - expand user markers;
   - resolve symlinks / normalized path as far as the platform permits;
   - reject it if the resolved path is the repository root or a descendant of the
     repository root;
2. otherwise:
   - allocate a system temporary root through the Python standard library;
3. create one unique per-run directory below that root.

Recommended run-directory shape:

```text
tools-v1-live-<run_id>/
```

`run_id` MUST be unique per execution.

## 3.2 Source-tree prohibition

The harness MUST reject configured artifact roots that resolve to:

```text
<repo-root>
<repo-root>/**
```

This rejection occurs before scenario side effects.

Historical locations such as:

```text
tools/v1/live/logs/**
tools/v1/live/test_*/**
```

are forbidden as T10 run output.

## 3.3 Ambient cwd rule

The harness MUST NOT use the source directory as process-wide ambient cwd authority.

Each tool/subprocess call that needs a cwd receives an explicit scenario-owned path.

A scenario runner MUST NOT depend on `os.chdir(tools/v1/live)`.

---

# 4. Structured ToolResult authority

The canonical ToolResult envelope remains the semantic authority:

```text
ok
tool
action
data
error
meta
```

T10 live correctness MUST NOT depend on:

- `str(result)`;
- `repr(result)`;
- regex parsing of a rendered ToolResult;
- substring matching against the presentation form of a successful result.

## 4.1 Success/failure handling

Tool invocation transport success:

```python
result["ok"] is True
```

Tool contract failure:

```python
result["ok"] is False
result["error"]["code"]
result["error"]["message"]
result["error"]["retryable"]
result["error"]["details"]
```

Scenario assertions use action-specific `data` fields.

Important Terminal rule:

`terminal.run` may return `ok=true` when the process itself exits non-zero. A scenario
that requires command success MUST separately assert the structured exit-code field.

## 4.2 Web data rule

Network-live scenarios consume structured Web data from the canonical Web ToolResult
fields defined by the production tool.

URL discovery MUST come from structured search result entries.

Regex extraction from `str(web_result)` is forbidden.

---

# 5. Scenario model

Historical numbered `unittest` methods are not workflow authority.

T10-B MUST introduce an explicit scenario runner.

Each scenario has one category:

```text
LOCAL
NETWORK
GUI
```

Each scenario owns:

- setup;
- step sequence;
- generated files;
- launched processes;
- teardown;
- evidence result.

A scenario MUST NOT require a previous scenario to have executed.

Cross-step state inside one scenario is explicit scenario state, not class-global test
state.

---

# 6. Evidence contract

Every live execution MUST produce one structured JSON evidence document under its
per-run artifact directory.

Schema version:

```text
tools.v1.live.evidence/1
```

Required top-level fields:

```json
{
  "schema": "tools.v1.live.evidence/1",
  "run_id": "...",
  "category": "LOCAL | NETWORK | GUI",
  "started_at": "RFC3339 UTC",
  "finished_at": "RFC3339 UTC",
  "status": "PASS | FAIL | SKIP",
  "environment": {},
  "scenarios": [],
  "cleanup": {}
}
```

## 6.1 Environment fields

Required:

```text
platform_system
platform_release
python_version
python_executable
artifact_directory
enabled_gates
```

If repository commit identity can be obtained without making Git a runtime dependency,
it may be recorded as optional metadata.

Secrets, environment dumps, tokens, proxy credentials and cookies MUST NOT be written.

## 6.2 Scenario result

Each scenario entry contains:

```json
{
  "id": "stable-scenario-id",
  "category": "LOCAL | NETWORK | GUI",
  "status": "PASS | FAIL | SKIP",
  "steps": [],
  "error": null
}
```

Each step contains at minimum:

```text
id
status
tool
action
summary
```

Raw secret-bearing inputs MUST NOT be copied into evidence.

Failures record structured error classification instead of only presentation text.

---

# 7. Owned-process lifecycle

Every background process launched by a live scenario MUST become explicit scenario
state.

The canonical source of a launched process identity is the structured ToolResult data
returned by the launch action.

A process is eligible for cleanup only when its PID was recorded as created/owned by the
current scenario run.

Forbidden cleanup authority:

- process name alone;
- generic window title;
- global kill-by-name;
- stale PID from a previous run.

## 7.1 Cleanup requirements

Cleanup runs from `finally`-equivalent control flow.

For each owned live process:

1. observe whether it is still alive;
2. request bounded graceful termination when supported;
3. wait for a bounded interval;
4. escalate only for that owned PID/process tree when necessary;
5. record cleanup outcome in evidence.

Cleanup failure makes the run fail unless a later phase freezes a narrower explicit
exception.

---

# 8. GUI ownership contract

GUI mutation requires `RUN_TOOLS_V1_LIVE_GUI=1`.

A GUI scenario MUST create a uniquely identifiable test-owned target.

The scenario MUST capture and retain process/window identity after launch.

Mutation and cleanup prefer stable identity in this order:

```text
window handle
owned PID + verified window
unique generated title only as discovery aid
```

Generic titles such as:

```text
Notepad
gedit
Text Editor
```

MUST NOT be cleanup authority.

Window ambiguity remains a fail-closed result, not a reason to choose an arbitrary
window.

Desktop mouse/keyboard actions are allowed only after the target ownership/foreground
precondition for the scenario has been established.

---

# 9. Portability contract

Python subprocess scenarios use:

```python
sys.executable
```

as canonical Python discovery.

Hard-coded repository-specific interpreter paths are forbidden.

Shell commands MUST be platform-aware.

A Windows-only command such as `dir` cannot be used as a cross-platform correctness
assertion.

Command quoting MUST use platform-correct construction rather than hand-built shell
quoting when practical.

---

# 10. Network-live isolation

Network scenarios are a separate correctness domain from LOCAL scenarios.

A failed external dependency MUST NOT make local File/Glob/Terminal correctness appear
failed.

Network evidence must distinguish at least:

```text
tool contract failure
network/remote unavailability
empty valid result
scenario assertion failure
```

T10-D may choose stable public targets, but it cannot assume changing Web content is
byte-identical.

No CAPTCHA-solving/payment-bearing path is part of the ordinary T10 Web live gate
unless a new explicit audit authorizes it.

---

# 11. Default CI exclusion

Live real-machine scenarios MUST remain excluded from ordinary repository pytest/CI.

Permitted default-CI coverage:

- pure helper tests;
- environment-gate tests;
- artifact-root validation tests;
- evidence serialization tests;
- fake process ownership/cleanup tests;
- fake GUI ownership tests.

These tests MUST NOT:

- access external network;
- open GUI applications;
- mutate real desktop state;
- launch persistent background processes.

The real live runner remains explicit operator action.

---

# 12. T10 stage ownership after this freeze

```text
T10-A  THIS DOCUMENT / contract freeze
T10-B  scenario runner, gate/artifact/evidence helpers, deterministic lifecycle
T10-C  LOCAL Terminal + File + Glob real-machine scenarios
T10-D  NETWORK Web live scenarios
T10-E  GUI Window/Desktop owned-target scenarios
T10-F  cross-platform evidence + completion/final exit gate
```

T10-B may add side-effect-free helpers under `tools/v1/live/**` plus focused fake/unit
coverage.

T10-C/D/E are the first phases permitted to execute their respective real-machine
scenario categories.

---

# 13. Production boundary

Default T10 ownership remains:

```text
tools/v1/live/**
live-only docs/tests/helpers
```

Production files remain non-scope unless a rewritten live regression proves a real
production defect.

If that occurs:

1. stop T10 progression;
2. post a separate P0/P1 finding on Issue #38;
3. identify the production owner;
4. audit the boundary before any production patch.

No live harness requirement may silently redefine the production ToolResult or Metadata
V2 contracts.

---

# 14. T10-A regression/acceptance matrix

T10-A is complete when:

1. Issue #38 is the active coordination authority;
2. branch is based on exact canonical post-T9 main;
3. this contract exists;
4. master/network/GUI gate semantics are exact;
5. artifact-root precedence and source-tree rejection are exact;
6. structured ToolResult authority is explicit;
7. scenario independence is explicit;
8. evidence schema is frozen;
9. owned-process cleanup authority is frozen;
10. GUI ownership rule is frozen;
11. portability rule is frozen;
12. network/local isolation is frozen;
13. default-CI exclusion is frozen;
14. no real-machine scenario was executed;
15. no production runtime file was changed.

T10-B MUST NOT weaken these invariants without reopening Issue #38 boundary audit.
