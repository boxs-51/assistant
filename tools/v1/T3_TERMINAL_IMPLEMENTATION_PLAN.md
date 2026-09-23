# T3 Terminal Tool — Exact Implementation Design & T3-A→T3-G Plan

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Audit baseline HEAD: `78a7faac0f0b8a7f2c93f3bd5d61d9cb1406f8c7`  
Main baseline: `a55e4fd2a20ddccbd227e770d26fae72e33bc88e`  
T2 status: COMPLETE + FROZEN  
Boundary source: `tools/v1/T3_TERMINAL_BOUNDARY_FREEZE.md`  
Mode: **AUDIT + IMPLEMENTATION PLAN ONLY — NO T3 PRODUCTION CODE IMPLEMENTED**

---

# 1. T3 objective

T3 completes the standalone Terminal Tool without reopening T1/T2.

Production scope:

```text
tools/v1/terminal_tool.py
```

Primary tests:

```text
tools/v1/test/test_terminal_tool.py
```

Recommended real process-tree tests:

```text
tools/v1/test/test_terminal_process_tree.py
```

T3 does not modify:

```text
tools/v1/_shared/**
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
```

Metadata V2 projections remain T7.

---

# 2. Exact audit of current implementation

Current `terminal_tool.py` uses:

```python
subprocess.run(
    command,
    shell=True,
    capture_output=True,
    text=True,
    timeout=exec_timeout,
    cwd=valid_cwd,
    errors="replace",
)
```

and:

```python
subprocess.Popen(
    command,
    shell=True,
    stdout=DEVNULL,
    stderr=DEVNULL,
    cwd=valid_cwd,
)
```

The current unit suite mostly mocks `subprocess.run/Popen` and verifies presentation strings.

T3 must replace those behavioral assumptions rather than preserve them.

The existing direct repository consumers outside tests are live harnesses under `tools/v1/live/**`; those remain T8 migration work.

---

# 3. T1 primitives T3 will use

No T1 foundation change is needed.

Import only existing primitives:

```text
success_result
failure_result

IntLimitSpec
resolve_int_limit
```

Terminal-local validation/process helpers remain private to `terminal_tool.py`.

T3 MUST NOT add execution helpers to `_shared`.

---

# 4. Final T3 constants

Freeze the following module constants:

```text
TERMINAL_TOOL_VERSION = "2.0.0"

MAX_COMMAND_CHARS = 32_768
MAX_CWD_CHARS = 4_096
MAX_ENCODING_CHARS = 64

RUN_TIMEOUT:
  default = 30
  minimum = 1
  maximum = 3_600

MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 4 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 8 * 1024 * 1024

TERMINATION_GRACE_SECONDS = 2.0
TERMINATION_GRACE_HARD_MAX_SECONDS = 10.0

PIPE_READ_CHUNK_BYTES = 64 * 1024
PROCESS_POLL_INTERVAL_SECONDS = 0.05
PIPE_JOIN_GRACE_SECONDS = 1.0
MAX_REPORTED_SURVIVOR_PIDS = 64
```

The last four are private implementation bounds, not public caller inputs.

No environment variable may disable these limits.

---

# 5. Dependency decision

T3 may import `psutil`.

Repository baseline already declares:

```text
requirements.txt
psutil==7.2.2
```

T3 uses `psutil` only for:

- descendant observation;
- descendant terminate/kill;
- bounded wait verification;
- survivor reporting.

Process creation remains `subprocess.Popen`.

No SE/CL/runtime dependency is introduced.

---

# 6. Internal type layout

Recommended private types:

```text
_TerminalToolError
_OutputCaptureState
_ProcessIdentity
_CleanupReport
```

Conceptual shapes:

```text
_TerminalToolError
  code
  message
  details

_OutputCaptureState
  stdout_buffer: bytearray
  stderr_buffer: bytearray
  stdout_bytes_observed
  stderr_bytes_observed
  overflow_stream
  io_error_stream
  io_error_type
  stop_event
  lock

_ProcessIdentity
  pid
  create_time

_CleanupReport
  attempted
  graceful_pids
  killed_pids
  survivor_pids
  verified
```

All result-facing structures must be JSON-safe plain values.

---

# 7. Input validation design

## 7.1 Command

Before process creation:

- must be `str`;
- after `.strip()`, must be non-empty;
- original command length <= `MAX_COMMAND_CHARS`;
- reject NUL;
- never normalize or rewrite shell syntax;
- never echo the command into result/error payloads.

Stable error:

```text
INVALID_ARGUMENT
```

## 7.2 Timeout

Use:

```python
RUN_TIMEOUT = IntLimitSpec(
    "timeout",
    default=30,
    minimum=1,
    maximum=3600,
)
```

Rules:

```text
None → constructor default_timeout
0 → invalid
negative → invalid
True/False → invalid
> 3600 → invalid
```

The constructor's `default_timeout` is validated at construction with the same hard range.

No truthiness fallback is allowed.

## 7.3 cwd

`_validate_cwd(cwd)` becomes an exception-based helper, not a string-return protocol.

Rules:

- `None` -> canonical current working directory;
- explicit cwd must be non-empty `str`;
- reject NUL;
- reject > `MAX_CWD_CHARS`;
- path must exist;
- path must be a directory;
- return canonical absolute execution path.

Errors:

```text
INVALID_ARGUMENT
TERMINAL_CWD_NOT_FOUND
TERMINAL_CWD_NOT_DIRECTORY
```

## 7.4 Encoding

Optional `encoding` is accepted for `run`.

Rules:

- omitted -> `locale.getpreferredencoding(False)`;
- non-empty string;
- max `MAX_ENCODING_CHARS`;
- codec must be a usable text decoder;
- normalize to canonical codec name.

Error:

```text
TERMINAL_ENCODING_INVALID
```

Bytes are captured before decoding.

---

# 8. Public execution model

T3 keeps:

```text
shell=True
```

and explicitly defines Terminal Tool as a shell-text executor.

T3 `run` is **non-interactive**:

```text
stdin = DEVNULL
```

Commands that require interactive stdin are outside the T3 contract.

Reason:

- prevents accidental reads from the host terminal;
- prevents commands from waiting indefinitely for user input;
- gives the tool explicit ownership of process I/O.

Use `launch` for intentionally detached work.

---

# 9. Platform process creation

## 9.1 run on POSIX

```text
shell=True
stdin=DEVNULL
stdout=PIPE
stderr=PIPE
text=False
bufsize=0
close_fds=True
start_new_session=True
```

Because `start_new_session=True` creates a new session/process group:

```text
managed pgid == Popen.pid
```

This gives T3 an OS-level group kill boundary.

## 9.2 run on Windows

Use:

```text
shell=True
stdin=DEVNULL
stdout=PIPE
stderr=PIPE
text=False
bufsize=0
close_fds=True
creationflags=CREATE_NEW_PROCESS_GROUP
```

Descendant cleanup is verified with `psutil`.

## 9.3 launch on POSIX

```text
shell=True
stdin=DEVNULL
stdout=DEVNULL
stderr=DEVNULL
close_fds=True
start_new_session=True
```

## 9.4 launch on Windows

```text
shell=True
stdin=DEVNULL
stdout=DEVNULL
stderr=DEVNULL
close_fds=True
creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
```

Use `getattr(subprocess, ...)` only inside the Windows branch so Linux import remains valid.

---

# 10. Why T3 cannot use subprocess.run()/communicate()

T3 must not use:

```text
subprocess.run(capture_output=True)
Popen.communicate() for unbounded normal capture
```

because those APIs accumulate output before the Tool's hard byte limits can stop the child.

The output budget must be enforced while bytes are being produced.

---

# 11. Bounded output capture design

## 11.1 Reader threads

Start exactly two private daemon reader threads:

```text
stdout reader
stderr reader
```

Threads are used instead of `selectors` because Windows pipe handles are not portable through the same selector design.

Each reader:

1. calls `pipe.read(PIPE_READ_CHUNK_BYTES)`;
2. stops on EOF;
3. under one shared lock:
   - increments observed stream bytes;
   - increments observed total bytes;
   - appends only while within retention caps;
   - sets output-limit event when a stream/aggregate hard cap is crossed;
4. after overflow, continues draining/discarding until EOF or cleanup closes the pipe;
5. never grows retained buffers beyond the hard caps.

This avoids both:

- unbounded memory retention;
- child deadlock on a full pipe before cleanup completes.

## 11.2 Per-stream limits

Overflow triggers when:

```text
stdout_observed > MAX_STDOUT_BYTES
stderr_observed > MAX_STDERR_BYTES
```

## 11.3 Aggregate limit

Also trigger when:

```text
stdout_observed + stderr_observed > MAX_TOTAL_OUTPUT_BYTES
```

The first detected overflow records:

```text
overflow_stream = stdout | stderr | aggregate
```

Only numeric counts/limit metadata are returned in failure details.

Raw partial output is not returned on failure.

---

# 12. Reader I/O error semantics

Expected pipe-level `OSError` while the process is otherwise active records an I/O failure event.

If the pipe error is caused by T3 closing pipes during an already active cleanup path, it is ignored as cleanup fallout.

Otherwise trigger:

```text
TERMINAL_IO_ERROR
```

No generic `Exception` catch is placed around process execution.

---

# 13. Managed process observation

Immediately after `Popen` succeeds:

1. record root PID;
2. obtain root `psutil.Process(pid).create_time()` when possible;
3. during the monitor loop, periodically sample:
   ```text
   root.children(recursive=True)
   ```
4. maintain a bounded set/map of observed descendant:
   ```text
   pid + create_time
   ```

The create-time component prevents treating a reused PID as the original managed process.

This observed set is important on Windows if the shell leader exits before a descendant cleanup pass.

---

# 14. run monitor loop

Use `time.monotonic()`.

Conceptual loop:

```text
start_time
deadline = start + timeout

while true:
    observe descendants

    if output_limit_event:
        trigger = OUTPUT_LIMIT
        break

    if reader_io_error:
        trigger = IO_ERROR
        break

    rc = process.poll()
    if rc is not None:
        trigger = NORMAL_EXIT
        break

    if monotonic >= deadline:
        trigger = TIMEOUT
        break

    wait at most PROCESS_POLL_INTERVAL_SECONDS
```

No busy spin.

---

# 15. run owns its process tree

T3 freezes this semantic:

```text
run = managed tree execution
launch = detached execution
```

A `run` command is not allowed to leave intentionally backgrounded descendants as an unowned side effect after the shell leader returns.

Therefore after normal shell exit:

- observe known descendants;
- wait a bounded `PIPE_JOIN_GRACE_SECONDS` for natural EOF/tree quiescence;
- if descendants remain or pipe readers remain blocked because descendants inherited handles, T3 cleans the remaining managed tree before returning success.

If a user wants detached work, they must use `launch`.

This closes the "shell exited but background child survived" resource leak.

---

# 16. Process-tree cleanup design

Private helper:

```text
_terminate_process_tree(process, identities, trigger)
    -> _CleanupReport
```

## 16.1 POSIX graceful phase

Because run uses a dedicated session/process group:

```text
os.killpg(process.pid, SIGTERM)
```

is attempted.

Also terminate any known `psutil` descendants/root still matching recorded create times.

## 16.2 Windows graceful phase

Attempt process-group graceful signal when valid:

```text
CTRL_BREAK_EVENT
```

Then call `psutil.Process(...).terminate()` for matching descendants/root.

T3 does not rely on CTRL_BREAK alone.

## 16.3 Bounded wait

Wait no longer than:

```text
TERMINATION_GRACE_SECONDS
```

using `psutil.wait_procs` / process polling.

## 16.4 Force phase

Remaining matching processes:

- POSIX:
  ```text
  os.killpg(process.pid, SIGKILL)
  ```
  where the group still exists;
- both platforms:
  call `kill()` on remaining verified psutil processes.

## 16.5 Final verification

Perform a bounded final process check.

A PID counts as survivor only when both PID and recorded create time still match.

Report at most:

```text
MAX_REPORTED_SURVIVOR_PIDS
```

survivor PIDs.

No unbounded process list is returned.

---

# 17. Cleanup result precedence

If timeout/output-limit/reader-error occurs and cleanup succeeds:

```text
TIMEOUT      -> TERMINAL_TIMEOUT
OUTPUT_LIMIT -> TERMINAL_OUTPUT_LIMIT
IO_ERROR     -> TERMINAL_IO_ERROR
```

If cleanup cannot be verified:

```text
TERMINAL_CLEANUP_FAILED
```

takes precedence.

Its details contain:

```json
{
  "trigger": "timeout | output_limit | io_error | post_exit_descendant",
  "survivor_pids": [123, 456],
  "survivor_count": 2
}
```

No command/output content appears in cleanup errors.

---

# 18. BaseException cleanup rule

T3 must not convert:

```text
KeyboardInterrupt
SystemExit
programmer errors
```

into ToolResult failures.

However an unexpected `BaseException` while a managed `run` process exists must not orphan that tree.

Pattern:

```python
try:
    monitor_managed_process()
except BaseException:
    best_effort_cleanup_without_masking_original()
    raise
finally:
    close parent pipe handles
    bounded_join_reader_threads
```

The original exception is re-raised.

Cleanup failure does not replace the original control-flow exception.

---

# 19. Pipe close/join boundary

After process termination/normal cleanup:

1. close parent copies of stdout/stderr pipes;
2. join reader threads for at most `PIPE_JOIN_GRACE_SECONDS`;
3. if threads cannot quiesce after verified process-tree cleanup, return/upgrade to `TERMINAL_CLEANUP_FAILED`.

Reader threads are daemon threads only as a last interpreter-shutdown safety mechanism; successful operations must not rely on daemon abandonment.

---

# 20. Output decoding

Only a successfully bounded completed command reaches decoding.

Decode retained bytes using the validated effective codec:

```text
errors="replace"
```

This is intentionally different from File Tool mutation decoding.

Reason:

Terminal output is observational data and is never written back to the source process.

Return exact decoded stream content; do not call `.strip()`.

Whitespace/newlines are part of terminal output.

---

# 21. run result contract

A process that starts and exits is an executed command, even when its exit code is nonzero.

Therefore:

```text
exit_code == 0 -> ok=true
exit_code != 0 -> ok=true
```

Canonical success:

```json
{
  "ok": true,
  "tool": "terminal_tool",
  "action": "run",
  "data": {
    "exit_code": 1,
    "stdout": "...",
    "stderr": "...",
    "stdout_bytes": 123,
    "stderr_bytes": 45,
    "encoding": "utf-8",
    "duration_ms": 250,
    "cwd": "C:/repo"
  },
  "error": null,
  "meta": {
    "version": "2.0.0",
    "truncated": false,
    "warnings": []
  }
}
```

No successful run uses `meta.truncated=true`.

Crossing an output limit is a failure, not a truncated success.

---

# 22. run timeout/error result contract

Timeout example:

```json
{
  "ok": false,
  "tool": "terminal_tool",
  "action": "run",
  "data": null,
  "error": {
    "code": "TERMINAL_TIMEOUT",
    "message": "terminal command exceeded the execution timeout",
    "retryable": false,
    "details": {
      "timeout_seconds": 5,
      "duration_ms": 5030
    }
  },
  "meta": {
    "version": "2.0.0",
    "truncated": false,
    "warnings": []
  }
}
```

The command text is not included.

Output-limit details may include:

```text
overflow_stream
stdout_bytes_observed
stderr_bytes_observed
max_stdout_bytes
max_stderr_bytes
max_total_output_bytes
duration_ms
```

No partial output bytes/text are included in failure details.

---

# 23. launch exact design

`launch` validates command/cwd exactly as `run`.

It creates a detached process with:

```text
stdin=DEVNULL
stdout=DEVNULL
stderr=DEVNULL
```

No capture threads are started.

No timeout applies.

No registry is created.

Success:

```json
{
  "ok": true,
  "tool": "terminal_tool",
  "action": "launch",
  "data": {
    "pid": 1234,
    "cwd": "C:/repo",
    "started": true
  },
  "error": null,
  "meta": {
    "version": "2.0.0",
    "truncated": false,
    "warnings": []
  }
}
```

PID must be an integer > 0.

The returned PID is the process/shell leader created by Terminal Tool, not necessarily a final GUI child PID.

T3 does not later own/kill/status-track a successful `launch`.

---

# 24. launch startup failure

Catch expected `OSError` process-creation failures only.

Return:

```text
TERMINAL_START_FAILED
```

Allowed details:

```text
errno
winerror
cwd
exception_type
```

Do not include:

```text
command
str(exception) when it can contain command text
```

---

# 25. confirm_callback compatibility decision

Current constructor accepts:

```text
confirm_callback
```

T3 will retain the constructor argument temporarily for physical backward compatibility.

Exact behavior:

- if `None`: no action;
- if non-None:
  - issue `DeprecationWarning`;
  - do not call/store/use it for authorization.

Reason:

Tool-local approval is outside the frozen standalone contract and future consumer policy owns HITL.

Tests must prove the callback is never invoked.

T7 or a later major physical API cleanup may remove the compatibility argument.

---

# 26. Danger-pattern contract

Define:

```text
DEFAULT_DANGER_PATTERNS
```

as an immutable tuple.

Legacy `TOOL_METADATA["danger_patterns"]` is derived from it.

Terminal execution does not inspect/enforce those patterns.

This avoids false claims of shell sanitization.

---

# 27. Legacy TOOL_METADATA corrections

T3 may update V1 metadata only.

Required corrections:

```text
command:
  minLength = 1
  maxLength = 32768

timeout:
  type = integer
  minimum = 1
  maximum = 3600

cwd:
  maxLength = 4096

encoding:
  type = string
  minLength = 1
  maxLength = 64
```

Description must state:

- `run` returns structured stdout/stderr/exit code;
- `launch` returns process-leader PID;
- shell execution remains high risk.

Do not add Metadata V2 fields in T3.

---

# 28. Stable T3 error codes

Freeze:

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

Nonzero exit code is not a Tool error.

---

# 29. Exact helper decomposition

Recommended `terminal_tool.py` private helpers:

```text
_validate_command(...)
_validate_cwd(...)
_validate_encoding(...)
_resolve_timeout(...)

_platform_run_popen_kwargs(...)
_platform_launch_popen_kwargs(...)

_reader_loop(...)
_record_output_chunk(...)

_capture_process_identity(...)
_observe_descendants(...)
_identity_matches(...)

_terminate_process_tree(...)
_close_pipes(...)
_join_readers(...)

_run_managed_process(...)
```

Keep public class methods small:

```text
TerminalTool.run
TerminalTool.launch
TerminalTool.execute
module run
```

No helper moves to `_shared`.

---

# 30. T3-A — Contract identity + validation

Modify:

```text
tools/v1/terminal_tool.py
tools/v1/test/test_terminal_tool.py
```

Implement:

- version/hard constants;
- `DEFAULT_DANGER_PATTERNS`;
- `_TerminalToolError`;
- command validation;
- timeout resolver;
- cwd structured validation;
- encoding validation;
- canonical ToolResult helper methods;
- V1 metadata bounds;
- deprecated no-op `confirm_callback`.

No real process capture redesign yet in this substep.

Tests:

- validation matrix;
- no command echo;
- callback deprecation/no invocation;
- metadata hard bounds.

---

# 31. T3-B — Managed Popen + bounded output capture

Replace `subprocess.run` with `subprocess.Popen`.

Implement:

- platform group/session creation;
- stdin DEVNULL;
- stdout/stderr PIPE in binary mode;
- two bounded reader threads;
- shared output state;
- per-stream + aggregate budgets;
- reader I/O error signaling.

Tests:

- mocked byte capture;
- stdout/stderr independent caps;
- aggregate cap;
- exact whitespace retention;
- no use of `subprocess.run`;
- no `communicate()` dependency for normal capture.

---

# 32. T3-C — Process-tree ownership + cleanup

Implement:

- psutil PID/create-time identities;
- periodic recursive descendant observation;
- POSIX process group signal path;
- Windows group + psutil cleanup path;
- graceful wait;
- force-kill;
- survivor verification;
- cleanup report;
- cleanup precedence.

Tests:

- unit tests for PID reuse protection;
- graceful->force escalation;
- survivor reporting cap;
- cleanup failure precedence;
- BaseException cleanup/re-raise.

No destructive real commands.

---

# 33. T3-D — Complete run state machine

Implement exact state machine:

```text
VALIDATE
  ↓
START
  ↓
CAPTURE + OBSERVE
  ├─ NORMAL_EXIT
  ├─ TIMEOUT
  ├─ OUTPUT_LIMIT
  └─ IO_ERROR
       ↓
TREE QUIESCENCE / CLEANUP
       ↓
PIPE CLOSE + READER JOIN
       ↓
DECODE (normal only)
       ↓
ToolResult
```

Normal shell exit additionally cleans lingering managed descendants before success.

Tests:

- exit 0;
- nonzero exit;
- timeout;
- output limit;
- I/O error;
- post-exit background child cleanup;
- duration/cwd/encoding fields.

---

# 34. T3-E — launch completion

Implement:

- exact command/cwd validation;
- detached POSIX/Windows creation flags;
- stdin/out/err DEVNULL;
- PID return;
- structured startup failure;
- no command echo;
- no persistent registry/monitor.

Tests:

- Popen flags;
- PID result;
- invalid input;
- OSError mapping;
- no timeout/capture ownership.

---

# 35. T3-F — Real process-tree regression tests

Recommended new file:

```text
tools/v1/test/test_terminal_process_tree.py
```

Use only harmless temporary Python scripts.

## 35.1 Timeout tree fixture

Temporary parent script:

1. writes its PID;
2. starts a Python child;
3. child writes its PID;
4. both sleep long enough to exceed Terminal timeout.

Terminal runs the parent through the shell with timeout=1.

Assert:

```text
result.error.code == TERMINAL_TIMEOUT
parent PID not alive
child PID not alive
bounded completion time
```

Use PID + create-time where possible to avoid PID-reuse false positives.

Linux CI must execute this test.

Windows may execute it when supported; platform-specific skips require an explicit reason.

## 35.2 Output-limit fixture

Temporary script:

1. records PID;
2. writes bytes continuously to stdout or stderr;
3. sleeps if not terminated.

Patch T3 hard output constants to a small value inside the test process.

Assert:

```text
TERMINAL_OUTPUT_LIMIT
producer PID gone
no retained output beyond cap
command text absent
```

## 35.3 Background-descendant-after-shell-exit fixture

Temporary script spawns a long-lived child and then exits.

Assert `run` does not return while leaving the child alive.

This proves the frozen:

```text
run owns tree
launch detaches
```

semantic.

---

# 36. T3-G — Full regression + completion gate

Focused gate:

```text
python -m pytest -q   tools/v1/test/test_terminal_tool.py   tools/v1/test/test_terminal_process_tree.py   tools/v1/test/test_shared_contracts.py   tools/v1/test/test_shared_limits.py   tools/v1/test/test_scope_boundary.py
```

Then:

```text
python -m pytest -q tools/v1/test
```

Then repository CI:

```text
python -m pytest -q
```

Only after all are green:

- create T3 completion document;
- update `TOOLS_V1_CONTRACT_FREEZE.md`;
- freeze T3→T4.

T3-G does not implement Window code.

---

# 37. Detailed unit regression matrix

## Validation

1. empty command;
2. whitespace-only command;
3. NUL command;
4. command at hard maximum;
5. command above hard maximum;
6. timeout None uses validated constructor default;
7. timeout minimum accepted;
8. timeout maximum accepted;
9. timeout zero rejected;
10. timeout negative rejected;
11. timeout bool rejected;
12. timeout over max rejected;
13. invalid constructor default rejected;
14. cwd None resolves current cwd;
15. missing cwd;
16. cwd is file;
17. cwd contains NUL;
18. cwd too long;
19. valid encoding normalized;
20. invalid encoding;
21. encoding name too long.

## run result

22. exit zero is `ok=true`;
23. nonzero exit is `ok=true`;
24. exact stdout whitespace retained;
25. exact stderr whitespace retained;
26. stdout byte count;
27. stderr byte count;
28. effective encoding;
29. duration is non-negative integer;
30. canonical cwd returned;
31. command absent from result;
32. `meta.truncated=false`.

## bounded capture

33. stdout exact hard cap succeeds if process exits;
34. stdout hard cap + 1 fails;
35. stderr hard cap + 1 fails;
36. aggregate hard cap + 1 fails;
37. retained buffers never exceed hard caps;
38. overflowing reader continues drain/discard until cleanup;
39. reader I/O error maps to TERMINAL_IO_ERROR.

## process lifecycle

40. timeout cleanup succeeds -> TERMINAL_TIMEOUT;
41. output cleanup succeeds -> TERMINAL_OUTPUT_LIMIT;
42. cleanup survivor -> TERMINAL_CLEANUP_FAILED;
43. survivor list bounded;
44. PID create-time mismatch is not killed/reported as owned process;
45. graceful cleanup escalates to force;
46. reader threads joined;
47. BaseException re-raised after cleanup;
48. normal exit with lingering child performs cleanup.

## launch

49. launch validates command/cwd;
50. launch stdin DEVNULL;
51. launch stdout DEVNULL;
52. launch stderr DEVNULL;
53. POSIX start_new_session;
54. Windows detached/group flags;
55. returned PID positive;
56. command absent from result;
57. OSError -> TERMINAL_START_FAILED;
58. no capture threads;
59. no process registry.

## compatibility/scope

60. standalone `run(...)` dispatch;
61. invalid action -> INVALID_ARGUMENT;
62. confirm_callback non-None warns;
63. confirm_callback is never invoked;
64. legacy metadata matches bounds;
65. ToolResult JSON-safe;
66. no `se` import;
67. no `cl` import;
68. File/Glob regression remains green.

---

# 38. Expected implementation diff

Production:

```text
M tools/v1/terminal_tool.py
```

Tests:

```text
M tools/v1/test/test_terminal_tool.py
A tools/v1/test/test_terminal_process_tree.py
```

Completion docs only after green implementation:

```text
A tools/v1/T3_TERMINAL_COMPLETION.md
M tools/v1/TOOLS_V1_CONTRACT_FREEZE.md
```

Current plan document:

```text
A tools/v1/T3_TERMINAL_IMPLEMENTATION_PLAN.md
```

Forbidden diff:

```text
NO tools/v1/_shared/**
NO File/Glob
NO Window/Desktop/Web
NO live/**
NO se/**
NO cl/**
```

---

# 39. Design non-goals

T3 does not implement:

- argv/direct-executable mode;
- shell command escaping/sanitization;
- authorization/HITL;
- danger-pattern enforcement;
- interactive stdin;
- PTY/ConPTY;
- terminal streaming API;
- persistent process registry;
- terminal.status;
- terminal.kill;
- launch output collection;
- durable process recovery;
- Metadata V2 exports;
- live harness migration.

Any future addition needs an explicit later contract/version.

---

# 40. Exact completion invariants

T3 is complete only if all are true:

1. `subprocess.run(capture_output=True)` is absent from Terminal run path;
2. normal capture never retains more than frozen stdout/stderr/aggregate byte limits;
3. timeout cannot silently leave the tested descendant tree alive;
4. output-limit cleanup cannot silently leave the tested producer alive;
5. normal `run` does not intentionally leave background descendants;
6. cleanup failure is reported instead of hidden;
7. command text is never echoed in ToolResult/error;
8. explicit invalid timeout never falls back;
9. cwd errors are structured;
10. encoding is explicit and recorded;
11. nonzero exit is successful execution data;
12. launch returns PID;
13. launch is detached with no capture ownership;
14. no tool-local authorization is implied by `confirm_callback` or danger patterns;
15. T1/T2 files remain unchanged;
16. no SE/CL/live changes;
17. focused tests green;
18. all `tools/v1/test` green;
19. repository CI green.

---

# 41. T3-A→T3-G implementation order

```text
T3-A  Identity + validation + ToolResult contract
  ↓
T3-B  Managed Popen + bounded stdout/stderr readers
  ↓
T3-C  Process-tree observation + cleanup
  ↓
T3-D  run state machine
  ↓
T3-E  detached launch + PID contract
  ↓
T3-F  real process-tree/output-limit regression
  ↓
T3-G  full gates + completion document
```

Do not merge T3-F into mock-only unit coverage.

The real process-tree gate is mandatory evidence for closing P0-TM2.

---

# 42. Implementation verdict

The existing T1 foundation is sufficient.

The exact architecture is:

```text
T1 ToolResult + limits
          |
          v
Terminal Tool 2.0
  ┌───────────────┐
  │ run           │
  │ managed tree  │
  │ bounded pipes │
  │ bounded time  │
  └───────────────┘
          |
          +-----------------+
                            |
                            v
                    launch detached
                    PID only / no registry
```

No consumer/runtime code is needed.

**AUDIT STATUS:** COMPLETE  
**T3 IMPLEMENTATION DESIGN:** IMPLEMENTED AS FROZEN  
**T3-A→T3-G PLAN:** COMPLETE  
**PRODUCTION CODE STATUS:** COMPLETE  
**FINAL CODE HEAD:** `fa21ebaff83d8c5995d2a12fd5c872db8a14229b`  
**FINAL CI:** Architecture Baseline run `35748969683` — SUCCESS  
**COMPLETION RECORD:** `tools/v1/T3_TERMINAL_COMPLETION.md`  
**NEXT ROADMAP PHASE:** T4 Window Tool boundary audit; no T4 code started.
