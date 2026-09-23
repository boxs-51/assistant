# T3 Terminal Tool Completion Record

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Implementation-plan baseline: `b8ab9d1cd6b6e4e14926b9e53b7ea5f72a591ffe`  
Final T3 code HEAD: `fa21ebaff83d8c5995d2a12fd5c872db8a14229b`  
Status: **T3 COMPLETE — FROZEN**

---

## 1. Completion summary

T3 completes the standalone Terminal Tool phase for:

```text
tools/v1/terminal_tool.py
```

with regression coverage in:

```text
tools/v1/test/test_terminal_tool.py
tools/v1/test/test_terminal_process_tree.py
```

The phase adopts the T1 canonical ToolResult contract, closes the Terminal P0/P1 resource and lifecycle findings, and keeps Metadata V2 projections deferred to T7.

No SE/CL implementation is part of T3.

---

## 2. Implementation commits

Primary implementation:

```text
8d8fdbb634180427a301f7d8c8e72c23de05899d
feat(tools-v1): implement T3 managed terminal execution
```

Last-mile verification hardening:

```text
fa21ebaff83d8c5995d2a12fd5c872db8a14229b
fix(tools-v1): harden T3 terminal verification boundaries
```

Final T3 code baseline:

```text
fa21ebaff83d8c5995d2a12fd5c872db8a14229b
```

---

## 3. Closed P0 findings

### P0-TM1 — unbounded stdout/stderr capture

Removed the `subprocess.run(capture_output=True)` execution path.

Final `run` uses:

```text
Popen
  + stdout=PIPE
  + stderr=PIPE
  + binary capture
  + two bounded reader threads
```

Hard bounds:

```text
stdout retained <= 4 MiB
stderr retained <= 4 MiB
aggregate retained <= 8 MiB
```

Readers enforce limits while bytes are produced rather than after process completion.

When a hard output limit is crossed:

```text
TERMINAL_OUTPUT_LIMIT
```

is returned after managed-tree cleanup.

Partial output is not returned as a fake truncated success.

### P0-TM2 — timeout could leave descendants alive

`run` now owns a managed process tree.

POSIX:

```text
start_new_session=True
process-group TERM
bounded grace
process-group KILL
psutil verification
```

Windows:

```text
CREATE_NEW_PROCESS_GROUP
CTRL_BREAK where valid
psutil terminate/kill fallback
PID + create-time verification
```

Timeout now returns `TERMINAL_TIMEOUT` only after cleanup is verified.

If cleanup cannot be verified:

```text
TERMINAL_CLEANUP_FAILED
```

takes precedence.

---

## 4. Final run semantics

`terminal.run` physical action remains shell-text execution:

```text
shell=True
stdin=DEVNULL
```

The command is non-interactive.

The tool owns the process tree for the lifetime of the run.

A normal shell exit does not permit intentionally backgrounded descendants to remain alive under `run`; lingering managed descendants are cleaned before success is returned.

Detached work belongs to `launch`.

A command that starts and exits with a nonzero exit code is still a successful tool invocation:

```text
exit_code == 0  -> ok=true
exit_code != 0  -> ok=true
```

The exit code is execution data, not a transport/tool failure.

Canonical run data:

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

---

## 5. Final launch semantics

`launch` is intentionally detached.

It uses:

```text
stdin=DEVNULL
stdout=DEVNULL
stderr=DEVNULL
```

and returns:

```json
{
  "pid": 1234,
  "cwd": "C:/repo",
  "started": true
}
```

The returned PID is the process/shell leader created by Terminal Tool.

T3 does not add:

```text
terminal.status
terminal.kill
persistent launch registry
background monitor thread
```

A successful launch is no longer owned by `run` cleanup semantics.

---

## 6. Final hard limits

```text
MAX_COMMAND_CHARS = 32,768
MAX_CWD_CHARS = 4,096
MAX_ENCODING_CHARS = 64

RUN_TIMEOUT:
  default = 30 seconds
  minimum = 1 second
  maximum = 3,600 seconds

MAX_STDOUT_BYTES = 4 MiB
MAX_STDERR_BYTES = 4 MiB
MAX_TOTAL_OUTPUT_BYTES = 8 MiB

TERMINATION_GRACE_SECONDS = 2.0
TERMINATION_GRACE_HARD_MAX_SECONDS = 10.0

PIPE_READ_CHUNK_BYTES = 64 KiB
PROCESS_POLL_INTERVAL_SECONDS = 0.05
PIPE_JOIN_GRACE_SECONDS = 1.0
MAX_REPORTED_SURVIVOR_PIDS = 64
```

Explicit invalid timeout values never silently select a default.

---

## 7. Output and encoding contract

Output is captured as bytes first.

Successful bounded output is decoded using:

```text
caller-supplied validated codec
or
locale.getpreferredencoding(False)
```

with:

```text
errors="replace"
```

This is observational terminal output and is not written back into source data.

The effective codec is included in the result.

Terminal output whitespace is preserved; success does not call `.strip()`.

---

## 8. Command secrecy

T3 no longer echoes shell command text in:

```text
success data
timeout errors
output-limit errors
startup errors
cleanup errors
launch results
```

This closes the previous command-secret leakage path for tokens, signed URLs or credentials embedded in a command string.

T3 does not generically redact arbitrary stdout/stderr text because doing so would corrupt legitimate process output.

---

## 9. confirm_callback decision

The legacy constructor parameter remains temporarily accepted for physical backward compatibility.

When non-null:

```text
DeprecationWarning
callback is NOT invoked
callback is NOT used for authorization
```

HITL/authorization remains a future consumer responsibility.

Danger patterns remain declarative legacy metadata; Terminal Tool does not claim that they sanitize shell execution.

---

## 10. Stable T3 error codes

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

A nonzero process exit code is not a Tool error.

---

## 11. Last-mile verification hardening

Before signing completion, static audit tightened two verification boundaries:

### Zombie handling

Zombie processes are treated as terminal/dead rather than live cleanup survivors.

This prevents false `TERMINAL_CLEANUP_FAILED` results after a process is already dead but awaiting reaping.

### AccessDenied handling

When a previously-owned PID still exists but `psutil` cannot inspect it due to `AccessDenied`, verification fails closed.

It is not silently interpreted as "process disappeared."

### cwd OS errors

Expected OS failures while inspecting/resolving the working directory are mapped to:

```text
TERMINAL_IO_ERROR
```

rather than leaking as presentation strings or being swallowed by a broad exception handler.

---

## 12. Real process-tree regression evidence

T3 adds:

```text
tools/v1/test/test_terminal_process_tree.py
```

The Linux/POSIX authoritative tests use harmless temporary Python processes and prove:

1. timeout kills a real parent and descendant;
2. output-limit cleanup kills a real producer;
3. a normal `run` cleans a background descendant after the foreground shell exits.

These tests are not mock-only process lifecycle checks.

They are included in the final repository full-suite gate.

---

## 13. Final CI evidence

Final code HEAD:

```text
fa21ebaff83d8c5995d2a12fd5c872db8a14229b
```

Architecture Baseline run:

```text
35748969683
```

Linux full repository suite:

```text
855 passed
1 skipped
14 warnings
48 subtests passed
75.86s
```

Windows client contracts:

```text
68 passed
8.44s
```

Both jobs concluded:

```text
success
```

Phase 5 Exit Gates on the same final code HEAD:

```text
run 35748969817
40 passed
1.23s
```

Workflow conclusion:

```text
success
```

---

## 14. Scope evidence

Compare:

```text
base = b8ab9d1cd6b6e4e14926b9e53b7ea5f72a591ffe
head = fa21ebaff83d8c5995d2a12fd5c872db8a14229b
```

Final T3 implementation diff:

```text
M tools/v1/terminal_tool.py
M tools/v1/test/test_terminal_tool.py
A tools/v1/test/test_terminal_process_tree.py
```

Forbidden diff:

```text
0
```

T3 implementation did not modify:

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

---

## 15. Intentional non-goals

T3 does not implement:

- argv/direct-executable mode;
- shell escaping/sanitization;
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

These are not silently claimed.

---

## 16. Completion verdict

```text
T0 Contract Freeze        COMPLETE
T1 Shared Foundation      COMPLETE
T2 File + Glob            COMPLETE
T3 Terminal               COMPLETE
T3 P0/P1 hardening        COMPLETE
T3 Real tree regression   GREEN
T3 Repository CI          GREEN
T3 Scope invariant        GREEN
```

No open T3 P0/P1 was identified by the final implementation audit.

T3 is frozen. The next roadmap phase is T4 Window Tool, but T4 implementation has not started.
