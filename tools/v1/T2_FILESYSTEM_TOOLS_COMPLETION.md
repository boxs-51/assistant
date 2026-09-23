# T2 Filesystem Tools Completion Record

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Main rebase base: `a55e4fd2a20ddccbd227e770d26fae72e33bc88e`  
Original T2 implementation HEAD before rebase: `2bbbf494c1029262e9dd035eae1e076be155d0e2`  
Final T2 code HEAD: `c1e96899d5b7a94d091c3b3490738c4f7c4a2f17`  
Status: **T2 COMPLETE — FROZEN**

---

## 1. Completion summary

T2 completes the standalone filesystem tool phase for:

```text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
```

with regression coverage in:

```text
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
```

T2 adopts the T1 canonical ToolResult contract for File/Glob while intentionally deferring Metadata V2 projections to T7.

No SE/CL implementation is part of T2.

---

## 2. Rebase onto main

The Tools V1 branch was moved from the previous project baseline to:

```text
main = a55e4fd2a20ddccbd227e770d26fae72e33bc88e
```

Audit before rebase proved that main had no `tools/v1/**` changes relative to the earlier Tools V1 baseline, so there was no semantic conflict.

Safety backup:

```text
tools-v1-contract-freeze-pre-main-a55e4fd
    -> 2bbbf494c1029262e9dd035eae1e076be155d0e2
```

Rebased commit:

```text
d8343dbda916ba24ab6554acc4f6d8f46ae46c4f
parent = a55e4fd2a20ddccbd227e770d26fae72e33bc88e
```

At the rebase point, the complete `tools/v1` subtree SHA was preserved exactly:

```text
524418c2762c003aefc6f922a23fa8bff9ba12c9
```

Therefore the original T2 implementation tree was preserved byte-for-byte before last-mile hardening.

---

## 3. Last-mile audit result

The requested audit started from the T2 implementation represented by `2bbbf494`.

The audit found no remaining data-integrity P0 in the File/Glob implementation, but it did find additional P1 resource-bound issues that were fixed before completion was signed.

### P1-LM1 — write input was byte-bounded only after encoding

A very large Python string could reach `.encode()` before the byte limit rejected it.

Closed by adding a pre-encoding character bound using the frozen write-content ceiling.

Result:

```text
oversized content
    -> fail before encoding/allocation expansion
    -> FILE_TOO_LARGE
```

### P1-LM2 — multi-file calls lacked an aggregate byte budget

Each file was individually bounded, but up to 32 files could still retain a much larger aggregate snapshot in one call.

Closed by adding:

```text
MAX_TOTAL_FILE_BYTES = 32 MiB
```

for multi-file search/replace snapshot planning.

### P1-LM3 — match/replacement occurrence work lacked a total call budget

Bounded file size and report count did not fully bound CPU work for pathological zero-width/high-frequency regex matches.

Closed by adding:

```text
MAX_TOTAL_MATCH_OCCURRENCES = 1_000_000
```

across one search/replace call.

### P1-LM4 — replacement output limit was checked after subn materialization

A replacement could expand the current line before the post-condition rejected it.

Closed by replacing the unbounded `subn()` mutation path with bounded incremental substitution:

```text
finditer
  -> bounded unchanged prefix
  -> bounded replacement expansion
  -> bounded output accumulation
  -> bounded tail
```

### P1-LM5 — invalid regex replacement backreferences could be latent

A bad replacement such as an invalid group reference could be treated as no-change when the search pattern did not match.

Closed by validating each regex replacement template before file planning.

Invalid replacement template now deterministically returns:

```text
FILE_REGEX_INVALID
query_index = N
```

even when no file content matches the pattern.

---

## 4. Closed T2 File invariants

Final File Tool contract guarantees:

1. existing unreadable files are never treated as empty mutation input;
2. mutation uses strict decode/encode paths;
3. invalid bytes fail closed;
4. file size is hard bounded;
5. multi-file aggregate snapshot bytes are hard bounded;
6. write content is bounded before encoding;
7. query/replacement input counts and lengths are bounded;
8. regex/match occurrence work is call-bounded;
9. replacement line expansion is bounded before materialization growth;
10. symlink mutation is rejected;
11. missing empty-file creation is correct;
12. temporary files are cleaned on tested failure paths;
13. POSIX mode bits are preserved where supported;
14. optimistic SHA-256 conflict detection prevents overwriting a changed destination;
15. replace preflights and confirms all files before first commit;
16. later commit failure attempts guarded rollback;
17. guarded rollback never overwrites an external post-write change;
18. report truncation never changes replace mutation scope;
19. CRLF is preserved by replacement;
20. invalid regex/replacement syntax is structured and fail-closed;
21. public results are canonical T1 ToolResult values.

---

## 5. Closed T2 Glob invariants

Final Glob Tool contract guarantees:

1. absolute patterns are rejected;
2. `..` traversal segments are rejected before traversal;
3. caller pattern and root path have hard length bounds;
4. `max_results` is hard bounded;
5. zero/negative/bool/over-max explicit limits are rejected;
6. result subset is deterministic lexical first-N;
7. truncation is exact using N+1 detection;
8. warning strings are never inserted into path arrays;
9. result entries are structured descriptors;
10. result paths are canonical absolute POSIX-separated strings;
11. symlink identity is visible;
12. public result is canonical T1 ToolResult.

---

## 6. Final stable File error codes

```text
INVALID_ARGUMENT
OUTPUT_LIMIT_EXCEEDED

FILE_NOT_FOUND
FILE_NOT_REGULAR
FILE_SYMLINK_MUTATION_BLOCKED
FILE_TOO_LARGE
FILE_DECODE_ERROR
FILE_ENCODING_INVALID
FILE_CHANGED_DURING_OPERATION
FILE_CHANGE_REJECTED
FILE_REGEX_INVALID
FILE_IO_ERROR
```

No-match and no-change remain successful results.

---

## 7. Final stable Glob error codes

```text
INVALID_ARGUMENT

GLOB_ROOT_NOT_FOUND
GLOB_ROOT_NOT_DIRECTORY
GLOB_PATTERN_OUTSIDE_ROOT
GLOB_IO_ERROR
```

No-match is a successful empty result.

---

## 8. Final CI evidence

Final T2 code HEAD:

```text
c1e96899d5b7a94d091c3b3490738c4f7c4a2f17
```

### Architecture Baseline

Run:

```text
35744764709
```

Linux full repository suite:

```text
845 passed
1 skipped
14 warnings
36 subtests passed
68.21s
```

Windows client contracts:

```text
68 passed
9.79s
```

Both jobs concluded `success`.

### Phase 5 exit gates

Run:

```text
35744765114
```

Result:

```text
40 passed
1.21s
```

Workflow concluded `success`.

---

## 9. Scope evidence

Comparison:

```text
base = a55e4fd2a20ddccbd227e770d26fae72e33bc88e
head = c1e96899d5b7a94d091c3b3490738c4f7c4a2f17
```

shows no changed path outside:

```text
tools/v1/**
```

The T2 physical implementation itself modifies only File/Glob and their tests after the shared T0/T1 foundation already present on this branch.

No T2 code change was made to:

```text
se/**
cl/**
tools/v1/_shared/**
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
```

during the final T2 hardening sequence.

---

## 10. Intentional non-goals retained

T2 does not claim:

- File Tool sandbox-root authorization;
- HITL/consumer policy;
- cross-filesystem multi-file atomic transactions;
- kernel-level compare-and-swap replacement;
- portable ACL/ownership preservation;
- wall-clock preemption of Python's regex engine;
- Metadata V2 projection exports;
- migration of live harness presentation parsing.

These remain future-phase concerns and are not hidden guarantees.

---

## 11. Completion verdict

```text
T0 Contract Freeze        COMPLETE
T1 Shared Foundation      COMPLETE
T2 File + Glob            COMPLETE
T2 Last-mile audit        COMPLETE
T2 Final CI               GREEN
T2 Scope invariant        GREEN
```

There is no open T2 P0/P1 identified by the final audit.

The next phase may freeze the exact boundary for T3 Terminal Tool. T3 must not reopen File/Glob unless a concrete regression is later demonstrated.
