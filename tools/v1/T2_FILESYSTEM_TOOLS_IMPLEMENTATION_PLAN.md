# T2 Filesystem Tools Completion — Exact Boundary Audit & Patch Plan

Repository: boxs-51/assistant  
Branch: tools-v1-contract-freeze  
Audit baseline HEAD: dda9087f94f1c5bc3b3f23e3363671ad328a8e75  
T1 status: COMPLETE  
Scope: File Tool + Glob Tool only  
Mode: AUDIT + PATCH PLAN — NO T2 PRODUCTION CODE IMPLEMENTED

---

# 1. T2 objective

T2 is the first physical-tool adoption phase after the T1 shared foundation.

It completes:

~~~text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
~~~

against the frozen T1 contracts.

T2 must close the filesystem P0/P1 findings while preserving the repository scope boundary:

~~~text
tools/v1 only
NO se/**
NO cl/**
NO R7
NO DB
NO Asset Store
~~~

T2 does not implement Metadata V2 exports. That remains T7.

---

# 2. Exact write boundary

T2 implementation may modify:

~~~text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
~~~

T2 may add one focused cross-tool regression file if useful:

~~~text
tools/v1/test/test_filesystem_tools_contract.py
~~~

T2 documentation:

~~~text
tools/v1/T2_FILESYSTEM_TOOLS_IMPLEMENTATION_PLAN.md
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md   # status update only after T2 is green
~~~

T2 MUST NOT modify:

~~~text
tools/v1/_shared/**
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
~~~

The T1 foundation is sufficient; no shared-contract expansion is required for T2.

---

# 3. Consumer blast radius found by audit

Repository search shows direct File/Glob callers outside their unit tests only under:

~~~text
tools/v1/live/live_real_system_operations.py
tools/v1/live/live_multisource_pipeline.py
tools/v1/live/live_terminal_python_pipeline.py
~~~

Those live harnesses are intentionally deferred to T8.

No SE/CL production call-site needs to be edited in T2.

Important consequence:

T2 is allowed to migrate File/Glob public terminal results to canonical ToolResult even though the current live harness still assumes presentation strings/lists. Live compatibility is not a T2 gate.

The normal tools/v1 unit suite and repository CI remain mandatory gates.

---

# 4. T1 contracts T2 must adopt

Both physical tools must use T1:

~~~text
success_result(...)
failure_result(...)
IntLimitSpec
resolve_int_limit(...)
assert_json_safe via result builders
~~~

T2 errors use stable uppercase codes accepted by T1.

T2 must not introduce another result envelope.

Terminal public value:

~~~json
{
  "ok": true,
  "tool": "...",
  "action": "...",
  "data": {},
  "error": null,
  "meta": {
    "version": "2.0.0",
    "truncated": false,
    "warnings": []
  }
}
~~~

No expected validation/I/O error may be encoded as a Vietnamese success-shaped string.

---

# 5. T2 version boundary

Freeze standalone result-contract versions:

~~~text
FILE_TOOL_VERSION = "2.0.0"
GLOB_TOOL_VERSION = "2.0.0"
~~~

These constants drive ToolResult.meta.version.

T2 does NOT yet add:

~~~text
manifest_version
exports
expose_root
V2 output_schema
projection bind execution
~~~

Those remain T7.

Legacy TOOL_METADATA may be corrected for current input accuracy and hard bounds, but it remains a V1 compatibility manifest.

---

# PART I — FILE TOOL EXACT AUDIT

# 6. P0-F1 — unreadable existing file can be overwritten as if empty

Current behavior:

~~~python
old_content = ""
if path.exists():
    try:
        old_content = path.read_text(...)
    except Exception:
        old_content = ""
~~~

A failed read therefore becomes a valid empty snapshot.

For overwrite/append this can continue to mutation.

T2 invariant:

~~~text
existing target + snapshot/read failure
        ↓
FAIL CLOSED
        ↓
no temp commit
no content mutation
~~~

Required error:

~~~text
FILE_IO_ERROR
~~~

or a more specific decode error when decoding is the cause.

---

# 7. P0-F2 — errors="replace" can silently corrupt mutation input

Current read/replace logic uses decoding with:

~~~text
errors="replace"
~~~

For append and replace, undecodable bytes can become Unicode replacement characters and then be written back permanently.

This is a data-integrity failure.

T2 freeze:

- mutation snapshots use strict decoding;
- read/search also use strict decoding by default;
- invalid bytes produce FILE_DECODE_ERROR;
- the original file remains byte-for-byte unchanged.

No silent replacement characters are used in filesystem mutation paths.

---

# 8. P0-G1 — glob pattern can escape root_dir

Current Glob passes user pattern to Path.glob.

A pattern containing parent traversal, for example:

~~~text
../*.txt
~~~

can match outside the requested root.

This violates the semantic meaning of root_dir and is unsafe for future consumers that treat it as a traversal boundary.

T2 freeze:

Glob pattern MUST be relative and MUST NOT contain a ".." path segment.

Reject:

~~~text
../*.txt
a/../../*.py
/absolute/*.txt
C:\absolute\*.txt
NUL-containing patterns
~~~

Error:

~~~text
GLOB_PATTERN_OUTSIDE_ROOT
~~~

No filesystem traversal happens before this validation.

---

# 9. P1-F3 — read/search/replace are memory/output unbounded

Current behaviors include:

~~~text
f.read()
f.readlines()
full formatted report strings
~~~

T2 must bound:

- file byte size;
- file count;
- read output chars;
- number of queries;
- query length;
- replacement length;
- result records;
- line/snippet size.

Exact limits are frozen below.

---

# 10. P1-F4 — temporary file cleanup is incomplete

Current atomic writer uses NamedTemporaryFile(delete=False) then os.replace.

If replacement fails, the temp file may remain.

T2 invariant:

~~~text
temp created
  ↓
commit succeeds → temp path no longer exists
commit fails    → finally removes temp
~~~

No T2 failure path may intentionally leave its own temporary file behind.

---

# 11. P1-F5 — atomic replacement loses existing POSIX mode bits

Replacing an existing file with a newly-created temp can change its mode.

T2 freeze:

- when replacing an existing regular file, preserve stat.S_IMODE(existing_mode) on POSIX;
- ownership/ACL preservation is not promised cross-platform;
- T2 "atomic" means same-filesystem namespace replacement, not a full filesystem transaction or crash-proof journaling.

---

# 12. P1-F6 — concurrent modification has no fence

Current mutation flow reads old data, may call confirmation, then replaces the path without checking whether another process changed it.

T2 must use optimistic local conflict detection.

Snapshot includes at least:

~~~text
exists
size
sha256(raw bytes)
mode where available
~~~

Immediately before commit:

- an originally missing path must still be missing;
- an originally existing path must still be the expected regular file;
- raw SHA-256 must still match the snapshot.

Mismatch:

~~~text
FILE_CHANGED_DURING_OPERATION
~~~

No overwrite is performed.

This is a best-effort pre-commit conflict fence. T2 does not claim portable cross-process compare-and-swap atomicity between the final check and os.replace.

---

# 13. P1-F7 — symlink mutation semantics are surprising

Current atomic os.replace on a symlink path replaces the symlink entry itself instead of reliably expressing "edit target".

T2 freeze:

~~~text
read/search:
  explicit symlink to a regular file may be read
  result identifies is_symlink=true

write/append/replace:
  symlink target path is rejected
~~~

Error:

~~~text
FILE_SYMLINK_MUTATION_BLOCKED
~~~

This prevents silent link destruction.

Non-regular filesystem objects are rejected.

---

# 14. P1-F8 — empty write to a missing file is incorrectly skipped

Current no-change logic compares:

~~~text
old_content == new_content
~~~

with old_content initialized to empty even when the path does not exist.

Therefore writing an empty string to a missing file may report "no change" without creating the file.

T2 freeze:

Filesystem existence is part of mutation state.

~~~text
missing file + write("") → create zero-byte file, changed=true
existing empty file + write("") → changed=false
~~~

Append of empty content to a missing target follows normal append/open semantics and creates an empty file.

---

# 15. P1-F9 — max_results_per_file=0 silently means unlimited

Current truthiness checks make explicit zero behave like "no limit".

T1 expressly forbids this pattern.

T2 must route every explicit numeric limit through IntLimitSpec/resolve_int_limit.

Invalid zero/negative/over-hard-max values return INVALID_ARGUMENT and never silently fall back.

Bool values are not integers for this contract.

---

# 16. P1-F10 — danger-pattern metadata and runtime drift

Current root TOOL_METADATA includes:

~~~text
config/AGENT.md
~~~

while the FileTool constructor default does not.

Also:

~~~python
danger_patterns or defaults
~~~

makes an explicitly empty list impossible.

T2 freeze:

One module-level DEFAULT_DANGER_PATTERNS tuple is the source of truth for both legacy metadata and runtime.

Constructor semantics:

~~~text
danger_patterns is None → defaults
danger_patterns == []   → explicit empty set
~~~

Patterns are compiled once during construction.

danger_patterns remain declarative/advisory; T2 does not turn them into authorization policy.

---

# 17. P1-F11 — legacy schema and runtime shapes disagree

Current legacy metadata says arrays for file_paths/queries/replacements, while runtime also accepts strings.

T2 may correct V1 metadata so it honestly describes the accepted compatibility surface.

No Metadata V2 exports are added.

Compatibility aliases such as file_path may remain runtime-only until T7 projection cleanup.

---

# 18. P1-F12 — multi-file replace can partially mutate before later validation/failure

Current replace loops files and writes each immediately.

T2 mutation phases must be:

~~~text
1 validate all request arguments
2 normalize/deduplicate all paths
3 preflight every file
4 strict-decode every file
5 compute every replacement plan
6 obtain all local confirmation callbacks
7 commit per-file atomic replacements
~~~

Thus invalid input, missing file, decode error, or confirmation rejection causes zero mutations.

Cross-file commit is not truly atomic.

If a later commit fails after earlier files were committed:

1. T2 attempts best-effort rollback of already committed files;
2. rollback only occurs if the committed file still matches T2's post-write SHA-256;
3. external changes are never overwritten during rollback;
4. failure details report committed paths, rolled-back paths, and rollback conflicts/failures.

A normal simulated second-file failure must restore the first file in tests.

---

# 19. P1-F13 — replace limit currently changes mutation semantics

Current max_results_per_file stops scanning and therefore also limits what gets replaced.

T2 freezes a safer contract:

~~~text
max_results_per_file limits returned/report records only.
It MUST NOT silently limit the actual replacement operation.
~~~

Within the bounded file, replace applies the requested transformations to the whole file.

Result reports:

~~~text
replacement_count
reported_change_count
meta.truncated
~~~

when not all change records are returned.

---

# 20. Ordered multi-query replacement semantics

T2 preserves deterministic current semantics:

Queries/replacements are applied in caller order.

For a line:

~~~text
query[0] transforms current text
query[1] sees the transformed text
...
~~~

This must be documented and regression-tested.

T2 does not introduce simultaneous/non-overlapping replacement semantics.

---

# 21. Regex safety boundary

T2 will:

- hard-limit file bytes;
- hard-limit line length used for regex processing;
- hard-limit query count;
- hard-limit query length;
- compile regex before any mutation;
- report invalid regex by index without echoing the full query text.

T2 does not claim wall-clock preemption of Python's stdlib regex engine.

This is a documented engine limitation rather than an unbounded input contract: regex input size and target text size are hard bounded.

No new third-party regex dependency is introduced because T2 may not modify repository-level dependency files outside tools/v1.

---

# 22. File path normalization

T2 canonical result paths are:

~~~text
absolute
lexically normalized
POSIX-separated string form
not symlink-resolved for identity display
~~~

Conceptually:

~~~text
Path(os.path.abspath(path)).as_posix()
~~~

Do not use resolve() for the public requested path because that would hide symlink identity.

For existing files, duplicate-path detection must also reject aliases referring to the same file where os.path.samefile is available.

---

# 23. File text/encoding contract

Default:

~~~text
utf-8
~~~

Encoding names are validated before I/O through the standard codec registry.

Invalid codec:

~~~text
FILE_ENCODING_INVALID
~~~

Mutation decoding is strict.

Newline behavior for mutation must preserve existing newline bytes as far as text decoding/encoding permits by avoiding universal-newline rewriting.

T2 must not silently normalize a CRLF file to LF or vice versa during an unrelated replace.

---

# 24. Exact File hard limits

Freeze T2 constants:

~~~text
MAX_PATH_CHARS                 = 4096
MAX_ENCODING_CHARS             = 64
MAX_FILE_COUNT                 = 32
MAX_TEXT_FILE_BYTES            = 8 MiB
MAX_WRITE_CONTENT_BYTES        = 8 MiB
MAX_QUERY_COUNT                = 32
MAX_QUERY_CHARS                = 2048
MAX_REPLACEMENT_CHARS          = 65536
MAX_REGEX_LINE_CHARS           = 1_000_000
MAX_MATCH_SNIPPET_CHARS        = 2000
MAX_TOTAL_RETURNED_MATCHES     = 1000
~~~

Read output:

~~~text
READ_MAX_CHARS:
  default = 100_000
  minimum = 1
  maximum = 1_000_000
~~~

Line pagination:

~~~text
START_LINE:
  minimum = 1
  maximum = 10_000_000

NUM_LINES when supplied:
  minimum = 1
  maximum = 100_000
~~~

Search/change reporting:

~~~text
MAX_RESULTS_PER_FILE:
  default = 50
  minimum = 1
  maximum = 500
~~~

These are standalone hard limits. No environment variable may disable them.

---

# 25. File read pagination/output semantics

Read returns complete lines only.

If num_lines is supplied and the requested range ends before EOF:

~~~text
meta.truncated = false
eof = false
next_start_line = first line after returned range
~~~

This is caller-requested pagination, not truncation.

If max_chars prevents returning the full requested range:

~~~text
meta.truncated = true
next_start_line = first line not returned
~~~

If one individual selected line itself exceeds max_chars and no whole line can be returned:

~~~text
OUTPUT_LIMIT_EXCEEDED
~~~

This avoids returning malformed partial text while still allowing deterministic line pagination.

start_line beyond EOF is a successful empty read with eof=true.

---

# 26. File read result shape

ToolResult identity:

~~~text
tool   = file_tool
action = read
version = 2.0.0
~~~

data:

~~~json
{
  "path": "C:/repo/file.txt",
  "content": "...",
  "encoding": "utf-8",
  "size_bytes": 123,
  "sha256": "...",
  "is_symlink": false,
  "start_line": 1,
  "returned_line_count": 4,
  "next_start_line": null,
  "eof": true
}
~~~

No human presentation prefix is mixed into content.

---

# 27. File write result shape

ToolResult identity:

~~~text
tool   = file_tool
action = write
~~~

data:

~~~json
{
  "path": "C:/repo/file.txt",
  "mode": "w",
  "created": false,
  "changed": true,
  "input_bytes": 120,
  "final_size_bytes": 900,
  "before_sha256": "...",
  "after_sha256": "..."
}
~~~

For a missing target:

~~~text
before_sha256 = null
~~~

No-change is successful:

~~~text
ok = true
changed = false
~~~

No success/error message contains the submitted content.

---

# 28. File search result shape

data:

~~~json
{
  "files": [
    {
      "path": "C:/repo/a.py",
      "size_bytes": 1000,
      "sha256": "...",
      "is_symlink": false,
      "match_count": 3,
      "returned_count": 2,
      "matches": [
        {
          "line": 10,
          "query_index": 0,
          "match_start": 4,
          "match_end": 9,
          "text": "...bounded snippet...",
          "snippet_start": 0,
          "snippet_end": 42
        }
      ]
    }
  ],
  "total_match_count": 3,
  "returned_count": 2
}
~~~

Line numbers are 1-based.

match_start/match_end are 0-based half-open character offsets within the logical line.

Query text itself is not repeated in error/result metadata; query_index identifies it.

meta.truncated is true if per-file or aggregate result-record limits omit records.

No matches is a successful result with zero counts.

---

# 29. File replace result shape

data:

~~~json
{
  "files": [
    {
      "path": "C:/repo/a.py",
      "changed": true,
      "replacement_count": 4,
      "reported_change_count": 2,
      "before_sha256": "...",
      "after_sha256": "...",
      "changes": [
        {
          "line": 3,
          "query_index": 0,
          "replacement_count": 2,
          "before": "...bounded snippet...",
          "after": "...bounded snippet..."
        }
      ]
    }
  ],
  "changed_files": 1,
  "total_replacements": 4,
  "reported_change_count": 2
}
~~~

Mutation applies all computed replacements even when changes[] is report-truncated.

meta.truncated means report truncation only, never partial mutation.

---

# 30. File multi-file failure semantics

Preflight failures produce:

~~~text
ok=false
data=null
zero writes
~~~

Commit-stage failure may carry structured details:

~~~json
{
  "failed_path": "...",
  "committed_paths": ["..."],
  "rolled_back_paths": ["..."],
  "rollback_conflicts": [],
  "rollback_failures": []
}
~~~

No failure detail contains file contents or replacement/query text.

---

# 31. File stable error codes

Freeze for T2:

~~~text
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
~~~

No-match and no-change are successes, not errors.

---

# PART II — GLOB EXACT AUDIT

# 32. P1-G2 — max_results has no hard upper bound

Current caller-controlled max_results can be arbitrarily large.

T2:

~~~text
GLOB_MAX_RESULTS:
  default = 500
  minimum = 1
  maximum = 5000
~~~

GlobSearchTool(default_max_results=...) must itself validate the configured default against the same hard range.

---

# 33. P1-G3 — early truncation happens before sorting

Current implementation stops the filesystem generator at N and sorts only those N entries.

Path.glob traversal order is not the public ordering contract.

Therefore the selected bounded subset can differ by filesystem traversal order.

T2 freeze:

The returned subset is the lexically smallest N canonical result paths from the full matching generator.

Implementation may use a bounded heap such as heapq.nsmallest(N + 1, generator, key=...) so retained memory remains O(N) while the generator is exhausted.

The N+1 item determines exact truncation.

---

# 34. P1-G4 — warning string pollutes path array

Current result appends:

~~~text
"... [CẢNH BÁO: ...]"
~~~

to a list otherwise containing paths.

T2 removes presentation markers entirely.

Truncation belongs in:

~~~text
ToolResult.meta.truncated
~~~

---

# 35. P1-G5 — truncation warning can be false positive

Current code reports truncation whenever returned count equals the requested limit, even when the filesystem has exactly N matches.

T2 uses N+1 detection.

~~~text
exactly N matches → truncated=false
N+1 or more       → truncated=true
~~~

---

# 36. P1-G6 — path representation is not frozen

T2 canonical Glob paths use the same representation as File Tool:

~~~text
absolute
lexically normalized
POSIX separators
not symlink-resolved
~~~

This allows a Glob result path to be passed directly to File Tool without relative-CWD ambiguity.

---

# 37. Glob symlink policy

T2 does not intentionally recurse through symlinked directories for recursive ** traversal.

A symlink entry itself may be returned when it matches.

Each result descriptor explicitly states:

~~~text
is_symlink
kind
~~~

kind is one of:

~~~text
file
directory
other
~~~

No target resolution is hidden in the displayed path.

Platform tests may skip symlink creation where the OS/test account does not permit it.

---

# 38. Glob exact limits

Freeze:

~~~text
MAX_GLOB_PATTERN_CHARS = 2048
MAX_GLOB_ROOT_CHARS    = 4096

GLOB_MAX_RESULTS:
  default = 500
  minimum = 1
  maximum = 5000
~~~

recursive must be an actual boolean.

Explicit zero/negative/boolean max_results is invalid.

---

# 39. Glob result shape

ToolResult identity:

~~~text
tool    = find_by_glob
action  = find
version = 2.0.0
~~~

data:

~~~json
{
  "root_dir": "C:/repo",
  "pattern": "*.py",
  "recursive": true,
  "returned_count": 2,
  "matches": [
    {
      "path": "C:/repo/a.py",
      "kind": "file",
      "is_symlink": false
    },
    {
      "path": "C:/repo/pkg",
      "kind": "directory",
      "is_symlink": false
    }
  ]
}
~~~

No matches:

~~~text
ok=true
returned_count=0
matches=[]
meta.truncated=false
~~~

---

# 40. Glob stable error codes

Freeze:

~~~text
INVALID_ARGUMENT

GLOB_ROOT_NOT_FOUND
GLOB_ROOT_NOT_DIRECTORY
GLOB_PATTERN_OUTSIDE_ROOT
GLOB_IO_ERROR
~~~

Glob never returns a human notification string as terminal data.

---

# PART III — LEGACY METADATA BOUNDARY

# 41. What T2 may change in TOOL_METADATA

T2 may correct V1 metadata to match actual T2 public inputs:

File:

- string-or-array compatibility for file_paths;
- string-or-array compatibility for queries/replacements;
- bounds for start_line/num_lines/max_results_per_file;
- add max_chars;
- descriptions matching new structured terminal behavior;
- danger patterns sourced from DEFAULT_DANGER_PATTERNS.

Glob:

- maxLength for pattern/root_dir;
- minimum/maximum for max_results;
- existing required pattern.

T2 MUST NOT add:

~~~text
manifest_version
exports
expose_root
V2 bind
V2 per-export effects
V2 output_schema
~~~

That is T7.

---

# 42. Compatibility aliases

File execute may continue to accept runtime alias:

~~~text
file_path
~~~

when file_paths is omitted.

Aliases are compatibility-only and are not new canonical projected fields.

T2 validates aliases before executing them.

Glob has no additional canonical aliases.

---

# PART IV — T2 IMPLEMENTATION STRUCTURE

# 43. File Tool internal helpers

Recommended tool-local helpers/dataclasses:

~~~text
_FileSnapshot
_FileMutationPlan

_normalize_path(...)
_validate_encoding(...)
_snapshot_text_file(...)
_validate_file_list(...)
_validate_queries(...)
_compile_patterns(...)
_build_match_snippet(...)
_check_snapshot_unchanged(...)
_atomic_replace_bytes(...)
_plan_write(...)
_plan_replace_file(...)
_commit_replace_plans(...)
~~~

These remain private to file_tool.py.

Do not move them to _shared.

---

# 44. File snapshot contract

_FileSnapshot conceptually stores:

~~~text
requested_path
canonical_path
exists
is_symlink
size_bytes
sha256
mode
raw_bytes
decoded_text
~~~

Only bounded regular files receive raw_bytes/decoded_text.

Mutation snapshot of a symlink is rejected before planning.

Search/read may expose is_symlink but operate only when the opened final target is a regular file and size bound is satisfied.

---

# 45. File atomic write contract

Atomic writer order:

~~~text
encode final content strictly
validate final byte size
ensure parent directory as current write semantics require
create temp in destination parent
write all bytes
flush
fsync temp where supported
preserve existing POSIX mode bits
perform optimistic destination conflict check
os.replace(temp, destination)
cleanup temp in finally
~~~

A newly-created parent directory remains part of current write semantics.

T2 does not introduce a separate mkdir capability.

---

# 46. Replace transaction algorithm

For N files:

~~~text
normalize + dedupe paths
        ↓
snapshot every source
        ↓
compile/validate patterns once
        ↓
compute N replacement plans in memory
        ↓
run confirmation callbacks for every changed plan
        ↓
if any reject → zero commit
        ↓
commit plan[0..N-1]
        ↓
on failure:
  rollback already committed plans in reverse order
  but only if current hash == T2 after-hash
~~~

This is transaction-like best effort, not a cross-filesystem atomic transaction.

All destination files for replace already exist, so no new-file cross-file semantics are needed.

---

# 47. Search algorithm

Search should stream logical lines from each bounded strict-decoded file rather than create one giant presentation string.

For each query pattern:

- use finditer for occurrence counting;
- record match spans until report caps;
- continue counting after report cap so match_count is accurate;
- enforce aggregate returned-record cap;
- never echo invalid regex text in an error.

Search itself does not mutate.

---

# 48. Replace matching algorithm

For each line and each query in order:

- operate on current transformed line;
- use subn to obtain exact replacement occurrence count;
- update the line;
- collect one bounded change record per query/line when changed;
- continue mutation after report cap.

Files are bounded, so the final transformed text may be assembled in memory.

Final encoded bytes must remain <= MAX_TEXT_FILE_BYTES.

---

# 49. Glob deterministic bounded-selection algorithm

Recommended:

~~~text
generator = base_path.glob(normalized_pattern)
candidates = heapq.nsmallest(limit + 1, generator, key=canonical_sort_key)
truncated = len(candidates) > limit
selected = candidates[:limit]
~~~

recursive=true retains current compatibility behavior:

If caller pattern does not already start with recursive ** semantics, prefix recursive search behavior as the current tool does.

Before that transformation, root-escape validation runs against the caller pattern.

Sort key:

~~~text
(canonical_path.casefold(), canonical_path)
~~~

This yields deterministic case-insensitive primary order with a stable case-sensitive tie-break.

Memory retained for results stays bounded by N+1.

T2 does not promise traversal-time independence from filesystem size.

---

# PART V — EXACT PATCH PHASES

# 50. T2-A — Freeze constants + result identity + argument validation

Files:

~~~text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
~~~

Add:

- 2.0.0 tool version constants;
- hard limits;
- path normalization;
- exact bool/int/string/list validation;
- stable error construction through T1;
- legacy metadata bound corrections.

No mutation algorithm changes are committed without the validation layer.

---

# 51. T2-B — File snapshots + safe read

Implement:

- codec validation;
- regular-file checks;
- strict decode;
- byte-size cap;
- SHA-256 snapshot;
- read line pagination;
- max_chars whole-line truncation;
- structured read ToolResult.

Close:

~~~text
P0-F1 read side
P0-F2 read/decode side
P1-F3 read bounds
~~~

---

# 52. T2-C — Single-file write/append hardening

Implement:

- missing-empty-file creation;
- strict existing snapshot;
- symlink mutation reject;
- no-change state;
- size bounds;
- optimistic conflict check;
- temp cleanup;
- fsync best effort;
- POSIX mode preservation;
- structured write result.

Close:

~~~text
P0-F1
P0-F2 append corruption
P1-F4
P1-F5
P1-F6
P1-F7
P1-F8
~~~

---

# 53. T2-D — Structured search

Implement:

- path-list validation/dedupe;
- strict preflight of every file;
- query validation;
- regex compile-before-work;
- occurrence-based matches;
- bounded snippets;
- per-file/aggregate report caps;
- structured no-match success.

Close:

~~~text
P1-F3 search bounds
P1-F9
legacy presentation-result ambiguity
~~~

---

# 54. T2-E — Transaction-like replace

Implement:

- strict preflight of all files;
- full replacement plan before writes;
- ordered query semantics;
- confirmation of all plans before commit;
- full mutation independent of report cap;
- per-file atomic commit;
- rollback on later commit failure;
- rollback conflict protection;
- structured replace result.

Close:

~~~text
P0-F2 replace corruption
P1-F6
P1-F12
P1-F13
~~~

---

# 55. T2-F — Danger-pattern/source/schema synchronization

Implement:

- one DEFAULT_DANGER_PATTERNS;
- explicit [] constructor behavior;
- compiled regex patterns;
- legacy metadata input corrections.

No danger-pattern authorization enforcement.

No Metadata V2.

---

# 56. T2-G — Glob root containment + structured results

Implement:

- pattern/root length validation;
- reject absolute/parent traversal pattern;
- exact recursive bool validation;
- hard max_results;
- deterministic N+1 bounded selection;
- canonical absolute POSIX paths;
- result descriptors;
- structured empty success/errors.

Close all Glob P0/P1 findings.

---

# 57. T2-H — Regression matrix + full gate

Update:

~~~text
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
~~~

Optionally add cross-tool contract tests.

No live harness changes.

Only after all gates are green may TOOLS_V1_CONTRACT_FREEZE.md mark T2 complete.

---

# PART VI — REQUIRED TEST MATRIX

# 58. File read tests

Required:

1. full small-file read returns ToolResult;
2. line-range read;
3. caller-requested num_lines pagination is not marked truncated;
4. max_chars truncates only at whole-line boundary;
5. single oversized line returns OUTPUT_LIMIT_EXCEEDED;
6. start_line beyond EOF returns empty success/eof;
7. missing path -> FILE_NOT_FOUND;
8. directory -> FILE_NOT_REGULAR;
9. invalid start_line 0/-1/True rejected;
10. invalid num_lines 0/-1/True rejected;
11. invalid max_chars rejected;
12. invalid encoding -> FILE_ENCODING_INVALID;
13. invalid encoded bytes -> FILE_DECODE_ERROR;
14. oversized file -> FILE_TOO_LARGE;
15. read symlink-to-regular returns is_symlink=true when platform supports it.

---

# 59. File write tests

Required:

1. create new text file;
2. create missing empty file;
3. overwrite existing file;
4. same overwrite -> success changed=false;
5. append existing file;
6. append empty to missing file creates file;
7. existing unreadable snapshot fails closed;
8. invalid-decode existing file fails closed;
9. append invalid bytes leaves bytes unchanged;
10. directory target rejected before temp creation;
11. symlink mutation rejected;
12. oversized input rejected;
13. final append size overflow rejected;
14. os.replace failure leaves no T2 temp file;
15. POSIX executable/mode bits preserved where supported;
16. concurrent-content change -> FILE_CHANGED_DURING_OPERATION;
17. confirmation rejection leaves file unchanged;
18. result never echoes submitted content;
19. dangerous-pattern defaults include config/AGENT.md;
20. explicit danger_patterns=[] is honored.

---

# 60. File search tests

Required:

1. literal occurrence result shape;
2. regex occurrence result shape;
3. case-sensitive behavior;
4. invalid regex -> FILE_REGEX_INVALID with query_index only;
5. no match -> successful zero result;
6. multiple files;
7. missing member path fails before partial work;
8. duplicate lexical path rejected;
9. samefile alias rejected when supported;
10. query-count hard max;
11. query-length hard max;
12. max_results_per_file zero/negative/True rejected;
13. exact per-file truncation;
14. aggregate result cap;
15. bounded snippet still contains match;
16. file bytes/decode bounds respected.

---

# 61. File replace tests

Required:

1. literal replace;
2. regex replace;
3. multiple query order preserved;
4. replacement count counts occurrences;
5. report cap does not cap mutation;
6. no-match success changed=false;
7. mismatched replacement count rejected before write;
8. all files preflight before any mutation;
9. one confirmation rejection -> zero files changed;
10. invalid regex -> zero files changed;
11. second commit failure -> first file normally rolled back;
12. rollback only when post-write hash still matches;
13. rollback conflict reported without overwriting external change;
14. invalid-decode file -> zero files changed;
15. symlink member -> zero files changed;
16. resulting-size overflow -> zero files changed;
17. temporary artifacts cleaned.

---

# 62. Glob tests

Required:

1. recursive match;
2. non-recursive match;
3. explicit subfolder pattern compatibility;
4. canonical result envelope;
5. matches are descriptors, never warning strings;
6. no-match successful empty list;
7. nonexistent root -> GLOB_ROOT_NOT_FOUND;
8. root is file -> GLOB_ROOT_NOT_DIRECTORY;
9. empty pattern -> INVALID_ARGUMENT;
10. pattern length hard max;
11. absolute pattern rejected;
12. ../ escape rejected and outside file never returned;
13. nested a/../../ escape rejected;
14. recursive must be exact bool;
15. max_results 0/-1/True rejected;
16. max_results over hard max rejected;
17. constructor default over hard max rejected;
18. exactly N matches -> truncated=false;
19. N+1 matches -> truncated=true;
20. intentionally shuffled generator -> deterministic lexical first N;
21. absolute POSIX path representation;
22. file/directory kind classification;
23. symlink descriptor and no intentional ** recursion through symlink dirs where supported.

---

# 63. Cross-contract tests

Recommended assertions:

~~~text
File/Glob public run terminal values are JSON-safe
ok/error invariants match T1
meta.version is 2.0.0
no legacy "Lỗi:" / "Thành công:" control parsing is required
scope guard remains green
_shared files remain unchanged
~~~

---

# PART VII — EXIT GATE

# 64. Focused T2 gate

~~~text
python -m pytest -q   tools/v1/test/test_file_tool.py   tools/v1/test/test_glob_search_tool.py   tools/v1/test/test_filesystem_tools_contract.py   tools/v1/test/test_shared_contracts.py   tools/v1/test/test_shared_limits.py   tools/v1/test/test_shared_metadata.py   tools/v1/test/test_shared_validation.py   tools/v1/test/test_scope_boundary.py
~~~

If the optional cross-contract file is not added, omit it from the command.

---

# 65. Full Tools V1 gate

~~~text
python -m pytest -q tools/v1/test
~~~

Live scripts remain excluded.

---

# 66. Repository regression gate

Because File/Glob public terminal results change in T2, final T2 must also pass the repository's existing full pytest CI.

No SE/CL code may be altered to make that gate pass.

If an external consumer test fails because it encodes the legacy File/Glob string result, T2 must record that as a consumer migration requirement rather than patch SE/CL in this branch.

---

# 67. Diff gate

Compare against the T2 audit baseline:

~~~text
dda9087f94f1c5bc3b3f23e3363671ad328a8e75
~~~

Allowed physical code diff:

~~~text
tools/v1/file_tool.py
tools/v1/find_by_glob.py
~~~

Allowed tests/docs:

~~~text
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
tools/v1/test/test_filesystem_tools_contract.py
tools/v1/T2_FILESYSTEM_TOOLS_IMPLEMENTATION_PLAN.md
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md
~~~

Forbidden:

~~~text
tools/v1/_shared/**
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
tools/v1/web_tool/**
tools/v1/live/**
se/**
cl/**
~~~

---

# 68. T2 completion invariants

T2 can be marked complete only if all are true:

1. existing unreadable files can never be treated as empty mutation input;
2. mutation never uses errors="replace";
3. glob cannot escape root_dir through absolute/.. patterns;
4. all filesystem inputs/outputs defined above are hard bounded;
5. explicit invalid numeric limits never fall back silently;
6. no T2 temp file leaks on tested failure paths;
7. existing POSIX mode bits are preserved on replace where supported;
8. symlink mutation is rejected;
9. new empty-file creation works;
10. multi-file replace preflights and confirms before the first write;
11. replacement report limits do not change mutation scope;
12. normal partial-commit failure is rolled back;
13. rollback never overwrites an externally changed post-write file;
14. glob truncation is exact and deterministic;
15. no warning/error presentation string is mixed into result arrays;
16. File/Glob public results follow canonical ToolResult;
17. Metadata V2 remains untouched until T7;
18. _shared remains unchanged;
19. no se/cl/live file is changed;
20. focused + tools/v1 + repository regression gates are green.

---

# 69. Known non-goals / residual semantics

T2 does not provide:

- a filesystem sandbox root for File Tool;
- authorization/HITL;
- cross-filesystem multi-file atomic transactions;
- portable kernel-level compare-and-swap replacement;
- arbitrary-file ACL/owner preservation guarantees;
- wall-clock preemption of Python stdlib regex;
- directory create/delete/list capabilities beyond current write-parent behavior;
- Metadata V2 projection exports;
- live harness migration.

These are not silently claimed.

---

# 70. Implementation verdict

T2 can close the audited File/Glob P0/P1 set without changing T1 foundation or any consumer layer.

Correct architecture:

~~~text
T1 shared primitives
      ↓
File Tool 2.0   Glob Tool 2.0
      ↓              ↓
canonical ToolResult + hard intrinsic bounds
      ↓              ↓
T7 later publishes logical Metadata V2 projections
~~~

T2 code implementation must not start until this plan is accepted/frozen.

AUDIT STATUS: COMPLETE  
T2 PLAN STATUS: FROZEN FOR IMPLEMENTATION REVIEW  
PRODUCTION CODE STATUS: NOT IMPLEMENTED  
NEXT ALLOWED STEP: implement T2-A through T2-H on the same branch, within the exact diff boundary above.
