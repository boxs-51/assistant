# TV1-T9 Logical Export Migration Completion

Repository: `boxs-51/assistant`  
Track: `TV1-T*` — Tools V1 / Metadata / consumer convergence  
T9 origin: `3560a2d034d25659aa535ba91b90aa48256f5ac1`  
Accepted stacked T9-G: `b763a11ec73f178a422a9cf8781e06f507f3deba`  
Canonical integration base: `main@28ae61190bc0e4ccbdcf3d208cf32d3145988128`  
Canonical re-anchor candidate: `f0834c44bc8011f3a75874ae23f9bd634e51cdac`  
Issue: #12  
Final integration PR: #37  
Status: **TV1-T9-A→H IMPLEMENTED / RE-ANCHORED / GREEN BEFORE FINAL DOCS CI**

---

# 1. Objective completed

TV1-T9 migrates the remaining broad physical Tools V1 capability identities to
canonical Metadata V2 logical exports while retaining the physical Python
implementations and physical ToolResult provenance.

Physical capability umbrellas removed from canonical capability/model projection:

```text
file_tool
find_by_glob
terminal_tool
window_tool
desktop_automation
```

Canonical non-Web logical export surface:

```text
file.read
file.search
file.write
file.append
file.replace

glob.find

terminal.run
terminal.launch

window.list
window.find
window.geometry
window.focus
window.close
window.minimize
window.maximize
window.restore

desktop.screen_info
desktop.mouse_move
desktop.mouse_click
desktop.mouse_drag
desktop.mouse_scroll
desktop.type_text
desktop.press_key
desktop.hotkey
```

Exactly 24 non-Web logical IDs are advertised by the default CLIENT placement.
Web remains SERVER-only by default.

---

# 2. Identity contract

TV1-T9 preserves the separation established by T7/T8:

```text
logical capability identity
    !=
physical implementation identity
```

Examples:

```text
logical file.append
  -> bind action=write, mode=a
  -> physical file_tool.run(...)
  -> ToolResult.tool   = file_tool
  -> ToolResult.action = write

logical window.geometry
  -> bind action=get_geometry
  -> physical window_tool.run(...)
  -> ToolResult.tool   = window_tool
  -> ToolResult.action = get_geometry

logical desktop.screen_info
  -> bind action=get_screen_info
  -> physical desktop_automation.run(...)
  -> ToolResult.tool   = desktop_automation
  -> ToolResult.action = get_screen_info
```

Logical export version remains `1.0`.
Physical package version remains implementation provenance (`2.0.0` for the migrated
physical packages) and is not rewritten into logical identity.

---

# 3. Stage results

## TV1-T9-A — contract freeze

Frozen and regression-tested:

- exact 24 logical IDs;
- action-specific strict schemas;
- immutable bind maps;
- logical/physical version separation;
- exact CLIENT placement target;
- command-reviewer logical migration target;
- physical ToolResult provenance.

## TV1-T9-B — File / Glob / Terminal

Completed atomically:

- File -> five logical exports;
- Glob -> `glob.find` with empty bind;
- Terminal -> `terminal.run/launch`;
- command-reviewer -> exact eight logical File/Glob/Terminal IDs;
- CLIENT default placement -> first eight T9 IDs;
- physical direct `run(...)` compatibility retained.

## TV1-T9-C — Window / Desktop

Completed:

- Window -> eight logical exports;
- Desktop -> eight logical exports;
- CLIENT default placement -> exact 24 non-Web IDs;
- no implicit Agent manifest expansion;
- selector and coordinate-pair schema invariants retained;
- real-machine GUI side effects excluded from regression execution.

## TV1-T9-D — SERVER / CLIENT parity

Real repository manifests/config prove:

- identical canonical SERVER and CLIENT definitions for all 24 IDs;
- SERVER + CLIENT implementations coexist without canonical rewrite;
- divergent same-ID registration rejects atomically;
- routing priority remains same-connection CLIENT first, SERVER fallback;
- logical version and physical version remain separate.

## TV1-T9-E — capability / Agent projection

Real production loaders prove:

- registry/catalog/model-facing projection uses logical IDs only;
- physical roots are absent as capability IDs;
- public capability list/get returns canonical logical definitions plus separate
  implementation provenance;
- command-reviewer exposes exact eight logical File/Glob/Terminal IDs;
- web-researcher remains exactly `web.search/read/read_many`;
- coordinator references the two Agent capability IDs;
- Window/Desktop are not implicitly projected into Agent manifests;
- repository-wide Agent count is not frozen by T9-E.

## TV1-T9-F — execution equivalence

Representative execution proof confirms:

- `file.append -> action=write, mode=a`;
- `glob.find` uses empty bind;
- `terminal.run -> action=run`;
- `window.geometry -> get_geometry`;
- `desktop.screen_info -> get_screen_info`;
- non-idempotent `desktop.mouse_click` mapping is exercised through a fake only;
- immutable bound fields cannot be overridden;
- direct physical `run(...)` entrypoints remain compatible.

## TV1-T9-G — compatibility / collision / lifecycle gate

The frozen gate matrix verifies existing real regressions for:

- V1 compatibility;
- V1/V2 and V2/V2 collision/package fail-closed behavior;
- transitive tombstones and wildcard rejection;
- registration batch atomicity;
- ACK/READY and reconnect generation rebinding;
- R6/R7 fingerprint/reconciliation/continuation fences;
- real Agent projection;
- MCP ownership/execution regressions;
- Web SERVER-only default placement;
- no live real-machine harness execution.

## TV1-T9-H — canonical-main re-anchor

Entry dependencies were released only after:

- AE-R10 FINAL CLOSED;
- PTC-3B FINAL GREEN and merged;
- exact-main Architecture rerun GREEN.

Immediately before H branch creation:

```text
origin -> current main changed paths: 28
origin -> accepted T9-G paths:       78
exact overlap:                        0
```

H was created from exact:

```text
main@28ae61190bc0e4ccbdcf3d208cf32d3145988128
```

The accepted T9 path set was replayed by exact Git blob identity rather than by merging
historical Tools branch history.

Verified:

```text
main -> H changed paths:             78
accepted T9-G blob identity:         78/78 exact
current-main R10/PTC blob identity:  28/28 exact
missing accepted paths:               0
extra replay paths:                    0
```

No provider/R10/PTC path drift was introduced.

---

# 4. Mandatory TV1-T9 regression matrix

All frozen TV1-T9 requirements are covered on the accepted implementation and the
canonical-main re-anchor:

1. five remaining physical tools publish canonical Metadata V2 manifests;
2. physical package identity/version remain provenance;
3. all 24 logical export IDs are unique;
4. logical export names equal IDs;
5. logical input schemas are strict objects;
6. logical schemas do not expose caller-controlled `action`;
7. `file.write` binds `mode=w`;
8. `file.append` binds `mode=a`;
9. bound fields cannot be overridden;
10. `glob.find` works with empty bind;
11. physical roots are absent from SERVER capability identity;
12. physical roots are absent from CLIENT registry identity;
13. direct physical Python `run(...)` entrypoints remain compatible;
14. ToolResult physical identity is unchanged;
15. default CLIENT placement contains the 24 non-Web logical IDs;
16. default CLIENT placement contains zero Web logical IDs;
17. V2 wildcard remains rejected;
18. command-reviewer contains only the intended eight logical IDs;
19. web-researcher remains exactly the three logical Web IDs;
20. Window/Desktop are not implicitly added to Agent manifests;
21. equal SERVER+CLIENT definitions converge;
22. divergent registration rejects atomically;
23. top-level V2 CLIENT discovery/loading works;
24. top-level V2 SERVER loading works;
25. logical version is separate from physical package version;
26. capability list/get exposes canonical logical definitions;
27. routing priority is unchanged;
28. reconnect/ACK/READY behavior is unchanged;
29. R7 reconciliation/version/fingerprint fences remain unchanged;
30. MCP behavior remains unchanged;
31. provider/PTC files are not changed by the T9 re-anchor;
32. Agent Execution ownership is not absorbed by T9;
33. `tools/v1/live/**` is unchanged and not executed;
34. exact-head full CI is green before this completion-doc commit.

---

# 5. Canonical-main integration CI evidence

Re-anchor code/test HEAD:

```text
f0834c44bc8011f3a75874ae23f9bd634e51cdac
```

Architecture Baseline run:

```text
#937
run 35901536549
```

Linux full repository suite:

```text
1431 passed
1 skipped
51 warnings
159 subtests passed
```

Windows client contracts:

```text
101 passed
2 warnings
```

The Linux job executes `python -m pytest -q` at repository root, so the reconciled
head runs the Tools unit suite, architecture/integration/e2e regressions, lifecycle
gates, MCP regressions, and affected real WebSocket tests that belong to the default
suite. The Windows job independently executes all `cl/tests`.

No live real-machine Tools harness is part of this default CI and none was executed.

---

# 6. Production and ownership boundaries preserved

TV1-T9 does not redefine:

- provider-native tool aliases/function names;
- provider TOOL_CALLING eligibility;
- AE-R10 deadline/retry/fallback authority;
- CapabilityRoutingPolicy priority;
- R6/R7 execution/reconciliation authority;
- MCP ownership/protocol;
- Central Asset Storage;
- Context/Memory authority;
- SQL migrations;
- live real-machine harness behavior.

Provider-specific lowering remains PTC authority.

Agent Execution R11+ remains Agent Execution authority.

Central Asset / CTX remain downstream parked workstreams.

---

# 7. Completion-doc exact-head rule

This document is the only planned file added after the green reconciled H code/test
candidate.

Therefore final T9 freeze requires the documentation HEAD containing this file to pass
the same Architecture Baseline Linux + Windows gates before Issue #12 is closed or PR
#37 is merged.

If canonical `main` advances before final merge/freeze, T9-H must:

1. resolve the new main SHA;
2. recompute origin->main vs accepted T9 path overlap;
3. re-anchor again if required;
4. preserve the newly accepted upstream authority byte-for-byte;
5. rerun exact-head CI.

---

# 8. TV1-T9 -> TV1-T10 handoff

TV1-T10 remains **NOT OPENED by this completion document**.

Reserved TV1-T10 scope is the live harness / real-machine exit gate:

```text
tools/v1/live/**
live-only docs/tests/helpers
```

Expected T10 topics include:

- explicit `RUN_TOOLS_V1_LIVE=1` opt-in;
- temp/configured artifact roots;
- portable `sys.executable` command discovery;
- deterministic setup/teardown;
- structured ToolResult consumption;
- GUI target isolation;
- deterministic process cleanup;
- separate network live gates;
- exclusion from default unit CI.

A fresh TV1-T9 -> TV1-T10 boundary audit is required before any live-harness production
or real-machine implementation work.

---

# 9. Final state before documentation CI

```text
TV1-T8  CLOSED / GREEN / FINAL-FROZEN
TV1-T9-A COMPLETE
TV1-T9-B COMPLETE
TV1-T9-C COMPLETE
TV1-T9-D COMPLETE
TV1-T9-E COMPLETE
TV1-T9-F COMPLETE
TV1-T9-G COMPLETE
TV1-T9-H RE-ANCHORED / CODE+TEST GREEN @ f0834c44
TV1-T9   FINAL DOCS CI PENDING
TV1-T10  PHASE RESERVED / NOT OPEN
```

Issue #12 remains the durable coordination authority until final documentation CI,
final main re-resolution, merge/freeze, and T9->T10 handoff audit complete.
