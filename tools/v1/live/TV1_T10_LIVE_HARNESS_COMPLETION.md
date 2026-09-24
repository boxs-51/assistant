# TV1-T10 Live Harness / Real-Machine Exit Gate Completion

Repository: `boxs-51/assistant`  
Track: `TV1-T*` — Tools V1 live / real-machine validation  
Coordination authority: Issue #38  
Accepted T10-A→E source: `8f789897c2b7cc5ae06b0a3f9f3de2cb2cc0eed1`  
Canonical integration base: `main@0853f9bf9be06ff89b3e4b826850e56d2f92c786`  
Canonical convergence code/test candidate: `c5046a252a78a954f7c79514b514696e2eb589ab`  
Final integration PR: #61  
Status: **TV1-T10-A→F IMPLEMENTED / CANONICAL CONVERGENCE GREEN / FINAL DOCS CI PENDING**

---

# 1. Objective completed

TV1-T10 modernizes and hardens the legacy Tools V1 live / real-machine harness
without weakening the production contracts frozen through TV1-T9.

The completed live surface is intentionally opt-in and remains outside default
unit/Architecture CI side effects.

Primary implementation scope:

```text
tools/v1/live/**
tools/v1/test/test_live_*.py
```

Production File/Glob/Terminal/Window/Desktop/Web runtimes were not modified by
T10.

---

# 2. Final live-harness contract

The final harness preserves the T10-A frozen properties.

## 2.1 Explicit fail-closed opt-in

Master live gate:

```text
RUN_TOOLS_V1_LIVE=1
```

NETWORK additionally requires:

```text
RUN_TOOLS_V1_LIVE_NETWORK=1
```

GUI additionally requires:

```text
RUN_TOOLS_V1_LIVE_GUI=1
```

Only the literal string `"1"` enables a gate.

Missing/incorrect gates fail closed before scenario side effects.

## 2.2 Artifact and cwd authority

Live artifacts are written only under a configured absolute external root or a
system temporary root.

```text
TOOLS_V1_LIVE_ARTIFACT_ROOT=<absolute path outside repository>
```

Repository-root or descendant artifact roots are rejected.

The harness does not use the source-tree live directory as ambient cwd authority.
Scenario tool/subprocess calls receive explicit scenario-owned cwd/path/root
inputs.

## 2.3 Structured ToolResult authority

Live correctness consumes structured ToolResult fields:

```text
ok
tool
action
data
error
meta
```

No normal-path correctness depends on `str(ToolResult)`, `repr(ToolResult)`,
or regex parsing of a rendered result.

Evidence preserves structured error classification while replacing arbitrary raw
error messages with a bounded safe message and recursively redacting
secret-bearing detail fields.

## 2.4 Explicit scenario model

Historical ordered `unittest` workflows are no longer execution authority.

The live harness uses explicit independent scenario definitions with:

- deterministic setup;
- ordered steps;
- explicit cross-step scenario state;
- teardown/finally cleanup;
- structured evidence;
- LOCAL / NETWORK / GUI category separation.

## 2.5 Stable process ownership

Background-process cleanup uses captured stable `ProcessIdentity` authority,
not raw numeric PID alone.

PID reuse is fail-closed. Unowned processes are never targeted.

Cleanup records:

```text
owned_pids
terminated_pids
still_alive_pids
errors
```

Any cleanup survivor/error prevents a successful run result.

## 2.6 GUI ownership

GUI targeting is discovery-first and ownership-scoped.

Mutation authority requires an exact verified handle + PID belonging to the
scenario-owned process tree.

Generic title text is not cleanup or mutation authority.

Desktop click/type occurs only after owned target discovery, focus and geometry
preconditions.

## 2.7 Network separation

NETWORK proof is independent from LOCAL correctness.

The live network scenario distinguishes:

```text
REMOTE_UNAVAILABLE
EMPTY_VALID_RESULT
TOOL_CONTRACT_FAILURE
LIVE_SCENARIO_ASSERTION_FAILED
```

Malformed successful Web data cannot be collapsed into a harmless valid-empty
result.

LOCAL correctness is not invalidated by remote/network unavailability.

---

# 3. Stage results

## TV1-T10-A — contract freeze

Final accepted HEAD:

```text
71b5b355dfb449d5dd633e5cb7b9df6aa1451fac
```

Architecture Baseline #942 / run `35905988671`:

```text
Linux:   1431 passed, 1 skipped, 51 warnings, 159 subtests passed
Windows: 101 passed, 2 warnings
```

T10-A closed docs-only with no real-machine execution and no production/runtime
change.

Frozen contract includes:

- literal master/network/GUI gates;
- external artifact-root rules;
- no source ambient cwd authority;
- structured ToolResult consumption;
- evidence schema `tools.v1.live.evidence/1`;
- explicit LOCAL/NETWORK/GUI scenarios;
- owned-process lifecycle;
- handle/PID GUI ownership;
- portable `sys.executable`;
- default-CI live exclusion.

## TV1-T10-B — runner / helpers

Final accepted HEAD:

```text
e8a74f26b70fa76c75dd0607ccd131bdeb888826
```

Architecture Baseline #961 / run `35954453941`:

```text
Linux:   1447 passed, 1 skipped, 51 warnings, 159 subtests passed
Windows: 101 passed, 2 warnings
```

Accepted closures:

```text
P1-T10-B-1 raw-PID/PID-reuse cleanup authority: CLOSED
P1-T10-B-2 secret-bearing evidence message leak: CLOSED
```

T10-B introduced only the side-effect-free harness/runner infrastructure and
fake/unit coverage. No real network, GUI, or persistent background-process
execution occurred in B.

## TV1-T10-C — LOCAL File / Glob / Terminal

Final accepted HEAD:

```text
c4ee860f2be1bf6344b44c02d662b18b5aead66f
```

Architecture Baseline #973 / run `35958199325`:

```text
Linux:   1460 passed, 1 skipped, 51 warnings, 159 subtests passed
Windows: 101 passed, 2 warnings
```

Accepted closure:

```text
P1-T10-C-1 exact cwd/path/root evidence binding: CLOSED
```

Real Windows LOCAL operator evidence:

```text
schema:    tools.v1.live.evidence/1
category:  LOCAL
status:    PASS
scenario:  local-file-glob-terminal
exit code: 0
gates:     RUN_TOOLS_V1_LIVE only
NETWORK:   OFF
GUI:       OFF
artifact:  outside repository source tree

steps:
  terminal-run     PASS
  file-write       PASS
  file-read        PASS
  glob-find        PASS
  terminal-launch  PASS

cleanup:
  owned_pids:       [14428]
  terminated_pids:  [14428]
  still_alive_pids: []
  errors:           []
```

The accepted LOCAL validators bind returned cwd/path/root data to the exact
scenario-owned artifact directory and file.

## TV1-T10-D — NETWORK Web search / scrape

Final accepted HEAD:

```text
c68d66353017fc10c3775f64f20e88650111e047
```

Architecture Baseline #1079 / run `35988725772`:

```text
Linux:   1471 passed, 1 skipped, 51 warnings, 159 subtests passed
Windows: 101 passed, 2 warnings
```

Accepted closures:

```text
P1-T10-D-1 malformed-success vs valid-empty classification: CLOSED
P1-T10-D-2 WEB_CLEANUP_FAILED classification:              CLOSED
```

Real Windows NETWORK operator evidence:

```text
schema:           tools.v1.live.evidence/1
category:         NETWORK
status:           PASS
scenario:         network-web-search-scrape
exit code:        0
master gate:      ON
NETWORK gate:     ON
GUI gate:         OFF
LOCAL scenarios:  none
artifact:         outside repository source tree

environment:
  platform_system:  Windows
  platform_release: 10
  python_version:   3.12.10

steps:
  web-search  PASS
  web-scrape  PASS

cleanup:
  owned_pids:       []
  terminated_pids:  []
  still_alive_pids: []
  errors:           []
```

The final evidence-completeness audit accepted all required environment fields.

## TV1-T10-E — owned-target Window / Desktop GUI

Final accepted HEAD:

```text
8f789897c2b7cc5ae06b0a3f9f3de2cb2cc0eed1
```

Architecture Baseline #1122 / run `36008598644`, attempt 2:

```text
overall:                  SUCCESS
linux-full-suite:         SUCCESS
windows-client-contracts: SUCCESS
```

Accepted closures:

```text
P1-T10-E-1 repo-root / cwd authority:        CLOSED
P1-T10-E-2 PID-reuse identity proof:         CLOSED
P1-T10-E-3 discovery observability:          CLOSED / LIVE-PROVEN
P1-T10-E-4 Windows shell command transport:  CLOSED / LIVE-PROVEN
```

The P1-E-4 diagnostic boundary established that the production
Terminal/Window/Desktop stack was not defective; the live target command transport
was shell-unsafe on Windows. The harness fix uses a deterministic single-line
encoded Python loader while retaining the existing production Terminal launch
semantics.

Real Windows GUI operator evidence:

```text
schema:    tools.v1.live.evidence/1
category:  GUI
status:    PASS
scenario:  gui-window-desktop-owned-target
exit code: 0
master:    ON
GUI:       ON
NETWORK:   OFF
artifact:  outside repository source tree

steps:
  gui-target-launch          PASS
  window-discover-owned      PASS
  window-focus-owned         PASS
  window-geometry-owned      PASS
  desktop-click-owned        PASS
  desktop-type-owned         PASS
  window-verify-typed-owned  PASS
  window-close-owned         PASS

cleanup:
  owned_pids:       [30944]
  terminated_pids:  [30944]
  still_alive_pids: []
  errors:           []
```

Environment authority:

```text
platform_system:  Windows
platform_release: 10
python_version:   3.12.10
```

The final GUI run proves exact owned discovery, focus, geometry, Desktop input,
same-target verification, exact close and clean process-tree cleanup end-to-end.

## TV1-T10-F — canonical-main convergence / final exit gate

T10-F was claimed only after T10-E was independently FINAL GREEN.

Canonical main at claim and convergence:

```text
0853f9bf9be06ff89b3e4b826850e56d2f92c786
```

Accepted A→E replay source:

```text
8f789897c2b7cc5ae06b0a3f9f3de2cb2cc0eed1
```

Canonical convergence code/test candidate:

```text
c5046a252a78a954f7c79514b514696e2eb589ab
```

Branch:

```text
work/tv1-t10-f-0853f9bf
```

Draft integration PR:

```text
#61
```

Exact replay verification:

```text
replayed A→E paths:            9
accepted-source blob matches:  9/9 exact
main -> candidate paths:       9
extra paths:                   0
behind canonical main:         0 at convergence and pre-doc checkpoint
semantic replay drift:         none
content drift:                 none
```

Replayed path set:

```text
tools/v1/live/TV1_T10_A_LIVE_CONTRACT_FREEZE.md
tools/v1/live/harness.py
tools/v1/live/local_scenarios.py
tools/v1/live/network_scenarios.py
tools/v1/live/gui_scenarios.py
tools/v1/test/test_live_harness.py
tools/v1/test/test_live_local_scenarios.py
tools/v1/test/test_live_network_scenarios.py
tools/v1/test/test_live_gui_scenarios.py
```

No production Tool runtime, provider/PTC, Agent Execution, CAS, CTX, SQL,
requirements, or unrelated roadmap file is part of the convergence replay.

---

# 4. Mandatory TV1-T10 exit properties

All Issue #38 mandatory properties are satisfied on the accepted implementation:

1. explicit live opt-in before any side effect — **GREEN**;
2. no source-tree artifact writes — **GREEN**;
3. no ordered-test shared-state authority — **GREEN**;
4. no normal-path `str(ToolResult)` parsing — **GREEN**;
5. no generic-title GUI cleanup authority — **GREEN**;
6. deterministic cleanup for owned background processes — **GREEN**;
7. portable Python/command discovery — **GREEN**;
8. network-live gate separated from LOCAL correctness — **GREEN**;
9. live harness remains excluded from default real-machine CI execution — **GREEN**;
10. final evidence records OS/environment and exact scenario set — **GREEN**.

Open in-scope T10 P0/P1 at the convergence code/test checkpoint:

```text
P0: 0
P1: 0
```

---

# 5. Real-machine exit matrix

```text
LOCAL
  exact stage HEAD: c4ee860f
  platform:         Windows 10 / Python 3.12.10
  gate set:         master only
  scenario:         local-file-glob-terminal
  result:           PASS
  cleanup:          CLEAN

NETWORK
  exact stage HEAD: c68d6635
  platform:         Windows 10 / Python 3.12.10
  gate set:         master + NETWORK
  scenario:         network-web-search-scrape
  result:           PASS
  cleanup:          CLEAN

GUI
  exact stage HEAD: 8f789897
  platform:         Windows 10 / Python 3.12.10
  gate set:         master + GUI
  NETWORK:          OFF
  scenario:         gui-window-desktop-owned-target
  result:           PASS
  cleanup:          CLEAN
```

These are operator real-machine proofs. Default CI is not a substitute for them
and did not rerun their side effects.

---

# 6. Canonical convergence CI evidence

Code/test convergence HEAD:

```text
c5046a252a78a954f7c79514b514696e2eb589ab
```

Architecture Baseline:

```text
#1126
run 36012768166
overall: SUCCESS
```

Linux full repository suite:

```text
1732 passed
1 skipped
67 warnings
159 subtests passed
```

Windows client contracts:

```text
106 passed
2 warnings
```

Both jobs completed successfully on exact `c5046a25`.

The Linux job executes the normal repository pytest suite. The Windows job
independently executes `cl/tests`.

The live harness files are imported/tested through unit/fake coverage as part of
normal testing, but real LOCAL/NETWORK/GUI side effects remain opt-in and were not
triggered by Architecture #1126.

---

# 7. Ownership boundaries preserved

TV1-T10 does not redefine or absorb:

- production File/Glob/Terminal/Window/Desktop/Web runtime ownership;
- provider-native tool lowering or TOOL_CALLING policy;
- PTC contracts;
- Agent Execution retry/fallback/deadline/persistence ownership;
- AE-R11 persistence/performance ownership;
- Central Asset Storage ownership;
- CTX Context/Memory/Personalization ownership;
- SQL migrations/requirements authority;
- capability routing priority;
- MCP ownership/protocol.

Cross-track status at the convergence checkpoint:

```text
Issue #31 / AE-R11: soft main-drift / integration-order watch only
Issue #15 / CTX:    soft main-drift / integration-order watch only
Issue #47 / CAS-R0: closed authority / no active blocker
```

No concrete ownership conflict blocks the T10 completion path at this checkpoint.

---

# 8. Completion-document exact-head rule

This document is the only planned file added after the GREEN T10-F convergence
code/test candidate.

Therefore the final TV1-T10 freeze requires the documentation HEAD containing
this file to pass the same Architecture Baseline Linux + Windows gates before PR
#61 is eligible for final merge/freeze.

A documentation-only commit does not inherit #1126 as final exact-head CI
authority.

Required final sequence:

1. add this completion document on top of exact `c5046a25`;
2. run Architecture Baseline on the new documentation HEAD;
3. require Linux + Windows GREEN;
4. re-read Issue #38 / auditor findings;
5. re-resolve canonical `main`;
6. verify PR #61 changed-path scope and mergeability;
7. if `main` advanced, audit/re-anchor before merge as required;
8. merge only after the separate user/owner merge authorization.

No merge is authorized by this document.

---

# 9. Canonical-main drift rule before merge

If canonical `main` advances before final merge/freeze:

1. resolve the new main SHA;
2. inspect exact new main delta;
3. recompute overlap against the T10 owned path set;
4. preserve all accepted upstream authority;
5. re-anchor/replay T10 by exact content identity when required;
6. rerun exact-head Architecture CI;
7. repeat the final ownership/dependency audit.

Previously accepted detached LOCAL/NETWORK/GUI evidence remains stage evidence
unless a re-anchor changes the corresponding live semantics. Any semantic change
to those scenarios reopens the relevant real-machine gate.

---

# 10. Final state before documentation CI

```text
TV1-T10-A  CLOSED / GREEN
TV1-T10-B  CLOSED / GREEN
TV1-T10-C  CLOSED / GREEN / LOCAL LIVE-PROVEN
TV1-T10-D  CLOSED / GREEN / NETWORK LIVE-PROVEN
TV1-T10-E  CLOSED / GREEN / GUI LIVE-PROVEN
TV1-T10-F  CANONICAL CONVERGENCE GREEN @ c5046a25

T10 convergence CI #1126: GREEN
open T10 P0/P1:             NONE
completion document:        PRESENT
final documentation CI:     PENDING
PR #61 merge:               NOT AUTHORIZED YET
```

Issue #38 remains the durable coordination authority until the documentation
HEAD is GREEN, canonical main is re-resolved, the independent final audit is
clean, and the separately authorized merge/freeze is complete.

No subsequent TV1 stage is opened by this completion document.
