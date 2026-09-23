# T2 → T3 Boundary Freeze — Terminal Tool

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Main baseline: `a55e4fd2a20ddccbd227e770d26fae72e33bc88e`  
T2 final code baseline: `c1e96899d5b7a94d091c3b3490738c4f7c4a2f17`  
T2 completion: `tools/v1/T2_FILESYSTEM_TOOLS_COMPLETION.md`  
Mode: **BOUNDARY/AUDIT FREEZE ONLY — T3 CODE NOT IMPLEMENTED**

---

# 1. Boundary decision

T2 is closed.

T3 owns only the standalone Terminal Tool completion.

Canonical T3 production scope:

```text
tools/v1/terminal_tool.py
```

Canonical T3 unit/regression scope:

```text
tools/v1/test/test_terminal_tool.py
```

T3 may add one focused test module only if process-tree tests become clearer when separated:

```text
tools/v1/test/test_terminal_process_tree.py
```

T3 documentation:

```text
tools/v1/T3_TERMINAL_BOUNDARY_FREEZE.md
tools/v1/T3_TERMINAL_IMPLEMENTATION_PLAN.md        # only if a separate exact plan is requested
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md              # status only after T3 completion
```

---

# 2. Frozen no-touch boundary

T3 MUST NOT modify:

```text
tools/v1/_shared/**
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
```

T1 and T2 are dependencies, not editable implementation surfaces for T3.

If Terminal discovers a missing generic shared primitive, T3 must record it as a follow-up contract issue instead of silently expanding `_shared`.

Current audit does not require a T1 shared-foundation change.

---

# 3. Current Terminal public surface

Current physical module exposes:

```text
TOOL_METADATA
TerminalTool
TerminalTool.run(...)
TerminalTool.launch(...)
TerminalTool.execute(...)
run(...)
```

Current actions:

```text
run
launch
```

T3 preserves these physical actions.

Metadata V2 projections remain T7:

```text
terminal.run
terminal.launch
```

T3 does not implement `exports`, projection binding, registry or consumer routing.

---

# 4. Consumer blast radius

Repository search found direct Terminal Tool consumers only in:

```text
tools/v1/test/test_terminal_tool.py

tools/v1/live/live_real_system_operations.py
tools/v1/live/live_multisource_pipeline.py
tools/v1/live/live_terminal_python_pipeline.py
tools/v1/live/live_integration_real_machine.py
```

No SE/CL production call-site needs to be changed for T3.

Live harness migration remains T8.

Therefore T3 may migrate terminal public results from presentation strings to canonical ToolResult without changing live scripts in the same phase.

---

# 5. P0/P1 audit findings

## P0-TM1 — stdout/stderr capture is unbounded

Current:

```python
subprocess.run(
    ...,
    capture_output=True,
    text=True,
)
```

This buffers stdout/stderr until process completion.

A command can therefore exhaust process memory before timeout handling.

T3 must replace all-at-once capture with bounded incremental pipe consumption.

---

## P0-TM2 — timeout does not guarantee descendant cleanup

Current `subprocess.run(..., shell=True, timeout=N)` owns the shell process, but a timeout is not a sufficient cross-platform contract for killing all descendants spawned by that shell.

T3 must own a process group/session for `run`.

On timeout/output-limit/cancellation cleanup, T3 must make a bounded best effort to terminate the whole managed tree, not just the shell leader.

The exit gate must include a real subprocess-tree regression on supported CI platforms.

---

## P1-TM3 — invalid explicit timeout silently becomes the default

Current:

```python
timeout if timeout is not None and timeout > 0 else default_timeout
```

Thus:

```text
timeout = 0
timeout = -1
timeout = True
```

can acquire unintended semantics.

T3 must use the T1 hard-limit resolver contract.

Explicit invalid input fails with `INVALID_ARGUMENT`.

---

## P1-TM4 — command length is unbounded

Current command accepts arbitrary string length before invoking the shell.

T3 must impose a hard command-character limit before process creation.

---

## P1-TM5 — cwd validation returns presentation strings

Current `_validate_cwd` encodes errors as Vietnamese strings and the caller distinguishes errors using `startswith("Lỗi:")`.

T3 must use structured internal exceptions and canonical ToolResult.

---

## P1-TM6 — result/error text echoes the command

Current timeout and launch success messages include the complete command.

Commands may contain credentials, access tokens, signed URLs or other sensitive values.

T3 must not copy the command into result/error messages/details.

This is different from stdout/stderr: per the T1 contract, arbitrary process output is not generically secret-scrubbed because doing so would corrupt legitimate output.

---

## P1-TM7 — launch result has no process identity

Current launch returns only a human success sentence.

T3 must return the PID of the managed process/process-group leader when creation succeeds.

The PID is a correlation identity, not a promise that it is the final GUI application's PID when `shell=True`.

---

## P1-TM8 — output decoding is environment implicit

Current `text=True, errors="replace"` lets subprocess use platform defaults without recording the effective decoding contract.

T3 must capture bytes first, then decode explicitly.

---

## P1-TM9 — confirm_callback is dead/misleading API

The constructor accepts:

```text
confirm_callback
```

but neither `run` nor `launch` uses it.

T1 froze authorization/HITL as a future consumer responsibility.

T3 must not introduce tool-local approval logic.

The canonical T3 contract removes `confirm_callback` from the meaningful execution contract. Backward-compatibility handling, if retained temporarily, must be explicitly deprecated and must not pretend to enforce authorization.

---

## P1-TM10 — launch lifecycle is underspecified

Current launch:

```python
Popen(
    shell=True,
    stdout=DEVNULL,
    stderr=DEVNULL,
)
```

does not define:

- stdin behavior;
- process-group/session behavior;
- returned PID semantics;
- startup failure boundary.

T3 freezes these semantics below.

---

## P1-TM11 — broad Exception handling hides programming failures

Current run/launch catch `Exception`.

T3 must catch expected process/OS/validation errors only.

```text
KeyboardInterrupt
SystemExit
async cancellation where applicable
programmer errors
```

must not be converted into fake operational ToolResult failures.

Terminal is currently synchronous, so no async runtime is introduced in T3.

---

# 6. Frozen T3 version identity

```text
TERMINAL_TOOL_VERSION = "2.0.0"

tool   = terminal_tool
action = run | launch
```

Both actions return the T1 canonical ToolResult.

---

# 7. Frozen hard limits

T3 implementation must use explicit constants and T1 limit mechanics.

Freeze:

```text
MAX_COMMAND_CHARS = 32_768
MAX_CWD_CHARS     = 4_096

RUN_TIMEOUT:
  default = 30 seconds
  minimum = 1 second
  maximum = 3_600 seconds

MAX_STDOUT_BYTES = 4 MiB
MAX_STDERR_BYTES = 4 MiB
MAX_TOTAL_OUTPUT_BYTES = 8 MiB

TERMINATION_GRACE:
  default = 2 seconds
  hard maximum = 10 seconds
```

The constructor's `default_timeout` must itself satisfy the same hard timeout range.

No environment variable may disable these hard limits.

---

# 8. Shell execution semantics

T3 deliberately preserves:

```text
shell=True
```

because this physical tool executes shell command text, not an argv-safe direct executable contract.

This means:

- shell operators remain supported;
- quoting semantics remain OS/shell specific;
- command text may have arbitrary external side effects;
- command sanitization is not attempted by the tool.

Intrinsic Tool safety owns:

- input bounds;
- cwd validation;
- timeout;
- output bounds;
- process-tree cleanup;
- deterministic result/error shape.

Future consumer policy owns:

- whether the command is authorized;
- HITL;
- danger-pattern evaluation;
- user permissions.

T3 must not claim that danger patterns make shell execution safe.

---

# 9. Danger-pattern source of truth

T3 should define one immutable module-level source:

```text
DEFAULT_DANGER_PATTERNS
```

used by legacy metadata.

T3 does not execute/authorize against those patterns.

Metadata V2 per-export risk/effects remain T7.

---

# 10. cwd contract

Canonical cwd validation:

1. omitted cwd -> inherit current process cwd;
2. explicit cwd must be a string;
3. reject empty/NUL/over-limit cwd;
4. path must exist;
5. path must be a directory;
6. canonical execution cwd may use resolved absolute path;
7. no cwd error is encoded as a presentation string.

Stable errors:

```text
TERMINAL_CWD_NOT_FOUND
TERMINAL_CWD_NOT_DIRECTORY
INVALID_ARGUMENT
```

T3 does not add filesystem sandbox authorization.

---

# 11. Output capture contract

T3 run must capture stdout/stderr as bytes incrementally.

Requirements:

- stdout and stderr are consumed concurrently to avoid pipe deadlock;
- retained bytes never exceed the frozen per-stream/aggregate caps;
- once a hard output cap is crossed, the managed process tree is terminated;
- operation returns a failure, not a fake successful truncated command execution.

Output-limit error:

```text
TERMINAL_OUTPUT_LIMIT
```

Error details may include numeric byte counts and which stream crossed the limit.

Error details must not include the command or raw captured output.

---

# 12. Decoding contract

After a completed in-bound command:

1. captured bytes are decoded after resource-bounded collection;
2. if the caller supplied a supported encoding, use it;
3. otherwise use `locale.getpreferredencoding(False)`;
4. decoding uses `errors="replace"` because output is observational and is not written back into source data;
5. result records the effective encoding.

T3 may add optional legacy metadata input:

```text
encoding
```

with a hard encoding-name length limit.

Invalid codec:

```text
TERMINAL_ENCODING_INVALID
```

This does not conflict with File Tool's strict mutation decoding contract.

---

# 13. run result semantics

A process that starts and exits, even with nonzero exit code, is an executed command.

Therefore:

```text
exit_code == 0     -> ok=true
exit_code != 0     -> ok=true
```

Nonzero command status is data, not a tool-transport failure.

Canonical data:

```json
{
  "exit_code": 1,
  "stdout": "...",
  "stderr": "...",
  "stdout_bytes": 123,
  "stderr_bytes": 45,
  "encoding": "utf-8",
  "duration_ms": 250,
  "cwd": "C:/repo"
}
```

No command string is returned.

Normal completed output is not stripped in a way that destroys meaningful whitespace.

---

# 14. run failure semantics

Stable T3 failure classes:

```text
INVALID_ARGUMENT

TERMINAL_CWD_NOT_FOUND
TERMINAL_CWD_NOT_DIRECTORY
TERMINAL_ENCODING_INVALID
TERMINAL_START_FAILED
TERMINAL_TIMEOUT
TERMINAL_OUTPUT_LIMIT
TERMINAL_CLEANUP_FAILED
TERMINAL_IO_ERROR
```

Timeout/output limit are failures with:

```text
ok=false
data=null
```

The tool attempts process-tree cleanup before returning.

If cleanup cannot be verified within the bounded cleanup contract, failure must expose that fact rather than claiming successful termination.

---

# 15. Process-tree ownership for run

Each `run` command must be created as a tool-owned process group/session.

The implementation must support a bounded cleanup sequence conceptually equivalent to:

```text
request graceful termination of managed tree
        ↓
wait <= TERMINATION_GRACE
        ↓
force-kill remaining managed descendants/leader
        ↓
bounded reap/verification
```

Exact OS primitives may differ.

Allowed implementation strategies include standard-library process groups and the repository's existing process utilities/dependencies.

No T3 implementation may leave timeout cleanup as "kill only the immediate shell" while claiming full process-tree cleanup.

---

# 16. launch semantics

`launch` intentionally starts work without waiting for command completion.

Frozen creation behavior:

- validates command/cwd first;
- starts a new detached/independent process group/session as appropriate;
- stdin is `DEVNULL`;
- stdout is `DEVNULL`;
- stderr is `DEVNULL`;
- does not capture output;
- does not wait for terminal completion.

Success data:

```json
{
  "pid": 1234,
  "cwd": "C:/repo",
  "started": true
}
```

`pid` identifies the process/process-group leader created by Terminal Tool.

With `shell=True`, it is not promised to equal the final application PID.

No command string is returned.

---

# 17. launch ownership non-goal

T3 does not introduce:

```text
terminal.status
terminal.kill
persistent launch registry
background monitor thread
durable process ownership database
```

A successfully launched process is intentionally detached after creation.

Future capability expansion would require a new contract/version.

---

# 18. Canonical input validation

Before any process creation:

```text
action is exact supported string
command is non-empty string
command length <= MAX_COMMAND_CHARS
command contains no NUL
cwd is valid
timeout is exact numeric contract, bool rejected
encoding is valid when supplied
```

Explicit invalid values do not silently select defaults.

---

# 19. Legacy TOOL_METADATA boundary

T3 may correct V1 metadata to match the new physical input contract:

- command min/max length;
- timeout min/max;
- cwd max length;
- optional encoding;
- structured-result description;
- one danger-pattern source of truth.

T3 MUST NOT add:

```text
manifest_version
exports
expose_root
V2 bind
V2 output_schema
per-export V2 risk/effects
```

Those remain T7.

---

# 20. Required regression categories

The later T3 implementation plan must cover at minimum:

### Validation

1. empty command;
2. whitespace-only command;
3. NUL command;
4. command over hard cap;
5. timeout 0;
6. timeout negative;
7. timeout bool;
8. timeout over hard max;
9. invalid constructor default timeout;
10. cwd missing;
11. cwd is file;
12. cwd over limit;
13. invalid encoding.

### run

14. exit 0 structured success;
15. nonzero exit structured success;
16. stdout bytes/data;
17. stderr bytes/data;
18. stdout/stderr whitespace preservation;
19. effective encoding returned;
20. command never echoed in result;
21. command never echoed in error;
22. stdout limit terminates command tree;
23. stderr limit terminates command tree;
24. aggregate output limit;
25. timeout;
26. real descendant process cleanup;
27. startup failure;
28. cleanup failure surfaced honestly.

### launch

29. structured success;
30. PID present and positive;
31. stdin/stdout/stderr detached as frozen;
32. cwd honored;
33. startup failure structured;
34. command not echoed;
35. returned PID semantics documented/tested.

### shared/scope

36. JSON-safe terminal result;
37. T1 ToolResult invariants;
38. no `se` import;
39. no `cl` import;
40. File/Glob tests remain unchanged and green.

---

# 21. Real process-tree test requirement

T3 cannot close using mocks only.

At least one real subprocess-tree regression must:

1. start a shell command through Terminal Tool;
2. have it spawn a descendant process;
3. trigger timeout or output-limit cleanup;
4. verify the managed leader and descendant are no longer alive after bounded cleanup.

The test must use harmless temporary Python child processes.

No destructive shell command is needed.

Platform-specific skips are allowed only where the CI platform cannot support the assertion; Linux full-suite must prove the real tree invariant.

---

# 22. T3 patch boundary

Expected implementation diff:

```text
M tools/v1/terminal_tool.py
M tools/v1/test/test_terminal_tool.py
A tools/v1/test/test_terminal_process_tree.py     # optional but recommended
A/M tools/v1/T3_TERMINAL_IMPLEMENTATION_PLAN.md  # if separate plan is created
M tools/v1/TOOLS_V1_CONTRACT_FREEZE.md           # completion status only after green
```

Forbidden implementation diff:

```text
NO tools/v1/_shared/**
NO File/Glob
NO Window/Desktop/Web
NO tools/v1/live/**
NO se/**
NO cl/**
```

---

# 23. T3 exit-gate shape

Focused:

```text
python -m pytest -q   tools/v1/test/test_terminal_tool.py   tools/v1/test/test_terminal_process_tree.py   tools/v1/test/test_shared_contracts.py   tools/v1/test/test_shared_limits.py   tools/v1/test/test_scope_boundary.py
```

If process-tree tests remain in `test_terminal_tool.py`, omit the optional file.

Then:

```text
python -m pytest -q tools/v1/test
```

Then repository CI:

```text
python -m pytest -q
```

No SE/CL patch is allowed to make T3 green.

---

# 24. T2 → T3 freeze verdict

```text
T2 File/Glob
    CLOSED + FROZEN
          |
          v
T3 Terminal Tool
    terminal_tool.py
    test_terminal_tool.py
    optional process-tree test
          |
          X---- no File/Glob edits
          X---- no _shared edits
          X---- no live edits
          X---- no se/cl edits
```

T3 must solve Terminal resource/process/result semantics locally.

**BOUNDARY STATUS:** FROZEN  
**T3 CODE STATUS:** NOT STARTED  
**NEXT SAFE STEP:** write the exact T3-A→T3-G implementation plan, or implement only after that plan is accepted.
